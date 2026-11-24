"""Real web search for Bats AI.

Supports several back-ends and picks the best available one:

* ``brave``    – Brave Search API            (BRAVE_API_KEY)
* ``tavily``   – Tavily search API           (TAVILY_API_KEY)
* ``serper``   – Google results via Serper   (SERPER_API_KEY)
* ``exa``      – Exa neural search           (EXA_API_KEY)
* ``searxng``  – self hosted SearXNG         (SEARXNG_URL)
* ``duckduckgo`` – keyless HTML endpoint (works with no configuration)
* ``wikipedia``  – keyless encyclopedia fallback

``web_search()`` hits the first provider that returns results and
``build_search_context()`` turns the hits into clean text (plus the top pages'
readable content) that gets injected into the model prompt, together with the
source list so the model can cite URLs.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 BatsAI/1.0"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchBundle:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    provider: str = ""
    pages: list[dict[str, str]] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.results)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _clean(text: str, limit: int = 400) -> str:
    text = html_lib.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _dedupe(results: Iterable[SearchResult], limit: int) -> list[SearchResult]:
    seen: set[str] = set()
    out: list[SearchResult] = []
    for res in results:
        if not res.url or not res.title:
            continue
        key = res.url.split("#")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(res)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# providers
# --------------------------------------------------------------------------- #
async def _brave(client: httpx.AsyncClient, query: str, limit: int, key: str) -> list[SearchResult]:
    r = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": limit, "text_decorations": "false"},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=12,
    )
    r.raise_for_status()
    data = r.json()
    out = []
    for item in (data.get("web", {}) or {}).get("results", [])[:limit]:
        out.append(
            SearchResult(
                title=_clean(item.get("title", ""), 200),
                url=item.get("url", ""),
                snippet=_clean(item.get("description", ""), 500),
            )
        )
    return out


async def _tavily(client: httpx.AsyncClient, query: str, limit: int, key: str) -> list[SearchResult]:
    r = await client.post(
        "https://api.tavily.com/search",
        json={
            "api_key": key,
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
            "include_answer": False,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return [
        SearchResult(
            title=_clean(item.get("title", ""), 200),
            url=item.get("url", ""),
            snippet=_clean(item.get("content", ""), 500),
        )
        for item in data.get("results", [])[:limit]
    ]


async def _serper(client: httpx.AsyncClient, query: str, limit: int, key: str) -> list[SearchResult]:
    r = await client.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": limit},
        timeout=12,
    )
    r.raise_for_status()
    data = r.json()
    out = []
    for item in data.get("organic", [])[:limit]:
        out.append(
            SearchResult(
                title=_clean(item.get("title", ""), 200),
                url=item.get("link", ""),
                snippet=_clean(item.get("snippet", ""), 500),
            )
        )
    return out


async def _exa(client: httpx.AsyncClient, query: str, limit: int, key: str) -> list[SearchResult]:
    r = await client.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": key, "Content-Type": "application/json"},
        json={
            "query": query,
            "numResults": limit,
            "contents": {"text": {"maxCharacters": 600}},
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    out = []
    for item in data.get("results", [])[:limit]:
        out.append(
            SearchResult(
                title=_clean(item.get("title") or item.get("url", ""), 200),
                url=item.get("url", ""),
                snippet=_clean(item.get("text", ""), 500),
            )
        )
    return out


async def _searxng(client: httpx.AsyncClient, query: str, limit: int, base: str) -> list[SearchResult]:
    base = base.rstrip("/")
    r = await client.get(
        f"{base}/search",
        params={"q": query, "format": "json", "language": "en"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return [
        SearchResult(
            title=_clean(item.get("title", ""), 200),
            url=item.get("url", ""),
            snippet=_clean(item.get("content", ""), 500),
        )
        for item in data.get("results", [])[:limit]
    ]


async def _duckduckgo(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    """Keyless DuckDuckGo. Tries the lite/html endpoint with two shapes."""
    out: list[SearchResult] = []
    try:
        r = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "wt-wt"},
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=12,
            follow_redirects=True,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for node in soup.select(".result"):
            link = node.select_one("a.result__a")
            if not link:
                continue
            href = link.get("href", "")
            if href.startswith("//duckduckgo.com/l/"):
                qs = parse_qs(urlparse("https:" + href).query)
                href = unquote(qs.get("uddg", [href])[0])
            snippet_node = node.select_one(".result__snippet")
            out.append(
                SearchResult(
                    title=_clean(link.get_text(" "), 200),
                    url=href,
                    snippet=_clean(snippet_node.get_text(" ") if snippet_node else "", 500),
                )
            )
            if len(out) >= limit:
                break
    except (httpx.HTTPError, ValueError):
        pass

    if out:
        return out

    try:  # lite endpoint
        out = await _duckduckgo_lite(client, query, limit)
    except Exception:  # noqa: BLE001 - any transport/parse error -> next fallback
        out = []

    if out:
        return out

    try:  # JSON endpoint fallback
        r = await client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("AbstractText"):
            out.append(
                SearchResult(
                    title=data.get("Heading") or query,
                    url=data.get("AbstractURL") or "https://duckduckgo.com",
                    snippet=_clean(data["AbstractText"], 500),
                )
            )
        for topic in data.get("RelatedTopics", []):
            if not isinstance(topic, dict) or "FirstURL" not in topic:
                continue
            out.append(
                SearchResult(
                    title=_clean(topic.get("Text", ""), 200),
                    url=topic["FirstURL"],
                    snippet=_clean(topic.get("Text", ""), 500),
                )
            )
            if len(out) >= limit:
                break
    except (httpx.HTTPError, ValueError):
        pass
    return out


async def _duckduckgo_lite(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    """DuckDuckGo Lite (different host, sometimes succeeds when html/ fails)."""
    out: list[SearchResult] = []
    r = await client.post(
        "https://lite.duckduckgo.com/lite/",
        data={"q": query, "kl": "wt-wt"},
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=12,
        follow_redirects=True,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    for row in soup.select("a.result-link"):
        href = row.get("href", "")
        if href.startswith("//duckduckgo.com/l/"):
            qs = parse_qs(urlparse("https:" + href).query)
            href = unquote(qs.get("uddg", [href])[0])
        parent = row.find_parent("tr")
        snippet = parent.find_next_sibling("tr") if parent else None
        out.append(
            SearchResult(
                title=_clean(row.get_text(" "), 200),
                url=href,
                snippet=_clean(snippet.get_text(" ") if snippet else "", 500),
            )
        )
        if len(out) >= limit:
            break
    return out


async def _mojeek(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    """Mojeek — independent index, keyless HTML results."""
    r = await client.get(
        "https://www.mojeek.com/search",
        params={"q": query},
        timeout=12,
        headers=HEADERS,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    out: list[SearchResult] = []
    for li in soup.select("ul.results-standard li"):
        link = li.select_one("a.title") or li.select_one("h2 a")
        if not link or not link.get("href"):
            continue
        snippet_node = li.select_one("p.s")
        out.append(
            SearchResult(
                title=_clean(link.get_text(" "), 200),
                url=urljoin("https://www.mojeek.com", link["href"]),
                snippet=_clean(snippet_node.get_text(" ") if snippet_node else "", 500),
            )
        )
        if len(out) >= limit:
            break
    return out


async def _google_news(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    """Google News RSS — keyless, excellent for current events."""
    r = await client.get(
        "https://news.google.com/rss/search",
        params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        timeout=12,
        headers=HEADERS,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "xml")
    out: list[SearchResult] = []
    for item in soup.find_all("item")[: limit * 2]:
        title = item.title.get_text() if item.title else ""
        link = (item.link.get_text() if item.link else "").strip()
        desc = item.find("source")
        src = desc.get_text() if desc else ""
        if not title or not link:
            continue
        out.append(
            SearchResult(
                title=_clean(title, 200),
                url=link,
                snippet=_clean(f"{src}: {title}" if src else title, 300),
            )
        )
        if len(out) >= limit:
            break
    return out


async def _hackernews(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    """Hacker News (Algolia) — keyless, good for tech topics."""
    r = await client.get(
        "https://hn.algolia.com/api/v1/search",
        params={"query": query, "tags": "story", "hitsPerPage": limit},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    out: list[SearchResult] = []
    for hit in data.get("hits", [])[:limit]:
        title = hit.get("title") or hit.get("story_title") or ""
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        out.append(
            SearchResult(
                title=_clean(title, 200),
                url=url,
                snippet=_clean(hit.get("story_text") or hit.get("comment_text") or "", 400),
            )
        )
    return out


async def _wikipedia(client: httpx.AsyncClient, query: str, limit: int) -> list[SearchResult]:
    """Keyless encyclopedia fallback."""
    r = await client.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": limit,
            "origin": "*",
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    out = []
    for item in data.get("query", {}).get("search", [])[:limit]:
        title = item.get("title", "")
        out.append(
            SearchResult(
                title=title,
                url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                snippet=_clean(item.get("snippet", ""), 500),
            )
        )
    return out


PROVIDER_FUNCS = {
    "brave": _brave,
    "tavily": _tavily,
    "serper": _serper,
    "exa": _exa,
    "searxng": _searxng,
}


async def web_search(
    query: str,
    providers: list[str],
    limit: int = 6,
    client: httpx.AsyncClient | None = None,
) -> SearchBundle:
    """Run a search against the best available provider."""
    query = (query or "").strip()
    if not query:
        return SearchBundle(query=query, error="Empty query")

    import os

    own_client = client is None
    client = client or httpx.AsyncClient(headers=HEADERS, follow_redirects=True)
    bundle = SearchBundle(query=query)
    errors: list[str] = []
    try:
        for name in providers:
            try:
                if name in PROVIDER_FUNCS:
                    env_var = {
                        "brave": "BRAVE_API_KEY",
                        "tavily": "TAVILY_API_KEY",
                        "serper": "SERPER_API_KEY",
                        "exa": "EXA_API_KEY",
                        "searxng": "SEARXNG_URL",
                    }[name]
                    cred = (os.environ.get(env_var) or "").strip()
                    if not cred:
                        continue
                    results = await PROVIDER_FUNCS[name](client, query, limit, cred)
                elif name == "duckduckgo":
                    results = await _duckduckgo(client, query, limit)
                elif name == "mojeek":
                    results = await _mojeek(client, query, limit)
                elif name == "google_news":
                    results = await _google_news(client, query, limit)
                elif name == "hackernews":
                    results = await _hackernews(client, query, limit)
                elif name == "wikipedia":
                    results = await _wikipedia(client, query, limit)
                else:
                    continue
                results = _dedupe(results, limit)
                if results:
                    for res in results:
                        res.source = name
                    bundle.results = results
                    bundle.provider = name
                    return bundle
            except Exception as exc:  # noqa: BLE001 - never let one engine kill search
                errors.append(f"{name}: {type(exc).__name__}")
                continue
    finally:
        if own_client:
            await client.aclose()
    bundle.error = "; ".join(errors[-3:]) or "No search provider returned results"
    return bundle


# --------------------------------------------------------------------------- #
# page reading
# --------------------------------------------------------------------------- #
BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "pre", "blockquote", "td")
DROP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg")


def html_to_text(raw: str, limit: int = 4_500) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(list(DROP_TAGS)):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    chunks: list[str] = []
    total = 0
    for node in root.find_all(BLOCK_TAGS):
        text = node.get_text(" ", strip=True)
        if len(text) < 25:
            continue
        chunks.append(text)
        total += len(text)
        if total > limit:
            break
    if not chunks:  # last resort
        chunks = [root.get_text(" ", strip=True)[:limit]]
    text = "\n".join(chunks)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit].strip()


async def read_page(client: httpx.AsyncClient, url: str, limit: int = 4_500) -> dict[str, str]:
    try:
        r = await client.get(url, timeout=10, headers=HEADERS)
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400 or "html" not in ctype and "text" not in ctype:
            return {"url": url, "text": ""}
        return {"url": url, "text": html_to_text(r.text, limit)}
    except (httpx.HTTPError, ValueError, UnicodeDecodeError):
        return {"url": url, "text": ""}


async def enrich_with_pages(
    bundle: SearchBundle,
    pages: int = 3,
    limit: int = 4_500,
    client: httpx.AsyncClient | None = None,
) -> SearchBundle:
    """Fetch readable text for the top results (run in parallel)."""
    if not bundle.results or pages <= 0:
        return bundle
    own_client = client is None
    client = client or httpx.AsyncClient(headers=HEADERS, follow_redirects=True)
    try:
        targets = [r.url for r in bundle.results[:pages] if r.url.startswith("http")]
        fetched = await asyncio.gather(
            *(read_page(client, url, limit) for url in targets), return_exceptions=True
        )
        for item in fetched:
            if isinstance(item, dict) and item.get("text"):
                bundle.pages.append(item)
    finally:
        if own_client:
            await client.aclose()
    return bundle


def build_search_context(bundle: SearchBundle, max_chars: int = 12_000) -> str:
    """Format search results + page text for the model prompt."""
    if not bundle.results:
        return ""
    lines: list[str] = []
    lines.append(
        f'Web search results for "{bundle.query}" (via {bundle.provider}). '
        "Use them and cite sources as [1], [2] matching the numbers below. "
        "If the results do not answer the question, say so and answer from general knowledge."
    )
    lines.append("")
    for idx, res in enumerate(bundle.results, start=1):
        lines.append(f"[{idx}] {res.title}")
        lines.append(f"    URL: {res.url}")
        if res.snippet:
            lines.append(f"    Snippet: {res.snippet}")
    if bundle.pages:
        lines.append("")
        lines.append("Page extracts:")
        for page in bundle.pages:
            lines.append(f"--- {page['url']}")
            lines.append(page["text"])
    text = "\n".join(lines)
    return text[:max_chars]


def sources_payload(bundle: SearchBundle) -> list[dict[str, str]]:
    return [
        {"index": i, "title": r.title, "url": r.url, "snippet": r.snippet}
        for i, r in enumerate(bundle.results, start=1)
    ]
