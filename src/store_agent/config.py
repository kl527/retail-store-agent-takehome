"""Assignment-level constants and environment configuration."""

import os
from pathlib import Path

# Frozen by the assignment: every date-relative phrase resolves against this.
TODAY = "2026-06-19"
LAST_MONTH_START = "2026-05-01"
LAST_MONTH_END = "2026-05-31"

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_WORKERS_AI_MODEL = "@cf/zai-org/glm-4.7-flash"


class ConfigError(RuntimeError):
    pass


def data_dir() -> Path:
    override = os.getenv("STORE_DATA_DIR")
    if override:
        return Path(override)
    cwd_data = Path.cwd() / "data"
    if cwd_data.is_dir():
        return cwd_data
    return Path(__file__).resolve().parents[2] / "data"


def llm_config() -> dict | None:
    """Resolve LLM endpoint settings from the environment.

    Everything speaks the OpenAI chat-completions wire format, so the agent
    runs on whichever key is available, checked in this order:

      1. LLM_BASE_URL (+ LLM_API_KEY, LLM_MODEL) — any compatible endpoint
      2. OPENAI_API_KEY
      3. ANTHROPIC_API_KEY (Anthropic's OpenAI-compatible endpoint)
      4. CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN (Workers AI)

    LLM_MODEL overrides the per-provider default everywhere.
    Returns None when no credentials are configured.
    """
    model_override = os.getenv("LLM_MODEL")

    base_url = os.getenv("LLM_BASE_URL")
    if base_url:
        if not model_override:
            raise ConfigError("LLM_BASE_URL is set but LLM_MODEL is not — set both.")
        return {
            "provider": "custom",
            "base_url": base_url.rstrip("/"),
            "api_key": os.getenv("LLM_API_KEY", ""),
            "model": model_override,
        }

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        return {
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": openai_key,
            "model": model_override or DEFAULT_OPENAI_MODEL,
        }

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        return {
            "provider": "anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "api_key": anthropic_key,
            "model": model_override or DEFAULT_ANTHROPIC_MODEL,
        }

    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    if account_id and api_token:
        return {
            "provider": "cloudflare-workers-ai",
            "base_url": f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            "api_key": api_token,
            "model": model_override or os.getenv("WORKERS_AI_MODEL") or DEFAULT_WORKERS_AI_MODEL,
        }
    return None
