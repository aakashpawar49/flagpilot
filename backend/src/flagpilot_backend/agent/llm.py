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
        _client = openai.AsyncOpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key, max_retries=0, timeout=120)
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


class _Overloaded(Exception):
    """The model stayed unavailable (503 / quota exhausted); try the next model."""


async def _create_with(model: str, messages: list[dict[str, str]], json_mode: bool) -> str:
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    attempts = 3
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            resp = await get_client().chat.completions.create(**kwargs)
            choice = resp.choices[0]
            if choice.finish_reason == "length":
                raise LLMError("Model output was cut off (max tokens). Try a smaller change or a larger model.")
            return choice.message.content or ""
        except openai.RateLimitError as exc:
            if last:
                raise _Overloaded(f"{model} rate limited: {exc}") from exc
            await asyncio.sleep(min(_retry_after(exc) or 10 * (attempt + 1), 30))
        except openai.BadRequestError as exc:
            # Some providers reject response_format; fall back to prompt-only JSON.
            if json_mode and "response_format" in str(exc):
                kwargs.pop("response_format", None)
                json_mode = False
                continue
            raise LLMError(f"LLM request rejected: {exc}") from exc
        except openai.InternalServerError as exc:
            # 503 "high demand" can last minutes on free tiers: move to the next model now.
            raise _Overloaded(f"{model} unavailable: {exc}") from exc
        except openai.APIConnectionError as exc:  # includes timeouts
            if last:
                raise _Overloaded(f"{model} unreachable: {exc}") from exc
            await asyncio.sleep(3)
    raise _Overloaded(f"{model} failed after retries.")


async def _create(messages: list[dict[str, str]], json_mode: bool, emit: Emit | None = None) -> str:
    """Tries the main model, then each fallback model, when a model is overloaded or out of quota."""
    s = get_settings()
    models = [s.llm_model, *[m for m in s.llm_fallback_models if m != s.llm_model]]
    errors: list[str] = []
    for i, model in enumerate(models):
        try:
            return await _create_with(model, messages, json_mode)
        except _Overloaded as exc:
            errors.append(str(exc))
            if emit and i + 1 < len(models):
                await emit("info", f"{model} is overloaded or out of quota; switching to {models[i + 1]}.")
    raise LLMError("All models unavailable: " + " | ".join(errors))


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
        text = await _create(messages, json_mode=True, emit=emit)
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
