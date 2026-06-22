from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_settings, parse_bool


def test_linux_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "BROWSER_TYPE",
        "BROWSER_PATH",
        "BROWSER_USER_DATA_PATH",
        "BROWSER_HEADLESS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.browser_type == "chrome"
    assert settings.browser_headless is False
    assert settings.browser_user_data_path is None


def test_browser_profile_expands_and_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSER_USER_DATA_PATH", "~/drission-profile")
    monkeypatch.setenv("BROWSER_HEADLESS", "1")

    settings = load_settings()

    assert settings.browser_user_data_path == (Path.home() / "drission-profile").resolve()
    assert settings.browser_headless is True


def test_text_and_vision_llm_settings_are_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_LLM_API_KEY", "text-key")
    monkeypatch.setenv("TEXT_LLM_MODEL", "deepseek-text")
    monkeypatch.setenv("TEXT_LLM_BASE_URL", "https://text.example/v1")
    monkeypatch.setenv("VISION_LLM_API_KEY", "vision-key")
    monkeypatch.setenv("VISION_LLM_MODEL", "qwen-vision")
    monkeypatch.setenv("VISION_LLM_BASE_URL", "https://vision.example/v1")

    settings = load_settings()

    assert settings.openai_api_key == "text-key"
    assert settings.openai_model == "deepseek-text"
    assert settings.openai_base_url == "https://text.example/v1"
    assert settings.vision_llm_api_key == "vision-key"
    assert settings.vision_llm_model == "qwen-vision"
    assert settings.vision_llm_base_url == "https://vision.example/v1"


@pytest.mark.parametrize("value", ["maybe", "2", "enabled"])
def test_invalid_boolean_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        parse_bool(value)
