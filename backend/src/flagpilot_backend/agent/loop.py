"""A small tool-use loop on the Claude Messages API.

Each agent gets read-only tools plus one *terminal* tool (submit_plan, finish).
The loop runs until the terminal tool succeeds. Nothing here writes to Flagsmith
or GitHub: the pipeline does that, and only after a human approves.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anthropic

from ..config import get_settings

Emit = Callable[..., Awaitable[None]]

# Server-side fallback: if a request is declined by a safety classifier, the API
# re-runs it on a fallback model inside the same call instead of failing the run.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AgentError(RuntimeError):
    pass


class ToolInputError(ValueError):
    """Raised by a handler when the model's input is invalid; sent back as is_error."""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]
    terminal: bool = False

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            # Stream large inputs (whole files) as they're generated; handlers validate.
            "eager_input_streaming": True,
        }


_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        key = get_settings().anthropic_api_key
        _client = anthropic.AsyncAnthropic(api_key=key) if key else anthropic.AsyncAnthropic()
    return _client


def _summarize_input(data: dict[str, Any]) -> dict[str, Any]:
    """Keep tool-call events readable in the UI: trim long string fields."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + f"... ({len(v)} chars)"
        else:
            out[k] = v
    return out


async def run_agent(
    *,
    system: str,
    user: str,
    tools: list[Tool],
    emit: Emit,
    effort: str = "high",
    max_turns: int = 30,
) -> dict[str, Any]:
    """Runs until a terminal tool succeeds; returns that tool's input."""
    client = get_client()
    model = get_settings().anthropic_model
    by_name = {t.name: t for t in tools}
    terminal_names = [t.name for t in tools if t.terminal]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

    for _ in range(max_turns):
        async with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            system=system,
            tools=[t.definition() for t in tools],
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            response = await stream.get_final_message()

        if response.stop_reason == "refusal":
            raise AgentError(f"Model declined the request: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise AgentError("Model hit max_tokens before finishing; tool input may be truncated.")

        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "thinking" and block.thinking:
                await emit("thinking", block.thinking)
            elif block.type == "text" and block.text.strip():
                await emit("info", block.text.strip())

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            if response.stop_reason == "pause_turn":
                continue
            messages.append({"role": "user", "content": f"Finish by calling {' or '.join(terminal_names)}."})
            continue

        results: list[dict[str, Any]] = []
        finished: dict[str, Any] | None = None
        for tu in tool_uses:
            tool = by_name.get(tu.name)
            tool_input = tu.input if isinstance(tu.input, dict) else json.loads(tu.input)
            await emit("tool", f"{tu.name}", {"input": _summarize_input(tool_input)})
            if tool is None:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"Unknown tool {tu.name}",
                        "is_error": True,
                    }
                )
                continue
            try:
                output = await tool.handler(tool_input)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": output})
                if tool.terminal:
                    finished = tool_input
            except ToolInputError as exc:
                await emit("error", f"{tu.name}: {exc}")
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(exc), "is_error": True})

        if finished is not None:
            return finished
        messages.append({"role": "user", "content": results})

    raise AgentError(f"Agent did not finish within {max_turns} turns.")


async def run_structured(*, system: str, user: str, output_model: type, effort: str = "medium") -> Any:
    """Single call that returns a validated Pydantic object (structured outputs)."""
    client = get_client()
    response = await client.beta.messages.parse(
        model=get_settings().anthropic_model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=output_model,
        output_config={"effort": effort},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise AgentError(f"Model declined the request: {response.stop_details}")
    if response.parsed_output is None:
        raise AgentError("Model returned no structured output.")
    return response.parsed_output
