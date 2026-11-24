import json

import httpx2
import openai
from pydantic import BaseModel

from flagpilot_backend.agent import llm

BASE = "https://llm.test/v1"


class Answer(BaseModel):
    value: int


def completion(content: str) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        },
    )


def fake_llm(monkeypatch, responses: list[httpx2.Response]) -> list[dict]:
    """Points the shared client at a mock transport; returns the captured request bodies."""
    sent: list[dict] = []
    queue = list(responses)

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return queue.pop(0)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(llm, "_last_good", None)
    monkeypatch.setattr(llm, "_client", openai.AsyncOpenAI(base_url=BASE, api_key="k", max_retries=0, http_client=http))
    return sent


def test_extract_json_handles_fences_and_prose():
    assert llm.extract_json('```json\n{"value": 1}\n```') == {"value": 1}
    assert llm.extract_json('Here you go: {"value": 2} hope that helps') == {"value": 2}


async def test_complete_json_self_corrects(monkeypatch):
    sent = fake_llm(monkeypatch, [completion('{"value": "not a number"}'), completion('{"value": 7}')])
    events = []

    async def emit(kind, message, detail=None):
        events.append(kind)

    result = await llm.complete_json(system="s", user="u", output=Answer, emit=emit)
    assert result.value == 7
    assert len(sent) == 2
    assert sent[1]["messages"][-1]["role"] == "user"
    assert "invalid" in sent[1]["messages"][-1]["content"]
    assert events == ["error"]


async def test_complete_json_runs_semantic_check(monkeypatch):
    fake_llm(monkeypatch, [completion('{"value": 3}'), completion('{"value": 4}')])

    async def must_be_even(a: Answer):
        return None if a.value % 2 == 0 else "value must be even"

    assert (await llm.complete_json(system="s", user="u", output=Answer, check=must_be_even)).value == 4


async def test_falls_back_when_response_format_unsupported(monkeypatch):
    sent = fake_llm(
        monkeypatch,
        [
            httpx2.Response(400, json={"error": {"message": "response_format is not supported"}}),
            completion('{"value": 1}'),
        ],
    )
    assert (await llm.complete_json(system="s", user="u", output=Answer)).value == 1
    assert "response_format" in sent[0]
    assert "response_format" not in sent[1]


async def test_switches_to_fallback_model_when_overloaded(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    overloaded = httpx2.Response(503, json={"error": {"message": "high demand"}})
    sent = fake_llm(monkeypatch, [overloaded, completion('{"value": 5}')])
    events = []

    async def emit(kind, message, detail=None):
        events.append(message)

    assert (await llm.complete_json(system="s", user="u", output=Answer, emit=emit)).value == 5
    s = llm.get_settings()
    assert [b["model"] for b in sent] == [s.llm_model, s.llm_fallback_models[0]]
    assert any("switching to" in m for m in events)
