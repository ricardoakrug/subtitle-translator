import json

import httpx
import pytest
from tenacity import wait_none

from subtrans import translate
from subtrans.errors import FatalError
from subtrans.translate import RejectedError, TransientError, TranslationError, preflight, translate_batch

CUES = [(1, "Hello."), (2, "Bye.")]


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(translate._post.retry, "wait", wait_none())


def _client(responses: list[httpx.Response]) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen), len(responses)) - 1]

    return httpx.Client(base_url="https://x/api/v1", transport=httpx.MockTransport(handler)), seen


def _answer(content: object) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _translations(items: list[dict]) -> httpx.Response:
    return _answer(json.dumps({"translations": items}))


def test_parses_and_ignores_unrequested_ids() -> None:
    client, _ = _client(
        [_translations([{"id": 1, "text": "Olá.\nTudo bem?"}, {"id": 2, "text": "Tchau."}, {"id": 9, "text": "x"}])]
    )
    assert translate_batch(client, "m", CUES) == {1: "Olá.\\NTudo bem?", 2: "Tchau."}


def test_code_fenced_json_is_accepted() -> None:
    fenced = '```json\n{"translations": [{"id": 1, "text": "Olá."}, {"id": 2, "text": "Tchau."}]}\n```'
    client, _ = _client([_answer(fenced)])
    assert translate_batch(client, "m", CUES)[1] == "Olá."


@pytest.mark.parametrize(
    "response",
    [
        _answer(None),
        _answer("[1, 2]"),
        _answer("not json"),
        _translations([{"id": 1, "text": "Olá."}]),
        _translations([{"id": 1, "text": None}, {"id": 2, "text": "Tchau."}]),
    ],
    ids=["null-content", "json-array", "non-json", "missing-id", "null-text"],
)
def test_bad_answer_is_a_translation_error_tried_twice(response: httpx.Response) -> None:
    client, seen = _client([response])
    with pytest.raises(TranslationError):
        translate_batch(client, "m", CUES)
    assert len(seen) == 2


@pytest.mark.parametrize("status", [400, 403, 413])
def test_rejected_content_is_sent_once(status: int) -> None:
    client, seen = _client([httpx.Response(status, json={"error": {"message": "flagged"}})])
    with pytest.raises(RejectedError):
        translate_batch(client, "m", CUES)
    assert len(seen) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"error": {"message": "upstream down"}}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, text="<html>proxy</html>"),
        httpx.Response(502),
        httpx.Response(429),
    ],
    ids=["error-body-200", "no-choices", "html-body", "502", "429"],
)
def test_provider_trouble_is_transient(response: httpx.Response) -> None:
    client, seen = _client([response])
    with pytest.raises(TransientError):
        translate_batch(client, "m", CUES)
    assert len(seen) == 5


def test_transient_failure_recovers() -> None:
    client, _ = _client([httpx.Response(503), _translations([{"id": 1, "text": "a"}, {"id": 2, "text": "b"}])])
    assert translate_batch(client, "m", CUES) == {1: "a", 2: "b"}


@pytest.mark.parametrize("status", [401, 402, 404])
def test_account_problems_are_fatal_at_once(status: int) -> None:
    client, seen = _client([httpx.Response(status)])
    with pytest.raises(FatalError):
        translate_batch(client, "m", CUES)
    assert len(seen) == 1


def test_preflight_rejects_bad_key_and_unknown_model() -> None:
    client, _ = _client([httpx.Response(401), httpx.Response(200)])
    with pytest.raises(FatalError, match="API key"):
        preflight(client, "m")
    client, _ = _client([httpx.Response(200), httpx.Response(404)])
    with pytest.raises(FatalError, match="no model"):
        preflight(client, "m")
