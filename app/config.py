"""Configuration for Bats AI.

Everything is driven by environment variables so the app can be deployed to
Render (or any container host) without editing code.  A ``.env`` file is read
on start-up when present (real environment variables always win).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# .env loading (tiny, dependency free)
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path | None = None) -> None:
    path = path or BASE_DIR / ".env"
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "y"}


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name) or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name) or default)
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# Model / provider catalogue
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    vision: bool = False


@dataclass(frozen=True)
class ProviderSpec:
    """A chat provider definition (env driven)."""

    id: str
    label: str
    kind: str  # "openai_compat" | "anthropic"
    env_key: str  # name of the env var holding the API key
    base_url: str
    models: tuple[ModelInfo, ...]
    requires_key: bool = True
    docs_url: str = ""
    note: str = ""


def _m(model_id: str, label: str, vision: bool = False) -> ModelInfo:
    return ModelInfo(id=model_id, label=label, vision=vision)


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openai",
        label="OpenAI",
        kind="openai_compat",
        env_key="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
        docs_url="https://platform.openai.com/api-keys",
        models=(
            _m("gpt-4o-mini", "GPT-4o mini", True),
            _m("gpt-4o", "GPT-4o", True),
            _m("gpt-4.1-mini", "GPT-4.1 mini", True),
            _m("gpt-4.1", "GPT-4.1", True),
            _m("o4-mini", "o4-mini", True),
        ),
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic",
        kind="anthropic",
        env_key="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com/v1",
        docs_url="https://console.anthropic.com/settings/keys",
        models=(
            _m("claude-sonnet-4-5", "Claude Sonnet 4.5", True),
            _m("claude-3-7-sonnet-latest", "Claude 3.7 Sonnet", True),
            _m("claude-3-5-haiku-latest", "Claude 3.5 Haiku", True),
        ),
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        kind="openai_compat",
        env_key="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        docs_url="https://aistudio.google.com/apikey",
        models=(
            _m("gemini-2.5-flash", "Gemini 2.5 Flash", True),
            _m("gemini-2.5-pro", "Gemini 2.5 Pro", True),
            _m("gemini-2.0-flash", "Gemini 2.0 Flash", True),
        ),
    ),
    ProviderSpec(
        id="groq",
        label="Groq",
        kind="openai_compat",
        env_key="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        docs_url="https://console.groq.com/keys",
        models=(
            _m("llama-3.3-70b-versatile", "Llama 3.3 70B (Groq)", False),
            _m("llama-3.2-11b-vision-preview", "Llama 3.2 11B Vision", True),
            _m("openai/gpt-oss-120b", "GPT-OSS 120B (Groq)", False),
        ),
    ),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        kind="openai_compat",
        env_key="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        docs_url="https://openrouter.ai/keys",
        note="One key, hundreds of models.",
        models=(
            _m("openrouter/auto", "OpenRouter Auto"),
            _m("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5", True),
            _m("openai/gpt-4o-mini", "GPT-4o mini", True),
            _m("google/gemini-2.5-flash", "Gemini 2.5 Flash", True),
            _m("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B"),
        ),
    ),
    ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        kind="openai_compat",
        env_key="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        docs_url="https://platform.deepseek.com/api_keys",
        models=(
            _m("deepseek-chat", "DeepSeek Chat"),
            _m("deepseek-reasoner", "DeepSeek Reasoner"),
        ),
    ),
    ProviderSpec(
        id="mistral",
        label="Mistral",
        kind="openai_compat",
        env_key="MISTRAL_API_KEY",
        base_url="https://api.mistral.ai/v1",
        docs_url="https://console.mistral.ai/api-keys",
        models=(
            _m("mistral-large-latest", "Mistral Large"),
            _m("mistral-small-latest", "Mistral Small"),
            _m("pixtral-large-latest", "Pixtral Large", True),
        ),
    ),
    ProviderSpec(
        id="xai",
        label="xAI Grok",
        kind="openai_compat",
        env_key="XAI_API_KEY",
        base_url="https://api.x.ai/v1",
        docs_url="https://console.x.ai",
        models=(
            _m("grok-4", "Grok 4", True),
            _m("grok-3-mini", "Grok 3 mini"),
        ),
    ),
    ProviderSpec(
        id="together",
        label="Together AI",
        kind="openai_compat",
        env_key="TOGETHER_API_KEY",
        base_url="https://api.together.xyz/v1",
        docs_url="https://api.together.ai/settings/api-keys",
        models=(
            _m("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Llama 3.3 70B"),
            _m("Qwen/Qwen2.5-72B-Instruct-Turbo", "Qwen 2.5 72B"),
        ),
    ),
    ProviderSpec(
        id="ollama",
        label="Ollama (self hosted)",
        kind="openai_compat",
        env_key="OLLAMA_API_KEY",
        base_url="http://127.0.0.1:11434/v1",
        requires_key=False,
        docs_url="https://ollama.com",
        note="Set OLLAMA_BASE_URL if Ollama runs elsewhere.",
        models=(
            _m("llama3.2", "Llama 3.2"),
            _m("qwen2.5", "Qwen 2.5"),
        ),
    ),
    ProviderSpec(
        id="custom",
        label="Custom (OpenAI compatible)",
        kind="openai_compat",
        env_key="CUSTOM_API_KEY",
        base_url="",
        docs_url="",
        note="Point CUSTOM_BASE_URL at any OpenAI-compatible server.",
        models=(_m("", "Custom model"),),
    ),
)


# Image generation providers ------------------------------------------------- #
@dataclass(frozen=True)
class ImageProviderSpec:
    id: str
    label: str
    kind: str  # "openai_images" | "pollinations"
    env_key: str
    base_url: str
    models: tuple[str, ...]
    requires_key: bool = True


IMAGE_PROVIDER_SPECS: tuple[ImageProviderSpec, ...] = (
    ImageProviderSpec(
        id="openai",
        label="OpenAI Images",
        kind="openai_images",
        env_key="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
        models=("gpt-image-1", "dall-e-3"),
    ),
    ImageProviderSpec(
        id="openai_images_custom",
        label="Custom image API",
        kind="openai_images",
        env_key="IMAGE_API_KEY",
        base_url="",
        models=("",),
    ),
    ImageProviderSpec(
        id="demo",
        label="Bats Demo images (no key)",
        kind="demo",
        env_key="",
        base_url="local://demo",
        models=("bats-demo",),
        requires_key=False,
    ),
    ImageProviderSpec(
        id="pollinations",
        label="Pollinations (free, no key)",
        kind="pollinations",
        env_key="",
        base_url="https://image.pollinations.ai",
        models=("flux", "turbo"),
        requires_key=False,
    ),
)


# --------------------------------------------------------------------------- #
# Runtime settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    app_name: str = "Bats AI"
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    max_upload_mb: int = 20
    max_attachments: int = 6
    history_char_budget: int = 28_000
    request_timeout: float = 90.0
    search_timeout: float = 12.0
    page_fetch_chars: int = 4_500
    search_context_chars: int = 12_000
    max_search_results: int = 6
    pages_to_read: int = 3
    default_temperature: float = 0.7
    default_max_tokens: int = 2_048
    allow_client_keys: bool = True
    search_enabled_default: bool = True
    public_url: str = ""
    extra_cors_origins: list[str] = field(default_factory=list)
    access_code: str = ""
    rate_per_minute: int = 40
    rate_per_day: int = 2000

    # provider selections
    providers: list[dict[str, Any]] = field(default_factory=list)
    available_providers: list[str] = field(default_factory=list)
    image_providers: list[dict[str, Any]] = field(default_factory=list)
    search_providers: list[str] = field(default_factory=list)
    default_provider: str = ""
    default_model: str = ""
    default_image_provider: str = ""

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def image_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bats.db"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.image_dir):
            path.mkdir(parents=True, exist_ok=True)

    def public(self) -> dict[str, Any]:
        """Config payload safe to expose to the browser (no secrets)."""
        return {
            "app_name": self.app_name,
            "providers": self.providers,
            "image_providers": self.image_providers,
            "search_providers": self.search_providers,
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "default_image_provider": self.default_image_provider,
            "limits": {
                "max_upload_mb": self.max_upload_mb,
                "max_attachments": self.max_attachments,
                "history_char_budget": self.history_char_budget,
            },
            "features": {
                "client_keys": self.allow_client_keys,
                "search": bool(self.search_providers),
                "images": bool(self.image_providers),
                "vision": any(p.get("vision") for p in self.providers),
                "streaming": True,
            },
            "needs_setup": not self.available_providers and not self.allow_client_keys,
            "ready": bool(self.available_providers),
            "search_enabled_default": self.search_enabled_default,
            "access_code_required": bool(self.access_code),
            "accepted_image_types": [
                "png",
                "jpg",
                "jpeg",
                "gif",
                "webp",
                "bmp",
                "tif",
                "tiff",
                "ico",
                "heic",
                "heif",
                "avif",
                "svg",
            ],
        }


def _detect_providers() -> list[dict[str, Any]]:
    """Return the full provider catalogue with a ``configured`` flag.

    Unconfigured providers are still listed so a user can paste their own API
    key in the settings panel and use them without redeploying.
    """
    found: list[dict[str, Any]] = []
    for spec in PROVIDER_SPECS:
        key = env(spec.env_key)
        base = env(f"{spec.env_key.replace('_API_KEY', '')}_BASE_URL")
        if spec.id == "openai":
            base = env("OPENAI_BASE_URL") or base
        if spec.id == "custom":
            base = env("CUSTOM_BASE_URL") or base
            base = base or env("OPENAI_BASE_URL")
        if spec.id == "ollama":
            base = env("OLLAMA_BASE_URL") or base
        base = base or spec.base_url
        configured = (bool(key) or not spec.requires_key) and bool(base)
        if spec.id == "custom" and not base:
            configured = False
        if spec.id == "ollama" and not env("OLLAMA_BASE_URL"):
            # a cloud host has no local Ollama — only enable it when pointed at one
            configured = False
        models = [{"id": m.id, "label": m.label, "vision": m.vision} for m in spec.models]
        found.append(
            {
                "id": spec.id,
                "label": spec.label,
                "kind": spec.kind,
                "base_url": base,
                "models": models,
                "requires_key": spec.requires_key,
                "has_key": bool(key),
                "env_var": spec.env_key,
                "configured": configured,
                "docs_url": spec.docs_url,
                "note": spec.note,
                "vision": any(m.vision for m in spec.models),
            }
        )
    # Extra models from env: EXTRA_MODELS='{"provider":"openai","id":"gpt-5","label":"GPT-5","vision":true}'
    raw_extra = env("EXTRA_MODELS")
    if raw_extra:
        try:
            extras = json.loads(raw_extra)
            for extra in extras if isinstance(extras, list) else [extras]:
                pid = extra.get("provider", "openai")
                target = next((p for p in found if p["id"] == pid), None)
                if not target:
                    continue
                target["models"].insert(
                    0,
                    {
                        "id": extra.get("id", ""),
                        "label": extra.get("label") or extra.get("id", ""),
                        "vision": bool(extra.get("vision", False)),
                    },
                )
        except (ValueError, AttributeError):
            pass
    return found


def _detect_image_providers() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for spec in IMAGE_PROVIDER_SPECS:
        key = env(spec.env_key) if spec.env_key else ""
        base = env("IMAGE_BASE_URL") if spec.id == "openai_images_custom" else ""
        if spec.id == "openai":
            base = env("IMAGE_BASE_URL") or spec.base_url
        base = base or spec.base_url
        if spec.requires_key and not key:
            continue
        if spec.id == "openai_images_custom" and not base:
            continue
        models = list(spec.models)
        custom_model = env("IMAGE_MODEL")
        if custom_model and spec.id in {"openai", "openai_images_custom"}:
            models = [custom_model] + [m for m in models if m != custom_model]
        found.append(
            {
                "id": spec.id,
                "label": spec.label,
                "kind": spec.kind,
                "base_url": base,
                "models": models,
                "requires_key": spec.requires_key,
                "has_key": bool(key),
                "env_var": spec.env_key,
            }
        )
    return found


def build_settings() -> Settings:
    load_dotenv()
    settings = Settings()
    settings.app_name = env("APP_NAME", settings.app_name)
    data_dir = env("DATA_DIR")
    settings.data_dir = Path(data_dir) if data_dir else settings.data_dir
    settings.max_upload_mb = env_int("MAX_UPLOAD_MB", settings.max_upload_mb)
    settings.max_attachments = env_int("MAX_ATTACHMENTS", settings.max_attachments)
    settings.history_char_budget = env_int("HISTORY_CHAR_BUDGET", settings.history_char_budget)
    settings.request_timeout = env_float("REQUEST_TIMEOUT", settings.request_timeout)
    settings.search_timeout = env_float("SEARCH_TIMEOUT", settings.search_timeout)
    settings.page_fetch_chars = env_int("PAGE_FETCH_CHARS", settings.page_fetch_chars)
    settings.search_context_chars = env_int("SEARCH_CONTEXT_CHARS", settings.search_context_chars)
    settings.max_search_results = env_int("MAX_SEARCH_RESULTS", settings.max_search_results)
    settings.pages_to_read = env_int("PAGES_TO_READ", settings.pages_to_read)
    settings.default_temperature = env_float("DEFAULT_TEMPERATURE", settings.default_temperature)
    settings.default_max_tokens = env_int("DEFAULT_MAX_TOKENS", settings.default_max_tokens)
    settings.allow_client_keys = env_bool("ALLOW_CLIENT_KEYS", settings.allow_client_keys)
    settings.search_enabled_default = env_bool("WEB_SEARCH_DEFAULT", settings.search_enabled_default)
    settings.public_url = env("PUBLIC_URL")
    settings.access_code = env("ACCESS_CODE")
    settings.rate_per_minute = env_int("RATE_LIMIT_PER_MINUTE", settings.rate_per_minute)
    settings.rate_per_day = env_int("RATE_LIMIT_PER_DAY", settings.rate_per_day)
    settings.extra_cors_origins = [
        o.strip() for o in env("EXTRA_CORS_ORIGINS").split(",") if o.strip()
    ]

    settings.providers = _detect_providers()
    settings.image_providers = _detect_image_providers()
    settings.search_providers = detect_search_providers()

    usable = [p for p in settings.providers if p["configured"]]
    settings.available_providers = [p["id"] for p in usable]
    if usable:
        wanted = env("DEFAULT_PROVIDER")
        default_provider = next((p for p in usable if p["id"] == wanted), usable[0])
        settings.default_provider = default_provider["id"]
        wanted_model = env("DEFAULT_MODEL") or env("OPENAI_MODEL")
        model_ids = [m["id"] for m in default_provider["models"] if m["id"]]
        settings.default_model = (
            wanted_model if wanted_model in model_ids else (model_ids[0] if model_ids else "")
        )

    from . import demo  # local demo provider (no-op unless DEMO_MODE=true)

    demo.register(settings)

    if env_bool("DEMO_MODE", False) and not any(
        p["id"] != "demo" for p in settings.image_providers
    ):
        # no real image key -> demo art, so the UI is fully usable
        settings.image_providers = [
            {"id": "demo", "label": "Bats Demo images (no key)", "kind": "demo",
             "base_url": "local://demo", "models": ["bats-demo"],
             "requires_key": False, "has_key": True, "env_var": ""}
        ] + settings.image_providers

    if settings.image_providers:
        wanted = env("DEFAULT_IMAGE_PROVIDER") or env("IMAGE_PROVIDER")
        chosen = next(
            (p for p in settings.image_providers if p["id"] == wanted),
            settings.image_providers[0],
        )
        settings.default_image_provider = chosen["id"]

    return settings


# --------------------------------------------------------------------------- #
# Web search provider detection
# --------------------------------------------------------------------------- #
SEARCH_PROVIDER_ENV: dict[str, str] = {
    "brave": "BRAVE_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "serper": "SERPER_API_KEY",
    "exa": "EXA_API_KEY",
    "searxng": "SEARXNG_URL",
}


def detect_search_providers() -> list[str]:
    """Return available search back-ends, best first.

    Keyless DuckDuckGo is always available as a last resort, so web search
    works out of the box on a fresh deployment.
    """
    found: list[str] = []
    forced = env("SEARCH_PROVIDER")
    for name, var in SEARCH_PROVIDER_ENV.items():
        if env(var):
            found.append(name)
    if forced:
        found = [forced] + [p for p in found if p != forced]
    # keyless engines, tried in this order (first one with results wins)
    for name in ("duckduckgo", "google_news", "mojeek", "hackernews", "wikipedia"):
        if name not in found:
            found.append(name)
    return found
