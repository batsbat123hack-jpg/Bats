"""Bats AI — FastAPI application.

Routes
------
GET  /                       the single-page app
GET  /healthz                liveness probe (Render)
GET  /api/config             provider catalogue + feature flags
POST /api/chat               streaming chat (SSE) with web search grounding
POST /api/chat/stop          cancel an in-flight generation (best effort)
GET  /api/conversations      list / search chats
POST /api/conversations      create chat
PATCH/DELETE /api/conversations/{id}
GET  /api/conversations/{id} full transcript
POST /api/upload             any image format -> normalised + vision copy
GET  /api/files/{id}         serve uploads (original / preview)
GET  /api/generated/{id}     serve generated images
POST /api/images/generate    text -> image
POST /api/search             raw web search (used by the UI and debugging)
POST /api/title              regenerate a chat title
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import chat, config, images, providers, search
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bats")

STATIC_DIR = config.BASE_DIR / "static"


# --------------------------------------------------------------------------- #
# app state
# --------------------------------------------------------------------------- #
class State:
    settings: config.Settings
    store: Store
    active: dict[str, asyncio.Event]
    hits: dict[str, deque]


state = State()
state.active = {}
state.hits = defaultdict(deque)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(request: Request) -> bool:
    """Simple in-memory sliding window so one visitor cannot drain your quota."""
    cfg = state.settings
    per_min = cfg.rate_per_minute
    per_day = cfg.rate_per_day
    if per_min <= 0 and per_day <= 0:
        return False
    ip = client_ip(request)
    now = time.time()
    bucket = state.hits[ip]
    while bucket and now - bucket[0] > 86_400:
        bucket.popleft()
    if per_day > 0 and len(bucket) >= per_day:
        return True
    last_minute = sum(1 for stamp in bucket if now - stamp < 60)
    if per_min > 0 and last_minute >= per_min:
        return True
    bucket.append(now)
    return False


def access_code_ok(request: Request) -> bool:
    code = state.settings.access_code
    if not code:
        return True
    supplied = request.headers.get("x-bats-code") or request.query_params.get("code", "")
    return hmac.compare_digest(supplied, code)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = config.build_settings()
    settings.ensure_dirs()
    state.settings = settings
    state.store = Store(settings.db_path)
    log.info(
        "Bats AI ready — providers=%s image=%s search=%s data=%s",
        settings.available_providers or ["(client keys only)"],
        [p["id"] for p in settings.image_providers],
        settings.search_providers,
        settings.data_dir,
    )
    try:
        yield
    finally:
        state.store.close()


app = FastAPI(title="Bats AI", version="1.0.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


def _cors_origins() -> list[str]:
    settings = config.build_settings()
    origins = {
        "https://www.bats-ai.onrender.com",
        "https://bats-ai.onrender.com",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
    if settings.public_url:
        origins.add(settings.public_url.rstrip("/"))
    origins.update(settings.extra_cors_origins)
    return sorted(origins)


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?|https://([a-z0-9-]+\.)*onrender\.com|https://([a-z0-9-]+\.)*e2b\.app",
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def add_headers(request: Request, call_next):
    start = time.perf_counter()
    path = request.url.path
    # guard the expensive endpoints
    if path.startswith("/api/") and path not in {"/api/config", "/api/docs"}:
        if not access_code_ok(request):
            return JSONResponse(
                {"detail": "This Bats AI instance requires an access code. Add it in Settings."},
                status_code=401,
            )
        if path in {"/api/chat", "/api/images/generate", "/api/search"} and rate_limited(request):
            return JSONResponse(
                {"detail": "Too many requests — slow down for a moment."}, status_code=429
            )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - start) * 1000:.0f}"
    if request.url.path.startswith("/api/files") or request.url.path.startswith("/api/generated"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response


def settings() -> config.Settings:
    return state.settings


def store() -> Store:
    return state.store


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class ChatOptions(BaseModel):
    provider: str = ""
    model: str = ""
    conversation_id: str = ""
    message: str
    attachments: list[str] = Field(default_factory=list)
    web_search: bool | None = None
    image_mode: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    regenerate: bool = False
    # client supplied credentials (used when the server has none)
    api_key: str = ""
    base_url: str = ""
    client_id: str = ""


class ConversationCreate(BaseModel):
    title: str = "New chat"


class ConversationPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class ImageRequest(BaseModel):
    prompt: str
    provider: str = ""
    model: str = ""
    size: str = "1024x1024"
    style: str = ""
    conversation_id: str = ""
    api_key: str = ""
    base_url: str = ""


class SearchRequest(BaseModel):
    query: str
    limit: int = 6


class TitleRequest(BaseModel):
    conversation_id: str
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
TEXT_ONLY_HINTS = (
    "deepseek-chat", "deepseek-reasoner", "deepseek-coder", "gpt-3.5", "o1-mini",
    "llama-3.1-", "llama-3.3-", "llama3", "mixtral", "mistral-large", "mistral-small",
    "qwen2.5-72b", "gemma", "codestral", "command-r", "phi-",
)


def _guess_vision(model: str, provider_default: bool = True) -> bool:
    """Models outside the catalogue: assume vision unless the name says otherwise."""
    lowered = (model or "").lower()
    if any(hint in lowered for hint in TEXT_ONLY_HINTS):
        return False
    return provider_default


def _resolve_runtime(opts: ChatOptions) -> tuple[providers.Runtime, str, bool]:
    cfg = settings()
    provider_id = opts.provider or cfg.default_provider or "openai"
    runtime = providers.resolve(
        provider_id,
        client_key=opts.api_key,
        client_base_url=opts.base_url,
        allow_client_keys=cfg.allow_client_keys,
    )
    catalogue = {p["id"]: p for p in cfg.providers}
    entry = catalogue.get(runtime.id, {})
    model = opts.model or (cfg.default_model if runtime.id == cfg.default_provider else "")
    if not model:
        models = entry.get("models") or []
        model = models[0]["id"] if models else ""
    if not model:
        raise HTTPException(400, "No model selected. Pick one in the model menu.")
    info = next((m for m in entry.get("models", []) if m.get("id") == model), None)
    if info is not None:
        vision = bool(info.get("vision"))
    else:
        vision = _guess_vision(model, bool(entry.get("vision", True)))
    return runtime, model, vision


def _attachments_for(ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for image_id in ids[: settings().max_attachments]:
        try:
            path = images.resolve_file(settings(), image_id)
        except images.UploadError:
            continue
        out.append(
            {
                "id": image_id,
                "name": path.name,
                "url": f"/api/files/{image_id}",
                "preview_url": f"/api/files/{image_id}?variant=preview",
                "vision_url": images.vision_data_url(settings(), image_id),
            }
        )
    return out


def _public_attachments(atts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in a.items() if k != "vision_url"} for a in atts]


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    cfg = settings()
    return {
        "status": "ok",
        "time": dt.datetime.now(dt.timezone.utc).isoformat(),
        "providers": cfg.available_providers,
        "search": cfg.search_providers[:3],
        "images": [p["id"] for p in cfg.image_providers],
        **store().stats(),
    }


@app.get("/api/config")
async def api_config() -> dict[str, Any]:
    cfg = config.build_settings()
    state.settings = cfg
    payload = cfg.public()
    payload["server_time"] = dt.datetime.now().isoformat()
    return payload


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    path = STATIC_DIR / "index.html"
    if not path.is_file():
        return HTMLResponse("<h1>Bats AI</h1><p>UI not found.</p>", status_code=500)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/favicon.ico")
async def favicon():
    path = STATIC_DIR / "favicon.svg"
    if path.is_file():
        return FileResponse(path, media_type="image/svg+xml")
    return JSONResponse({"detail": "no favicon"}, status_code=404)


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse(
        {
            "name": "Bats AI",
            "short_name": "Bats",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0b0d12",
            "theme_color": "#0b0d12",
            "icons": [{"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }
    )


# --------------------------------------------------------------------------- #
# conversations
# --------------------------------------------------------------------------- #
@app.get("/api/conversations")
async def list_conversations(q: str = "", archived: bool = False) -> dict[str, Any]:
    return {"items": store().list_conversations(q, archived)}


@app.post("/api/conversations")
async def create_conversation(body: ConversationCreate) -> dict[str, Any]:
    cfg = settings()
    return store().create_conversation(body.title, cfg.default_provider, cfg.default_model)


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str) -> dict[str, Any]:
    conv = store().get_conversation(cid)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    plain = [{k: v for k, v in m.items() if k != "conversation_id"} for m in store().get_messages(cid)]
    return {"conversation": conv, "messages": plain}


@app.patch("/api/conversations/{cid}")
async def patch_conversation(cid: str, body: ConversationPatch) -> dict[str, Any]:
    if not store().get_conversation(cid):
        raise HTTPException(404, "Conversation not found")
    if body.title is not None:
        store().rename_conversation(cid, body.title or "New chat")
    store().set_flags(cid, pinned=body.pinned, archived=body.archived)
    return store().get_conversation(cid)  # type: ignore[return-value]


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str) -> dict[str, Any]:
    store().delete_conversation(cid)
    return {"ok": True}


@app.delete("/api/conversations")
async def delete_all_conversations() -> dict[str, Any]:
    store().delete_all_conversations()
    return {"ok": True}


@app.post("/api/conversations/{cid}/messages/{mid}/delete-after")
async def delete_after(cid: str, mid: str) -> dict[str, Any]:
    store().delete_messages_after(cid, mid, include_self=True)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# uploads
# --------------------------------------------------------------------------- #
@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    cfg = settings()
    if len(files) > cfg.max_attachments:
        raise HTTPException(400, f"You can attach up to {cfg.max_attachments} images at once.")
    stored = []
    for file in files:
        raw = await file.read()
        try:
            image = images.save_upload(cfg, raw, file.filename or "image", file.content_type or "")
        except images.UploadError as exc:
            raise HTTPException(exc.status, exc.message)
        stored.append(image.as_dict())
    return {"items": stored}


@app.get("/api/files/{file_id}")
async def get_file(file_id: str, variant: str = ""):
    try:
        path = images.resolve_file(settings(), file_id, variant)
    except images.UploadError as exc:
        raise HTTPException(exc.status, exc.message)
    headers = {}
    if path.suffix.lower() == ".svg":
        headers["Content-Security-Policy"] = "script-src 'none'; object-src 'none'"
    return FileResponse(path, media_type=images.media_type(path), headers=headers)


@app.get("/api/generated/{image_id}")
async def get_generated(image_id: str):
    try:
        path = images.resolve_generated(settings(), image_id)
    except images.UploadError as exc:
        raise HTTPException(exc.status, exc.message)
    return FileResponse(path, media_type="image/png")


# --------------------------------------------------------------------------- #
# image generation
# --------------------------------------------------------------------------- #
@app.post("/api/images/generate")
async def generate_image(body: ImageRequest) -> dict[str, Any]:
    cfg = settings()
    # try the requested engine, then the other configured engines (free first)
    engine_ids: list[str] = []
    for candidate in (body.provider or cfg.default_image_provider,
                      cfg.default_image_provider,
                      *[p["id"] for p in cfg.image_providers]):
        if candidate and candidate not in engine_ids:
            engine_ids.append(candidate)
    engines = {p["id"]: p for p in cfg.image_providers}
    requested = engine_ids[0] if engine_ids else ""
    order = [e for e in engine_ids if e in engines]
    if engines.get(requested, {}).get("has_key") or engines.get(requested, {}).get("requires_key"):
        order = [requested]  # a paid engine with a key: no silent substitution
    else:
        order = sorted(order, key=lambda e: (engines[e]["kind"] != "demo"))
    image = None
    failure: images.UploadError | None = None
    for index, engine in enumerate(order):
        try:
            image = await images.generate_image(
                cfg,
                body.prompt,
                provider_id=engine,
                model=body.model if index == 0 else "",
                size=body.size,
                client_key=body.api_key,
                client_base_url=body.base_url,
                style=body.style,
            )
            break
        except images.UploadError as exc:
            failure = exc
            if exc.status in (400,) or engine == order[-1]:
                break
            log.warning("image engine %s failed: %s — trying next", engine, exc.message)
    if image is None:
        raise HTTPException(
            failure.status if failure else 502,
            failure.message if failure else "No image engine is available.",
        )
    if body.conversation_id:
        store().add_message(
            body.conversation_id,
            "assistant",
            "",
            images=[image.as_dict()],
            meta={"kind": "image"},
        )
    return image.as_dict()


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
@app.post("/api/search")
async def api_search(body: SearchRequest) -> dict[str, Any]:
    cfg = settings()
    bundle = await search.web_search(
        body.query, cfg.search_providers, limit=min(body.limit, 10)
    )
    return {
        "query": bundle.query,
        "provider": bundle.provider,
        "error": bundle.error,
        "results": [r.as_dict() for r in bundle.results],
    }


# --------------------------------------------------------------------------- #
# titles
# --------------------------------------------------------------------------- #
@app.post("/api/title")
async def api_title(body: TitleRequest) -> dict[str, Any]:
    conv = store().get_conversation(body.conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    messages = store().get_messages(body.conversation_id)
    user_msg = next((m for m in messages if m["role"] == "user"), None)
    reply = next((m for m in messages if m["role"] == "assistant"), None)
    if not user_msg:
        return {"title": conv["title"]}
    fallback = chat.title_from(user_msg["content"])
    try:
        runtime, model, vision = _resolve_runtime(
            ChatOptions(
                provider=body.provider,
                model=body.model,
                message="",
                api_key=body.api_key,
                base_url=body.base_url,
            )
        )
        title = await chat.suggest_title(
            runtime, model, user_msg["content"], (reply or {}).get("content", ""), vision
        )
    except (providers.ProviderError, HTTPException):
        title = fallback
    store().rename_conversation(body.conversation_id, title)
    return {"title": title}


# --------------------------------------------------------------------------- #
# chat (SSE)
# --------------------------------------------------------------------------- #
@app.post("/api/chat/stop")
async def stop_chat(request: Request) -> dict[str, Any]:
    client_id = request.headers.get("x-client-id", "")
    event = state.active.pop(client_id, None) if client_id else None
    if event:
        event.set()
        return {"ok": True, "stopped": True}
    if state.active:
        for key in list(state.active):
            state.active.pop(key).set()
        return {"ok": True, "stopped": True}
    return {"ok": True, "stopped": False}


@app.post("/api/chat")
async def api_chat(opts: ChatOptions):
    cfg = settings()
    if not opts.message.strip() and not opts.attachments:
        raise HTTPException(400, "Message is empty")
    try:
        runtime, model, vision = _resolve_runtime(opts)
    except providers.ProviderError as exc:
        raise HTTPException(exc.status, exc.message)

    conversation = store().get_conversation(opts.conversation_id) if opts.conversation_id else None
    if not conversation:
        conversation = store().create_conversation(
            chat.title_from(opts.message), runtime.id, model
        )
    cid = conversation["id"]

    if opts.regenerate:
        messages = store().get_messages(cid)
        if messages:
            store().delete_messages_after(cid, messages[-1]["id"], include_self=True)
        history = store().get_messages(cid)
    else:
        atts = _attachments_for(opts.attachments)
        store().add_message(
            cid,
            "user",
            opts.message,
            attachments=_public_attachments(atts),
            meta={"model": model, "provider": runtime.id},
        )
        history = store().get_messages(cid)

    # the current turn = last user message (+ attachments from that message)
    last_user = next((m for m in reversed(history) if m["role"] == "user"), None)
    if not last_user:
        raise HTTPException(400, "Nothing to answer")
    turn_atts = _attachments_for([a["id"] for a in last_user.get("attachments") or []])
    for msg in history:
        if msg["id"] == last_user["id"]:
            msg["attachments"] = _public_attachments(turn_atts)

    # web_search: None = automatic (only when the question needs fresh facts),
    # True = force search this turn, False = never.
    if opts.web_search is None:
        search_enabled = cfg.search_enabled_default and chat.wants_search(opts.message)
    else:
        search_enabled = bool(opts.web_search)
    if opts.attachments and opts.web_search is None:
        search_enabled = False  # questions about an attached image rarely need the web
    is_image_turn = opts.image_mode
    if not is_image_turn and chat.looks_like_image_request(opts.message):
        is_image_turn = cfg.default_image_provider != "" and not opts.attachments

    client_id = opts.client_id or f"anon-{time.time_ns()}"
    stop_event = asyncio.Event()
    state.active[client_id] = stop_event
    assistant_id = f"m_{time.time_ns():x}"
    created = dt.datetime.now().isoformat()

    async def event_stream():
        started = time.perf_counter()
        answer_parts: list[str] = []
        sources: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        meta: dict[str, Any] = {
            "provider": runtime.id,
            "model": model,
            "key_source": runtime.key_source,
            "created_at": created,
        }
        try:
            yield chat.sse(
                {
                    "type": "start",
                    "conversation_id": cid,
                    "message_id": assistant_id,
                    "provider": runtime.id,
                    "model": model,
                    "vision": vision,
                    "key_source": runtime.key_source,
                }
            )

            # ---------------- image turn ------------------------------- #
            if is_image_turn:
                prompt = chat.clean_image_prompt(opts.message)
                yield chat.sse({"type": "status", "text": "Painting your image…"})
                try:
                    if not cfg.image_providers:
                        raise images.UploadError(
                            "Image generation is not enabled on this server."
                        )
                    image = await images.generate_image(
                        cfg,
                        prompt or opts.message,
                        client_key=opts.api_key if runtime.id != "ollama" else "",
                        client_base_url="",
                    )
                except images.UploadError as exc:
                    error_text = f"Image generation failed: {exc.message}"
                    store().add_message(cid, "assistant", error_text, meta={**meta, "error": True})
                    yield chat.sse({"type": "error", "message": error_text})
                    yield chat.sse_done()
                    return
                caption = f"Here's the image I made for: **{prompt or opts.message}**"
                store().add_message(
                    cid,
                    "assistant",
                    caption,
                    images=[image.as_dict()],
                    meta={**meta, "kind": "image", "elapsed": time.perf_counter() - started},
                )
                yield chat.sse({"type": "text", "text": caption})
                yield chat.sse({"type": "image", "image": image.as_dict()})
                yield chat.sse(
                    {"type": "done", "conversation_id": cid, "message_id": assistant_id, "title": store().get_conversation(cid)["title"]}
                )
                yield chat.sse_done()
                return

            # ---------------- web search grounding ---------------------- #
            bundle: search.SearchBundle | None = None
            query = chat.search_query(opts.message, turn_atts)
            if search_enabled and query:
                yield chat.sse({"type": "status", "text": "Searching the web…"})
                bundle = await chat.gather_search(cfg, query, True)
                if bundle and bundle.ok:
                    sources = search.sources_payload(bundle)
                    yield chat.sse(
                        {
                            "type": "sources",
                            "provider": bundle.provider,
                            "items": sources,
                        }
                    )
                else:
                    yield chat.sse(
                        {
                            "type": "status",
                            "text": "Live search unavailable — answering from knowledge.",
                        }
                    )

            system = chat.system_message(cfg, bundle, bool(cfg.image_providers), vision)
            messages = chat.build_messages(history, cfg)
            # re-attach image bytes for this turn and (budget permitting) earlier ones
            seen_images = 0
            for msg in reversed(messages):
                if msg["role"] != "user":
                    continue
                if seen_images >= 4:
                    msg["images"] = []
                    continue
                if msg["content"] == last_user["content"] and turn_atts:
                    msg["images"] = [a["vision_url"] for a in turn_atts][: 4 - seen_images]
                else:
                    hydrated = []
                    for att in msg.get("images") or []:
                        try:
                            hydrated.append(images.vision_data_url(cfg, att))
                        except images.UploadError:
                            continue
                    msg["images"] = hydrated
                seen_images += len(msg["images"])

            options = {
                "temperature": opts.temperature
                if opts.temperature is not None
                else cfg.default_temperature,
                "max_tokens": opts.max_tokens or cfg.default_max_tokens,
                "top_p": None,
            }
            yield chat.sse({"type": "status", "text": "Thinking…"})

            finished = False
            async for event in chat.stream_answer(
                runtime, model, messages, system, options, vision
            ):
                if stop_event.is_set():
                    break
                etype = event["type"]
                if etype == "text":
                    answer_parts.append(event["text"])
                    yield chat.sse(event)
                elif etype == "thinking":
                    yield chat.sse(event)
                elif etype == "usage":
                    usage = event.get("usage", {})
                elif etype == "finish":
                    finished = True
            if stop_event.is_set():
                answer_parts.append("\n\n_[stopped]_")

            answer = "".join(answer_parts).strip()
            elapsed = time.perf_counter() - started
            meta.update({"usage": usage, "elapsed": round(elapsed, 2), "sources": len(sources)})
            if answer:
                saved = store().add_message(
                    cid,
                    "assistant",
                    answer,
                    sources=sources,
                    meta=meta,
                    message_id=assistant_id,
                )
                if not saved:
                    saved = {"id": assistant_id}
            title = store().get_conversation(cid)["title"]
            if title == "New chat" or title == chat.title_from(last_user["content"]):
                first_reply = answer[:1200]
                try:
                    title = await chat.suggest_title(
                        runtime, model, last_user["content"], first_reply, vision
                    )
                except providers.ProviderError:
                    title = chat.title_from(last_user["content"])
                store().rename_conversation(cid, title)

            yield chat.sse(
                {
                    "type": "done",
                    "conversation_id": cid,
                    "message_id": assistant_id,
                    "title": title,
                    "elapsed": round(elapsed, 2),
                    "usage": usage,
                    "sources": sources,
                    "stopped": stop_event.is_set(),
                }
            )
            yield chat.sse_done()
        except providers.ProviderError as exc:
            message = exc.message
            partial = "".join(answer_parts).strip()
            if partial:
                store().add_message(
                    cid,
                    "assistant",
                    partial + f"\n\n---\n⚠️ {message}",
                    sources=sources,
                    meta={**meta, "error": True},
                    message_id=assistant_id,
                )
            else:
                store().add_message(cid, "assistant", f"⚠️ {message}", meta={**meta, "error": True})
            yield chat.sse({"type": "error", "message": message})
            yield chat.sse_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("chat stream failed")
            message = f"Something went wrong: {exc.__class__.__name__}"
            yield chat.sse({"type": "error", "message": message})
            yield chat.sse_done()
        finally:
            state.active.pop(client_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-Id": assistant_id,
        },
    )


# --------------------------------------------------------------------------- #
# OpenAI-compatible API — lets other apps / the OpenAI SDK talk to Bats AI
# --------------------------------------------------------------------------- #
@app.get("/v1/models")
async def openai_models() -> dict[str, Any]:
    data = []
    for provider in config.build_settings().providers:
        if not provider["configured"]:
            continue
        for model in provider["models"]:
            if not model["id"]:
                continue
            data.append(
                {
                    "id": model["id"],
                    "object": "model",
                    "created": 0,
                    "owned_by": provider["id"],
                    "bats_provider": provider["id"],
                }
            )
    return {"object": "list", "data": data}


def _sse_payload(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


@app.post("/v1/chat/completions")
async def openai_chat(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(400, "messages is required")
    provider_id = body.get("bats_provider") or ""
    model = body.get("model") or ""
    if not provider_id:
        for provider in config.build_settings().providers:
            if any(m["id"] == model for m in provider["models"]):
                provider_id = provider["id"]
                break
    runtime, resolved_model, vision = _resolve_runtime(
        ChatOptions(
            provider=provider_id,
            model=model,
            message="",
            api_key=body.get("api_key", ""),
            base_url=body.get("base_url", ""),
        )
    )
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    history = [
        {
            "role": m.get("role", "user"),
            "content": m.get("content", "") if isinstance(m.get("content"), str) else " ".join(
                p.get("text", "") for p in m.get("content", []) if isinstance(p, dict)
            ),
        }
        for m in messages
        if m.get("role") in {"user", "assistant"}
    ]
    options = {
        "temperature": body.get("temperature", 0.7),
        "max_tokens": body.get("max_tokens", 2048),
        "top_p": body.get("top_p"),
    }
    completion_id = f"chatcmpl-{int(time.time() * 1000)}"
    created = int(time.time())

    if not body.get("stream"):
        parts: list[str] = []
        async for event in chat.stream_answer(
            runtime, resolved_model, history, system, options, vision
        ):
            if event["type"] == "text":
                parts.append(event["text"])
        content = "".join(parts)
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": resolved_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )

    async def stream():
        yield _sse_payload(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": resolved_model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        )
        try:
            async for event in chat.stream_answer(
                runtime, resolved_model, history, system, options, vision
            ):
                if event["type"] == "text":
                    yield _sse_payload(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": resolved_model,
                            "choices": [
                                {"index": 0, "delta": {"content": event["text"]}, "finish_reason": None}
                            ],
                        }
                    )
        except providers.ProviderError as exc:
            yield _sse_payload({"error": {"message": exc.message, "type": "provider_error"}})
        yield _sse_payload(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": resolved_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# static assets
# --------------------------------------------------------------------------- #
if (STATIC_DIR).is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(404)
async def not_found(request: Request, exc: Any):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    path = STATIC_DIR / "index.html"
    if path.is_file():
        return HTMLResponse(path.read_text(encoding="utf-8"))
    return JSONResponse({"detail": "Not found"}, status_code=404)
