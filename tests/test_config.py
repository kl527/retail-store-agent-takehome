"""Provider resolution: the agent runs on whichever key is available."""

import pytest

from store_agent.config import ConfigError, llm_config

ALL_VARS = [
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "WORKERS_AI_MODEL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)


def test_no_credentials_returns_none():
    assert llm_config() is None


def test_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = llm_config()
    assert settings["provider"] == "openai"
    assert settings["base_url"] == "https://api.openai.com/v1"
    assert settings["model"] == "gpt-5.4-mini"


def test_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = llm_config()
    assert settings["provider"] == "anthropic"
    assert settings["model"] == "claude-sonnet-5"


def test_cloudflare_pair(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    settings = llm_config()
    assert settings["provider"] == "cloudflare-workers-ai"
    assert "acct/ai/v1" in settings["base_url"]
    assert settings["model"] == "@cf/zai-org/glm-4.7-flash"


def test_precedence_openai_over_anthropic_over_cloudflare(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    assert llm_config()["provider"] == "anthropic"
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    assert llm_config()["provider"] == "openai"


def test_explicit_base_url_wins_and_requires_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1/")
    with pytest.raises(ConfigError):
        llm_config()
    monkeypatch.setenv("LLM_MODEL", "my-model")
    settings = llm_config()
    assert settings["provider"] == "custom"
    assert settings["base_url"] == "https://example.com/v1"  # trailing slash stripped
    assert settings["model"] == "my-model"


def test_llm_model_overrides_provider_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.4")
    assert llm_config()["model"] == "gpt-5.4"
