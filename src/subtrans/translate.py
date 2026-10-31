from __future__ import annotations

import json
import os

import httpx
from tenacity import retry, retry_if_exception, retry_if_exception_type, stop_after_attempt, wait_exponential

from .errors import FatalError, SubtransError

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
TIMEOUT = httpx.Timeout(120.0, connect=30.0)
TRANSIENT_STATUS = {408, 409, 425, 429}
FATAL_STATUS = {401, 402, 404}

SYSTEM_PROMPT = (
    "You translate film and TV subtitles into natural Brazilian Portuguese (pt-BR).\n"
    'Input: a JSON object {"cues":[{"id":N,"text":"..."}]}.\n'
    "Rules:\n"
    "1. Keep every formatting tag in a cue exactly as it is, for example {\\i1}, {\\i0} or {\\an8}.\n"
    "2. Keep the literal token \\N for a line break inside a cue. Do not add or remove line breaks.\n"
    "3. Never merge or split cues. Return exactly one translation per input cue, with the same id.\n"
    "4. Match the register and tone of the source: slang, formality, profanity.\n"
    "5. Keep proper nouns unless Brazil has an established translation for them.\n"
    'Return ONLY a JSON object: {"translations":[{"id":N,"text":"..."}]}'
)


class TranslationError(SubtransError):
    """The answer for this batch is unusable. A smaller batch can isolate the bad cue."""


class RejectedError(TranslationError):
    """OpenRouter refused this batch's content. The same request gets the same answer."""


class TransientError(SubtransError):
    """The provider or the network failed. A later run can resume from the checkpoint."""


def get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise FatalError("OPENROUTER_API_KEY is not set.")
    return key


def make_client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=OPENROUTER_BASE,
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/ricardoakrug/subtitle-translator",
            "X-Title": "subtitle-translator",
        },
        timeout=TIMEOUT,
    )


def preflight(client: httpx.Client, model: str) -> None:
    """Check the key and the model once, so a typo cannot turn every cue into a refusal."""
    try:
        key = client.get("/key")
        found = client.get(f"/models/{model}/endpoints")
    except httpx.TransportError as e:
        raise FatalError(f"Cannot reach OpenRouter: {e}") from e
    if key.status_code in (401, 403):
        raise FatalError("OpenRouter rejected the API key.")
    if found.status_code == 404:
        raise FatalError(f"OpenRouter has no model {model!r}.")


@retry(
    retry=retry_if_exception_type(TransientError),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _post(client: httpx.Client, body: dict) -> object:
    """Return the message content of the first choice."""
    try:
        resp = client.post("/chat/completions", json=body)
    except httpx.TransportError as e:
        raise TransientError(f"Network error: {e}") from e
    status = resp.status_code
    if status in FATAL_STATUS:
        raise FatalError(f"OpenRouter {status}: {resp.text[:300]}")
    if status in TRANSIENT_STATUS or status >= 500:
        raise TransientError(f"OpenRouter {status}: {resp.text[:300]}")
    if status >= 400:
        # 400, 403 and 413 follow from the content of this batch, for example a moderation flag.
        raise RejectedError(f"OpenRouter {status}: {resp.text[:300]}")
    try:
        data = resp.json()
    except ValueError as e:
        raise TransientError(f"OpenRouter sent a non-JSON body: {resp.text[:300]}") from e
    if not isinstance(data, dict) or "error" in data:
        raise TransientError(f"OpenRouter error: {str(data)[:300]}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise TransientError(f"Unexpected response shape: {str(data)[:300]}") from e


def _parse_json(content: str) -> object:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Some models wrap the JSON in a code fence although response_format asks for none.
    stripped = content.strip().strip("`").removeprefix("json").strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as e:
        raise TranslationError(f"Model returned non-JSON: {content[:300]}") from e


@retry(
    retry=retry_if_exception(lambda e: isinstance(e, TranslationError) and not isinstance(e, RejectedError)),
    stop=stop_after_attempt(2),
    reraise=True,
)
def translate_batch(client: httpx.Client, model: str, cues: list[tuple[int, str]]) -> dict[int, str]:
    payload = {"cues": [{"id": cid, "text": text} for cid, text in cues]}
    content = _post(
        client,
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
    )
    if not isinstance(content, str):
        raise TranslationError("Model returned no text")

    parsed = _parse_json(content)
    translations = parsed.get("translations") if isinstance(parsed, dict) else None
    if not isinstance(translations, list):
        raise TranslationError(f"No 'translations' array in: {content[:300]}")

    expected = {cid for cid, _ in cues}
    out: dict[int, str] = {}
    for item in translations:
        try:
            cid, text = int(item["id"]), item["text"]
        except (KeyError, TypeError, ValueError) as e:
            raise TranslationError(f"Bad translation entry: {item}") from e
        if cid not in expected:
            continue
        if not isinstance(text, str):
            raise TranslationError(f"Cue {cid} has no text")
        # A raw blank line would end the cue early in SRT output.
        out[cid] = text.replace("\r\n", "\n").replace("\n", "\\N")

    missing = expected - out.keys()
    if missing:
        raise TranslationError(f"Model omitted ids {sorted(missing)}")
    return out
