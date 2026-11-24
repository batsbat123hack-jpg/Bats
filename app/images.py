"""Uploads and image generation.

Upload side: every common image format is accepted (png, jpg/jpeg, gif, webp,
bmp, tif/tiff, ico, heic/heif, avif, svg) — the file is normalised with Pillow
so a vision model always receives something it can read.

Generation side: OpenAI Images (gpt-image-1 / dall-e-3 / custom) and the
keyless Pollinations back-end.  Results are downloaded server side and stored
locally so the browser only ever talks to this app (no CORS surprises, links
that survive deploys with a persistent disk).
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from . import config

try:  # optional but enables iPhone HEIC/HEIF uploads
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover - optional dependency
    HEIF_OK = False


MAX_VISION_SIDE = 1280
PREVIEW_SIDE = 512


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class StoredImage:
    id: str
    name: str
    mime: str
    size: int
    width: int
    height: int
    path: str
    preview_path: str
    format: str
    animated: bool = False

    @property
    def url(self) -> str:
        return f"/api/files/{self.id}"

    @property
    def preview_url(self) -> str:
        return f"/api/files/{self.id}?variant=preview"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("path", None)
        data.pop("preview_path", None)
        data["url"] = self.url
        data["preview_url"] = self.preview_url
        return data


def _is_svg(raw: bytes) -> bool:
    head = raw[:600].lstrip().lower()
    return head.startswith(b"<?xml") and b"<svg" in raw[:2000].lower() or head.startswith(b"<svg")


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ]+", "_", name or "image").strip() or "image"
    return name[:120]


def save_upload(
    settings: config.Settings, raw: bytes, filename: str, content_type: str = ""
) -> StoredImage:
    """Validate + normalise any supported image and persist it."""
    if not raw:
        raise UploadError("Empty file")
    limit = settings.max_upload_mb * 1024 * 1024
    if len(raw) > limit:
        raise UploadError(f"File is larger than {settings.max_upload_mb} MB", status=413)

    settings.ensure_dirs()
    image_id = uuid.uuid4().hex[:16]
    ext = Path(filename).suffix.lower().lstrip(".") or "png"

    if _is_svg(raw):
        return _store_svg(settings, raw, filename, image_id)

    try:
        probe = Image.open(io.BytesIO(raw))
        probe.load()
    except UnidentifiedImageError:
        raise UploadError(
            "That file is not a readable image. Supported: PNG, JPG, GIF, WEBP, BMP, "
            "TIFF, ICO, HEIC, AVIF, SVG."
        )
    except (OSError, ValueError) as exc:
        if ext in {"heic", "heif"} and not HEIF_OK:
            raise UploadError("HEIC support is unavailable on this server; export as JPG.")
        raise UploadError(f"Could not read image: {exc}")

    fmt = (probe.format or ext or "PNG").upper()
    width, height = probe.size
    animated = bool(getattr(probe, "is_animated", False) and getattr(probe, "n_frames", 1) > 1)

    # --- vision copy: RGB, bounded size, JPEG (small tokens, universal) ------ #
    frame = probe
    if animated:
        probe.seek(0)
    frame = ImageOps.exif_transpose(frame)
    vision = frame.convert("RGB")
    if max(vision.size) > MAX_VISION_SIDE:
        vision.thumbnail((MAX_VISION_SIDE, MAX_VISION_SIDE), Image.LANCZOS)
    vision_buf = io.BytesIO()
    vision.save(vision_buf, format="JPEG", quality=86, optimize=True)
    vision_bytes = vision_buf.getvalue()

    # --- preview (grid thumbnail) ------------------------------------------- #
    preview = frame.convert("RGB")
    preview.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.LANCZOS)
    preview_buf = io.BytesIO()
    preview.save(preview_buf, format="JPEG", quality=82, optimize=True)
    preview_bytes = preview_buf.getvalue()

    # --- original (for download / display fidelity) ------------------------- #
    keep_original = raw
    mime = content_type or Image.MIME.get(fmt, "image/png")
    orig_ext = ext if ext else fmt.lower()
    if animated and fmt == "GIF":
        pass  # keep as-is
    elif len(raw) > 8 * 1024 * 1024:  # shrink huge originals to JPEG
        big = frame.convert("RGB")
        if max(big.size) > 2600:
            big.thumbnail((2600, 2600), Image.LANCZOS)
        buf = io.BytesIO()
        big.save(buf, format="JPEG", quality=90, optimize=True)
        keep_original, orig_ext, mime = buf.getvalue(), "jpg", "image/jpeg"

    original_path = settings.upload_dir / f"{image_id}.{orig_ext}"
    original_path.write_bytes(keep_original)
    preview_path = settings.upload_dir / f"{image_id}.preview.jpg"
    preview_path.write_bytes(preview_bytes)
    # vision payload reused by the chat request
    (settings.upload_dir / f"{image_id}.vision.jpg").write_bytes(vision_bytes)

    return StoredImage(
        id=image_id,
        name=_safe_name(filename),
        mime=mime,
        size=len(keep_original),
        width=width,
        height=height,
        path=str(original_path),
        preview_path=str(preview_path),
        format=fmt.lower(),
        animated=animated,
    )


def _store_svg(settings: config.Settings, raw: bytes, filename: str, image_id: str) -> StoredImage:
    text = raw.decode("utf-8", "replace")
    if "<script" in text.lower() or "onload=" in text.lower():
        raise UploadError("That SVG contains scripting and was blocked for safety.")
    settings.ensure_dirs()
    path = settings.upload_dir / f"{image_id}.svg"
    path.write_bytes(raw)
    preview = settings.upload_dir / f"{image_id}.preview.svg"
    preview.write_bytes(raw)
    return StoredImage(
        id=image_id,
        name=_safe_name(filename),
        mime="image/svg+xml",
        size=len(raw),
        width=0,
        height=0,
        path=str(path),
        preview_path=str(preview),
        format="svg",
    )


def vision_data_url(settings: config.Settings, image_id: str) -> str:
    path = settings.upload_dir / f"{image_id}.vision.jpg"
    if not path.is_file():
        alt = settings.upload_dir / f"{image_id}.preview.jpg"
        if alt.is_file():
            path = alt
        else:
            raise UploadError("Image was not found on this server", status=404)
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/jpeg;base64,{data}"


def resolve_file(settings: config.Settings, file_id: str, variant: str = "") -> Path:
    if not re.fullmatch(r"[0-9a-f]{6,32}", file_id or ""):
        raise UploadError("Bad file id", status=404)
    if variant == "preview":
        for suffix in (".preview.jpg", ".preview.svg"):
            candidate = settings.upload_dir / f"{file_id}{suffix}"
            if candidate.is_file():
                return candidate
    for candidate in sorted(settings.upload_dir.glob(f"{file_id}.*")):
        if ".preview." in candidate.name or ".vision." in candidate.name:
            continue
        return candidate
    raise UploadError("File not found", status=404)


def media_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".ico": "image/x-icon",
        ".heic": "image/heic",
        ".heif": "image/heif",
        ".avif": "image/avif",
        ".svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #
IMAGE_SIZES = {"1024x1024": (1024, 1024), "1024x1536": (1024, 1536), "1536x1024": (1536, 1024)}


@dataclass
class GeneratedImage:
    id: str
    prompt: str
    model: str
    provider: str
    width: int
    height: int
    url: str
    revised_prompt: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _save_generated(settings: config.Settings, raw: bytes, prompt: str, model: str, provider: str) -> GeneratedImage:
    settings.ensure_dirs()
    image_id = uuid.uuid4().hex[:16]
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        path = settings.image_dir / f"{image_id}.png"
        img.save(path, format="PNG")
        width, height = img.size
    except (UnidentifiedImageError, OSError):
        path = settings.image_dir / f"{image_id}.png"
        path.write_bytes(raw)
        width, height = 0, 0
    return GeneratedImage(
        id=image_id,
        prompt=prompt,
        model=model,
        provider=provider,
        width=width,
        height=height,
        url=f"/api/generated/{image_id}",
    )


def resolve_generated(settings: config.Settings, image_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{6,32}", image_id or ""):
        raise UploadError("Bad image id", status=404)
    path = settings.image_dir / f"{image_id}.png"
    if not path.is_file():
        raise UploadError("Image not found", status=404)
    return path


async def generate_image(
    settings: config.Settings,
    prompt: str,
    provider_id: str = "",
    model: str = "",
    size: str = "1024x1024",
    client_key: str = "",
    client_base_url: str = "",
    style: str = "",
) -> GeneratedImage:
    prompt = (prompt or "").strip()
    if not prompt:
        raise UploadError("Describe the image you want first.")
    providers = settings.image_providers
    if not providers:
        raise UploadError(
            "Image generation is not configured. Free mode ships with Pollinations and "
            "Bats Demo art — set IMAGE_PROVIDER=pollinations (or demo) on the server."
        )
    spec = next((p for p in providers if p["id"] == provider_id), providers[0])
    kind = spec["kind"]
    key = client_key.strip() or os.environ.get(
        "IMAGE_API_KEY" if spec["id"] == "openai_images_custom" else "OPENAI_API_KEY", ""
    ).strip()
    if spec["id"] == "pollinations":
        kind = "pollinations"
    if spec["requires_key"] and not key:
        raise UploadError(
            "No image API key on the server. Add OPENAI_API_KEY, or paste a key in "
            "Settings → API keys.",
            status=400,
        )
    chosen_model = model or (spec["models"][0] if spec["models"] else "")
    width, height = IMAGE_SIZES.get(size, (1024, 1024))

    if kind == "demo":
        from . import demo

        raw = demo.generate(prompt, width, height)
        return _save_generated(settings, raw, prompt, chosen_model or "bats-demo", "demo")
    if kind == "pollinations":
        return await _generate_pollinations(settings, prompt, chosen_model, width, height)
    return await _generate_openai(
        settings,
        prompt,
        chosen_model or "gpt-image-1",
        size if size in IMAGE_SIZES else "1024x1024",
        key,
        client_base_url or os.environ.get("IMAGE_BASE_URL", "") or spec["base_url"],
        spec["id"],
        style,
    )


async def _generate_pollinations(
    settings: config.Settings, prompt: str, model: str, width: int, height: int
) -> GeneratedImage:
    """Free, keyless image generation with model and parameter fallback."""
    from urllib.parse import quote

    base = (os.environ.get("POLLINATIONS_BASE_URL") or "https://image.pollinations.ai").rstrip("/")
    token = (os.environ.get("POLLINATIONS_TOKEN") or "").strip()
    candidates: list[str] = []
    for name in (model, "flux", "turbo", "kontext"):
        if name and name not in candidates:
            candidates.append(name)

    timeout = httpx.Timeout(connect=20.0, read=180.0, write=30.0, pool=20.0)
    headers = {"User-Agent": "BatsAI/1.0 (+https://www.bats-ai.onrender.com)"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for candidate in candidates:
            url = f"{base}/prompt/{quote(prompt[:900])}"
            params = {
                "width": width,
                "height": height,
                "model": candidate,
                "nologo": "true",
                "seed": int.from_bytes(os.urandom(2), "big"),
            }
            if token:
                params["token"] = token
            try:
                r = await client.get(url, params=params)
            except httpx.HTTPError as exc:
                errors.append(f"{candidate}: {exc.__class__.__name__}")
                continue
            if r.status_code < 400 and r.content and r.headers.get("content-type", "").startswith("image"):
                return _save_generated(settings, r.content, prompt, candidate, "pollinations")
            errors.append(f"{candidate}: HTTP {r.status_code}")
            if r.status_code in (401, 402, 429):
                # anonymous tier limited: retry the same prompt with a lower load
                retry_params = dict(params, width=min(width, 1024), height=min(height, 1024))
                try:
                    r2 = await client.get(url, params=retry_params)
                    if r2.status_code < 400 and r2.content:
                        return _save_generated(settings, r2.content, prompt, candidate, "pollinations")
                except httpx.HTTPError:
                    pass
    raise UploadError(
        "The free image service did not return an image ("
        + "; ".join(errors[:3])
        + "). It is busy or rate limiting this server — try again, use Demo art in Settings, "
        "or add OPENAI_API_KEY for gpt-image-1.",
        status=502,
    )


async def _generate_openai(
    settings: config.Settings,
    prompt: str,
    model: str,
    size: str,
    key: str,
    base_url: str,
    provider_id: str,
    style: str,
) -> GeneratedImage:
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1, "size": size}
    if model.startswith("dall-e"):
        payload["response_format"] = "b64_json"
        if style in {"vivid", "natural"}:
            payload["style"] = style
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            r = await client.post(f"{base}/images/generations", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise UploadError(f"Could not reach the image provider ({exc.__class__.__name__}).", status=502)
        if r.status_code >= 400:
            detail = r.text[:300]
            raise UploadError(f"Image provider error {r.status_code}: {detail}")
        data = r.json()
        items = data.get("data") or []
        if not items:
            raise UploadError("The image provider returned no image.")
        item = items[0]
        revised = item.get("revised_prompt", "")
        if item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            try:
                download = await client.get(item["url"])
                download.raise_for_status()
                raw = download.content
            except httpx.HTTPError:
                raise UploadError("Could not download the generated image.", status=502)
        else:
            raise UploadError("Unsupported image response from provider.")

    image = _save_generated(settings, raw, prompt, model, provider_id)
    image.revised_prompt = revised
    return image
