"""Chat provider layer: streaming completions across many back-ends.

Two wire protocols are implemented:

* ``openai_compat`` – /chat/completions with SSE deltas (OpenAI, Groq, Gemini,
  Mistral, DeepSeek, xAI, OpenRouter, Together, Ollama, custom servers…)
* ``anthropic``     – /messages with SSE events

Keys come from the server environment, or per request from the user's browser
(pasted in Settings → API keys), which makes a keyless deployment usable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from . import config


class ProviderError(Exception):
    """A provider failed; the message is safe to show to the user."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Runtime:
    id: str
    label: str
    kind: str
    base_url: str
    api_key: str
    key_source: str  # "server" | "client" | "free" | "demo" | "none"


def catalogue() -> list[dict[str, Any]]:
    return config.build_settings().providers  # cheap: re-reads env each call


FREE_DEFAULT_BASE = "https://text.pollinations.ai/openai"


def _spec(provider_id: str) -> dict[str, Any] | None:
    if provider_id == "free":
        return {
            "id": "free",
            "label": "Bats Free",
            "kind": "free",
            "env_key": "FREE_API_KEY",
            "base_url": os.environ.get("FREE_LLM_BASE_URL", "").strip() or FREE_DEFAULT_BASE,
            "requires_key": False,
        }
    if provider_id == "demo":
        return {
            "id": "demo",
            "label": "Bats Demo",
            "kind": "demo",
            "env_key": "",
            "base_url": "local://demo",
            "requires_key": False,
        }
    for spec in config.PROVIDER_SPECS:
        if spec.id == provider_id:
            return {
                "id": spec.id,
                "label": spec.label,
                "kind": spec.kind,
                "env_key": spec.env_key,
                "base_url": spec.base_url,
                "requires_key": spec.requires_key,
            }
    return None


def resolve(
    provider_id: str,
    client_key: str = "",
    client_base_url: str = "",
    allow_client_keys: bool = True,
) -> Runtime:
    spec = _spec(provider_id) or _spec("openai")
    assert spec is not None
    server_key = os.environ.get(spec["env_key"], "").strip()
    if spec["id"] == "custom":
        server_key = os.environ.get("CUSTOM_API_KEY", "").strip()

    base_url = client_base_url.strip() or spec["base_url"]
    if spec["id"] == "custom" and not base_url:
        base_url = os.environ.get("CUSTOM_BASE_URL", "").strip()
    if spec["id"] == "openai" and not client_base_url:
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or base_url
    if spec["id"] == "ollama" and not client_base_url:
        base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or base_url

    if server_key:
        api_key, source = server_key, "server"
    elif client_key and allow_client_keys:
        api_key, source = client_key.strip(), "client"
    elif spec["kind"] == "free":
        api_key, source = "", "free"
    elif spec["kind"] == "demo":
        api_key, source = "", "demo"
    else:
        api_key, source = "", "none"

    if spec["requires_key"] and not api_key:
        raise ProviderError(
            f"No API key for {spec['label']}. Add {spec['env_key']} in the Render "
            "environment, or paste your own key in Settings → API keys.",
            status=400,
        )
    if not base_url:
        raise ProviderError(
            f"{spec['label']} has no base URL configured. Set its *_BASE_URL variable.",
            status=400,
        )
    return Runtime(
        id=spec["id"],
        label=spec["label"],
        kind=spec["kind"],
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        key_source=source,
    )


# --------------------------------------------------------------------------- #
# message conversion
# --------------------------------------------------------------------------- #
def _openai_messages(
    messages: list[dict[str, Any]],
    system: str,
    vision: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        role = msg.get("role", "user")
        if role == "system":
            continue
        text = msg.get("content") or ""
        images: list[str] = msg.get("images") or []
        if role == "assistant":
            out.append({"role": "assistant", "content": text})
            continue
        if images and vision:
            parts: list[dict[str, Any]] = []
            if text:
                parts.append({"type": "text", "text": text})
            for data_url in images:
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
            out.append({"role": role, "content": parts})
        else:
            note = ""
            if images and not vision:
                note = (
                    f"[{len(images)} image attachment(s) omitted: this model has no vision. "
                    "Ask the user to switch to a vision model if the image matters.]\n"
                )
            out.append({"role": role, "content": note + text})
    return out


def _anthropic_messages(
    messages: list[dict[str, Any]], vision: bool
) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = msg.get("content") or ""
        if role == "system":
            system_parts.append(text)
            continue
        images: list[str] = msg.get("images") or []
        if role == "user" and images and vision:
            content: list[dict[str, Any]] = []
            for data_url in images:
                media, _, b64 = data_url.partition(";base64,")
                media_type = media.replace("data:", "") or "image/png"
                content.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    }
                )
            if text:
                content.append({"type": "text", "text": text})
            out.append({"role": "user", "content": content})
            continue
        if role == "user" and images and not vision:
            text = (
                f"[{len(images)} image attachment(s) omitted: no vision support]\n" + text
            )
        if out and out[-1]["role"] == role:
            prev = out[-1]["content"]
            if isinstance(prev, str):
                out[-1]["content"] = prev + "\n\n" + text
            else:
                prev.append({"type": "text", "text": text})
        else:
            out.append({"role": role, "content": text})
    return "\n\n".join(system_parts), out


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
async def _iter_sse(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if not line:
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                yield json.loads(payload)
            except ValueError:
                continue


async def stream_chat(
    runtime: Runtime,
    messages: list[dict[str, Any]],
    system: str = "",
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2048,
    vision: bool = False,
    top_p: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield events: {"type":"text","text":...}, {"type":"usage",...}."""
    if runtime.kind == "free":
        async for event in _stream_free(
            runtime, messages, system, model, temperature, max_tokens, vision
        ):
            yield event
        return
    if runtime.kind == "demo":
        from . import demo

        async for event in demo.stream(messages, system, max_tokens):  # type: ignore[call-arg]
            yield event
        return
    if runtime.kind == "anthropic":
        async for event in _stream_anthropic(
            runtime, messages, system, model, temperature, max_tokens, vision
        ):
            yield event
        return
    async for event in _stream_openai(
        runtime, messages, system, model, temperature, max_tokens, vision, top_p
    ):
        yield event


async def _stream_openai(
    runtime: Runtime,
    messages: list[dict[str, Any]],
    system: str,
    model: str,
    temperature: float,
    max_tokens: int,
    vision: bool,
    top_p: float | None,
) -> AsyncIterator[dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": _openai_messages(messages, system, vision),
        "stream": True,
        "max_tokens": max_tokens,
    }
    if temperature is not None and temperature >= 0:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    headers = {"Content-Type": "application/json"}
    if runtime.api_key:
        headers["Authorization"] = f"Bearer {runtime.api_key}"
    if runtime.id == "openrouter":
        headers["HTTP-Referer"] = os.environ.get("PUBLIC_URL", "https://bats-ai.onrender.com")
        headers["X-Title"] = "Bats AI"

    url = f"{runtime.base_url}/chat/completions"
    timeout = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderError(_http_error(runtime, response.status_code, body))
                usage: dict[str, Any] = {}
                async for chunk in _iter_sse(response):
                    if chunk.get("error"):
                        raise ProviderError(_extract_error(chunk["error"]) or "Provider error")
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, list):  # some gateways send parts
                        piece = "".join(p.get("text", "") for p in piece if isinstance(p, dict))
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield {"type": "thinking", "text": reasoning}
                    if piece:
                        yield {"type": "text", "text": piece}
                    if choice.get("finish_reason"):
                        yield {"type": "finish", "reason": choice["finish_reason"]}
                if usage:
                    yield {"type": "usage", "usage": usage}
        except httpx.TimeoutException:
            raise ProviderError(f"{runtime.label} timed out. Try a smaller model or prompt.")
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach {runtime.label}: {exc.__class__.__name__}")


async def _stream_anthropic(
    runtime: Runtime,
    messages: list[dict[str, Any]],
    system: str,
    model: str,
    temperature: float,
    max_tokens: int,
    vision: bool,
) -> AsyncIterator[dict[str, Any]]:
    system_text, converted = _anthropic_messages(messages, vision)
    if system:
        system_text = (system + "\n\n" + system_text).strip()
    payload: dict[str, Any] = {
        "model": model,
        "messages": converted,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if system_text:
        payload["system"] = system_text
    if temperature is not None:
        payload["temperature"] = max(0.0, min(1.0, temperature))
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if runtime.api_key:
        headers["x-api-key"] = runtime.api_key

    url = f"{runtime.base_url}/messages"
    timeout = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderError(_http_error(runtime, response.status_code, body))
                async for chunk in _iter_sse(response):
                    ctype = chunk.get("type")
                    if ctype == "content_block_delta":
                        delta = chunk.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield {"type": "text", "text": delta["text"]}
                        elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                            yield {"type": "thinking", "text": delta["thinking"]}
                    elif ctype == "message_delta":
                        usage = (chunk.get("usage") or {})
                        if usage:
                            yield {"type": "usage", "usage": usage}
                        stop = (chunk.get("delta") or {}).get("stop_reason")
                        if stop:
                            yield {"type": "finish", "reason": stop}
                    elif ctype == "error":
                        raise ProviderError(_extract_error(chunk.get("error")) or "Anthropic error")
        except httpx.TimeoutException:
            raise ProviderError("Anthropic timed out.")
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Anthropic: {exc.__class__.__name__}")



# --------------------------------------------------------------------------- #
# Free (no key) provider
# --------------------------------------------------------------------------- #
FREE_MODEL_ORDER = [
    "openai",
    "openai-fast",
    "gemini",
    "mistral",
    "llama",
    "qwen-coder",
    "deepseek",
]


def _free_payload(
    model: str,
    messages: list[dict[str, Any]],
    system: str,
    temperature: float,
    max_tokens: int,
    vision: bool,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": _openai_messages(messages, system, vision),
        "stream": stream,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = max(0.0, min(2.0, temperature))
    return payload


def _free_headers(runtime: "Runtime") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "BatsAI/1.0 (+https://www.bats-ai.onrender.com)",
    }
    if runtime.api_key:
        headers["Authorization"] = f"Bearer {runtime.api_key}"
    return headers


async def _stream_free(
    runtime: Runtime,
    messages: list[dict[str, Any]],
    system: str,
    model: str,
    temperature: float,
    max_tokens: int,
    vision: bool,
) -> AsyncIterator[dict[str, Any]]:
    """Anonymous free models with automatic model + transport fallback.

    Tries the requested model first, then the other free models, then a
    non-streaming request, so a busy/renamed upstream model degrades instead
    of failing the turn.
    """
    tried: list[str] = []
    candidates = [model] + [m for m in FREE_MODEL_ORDER if m != model]
    timeout = httpx.Timeout(connect=15.0, read=150.0, write=30.0, pool=15.0)
    last_error: str = ""
    async with httpx.AsyncClient(timeout=timeout) as client:
        for candidate in candidates:
            if candidate in tried:
                continue
            tried.append(candidate)
            url = f"{runtime.base_url}/chat/completions"
            payload = _free_payload(
                candidate, messages, system, temperature, max_tokens, vision, True
            )
            try:
                async with client.stream(
                    "POST", url, json=payload, headers=_free_headers(runtime)
                ) as response:
                    ctype = response.headers.get("content-type", "")
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        last_error = _http_error(runtime, response.status_code, body)
                        continue
                    if "event-stream" not in ctype and "application/json" in ctype:
                        # upstream ignored stream=true and returned one JSON body
                        raw = (await response.aread()).decode("utf-8", "replace")
                        text = _json_completion_text(raw)
                        if text:
                            yield {"type": "text", "text": text}
                            yield {"type": "finish", "reason": "stop"}
                            return
                        last_error = "empty response"
                        continue
                    produced = False
                    usage: dict[str, Any] = {}
                    async for chunk in _iter_sse(response):
                        if chunk.get("error"):
                            last_error = _extract_error(chunk["error"]) or "provider error"
                            break
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if isinstance(piece, list):
                            piece = "".join(
                                part.get("text", "") for part in piece if isinstance(part, dict)
                            )
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if reasoning:
                            yield {"type": "thinking", "text": reasoning}
                        if piece:
                            produced = True
                            yield {"type": "text", "text": piece}
                        if choices[0].get("finish_reason"):
                            yield {"type": "finish", "reason": choices[0]["finish_reason"]}
                    if produced:
                        if usage:
                            yield {"type": "usage", "usage": usage}
                        return
                    last_error = last_error or "no content in stream"
            except httpx.TimeoutException:
                last_error = f"free model {candidate} timed out"
            except httpx.HTTPError as exc:
                last_error = f"free endpoint unreachable ({exc.__class__.__name__})"

        # last resort: non-streaming request (different code path upstream)
        for candidate in tried[:3]:
            try:
                payload = _free_payload(
                    candidate, messages, system, temperature, max_tokens, vision, False
                )
                response = await client.post(
                    f"{runtime.base_url}/chat/completions",
                    json=payload,
                    headers=_free_headers(runtime),
                )
                if response.status_code >= 400:
                    continue
                text = _json_completion_text(response.text)
                if text:
                    yield {"type": "text", "text": text}
                    yield {"type": "finish", "reason": "stop"}
                    return
            except httpx.HTTPError:
                continue

    raise ProviderError(
        "No free model answered just now"
        + (f" ({last_error})" if last_error else "")
        + ". Free services are busy sometimes — press Retry, or add a free key "
        "(Groq/Gemini) in Settings for guaranteed availability.",
        status=503,
    )


def _json_completion_text(raw: str) -> str:
    """Pull the assistant text out of a non-streamed (or oddly streamed) body."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("data:"):
        parts = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in {"", "[DONE]"}:
                continue
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            for choice in chunk.get("choices") or []:
                content = (choice.get("message") or {}).get("content") or (
                    choice.get("delta") or {}
                ).get("content")
                if content:
                    parts.append(content)
        return "".join(parts)
    try:
        data = json.loads(raw)
    except ValueError:
        return raw[:4000] if "<" not in raw[:80] else ""
    if isinstance(data, dict):
        choices = data.get("choices") or []
        if choices:
            first = choices[0]
            message = first.get("message") or {}
            if message.get("content"):
                return message["content"]
            if (first.get("delta") or {}).get("content"):
                return first["delta"]["content"]
            if first.get("text"):
                return first["text"]
        if isinstance(data.get("content"), str):
            return data["content"]
        if isinstance(data.get("response"), str):
            return data["response"]
    if isinstance(data, list) and data and isinstance(data[0], dict):
        if data[0].get("content"):
            return data[0]["content"]
    return ""


def _extract_error(error: Any) -> str:
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        inner = error.get("message") or error.get("detail") or error.get("error")
        if inner:
            return _extract_error(inner)
        return json.dumps(error)[:300]
    return str(error)[:300]


def _http_error(runtime: Runtime, status: int, body: str) -> str:
    detail = ""
    try:
        data = json.loads(body)
        detail = _extract_error(data.get("error") or data.get("message") or data)
    except ValueError:
        detail = body[:300]
    hint = ""
    if status in (401, 403):
        hint = " The API key was rejected — check the key and that billing is enabled."
    elif status == 404:
        hint = " The model or endpoint was not found — check the model name."
    elif status == 429:
        hint = " Rate limit or quota reached — wait a moment or switch provider."
    return f"{runtime.label} error {status}: {detail or 'unknown error'}.{hint}"


async def complete_once(
    runtime: Runtime,
    messages: list[dict[str, Any]],
    system: str = "",
    model: str = "",
    temperature: float = 0.2,
    max_tokens: int = 300,
    vision: bool = False,
) -> str:
    """Collect a full completion (used for titles and search queries)."""
    parts: list[str] = []
    async for event in stream_chat(
        runtime,
        messages,
        system=system,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        vision=vision,
    ):
        if event["type"] == "text":
            parts.append(event["text"])
    return "".join(parts).strip()
