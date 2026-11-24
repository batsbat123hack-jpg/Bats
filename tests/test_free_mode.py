"""Free-mode tests: the app must work with **no API keys at all**.

A mock stands in for the anonymous free text endpoint, so the whole flow
(streaming, grounding, images, uploads) is verified with zero credentials.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import time

import pytest
import uvicorn
from PIL import Image

from tests import mock_provider


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PORT = _free_port()


@pytest.fixture(scope="module")
def free_env(tmp_path_factory):
    """Start the mock, point the app at it, with every real key removed."""
    saved = {k: os.environ.get(k) for k in list(os.environ) if k.endswith("_API_KEY")}
    for key in saved:
        os.environ.pop(key, None)
    os.environ.update(
        {
            "FREE_MODE": "true",
            "FREE_LLM_BASE_URL": f"http://127.0.0.1:{PORT}/free",
            "DATA_DIR": str(tmp_path_factory.mktemp("free-data")),
            "IMAGE_PROVIDER": "demo",  # offline art: no network needed
            "ALLOW_CLIENT_KEYS": "true",
            "WEB_SEARCH_DEFAULT": "true",
        }
    )
    config = uvicorn.Config(mock_provider.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    time.sleep(0.2)
    yield
    server.should_exit = True
    thread.join(timeout=5)
    for key, value in saved.items():
        if value is not None:
            os.environ[key] = value


@pytest.fixture(scope="module")
def client(free_env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def read_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        events.append(json.loads(payload))
    return events


# --------------------------------------------------------------------------- #
def test_no_keys_needed_at_all(free_env):
    """Every real provider key is empty, yet the catalogue is usable for free."""
    real_keys = [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
        "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY", "XAI_API_KEY",
        "TOGETHER_API_KEY", "IMAGE_API_KEY", "CUSTOM_API_KEY",
    ]
    assert not any(os.environ.get(k) for k in real_keys)
    from app import config

    settings = config.build_settings()
    assert settings.available_providers == ["free"]
    assert settings.default_provider == "free"
    free = next(p for p in settings.providers if p["id"] == "free")
    assert free["configured"] is True
    assert free["requires_key"] is False
    assert len(free["models"]) >= 5


def test_config_reports_free_mode(client):
    cfg = client.get("/api/config").json()
    assert cfg["free_mode"] is True
    assert cfg["ready"] is True
    assert cfg["default_provider"] == "free"
    assert cfg["image_providers"], "free image engines must be listed"


def test_free_chat_streams(client):
    res = client.post("/api/chat", json={"message": "hello free world"})
    assert res.status_code == 200
    events = read_sse(res.text)
    assert events[0]["type"] == "start"
    assert events[0]["provider"] == "free"
    assert events[0]["key_source"] == "free"
    text = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert "Free model" in text and "hello free world" in text
    assert any(e["type"] == "done" for e in events)


def test_free_chat_uses_web_results_when_available(client, monkeypatch):
    from app import search as search_mod

    async def fake_search(query, providers, limit=6, client=None):
        bundle = search_mod.SearchBundle(query=query, provider="test")
        bundle.results = [
            search_mod.SearchResult(title="Fresh news", url="https://example.com/new", snippet="it happened")
        ]
        return bundle

    async def no_pages(bundle, pages=3, limit=4500, client=None):
        return bundle

    monkeypatch.setattr(search_mod, "web_search", fake_search)
    monkeypatch.setattr(search_mod, "enrich_with_pages", no_pages)

    mock_provider.CALLS.clear()
    res = client.post("/api/chat", json={"message": "latest news about bats", "web_search": True})
    events = read_sse(res.text)
    sources = next((e for e in events if e["type"] == "sources"), None)
    assert sources and sources["items"][0]["url"] == "https://example.com/new"
    text = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert "With live web results" in text

    free_call = next(c for c in mock_provider.CALLS if c["path"] == "free")
    system = next(m["content"] for m in free_call["body"]["messages"] if m["role"] == "system")
    assert "Web search results for" in system


def test_free_chat_falls_back_to_next_model_when_busy(client):
    mock_provider.BUSY.clear()
    mock_provider.BUSY.add("openai")  # the default model is unavailable
    try:
        res = client.post("/api/chat", json={"message": "fallback please", "model": "openai"})
        events = read_sse(res.text)
        text = "".join(e.get("text", "") for e in events if e["type"] == "text")
        assert "Free model" in text
        assert "**openai**" not in text  # answered by a different model
        assert any(e["type"] == "done" for e in events)
    finally:
        mock_provider.BUSY.clear()


def test_free_chat_degrades_gracefully_when_all_models_busy(client):
    mock_provider.BUSY.update(mock_provider.FREE_MODELS)
    try:
        res = client.post("/api/chat", json={"message": "everything is busy"})
        events = read_sse(res.text)
        text = "".join(e.get("text", "") for e in events if e["type"] == "text")
        assert "busy" in text.lower()
        assert any(e["type"] == "done" for e in events)
        assert not any(e["type"] == "error" for e in events), "must not hard-fail"
    finally:
        mock_provider.BUSY.clear()


def test_free_image_generation_offline(client):
    """Demo art needs no network, no key, and still returns a real PNG."""
    res = client.post(
        "/api/images/generate", json={"prompt": "free bat art", "provider": "demo"}
    )
    assert res.status_code == 200, res.text
    data = res.json()
    served = client.get(data["url"])
    assert served.status_code == 200
    img = Image.open(io.BytesIO(served.content))
    assert img.size[0] > 100


def test_upload_still_works_with_no_keys(client):
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), (10, 200, 120)).save(buf, format="PNG")
    item = client.post(
        "/api/upload", files=[("files", ("free.png", buf.getvalue(), "image/png"))]
    ).json()["items"][0]
    res = client.post("/api/chat", json={"message": "what is this?", "attachments": [item["id"]]})
    events = read_sse(res.text)
    assert any(e["type"] == "done" for e in events)


def test_openai_compatible_api_works_free(client):
    models = client.get("/v1/models").json()
    assert any(m["id"] == "openai" and m["bats_provider"] == "free" for m in models["data"])
    res = client.post(
        "/v1/chat/completions",
        json={"model": "openai", "messages": [{"role": "user", "content": "hey"}]},
    )
    assert res.status_code == 200
    assert "Free model" in res.json()["choices"][0]["message"]["content"]
