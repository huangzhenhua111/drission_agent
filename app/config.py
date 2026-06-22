from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    # Existing openai_* names are retained as the text-LLM boundary for
    # compatibility with the rest of the application.
    openai_api_key: str | None
    openai_model: str
    openai_base_url: str | None
    browser_type: str
    browser_path: str | None
    browser_user_data_path: Path | None
    browser_debug_port: int
    browser_headless: bool
    output_dir: Path
    vision_llm_api_key: str | None = None
    vision_llm_model: str | None = None
    vision_llm_base_url: str | None = None


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        openai_api_key=(
            os.getenv("TEXT_LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
        ),
        openai_model=(
            os.getenv("TEXT_LLM_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("DASHSCOPE_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or "gpt-4.1-mini"
        ),
        openai_base_url=(
            os.getenv("TEXT_LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("BASE_URL")
        ),
        browser_type=os.getenv("BROWSER_TYPE", "chrome"),
        browser_path=os.getenv("BROWSER_PATH"),
        browser_user_data_path=(
            Path(os.environ["BROWSER_USER_DATA_PATH"]).expanduser().resolve()
            if os.getenv("BROWSER_USER_DATA_PATH")
            else None
        ),
        browser_debug_port=int(os.getenv("BROWSER_DEBUG_PORT", "19222")),
        browser_headless=parse_bool(os.getenv("BROWSER_HEADLESS")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "outputs")).expanduser(),
        vision_llm_api_key=os.getenv("VISION_LLM_API_KEY"),
        vision_llm_model=os.getenv("VISION_LLM_MODEL"),
        vision_llm_base_url=os.getenv("VISION_LLM_BASE_URL"),
    )
