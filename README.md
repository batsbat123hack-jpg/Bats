# Bats AI

A ChatGPT-style assistant you own and can deploy to your own URL
(`www.bats-ai.onrender.com`) — streaming chat, **real web search**, image
understanding and image generation, in one small FastAPI app with a
dependency-free front end.

![status](https://img.shields.io/badge/deploy-Render-46E3B7) ![python](https://img.shields.io/badge/python-3.11-blue) ![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## What it does

| | |
| --- | --- |
| 💬 **ChatGPT-like UI** | sidebar history with pin/rename/delete/search, model picker, streaming answers, markdown + GFM tables, syntax-highlighted code with copy buttons, LaTeX, citations, dark/light themes, mobile layout, keyboard shortcuts, read-aloud, export to Markdown |
| 🔎 **Real web search** | Brave / Tavily / Serper / Exa / SearXNG when a key is present; automatic keyless fallback to DuckDuckGo, Google News RSS, Mojeek, Hacker News and Wikipedia. Results are fetched, page text is extracted and injected into the prompt, and sources appear as numbered clickable cards |
| 🖼️ **Every image format in** | PNG, JPG/JPEG, GIF (incl. animated), WEBP, BMP, TIFF, ICO, HEIC/HEIF (iPhone), AVIF and SVG — normalised with Pillow so any vision model can read them, EXIF-rotated, resized and re-encoded for the model |
| 🎨 **Image generation** | `gpt-image-1` / `dall-e-3` / any OpenAI-compatible image API, plus a **free keyless** Pollinations path and local demo art. Images are stored server-side and rendered inline in the chat |
| 🔌 **Many providers** | OpenAI, Anthropic, Google Gemini, Groq, OpenRouter, DeepSeek, Mistral, xAI, Together, Ollama, or any OpenAI-compatible base URL |
| 🔑 **Server or browser keys** | keys set on the server are used automatically; a visitor can also paste their own key in Settings, so a keyless deployment is still usable |
| 🗄️ **Persistence** | SQLite conversations/messages, attachments and generated images on disk — survives restarts with a Render disk |
| 🔒 **Ready for a public URL** | rate limiting, optional access code, CORS for your domain, health check, SSE-safe streaming, security headers |
| 🤝 **OpenAI-compatible API** | `GET /v1/models`, `POST /v1/chat/completions` — point any SDK or tool at your deployment |

## Quick start

```bash
git clone <your-repo>
cd Bats
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add a key, or paste one in the UI later
uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>. No keys? Set `DEMO_MODE=true` to try every screen
with canned answers and generated placeholder art.

## Deploy to `www.bats-ai.onrender.com`

Full walkthrough (custom domain, DNS, keys, disks, troubleshooting):
**[DEPLOY.md](DEPLOY.md)**.

Short version:

1. Push this repo to GitHub.
2. Render → **New + → Blueprint** → select the repo → **Apply** (uses `render.yaml`).
3. Paste at least one model key (e.g. `GROQ_API_KEY` or `GEMINI_API_KEY` — both free).
4. Render → **Settings → Custom Domains** → add `www.bats-ai.onrender.com` and point
   a `CNAME` at the hostname Render shows you.
5. Verify: `curl https://www.bats-ai.onrender.com/healthz`.

## Configuration

Everything is environment driven — see [`.env.example`](.env.example) and the
tables in [DEPLOY.md](DEPLOY.md). The important ones:

```ini
# a model provider (at least one)
OPENAI_API_KEY=               # or GROQ_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY ...
DEFAULT_PROVIDER=openai
DEFAULT_MODEL=gpt-4o-mini

# image generation
IMAGE_PROVIDER=pollinations   # free, no key; or "openai" with a key

# real web search (all optional — keyless fallbacks always work)
BRAVE_API_KEY=                # or TAVILY_API_KEY / SERPER_API_KEY / EXA_API_KEY

# storage & limits
DATA_DIR=/var/data
MAX_UPLOAD_MB=20
ACCESS_CODE=                  # optional private instance
```

## Architecture

```
static/index.html      single page: sidebar, thread, composer, settings modal
static/styles.css      design system, dark + light themes, responsive
static/app.js          state, SSE streaming, uploads, image generation, settings
static/markdown.js     safe markdown renderer + highlighter + citations
app/main.py            FastAPI routes, SSE chat endpoint, OpenAI-compatible API
app/chat.py            prompt building, search grounding, history budgets
app/providers.py       OpenAI-compatible + Anthropic streaming adapters
app/search.py          multi-engine web search, page extraction, citations
app/images.py          uploads (all formats), normalisation, image generation
app/store.py           SQLite persistence (chats, messages, prefs)
app/demo.py            keyless demo provider + local placeholder art
render.yaml            Render blueprint (service, disk, env vars)
```

The browser only ever talks to this one origin: uploads, generated images and
streaming all pass through the app, so there are no CORS or mixed-content
problems on your domain.

## Tests

```bash
python -m pytest tests -q      # 28 backend tests (mock OpenAI provider)
node tests/markdown.test.js    # 20 markdown renderer tests
node tests/ui.test.js          # 18 UI drive-through checks (jsdom)
```

The backend suite starts a mock OpenAI-compatible server and exercises streaming
chat, web-result grounding and citations, uploads in eight image formats plus
SVG, vision payloads, image generation, conversation CRUD, titles, regenerate and
error handling.

## License

MIT
