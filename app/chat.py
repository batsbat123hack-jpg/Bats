"""Chat orchestration: prompt building, web search grounding, streaming."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from typing import Any, AsyncIterator

from . import config, providers, search

SYSTEM_PROMPT = """You are Bats AI, a helpful, friendly and accurate assistant.

Today is {date}.

Formatting rules:
- Answer in the user's language and match their level of detail.
- Use GitHub-flavoured Markdown: headings, **bold**, lists, tables and fenced
  code blocks with a language tag. Keep answers tight unless depth is needed.
- Use LaTeX between $...$ for inline maths and $$...$$ for display maths.
- Never invent URLs, statistics or quotes. If you are not sure, say so.

Web results: {search_state}
{search_context}

Images: {image_state}
Attachments: images the user uploads are given to you directly; refer to what you
actually see. If you cannot see an attachment, say so instead of guessing.
"""

IMAGE_REQUEST_PATTERNS = [
    r"^\s*/(?:imagine|image|img|draw|paint)\b",
    r"\b(?:draw|sketch|paint|illustrate|render|generate|create|make|design)\b[^.?!]{0,40}\b(?:image|picture|photo|illustration|artwork|logo|wallpaper|poster|avatar|icon|drawing)\b",
    r"\b(?:image|picture|photo|illustration|logo|wallpaper|poster|drawing|artwork)\b[^.?!]{0,25}\bof\b",
]
NEGATIVE_IMAGE_PATTERNS = [
    r"\bhow (?:do|can|would) (?:i|you|we)\b",
    r"\bwhat (?:is|are|does)\b",
    r"\bexplain\b",
]


def looks_like_image_request(text: str) -> bool:
    lowered = (text or "").lower()
    if any(re.search(p, lowered) for p in NEGATIVE_IMAGE_PATTERNS):
        return False
    return any(re.search(p, lowered) for p in IMAGE_REQUEST_PATTERNS)


def clean_image_prompt(text: str) -> str:
    text = re.sub(r"^\s*/(?:imagine|image|img|draw|paint)\s*", "", text, flags=re.I)
    text = re.sub(r"^\s*(?:please\s+)?(?:can you\s+)?(?:generate|create|make|draw|paint|render)\s+(?:me\s+)?", "", text, flags=re.I)
    return text.strip()[:900]


def wants_search(text: str) -> bool:
    lowered = (text or "").lower()
    if len(lowered.split()) < 2:
        return False
    signals = (
        "latest", "today", "now", "current", "currently", "news", "price", "prices",
        "weather", "score", "who is", "what is", "when did", "when is", "how much",
        "release date", "update", "2024", "2025", "2026", "stock", "trending",
        "search", "google", "look up", "find out", "recent", "this week", "this month",
        "exchange rate", "rate of", "vs ", "compare", "best ", "top ",
    )
    return any(sig in lowered for sig in signals)


def search_query(text: str, files: list[dict[str, Any]]) -> str:
    cleaned = re.sub(r"```.*?```", " ", text, flags=re.S)
    cleaned = re.sub(r"https?://\S+", lambda m: m.group(0), cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 320:
        cleaned = cleaned[:320]
    return cleaned or (files[0]["name"] if files else "")


def _time_ago(ts: float) -> str:
    delta = max(0.0, dt.datetime.now().timestamp() - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def build_messages(
    history: list[dict[str, Any]],
    settings: config.Settings,
    attachments_by_message: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Trim history to the token/character budget, newest first."""
    attachments_by_message = attachments_by_message or {}
    budget = settings.history_char_budget
    used = 0
    kept: list[dict[str, Any]] = []
    for msg in reversed(history):
        if msg["role"] not in {"user", "assistant"}:
            continue
        text = msg.get("content") or ""
        cost = len(text) + 700 * len(msg.get("images") or [])
        if used + cost > budget and kept:
            break
        used += cost
        kept.append(msg)
    kept.reverse()
    out: list[dict[str, Any]] = []
    for msg in kept:
        images = []
        for att in msg.get("attachments") or attachments_by_message.get(msg["id"], []):
            if att.get("vision_url"):
                images.append(att["vision_url"])
        out.append(
            {
                "role": msg["role"],
                "content": msg.get("content") or "",
                "images": images,
            }
        )
    return out


def system_message(
    settings: config.Settings,
    search_bundle: search.SearchBundle | None,
    image_enabled: bool,
    vision_enabled: bool,
) -> str:
    now = dt.datetime.now().astimezone()
    search_state = (
        "web search is ON for this turn." if search_bundle else "web search is OFF for this turn."
    )
    if search_bundle is not None and not search_bundle.ok:
        search_context = (
            f"(Search failed: {search_bundle.error}. Answer from your own knowledge and "
            "tell the user that live results were unavailable if it matters.)"
        )
    elif search_bundle:
        search_context = search.build_search_context(
            search_bundle, settings.search_context_chars
        )
    else:
        search_context = (
            "(No live results. If the question needs up-to-date facts, say what you know "
            "and suggest turning web search on.)"
        )
    image_state = (
        "you can create images; the app shows a Generate image button, and if the user "
        "asks you to draw something, briefly describe your plan and let the app generate it."
        if image_enabled
        else "image generation is not configured on this deployment."
    )
    if not vision_enabled:
        image_state += " The selected model cannot view images."
    return SYSTEM_PROMPT.format(
        date=now.strftime("%A, %d %B %Y, %H:%M %Z"),
        search_state=search_state,
        search_context=search_context,
        image_state=image_state,
    )


def title_from(text: str, limit: int = 56) -> str:
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:hi|hello|hey|please|can you|i want to|i need|help me)\b[ ,]*", "", text, flags=re.I)
    if not text:
        return "New chat"
    if len(text) > limit:
        cut = text[:limit]
        space = cut.rfind(" ")
        text = cut[: space if space > 24 else limit].rstrip() + "…"
    return text[:1].upper() + text[1:]


async def suggest_title(
    runtime: providers.Runtime,
    model: str,
    first_user: str,
    first_reply: str,
    vision: bool = False,
) -> str:
    """Ask a small model for a chat title; falls back to a heuristic."""
    try:
        title = await providers.complete_once(
            runtime,
            [
                {
                    "role": "user",
                    "content": (
                        "Create a 3-6 word title for this conversation. No quotes, no "
                        "punctuation at the end, title case.\n\n"
                        f"User: {first_user[:800]}\nAssistant: {first_reply[:800]}"
                    ),
                }
            ],
            system="You write very short chat titles. Reply with the title only.",
            model=model,
            temperature=0.2,
            max_tokens=32,
            vision=vision,
        )
        title = title.strip().strip('"').strip("'").splitlines()[0]
        title = re.sub(r"^(title|chat title)\s*[:\-]\s*", "", title, flags=re.I)
        if 2 <= len(title) <= 80:
            return title
    except (providers.ProviderError, IndexError, AttributeError):
        pass
    return title_from(first_user)


async def gather_search(
    settings: config.Settings, query: str, enabled: bool
) -> search.SearchBundle | None:
    if not enabled or not query.strip():
        return None
    bundle = await search.web_search(
        query, settings.search_providers, limit=settings.max_search_results
    )
    if bundle.ok and settings.pages_to_read:
        await search.enrich_with_pages(
            bundle, pages=settings.pages_to_read, limit=settings.page_fetch_chars
        )
    return bundle


async def stream_answer(
    runtime: providers.Runtime,
    model: str,
    messages: list[dict[str, Any]],
    system: str,
    options: dict[str, Any],
    vision: bool,
    degrade_on_error: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Proxy provider events, tolerating gateways that reject some options."""
    attempts: list[dict[str, Any]] = [
        {
            "temperature": options.get("temperature", 0.7),
            "max_tokens": options.get("max_tokens", 2048),
            "top_p": options.get("top_p"),
        },
        {
            "temperature": options.get("temperature", 0.7),
            "max_tokens": options.get("max_tokens", 2048),
        },
        {"max_tokens": options.get("max_tokens", 2048)},
        {},
    ]
    if runtime.kind in {"free", "demo"}:
        # these adapters already fall back internally; never double-charge a turn
        attempts = attempts[:1]
    last_error: providers.ProviderError | None = None
    for index, params in enumerate(attempts):
        produced = False
        try:
            async for event in providers.stream_chat(
                runtime,
                messages,
                system=system,
                model=model,
                vision=vision,
                **params,
            ):
                produced = True
                yield event
            return
        except providers.ProviderError as exc:
            last_error = exc
            if runtime.kind == "free" and not produced:
                # free models are busy sometimes: answer with a graceful notice
                if not degrade_on_error:
                    raise
                yield {"type": "status", "text": "Free models busy — using offline reply…"}
                notice = (
                    f"**The free service is busy right now** ({exc.message.split('.')[0]}). "
                    "This is an offline placeholder answer; press **Retry** in a moment."
                )
                demo_module = __import__("app.demo", fromlist=["demo"])
                async for event in demo_module.stream(messages, system, 2048, notice):
                    yield event
                return
            if produced or exc.status == 400 and index >= 2:
                raise
            if index + 1 >= len(attempts):
                raise
            await asyncio.sleep(0.15)
    if last_error:
        raise last_error


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"
