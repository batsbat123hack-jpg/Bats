"""Demo back-end — lets anyone try the full UI with no API keys.

Enable with ``DEMO_MODE=true``. It behaves like a provider (registered as a
normal entry in the provider catalogue) but answers locally, streaming token by
token, so streaming, Markdown, web-search grounding, citations, image uploads
and image generation can all be exercised on a fresh deployment.

It is intentionally obvious that answers are canned.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncIterator

from PIL import Image, ImageDraw

from . import config

DEMO_SYSTEM_HINT = "demo"


def register(settings: config.Settings) -> None:
    """Add the demo provider to the catalogue when DEMO_MODE is on."""
    from . import providers as _providers  # noqa: F401  (kept for clarity)

    if not config.env_bool("DEMO_MODE", False):
        return
    entry = {
        "id": "demo",
        "label": "Bats Demo (no key needed)",
        "kind": "demo",
        "base_url": "local://demo",
        "models": [
            {"id": "bats-demo", "label": "Bats Demo model", "vision": True},
        ],
        "requires_key": False,
        "has_key": True,
        "env_var": "",
        "configured": True,
        "docs_url": "",
        "note": "Canned answers that exercise the whole interface.",
        "vision": True,
    }
    settings.providers = [entry] + [p for p in settings.providers if p["id"] != "demo"]
    if "demo" not in settings.available_providers:
        settings.available_providers = ["demo"] + settings.available_providers
    if not settings.default_provider:
        settings.default_provider = "demo"
        settings.default_model = "bats-demo"


def is_demo(provider_id: str) -> bool:
    return provider_id == "demo"


def _compose_art(prompt: str, width: int, height: int) -> bytes:
    """Deterministic generative-ish artwork (gradients + shapes + caption)."""
    import io
    import random

    seed = abs(hash(prompt)) % (2**32)
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), (10, 12, 18))
    draw = ImageDraw.Draw(img)
    hue_a = (rng.randint(0, 90), rng.randint(120, 220), rng.randint(120, 200))
    hue_b = (rng.randint(120, 255), rng.randint(20, 90), rng.randint(80, 190))
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(hue_a[i] * (1 - t) + hue_b[i] * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    for _ in range(26):
        x0 = rng.randint(-width // 4, width)
        y0 = rng.randint(-height // 4, height)
        r = rng.randint(width // 22, width // 5)
        fill = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        draw.ellipse([x0, y0, x0 + r, y0 + r], outline=fill, width=max(2, width // 320))
    for _ in range(8):
        draw.line(
            [
                (rng.randint(0, width), rng.randint(0, height)),
                (rng.randint(0, width), rng.randint(0, height)),
            ],
            fill=(255, 255, 255),
            width=max(1, width // 500),
        )
    # soft vignette (translucent overlay rather than hard frames)
    vignette = Image.new("L", (width, height), 0)
    vdraw = ImageDraw.Draw(vignette)
    for i in range(70):
        shade = int(120 * (1 - i / 70))
        vdraw.rectangle([i, i, width - 1 - i, height - 1 - i], outline=shade)
    img.paste(Image.new("RGB", (width, height), (0, 0, 0)), (0, 0), vignette.point(lambda v: v // 3))

    bar_h = max(48, height // 14)
    draw.rectangle([0, height - bar_h, width, height], fill=(6, 8, 12))
    draw.text((16, height - bar_h + bar_h // 2 - 5), f"BATS DEMO - {prompt[:52]}", fill=(226, 238, 252))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def demo_reply(text: str, system: str, has_image: bool, notice: str = "") -> str:
    grounded = "Web search results for" in system
    intro = notice or (
        "**Demo mode** — this deployment has no model API key yet, so you are seeing a "
        "canned answer that exercises the whole interface."
    )
    lines = [
        intro,
        "",
        "### Everything you just used works",
        "",
        "- streaming tokens, Markdown, code blocks and tables",
        "- web search grounding with citations [1]",
        "- image reading (vision payloads)",
        "- image generation",
        "",
        "```python",
        "def hello(name: str) -> str:",
        '    """Everything is wired up."""',
        '    return f"Hi {name}!"',
        "```",
        "",
        "| Feature | Status |",
        "| --- | --- |",
        "| Chat streaming | works |",
        "| Web search | works |",
        "| Image upload | works |",
        "| Image generation | works |",
        "",
    ]
    if grounded:
        lines.insert(0, "_Answer grounded with live web results._\n")
    if has_image:
        lines.insert(0, "_I received your image and would describe it here._\n")
    if text.strip():
        quoted = re.sub(r"\s+", " ", text).strip()[:160]
        lines.append(f"You said: *{quoted}*")
    lines.append("")
    lines.append(
        "Bats Free needs no key at all. If the free models are busy, press **Retry** — or "
        "add a free Groq/Gemini key in **Settings → API keys** for guaranteed availability."
    )
    return "\n".join(lines)


async def stream(
    messages: list[dict[str, Any]],
    system: str,
    max_tokens: int = 2048,
    notice: str = "",
) -> AsyncIterator[dict[str, Any]]:
    last_user = next((m for m in reversed(messages) if m["role"] == "user"), {"content": ""})
    has_image = bool(last_user.get("images"))
    reply = demo_reply(last_user.get("content", ""), system, has_image, notice)
    if "title for this conversation" in " ".join(
        str(m.get("content", "")) for m in messages
    ).lower():
        reply = "Bats Demo Chat"
    step = 8
    for index in range(0, len(reply), step):
        await asyncio.sleep(0.012)
        yield {"type": "text", "text": reply[index : index + step]}
    yield {"type": "finish", "reason": "stop"}
    yield {
        "type": "usage",
        "usage": {"prompt_tokens": 0, "completion_tokens": len(reply) // 4, "total_tokens": len(reply) // 4},
    }


def generate(prompt: str, width: int, height: int) -> bytes:
    return _compose_art(prompt, width, height)
