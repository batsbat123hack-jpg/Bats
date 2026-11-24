"""Bats AI self-test — run this in a Render Shell after deploying.

    python scripts/selftest.py

It checks that this host can actually reach every web-search engine, model
provider and image service the app can use, then tells you exactly what will
work on your deployment (and what to add if something is blocked).
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.search import HEADERS  # noqa: E402

TARGETS = [
    ("duckduckgo", "POST", "https://html.duckduckgo.com/html/", {"q": "test", "kl": "wt-wt"}),
    ("duckduckgo-lite", "POST", "https://lite.duckduckgo.com/lite/", {"q": "test"}),
    ("google_news", "GET", "https://news.google.com/rss/search?q=test", None),
    ("mojeek", "GET", "https://www.mojeek.com/search?q=test", None),
    ("hackernews", "GET", "https://hn.algolia.com/api/v1/search?query=test&tags=story", None),
    ("wikipedia", "GET", "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=test&format=json", None),
    ("brave-search", "GET", "https://api.search.brave.com/res/v1/web/search?q=test", None),
    ("tavily-search", "POST", "https://api.tavily.com/search", {"query": "test"}),
    ("openai", "GET", "https://api.openai.com/v1/models", None),
    ("groq", "GET", "https://api.groq.com/openai/v1/models", None),
    ("gemini", "GET", "https://generativelanguage.googleapis.com/v1beta/models", None),
    ("anthropic", "GET", "https://api.anthropic.com/v1/models", None),
    ("openrouter", "GET", "https://openrouter.ai/api/v1/models", None),
    ("pollinations-images", "GET", "https://image.pollinations.ai/models", None),
]

SEARCH_KEYS = {
    "duckduckgo": "no key",
    "duckduckgo-lite": "no key",
    "google_news": "no key",
    "mojeek": "no key",
    "hackernews": "no key",
    "wikipedia": "no key",
    "brave-search": "BRAVE_API_KEY",
    "tavily-search": "TAVILY_API_KEY",
}


async def probe() -> int:
    async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as client:

        async def check(name, method, url, data):
            try:
                r = await client.request(method, url, data=data or None)
                ok = r.status_code < 500  # 401/403 still proves the host is reachable
                return name, ok, f"HTTP {r.status_code}"
            except Exception as exc:  # noqa: BLE001
                return name, False, type(exc).__name__

        results = await asyncio.gather(*(check(*target) for target in TARGETS))

    width = max(len(name) for name, _, _ in results)
    print("\nBats AI outbound connectivity self-test")
    print("-" * (width + 34))
    for name, ok, status in results:
        print(f"{'OK  ' if ok else 'FAIL'} {name.ljust(width)}  {status}")
    print("-" * (width + 34))

    reachable_search = [n for n, ok, _ in results if ok and n in SEARCH_KEYS]
    print("Web search available with:", ", ".join(reachable_search) or "nothing")

    # what the app would actually choose right now
    try:
        from app import config, search

        for_engines = config.build_settings().search_providers
        print("Configured engine order:", " -> ".join(for_engines))
        bundle = await search.web_search("latest technology news", for_engines, limit=3)
        if bundle.ok:
            print(f"LIVE SEARCH WORKS via {bundle.provider}: {len(bundle.results)} results")
        else:
            print(f"LIVE SEARCH FAILED: {bundle.error}")
            print("  -> Add a free BRAVE_API_KEY (2k/month) or TAVILY_API_KEY (1k/month).")
    except Exception as exc:  # noqa: BLE001
        print("Search probe error:", exc)

    usable = [n for n, ok, _ in results if ok and n in {"openai", "groq", "gemini", "anthropic", "openrouter"}]
    print("Model providers reachable:", ", ".join(usable) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
