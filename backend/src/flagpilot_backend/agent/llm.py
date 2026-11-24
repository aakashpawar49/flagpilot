"""LLM access through any OpenAI-compatible chat completions API.

Every agent step is one request that must return a JSON object matching a
Pydantic model. Invalid output is sent back with the validation error so the
model can correct itself. Free tiers rate-limit hard, so 429s are retried.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from pydantic import BaseModel, ValidationError

from ..config import get_settings

Emit = Callable[..., Awaitable[None]]
# Extra checks beyond the schema. Return an error message, or None if valid.
Check = Callable[[Any], Awaitable[str | None]]


class LLMError(RuntimeError):
    pass


_client: openai.AsyncOpenAI | None = None


def get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        s = get_settings()
        if not s.llm_api_key:
            raise LLMError("LLM_API_KEY is not set.")
        _client = openai.AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key, max_retries=0, timeout=180)
    return _client


def extract_json(text: str) -> Any:
    """Parse a JSON object from model text, tolerating ```json fences or prose around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def schema_instructions(model: type[BaseModel]) -> str:
    schema = json.dumps(model.model_json_schema(), indent=1)
    return (
        "Respond with ONE JSON object only, no prose and no Markdown fences. "
        f"It must validate against this JSON Schema:\n{schema}"
    )


async def _create(messages: list[dict[str, str]], json_mode: bool) -> str:
    s = get_settings()
    kwargs: dict[str, Any] = {"model": s.llm_model, "messages": messages}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    for attempt in range(5):
        try:
            resp = await get_client().chat.completions.create(**kwargs)
            choice = resp.choices[0]
            if choice.finish_reason == "length":
                raise LLMError("Model output was cut off (max tokens). Try a smaller change or a larger model.")
            return choice.message.content or ""
        except openai.RateLimitError as exc:
            wait = _retry_after(exc) or min(60, 10 * (attempt + 1))
            if attempt == 4:
                raise LLMError(f"Rate limited by the LLM provider: {exc}") from exc
            await asyncio.sleep(wait)
        except openai.BadRequestError as exc:
            # Some providers reject response_format; fall back to prompt-only JSON.
            if json_mode and "response_format" in str(exc):
                kwargs.pop("response_format", None)
                json_mode = False
                continue
            raise LLMError(f"LLM request rejected: {exc}") from exc
        except (openai.APIConnectionError, openai.InternalServerError) as exc:
            if attempt == 4:
                raise LLMError(f"LLM provider unavailable: {exc}") from exc
            await asyncio.sleep(5 * (attempt + 1))
    raise LLMError("LLM request failed after retries.")


def _retry_after(exc: openai.RateLimitError) -> float | None:
    try:
        return float(exc.response.headers.get("retry-after", ""))
    except (ValueError, AttributeError):
        return None


async def complete_json[T: BaseModel](
    *,
    system: str,
    user: str,
    output: type[T],
    emit: Emit | None = None,
    check: Check | None = None,
    attempts: int = 3,
) -> T:
    """One structured LLM call with self-correction on invalid output."""
    messages = [
        {"role": "system", "content": f"{system}\n\n{schema_instructions(output)}"},
        {"role": "user", "content": user},
    ]
    last_error = ""
    for _ in range(attempts):
        text = await _create(messages, json_mode=True)
        try:
            result = output.model_validate(extract_json(text))
            problem = await check(result) if check else None
            if problem is None:
                return result
            last_error = problem
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:2000]
        if emit:
            await emit("error", f"Model output needs a fix, asking it to correct: {last_error[:300]}")
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"That response is invalid: {last_error}\nReturn the corrected JSON object."},
        ]
    raise LLMError(f"Model did not return valid output after {attempts} attempts: {last_error}")
