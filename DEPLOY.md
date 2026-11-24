# Deploy Bats AI to `www.bats-ai.onrender.com`

This walks through going live on Render, including the custom domain, the API
keys that switch on AI answers, image generation and web search, and the
persistent disk that keeps your chats.

> **60-second version**
> 1. Render → **New + → Blueprint** → pick this repo → **Apply**.
> 2. When Render asks for secrets, paste at least one model key (`OPENAI_API_KEY`).
> 3. Add your custom domain `www.bats-ai.onrender.com` in **Settings → Custom Domains**.
> 4. Open the site, pick a model, send a message. Done.

---

## 1. Push this repository

```bash
git add .
git commit -m "Bats AI: chat, web search, images"
git push origin main
```

Render deploys straight from GitHub, so the code must be pushed first.

## 2. Create the service

### Option A — Blueprint (recommended, uses `render.yaml`)

1. Go to <https://dashboard.render.com> → **New +** → **Blueprint**.
2. Select the `batsbat123hack-jpg/Bats` repository → **Apply**.
3. Render creates the web service `bats-ai`, the 1 GB disk at `/var/data`, the
   health check and all environment variables. Fill in the secrets it asks for.

### Option B — Manual web service

| Field | Value |
| --- | --- |
| Type | Web Service |
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers` |
| Health check path | `/healthz` |
| Instance type | Free (or Starter — see §7) |

Add a **Disk**: mount path `/var/data`, 1 GB, and set `DATA_DIR=/var/data`.
Without the disk, chats and uploaded images are lost on every deploy/restart.

## 3. The API keys that switch features on

Set these in **Dashboard → your service → Environment**. Only one model key is
strictly required. Keys can also be pasted in the app's **Settings → API keys**
(stored in the visitor's browser) if you would rather not put them on the server.

### AI answers (pick one or more)

| Variable | Provider | Free tier |
| --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI (gpt-4o-mini, gpt-4o, gpt-4.1, o4-mini) | paid |
| `GROQ_API_KEY` | Groq (Llama 3.3 70B, GPT-OSS 120B) — very fast | **yes, generous** |
| `GEMINI_API_KEY` | Google Gemini 2.5 Flash/Pro — [AI Studio](https://aistudio.google.com/apikey) | **yes** |
| `ANTHROPIC_API_KEY` | Claude Sonnet 4.5 / Haiku | paid |
| `OPENROUTER_API_KEY` | one key → hundreds of models | some free models |
| `DEEPSEEK_API_KEY` | DeepSeek Chat / Reasoner (cheap) | paid |
| `MISTRAL_API_KEY`, `XAI_API_KEY`, `TOGETHER_API_KEY` | Mistral, Grok, Together | varies |

Optional: `DEFAULT_PROVIDER=groq`, `DEFAULT_MODEL=llama-3.3-70b-versatile`
to control what new visitors start with.

> Cheapest good starting point: `GEMINI_API_KEY` or `GROQ_API_KEY`. Both have
> free tiers and support streaming + vision.

### Image generation

| Variable | Result |
| --- | --- |
| `IMAGE_PROVIDER=pollinations` | **free, no key** — FLUX images, works out of the box |
| `OPENAI_API_KEY` + `IMAGE_PROVIDER=openai` | `gpt-image-1` / `dall-e-3` quality |
| `IMAGE_PROVIDER=openai_images_custom`, `IMAGE_BASE_URL`, `IMAGE_API_KEY` | any OpenAI-compatible image API |

`DEMO_MODE=true` additionally generates placeholder art locally when no key is
present — handy for a first look, obviously not real art.

### Real web search

Set **one** of these for best results (all have free tiers):

| Variable | Notes |
| --- | --- |
| `BRAVE_API_KEY` | 2,000 free queries/month, best general quality |
| `TAVILY_API_KEY` | 1,000 free credits/month, AI-ready snippets |
| `SERPER_API_KEY` | 2,500 free Google queries |
| `EXA_API_KEY` | neural search |
| `SEARXNG_URL` | your own SearXNG instance |

**With no key at all, web search still works**: the app automatically falls back
to DuckDuckGo (HTML + lite), Google News RSS, Mojeek, Hacker News and Wikipedia.
Render's datacenter IPs are sometimes rate-limited by DuckDuckGo, so a free Brave
or Tavily key is strongly recommended for reliable search.

### Optional protection

| Variable | Purpose |
| --- | --- |
| `ACCESS_CODE=mysecret` | makes the API private; visitors enter the code in **Settings → Access**, or open `https://www.bats-ai.onrender.com/?code=mysecret` |
| `RATE_LIMIT_PER_MINUTE=40` | per-IP requests/minute (0 disables) |
| `RATE_LIMIT_PER_DAY=2000` | per-IP requests/day (0 disables) |
| `MAX_UPLOAD_MB=20` | per-image upload limit |
| `MAX_ATTACHMENTS=6` | images per message |

## 4. Add your custom domain

1. In Render → your service → **Settings → Custom Domains** → **Add Custom Domain**.
2. Enter `www.bats-ai.onrender.com` → Render gives you a CNAME target such as
   `bats-ai.onrender.com` (the service's `onrender.com` hostname).
3. At your DNS provider (where `bats-ai.onrender.com` is managed — likely your
   Render account's DNS or the registrar for the `bats-ai.com` zone) create:

   | Type | Name | Value |
   | --- | --- | --- |
   | CNAME | `www` | `bats-ai.onrender.com` (the target Render shows you) |

   For an apex domain (`bats-ai.onrender.com` without `www`) use an `A` record set
   to Render's anycast IPs, or `ALIAS`/`ANAME` to the same target.
4. Wait for Render to show **Certificate issued** (Let's Encrypt, usually < 10 min).
5. Confirm `PUBLIC_URL=https://www.bats-ai.onrender.com` is set — it is used for
   CORS and OpenRouter attribution.

`https://bats-ai.onrender.com` (without `www`) is already allowed by the app's
CORS regex, so both hosts work.

## 5. Verify the deployment

```bash
curl https://www.bats-ai.onrender.com/healthz
# {"status":"ok","providers":["openai"],"search":["brave",...],"images":["pollinations"],"conversations":0,"messages":0}

curl -X POST https://www.bats-ai.onrender.com/api/search \
     -H 'Content-Type: application/json' \
     -d '{"query":"latest AI news","limit":3}'
```

In the browser:

1. The model menu offers your configured providers (only those with keys).
2. Send a message → tokens stream in, Markdown/code/tables render.
3. Turn on **Search** in the composer → the answer shows clickable sources.
4. Click **Attach** and send a PNG/JPG/HEIC/WebP/GIF/AVIF/TIFF/SVG → the model
   describes it.
5. Click **Image** → type a prompt → **Generate** → the image renders and is
   saved into the chat.

## 6. Using Bats AI from other apps

The service also exposes an OpenAI-compatible API, so any tool or SDK can point at it:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://www.bats-ai.onrender.com/v1",
    api_key="unused",            # server keys are used; or send your own in the body
    default_headers={"X-Bats-Code": "mysecret"}  # only if ACCESS_CODE is set
)
print(client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello Bats"}],
).choices[0].message.content)
```

Endpoints: `GET /v1/models`, `POST /v1/chat/completions` (stream or not).

## 7. Free-plan notes

- **Cold starts**: a free instance sleeps after ~15 minutes idle and takes ~30 s
  to wake. The first message after a sleep can feel slow; the UI shows progress.
- **RAM**: the free plan gives 512 MB. HEIC/AVIF support (Pillow + pillow-heif)
  needs a little headroom; if you see worker restarts while uploading large
  iPhone photos, move to the Starter plan or raise `MAX_UPLOAD_MB` carefully.
- **Disk**: persistent disks require a paid instance on Render. On the free plan,
  set `DATA_DIR=/tmp/bats-data` to avoid warnings; chats will reset on restarts.
- **Streaming**: Render supports SSE out of the box; the app disables buffering
  via `X-Accel-Buffering: no` and never gzips event streams.

## 8. Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your keys, or paste them in the UI
uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

Tests:

```bash
pip install pytest
python -m pytest tests -q          # backend: chat, uploads, images, search grounding
node tests/markdown.test.js        # markdown renderer
node tests/ui.test.js              # full UI drive-through with jsdom
```

## 9. Troubleshooting

| Symptom | Fix |
| --- | --- |
| "No API key for OpenAI" | Add `OPENAI_API_KEY` on Render, or paste a key in Settings → API keys. |
| Model menu is empty | No provider configured; add a key. `DEMO_MODE=true` gives a keyless demo. |
| Web search always fails | Render IPs are rate-limited by DuckDuckGo — add `BRAVE_API_KEY` (free 2k/month) or `TAVILY_API_KEY`. |
| Upload says "not a readable image" | The file is corrupt or genuinely not an image; supported: PNG, JPG, GIF, WEBP, BMP, TIFF, ICO, HEIC, AVIF, SVG. |
| Image generation 401 | `IMAGE_PROVIDER=openai` needs a valid `OPENAI_API_KEY` with image access; use `pollinations` for a keyless path. |
| Chats disappear after deploy | Add a disk and `DATA_DIR=/var/data`. |
| 401 on every call | The service has `ACCESS_CODE` set — enter it in Settings → Access. |
| 429 responses | Your own rate limit; raise `RATE_LIMIT_PER_MINUTE`. |
| Cold start feels frozen | Free plan sleeps; the first request warms it up. |
