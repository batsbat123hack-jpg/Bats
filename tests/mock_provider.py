"""Mock OpenAI-compatible provider used by the test suite and local demos.

Run standalone:
    python tests/mock_provider.py 9999

Endpoints
    POST /v1/chat/completions   (streaming + non-streaming)
    POST /v1/images/generations (b64_json PNG)
"""

from __future__ import annotations

import base64
import io
import json
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image

app = FastAPI()
CALLS: list[dict] = []


def png_bytes(color=(16, 163, 127), size=(256, 256)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


FREE_MODELS = {"openai", "openai-fast", "gemini", "mistral", "llama", "qwen-coder", "deepseek"}
BUSY: set[str] = set()   # models whose turn "fails" (409/5xx) — set by tests


@app.get("/free/chat/completions")
async def free_info():
    return {"ok": True, "models": sorted(FREE_MODELS), "busy": sorted(BUSY)}


@app.post("/free/chat/completions")
async def free_chat(request: Request):
    body = await request.json()
    CALLS.append({"path": "free", "body": body})
    model = body.get("model", "")
    if model not in FREE_MODELS:
        return JSONResponse({"error": {"message": f"unknown model {model}"}}, status_code=400)
    if model in BUSY:
        return JSONResponse({"error": {"message": "model overloaded"}}, status_code=503)

    content = body.get("messages", [{}])[-1].get("content")
    if isinstance(content, list):
        text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
    else:
        text = content or ""
    system = next((m["content"] for m in body.get("messages", []) if m["role"] == "system"), "")
    reply = (
        f"Free model **{model}** here. You said: {text[:100]}"
        + (" With live web results." if "Web search results for" in system else "")
    )

    if not body.get("stream"):
        return {
            "id": "free-1",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
        }

    async def gen():
        for i in range(0, len(reply), 10):
            chunk = {"choices": [{"index": 0, "delta": {"content": reply[i:i + 10]}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
        yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 12}})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/models")
async def models():
    return {"data": [{"id": "mock-gpt"}, {"id": "mock-vision"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    CALLS.append({"path": "chat", "body": body})
    messages = body.get("messages", [])
    last = messages[-1] if messages else {"content": ""}
    content = last.get("content")
    saw_image = False
    if isinstance(content, list):
        saw_image = any(part.get("type") == "image_url" for part in content)
        text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
    else:
        text = content or ""

    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    used_search = "Web search results for" in system

    reply_parts = [
        f"Mock answer to: **{text[:120]}**",
        "",
        "Here is a code block:",
        "```python",
        "print('hello from Bats')",
        "```",
        "",
        "| a | b |",
        "| --- | --- |",
        "| 1 | 2 |",
    ]
    if saw_image:
        reply_parts.insert(0, "_I can see your image._")
    if used_search:
        reply_parts.insert(0, "_Grounded with live web results._")
        reply_parts.append("Source: [1]")
    if "TITLE" in text or "title for this conversation" in text:
        reply_parts = ["Bats Mock Chat"]
    reply = "\n".join(reply_parts)

    if not body.get("stream"):
        return JSONResponse(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": body.get("model", "mock"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
            }
        )

    async def gen():
        chunks = [reply[i : i + 12] for i in range(0, len(reply), 12)]
        first = {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
        yield f"data: {json.dumps(first)}\n\n"
        for chunk in chunks:
            payload = {
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]
            }
            yield f"data: {json.dumps(payload)}\n\n"
        final = {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
        }
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/images/generations")
async def images(request: Request):
    body = await request.json()
    CALLS.append({"path": "images", "body": body})
    return {
        "created": 1,
        "data": [
            {
                "b64_json": base64.b64encode(png_bytes()).decode(),
                "revised_prompt": body.get("prompt", ""),
            }
        ],
    }


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
