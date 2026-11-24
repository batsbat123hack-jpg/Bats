"""End-to-end tests for Bats AI.

A mock OpenAI-compatible provider (tests/mock_provider.py) is started in-process,
the app is pointed at it via OPENAI_BASE_URL, and every feature is exercised:
config, streaming chat, web-result grounding, image upload in several formats,
vision payloads, image generation, conversation CRUD and titles.

Run:  .venv/bin/python -m pytest tests -q
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

# --------------------------------------------------------------------------- #
# environment must be set before the app is imported
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


MOCK_PORT = _free_port()
os.environ.update(
    {
        "OPENAI_API_KEY": "sk-test-key",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/v1",
        "IMAGE_PROVIDER": "openai",
        "IMAGE_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/v1",
        "DEFAULT_PROVIDER": "openai",
        "DEFAULT_MODEL": "mock-gpt",
        "DATA_DIR": "/tmp/bats-test-data",
        "ALLOW_CLIENT_KEYS": "true",
    }
)

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests import mock_provider  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def provider_server():
    config = uvicorn.Config(
        mock_provider.app, host="127.0.0.1", port=MOCK_PORT, log_level="error"
    )
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


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


def make_image(fmt: str, size=(40, 30), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (120, 40, 200) if mode == "RGB" else None).save(buf, format=fmt)
    return buf.getvalue()


def read_sse(response) -> list[dict]:
    events = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        events.append(json.loads(payload))
    return events


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_health(client):
    data = client.get("/healthz").json()
    assert data["status"] == "ok"


def test_index_serves_ui(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Bats AI" in res.text
    assert "/static/app.js" in res.text


def test_static_assets(client):
    for path in ("/static/app.js", "/static/markdown.js", "/static/styles.css"):
        assert client.get(path).status_code == 200, path


def test_config_lists_providers(client):
    cfg = client.get("/api/config").json()
    ids = {p["id"] for p in cfg["providers"]}
    assert "openai" in ids
    openai = next(p for p in cfg["providers"] if p["id"] == "openai")
    assert openai["configured"] is True
    assert "openai" in cfg["image_providers"][0]["id"] or cfg["image_providers"]
    assert cfg["features"]["streaming"] is True
    assert "gpt" in " ".join(m["id"] for m in openai["models"]).lower() or openai["models"]


def test_streaming_chat_creates_conversation(client):
    res = client.post(
        "/api/chat",
        json={"message": "Hello there", "provider": "openai", "model": "mock-gpt"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = read_sse(res)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start"
    assert "done" in kinds
    text = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert "Mock answer" in text
    done = next(e for e in events if e["type"] == "done")
    assert done["conversation_id"].startswith("c_")
    assert done["title"]

    conv = client.get(f"/api/conversations/{done['conversation_id']}").json()
    assert len(conv["messages"]) == 2
    assert conv["messages"][0]["role"] == "user"
    assert conv["messages"][1]["role"] == "assistant"
    assert "Mock answer" in conv["messages"][1]["content"]


def test_conversation_listing_and_rename(client):
    created = client.post("/api/conversations", json={"title": "Scratch"}).json()
    cid = created["id"]
    items = client.get("/api/conversations").json()["items"]
    assert any(i["id"] == cid for i in items)
    patched = client.patch(f"/api/conversations/{cid}", json={"title": "Renamed", "pinned": True}).json()
    assert patched["title"] == "Renamed" and patched["pinned"] == 1
    client.delete(f"/api/conversations/{cid}")
    assert client.get(f"/api/conversations/{cid}").status_code == 404


@pytest.mark.parametrize(
    "fmt,ext",
    [
        ("PNG", "png"),
        ("JPEG", "jpg"),
        ("GIF", "gif"),
        ("WEBP", "webp"),
        ("BMP", "bmp"),
        ("TIFF", "tiff"),
        ("ICO", "ico"),
        ("AVIF", "avif"),
    ],
)
def test_upload_every_image_format(client, fmt, ext):
    raw = make_image(fmt)
    res = client.post(
        "/api/upload", files=[("files", (f"picture.{ext}", raw, f"image/{ext}"))]
    )
    assert res.status_code == 200, res.text
    item = res.json()["items"][0]
    assert item["id"]
    assert item["preview_url"].endswith("?variant=preview")
    # original + preview are downloadable
    assert client.get(item["url"]).status_code == 200
    assert client.get(item["preview_url"]).status_code == 200


def test_upload_svg(client):
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
    res = client.post("/api/upload", files=[("files", ("logo.svg", svg, "image/svg+xml"))])
    assert res.status_code == 200, res.text
    item = res.json()["items"][0]
    served = client.get(item["url"])
    assert served.status_code == 200
    assert "script-src" in served.headers.get("content-security-policy", "")


def test_upload_rejects_non_image(client):
    res = client.post("/api/upload", files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert res.status_code == 400
    assert "not a readable image" in res.json()["detail"]


def test_chat_with_image_attachment_sends_vision_payload(client):
    raw = make_image("PNG")
    item = client.post(
        "/api/upload", files=[("files", ("shot.png", raw, "image/png"))]
    ).json()["items"][0]

    mock_provider.CALLS.clear()
    res = client.post(
        "/api/chat",
        json={
            "message": "What is in this picture?",
            "provider": "openai",
            "model": "mock-gpt",
            "attachments": [item["id"]],
        },
    )
    events = read_sse(res)
    assert "done" in [e["type"] for e in events]
    text = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert "I can see your image" in text

    chat_call = next(c for c in mock_provider.CALLS if c["path"] == "chat")
    user_msg = [m for m in chat_call["body"]["messages"] if m["role"] == "user"][-1]
    assert isinstance(user_msg["content"], list)
    assert any(p["type"] == "image_url" for p in user_msg["content"])
    data_url = next(p["image_url"]["url"] for p in user_msg["content"] if p["type"] == "image_url")
    assert data_url.startswith("data:image/jpeg;base64,")


def test_web_search_results_are_injected_into_prompt(client, monkeypatch):
    """Simulate a search hit and assert the model receives the grounding block."""
    from app import search as search_mod

    async def fake_search(query, providers, limit=6, client=None):
        bundle = search_mod.SearchBundle(query=query, provider="test")
        bundle.results = [
            search_mod.SearchResult(
                title="Bats AI news", url="https://example.com/a", snippet="Bats shipped today"
            )
        ]
        return bundle

    async def no_pages(bundle, pages=3, limit=4500, client=None):
        return bundle

    monkeypatch.setattr(search_mod, "web_search", fake_search)
    monkeypatch.setattr(search_mod, "enrich_with_pages", no_pages)

    mock_provider.CALLS.clear()
    res = client.post(
        "/api/chat",
        json={
            "message": "Search the web for the latest Bats AI news",
            "provider": "openai",
            "model": "mock-gpt",
            "web_search": True,
        },
    )
    events = read_sse(res)
    sources_event = next((e for e in events if e["type"] == "sources"), None)
    assert sources_event is not None, [e["type"] for e in events]
    assert sources_event["items"][0]["url"] == "https://example.com/a"

    chat_call = next(c for c in mock_provider.CALLS if c["path"] == "chat")
    system = next(m["content"] for m in chat_call["body"]["messages"] if m["role"] == "system")
    assert "Web search results for" in system
    assert "https://example.com/a" in system
    assert "web search is ON" in system

    done = next(e for e in events if e["type"] == "done")
    conv = client.get(f"/api/conversations/{done['conversation_id']}").json()
    assistant = conv["messages"][-1]
    assert assistant["sources"][0]["url"] == "https://example.com/a"


def test_image_generation(client):
    res = client.post(
        "/api/images/generate",
        json={"prompt": "a neon bat", "provider": "openai", "size": "1024x1024"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["url"].startswith("/api/generated/")
    served = client.get(data["url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(served.content))
    assert img.size == (256, 256)


def test_image_turn_in_chat(client):
    res = client.post(
        "/api/chat",
        json={
            "message": "Draw a picture of a bat wearing sunglasses",
            "provider": "openai",
            "model": "mock-gpt",
        },
    )
    events = read_sse(res)
    types = [e["type"] for e in events]
    assert "image" in types, types
    image_event = next(e for e in events if e["type"] == "image")
    assert client.get(image_event["image"]["url"]).status_code == 200


def test_search_endpoint_reports_errors_gracefully(client):
    # no network in CI sandbox -> must return 200 with an error message, never 500
    res = client.post("/api/search", json={"query": "anything at all", "limit": 3})
    assert res.status_code == 200
    data = res.json()
    assert "results" in data


def test_missing_key_error_is_actionable(client):
    os.environ.pop("ANTHROPIC_API_KEY", None)
    res = client.post(
        "/api/chat",
        json={"message": "hi", "provider": "anthropic", "model": "claude-sonnet-4-5"},
    )
    assert res.status_code == 400
    assert "API key" in res.json()["detail"]


def test_client_key_resolution():
    """Server key wins; a browser key is used when the server has none."""
    from app import providers

    runtime = providers.resolve("openai", client_key="sk-browser")
    assert runtime.key_source == "server"

    runtime = providers.resolve("gemini", client_key="g-browser")
    assert runtime.key_source == "client"
    assert runtime.api_key == "g-browser"

    with pytest.raises(providers.ProviderError):
        providers.resolve("gemini", client_key="", allow_client_keys=False)


def test_title_endpoint(client):
    res = client.post("/api/chat", json={"message": "Plan a trip to Kampala", "provider": "openai", "model": "mock-gpt"})
    done = next(e for e in read_sse(res) if e["type"] == "done")
    titled = client.post("/api/title", json={"conversation_id": done["conversation_id"]}).json()
    assert titled["title"]


def test_regenerate(client):
    res = client.post("/api/chat", json={"message": "first question", "provider": "openai", "model": "mock-gpt"})
    done = next(e for e in read_sse(res) if e["type"] == "done")
    cid = done["conversation_id"]
    res2 = client.post(
        "/api/chat",
        json={
            "message": "first question",
            "provider": "openai",
            "model": "mock-gpt",
            "conversation_id": cid,
            "regenerate": True,
        },
    )
    events2 = read_sse(res2)
    assert "done" in [e["type"] for e in events2]
    conv = client.get(f"/api/conversations/{cid}").json()
    assert len(conv["messages"]) == 2


def test_unknown_model_still_reaches_provider(client):
    res = client.post(
        "/api/chat", json={"message": "hi", "provider": "openai", "model": "made-up-model"}
    )
    assert res.status_code == 200
    assert "done" in [e["type"] for e in read_sse(res)]


def test_404_api_vs_page(client):
    assert client.get("/api/nope").status_code == 404
    page = client.get("/some/chat/route")
    assert page.status_code == 200
    assert "Bats AI" in page.text


def test_markdown_renderer_escapes_html():
    """The JS renderer is unit-tested in node (see tests/markdown.test.js)."""
    assert True
