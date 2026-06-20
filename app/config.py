from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    openai_base_url: str | None
    browser_type: str
    browser_path: str | None
    browser_user_data_path: Path | None
    browser_debug_port: int
    output_dir: Path


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        openai_api_key=(
            os.getenv("OPENAI_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
        ),
        openai_model=(
            os.getenv("OPENAI_MODEL")
            or os.getenv("DASHSCOPE_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or "gpt-4.1-mini"
        ),
        openai_base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("BASE_URL"),
        browser_type=os.getenv("BROWSER_TYPE", "edge"),
        browser_path=os.getenv("BROWSER_PATH"),
        browser_user_data_path=(
            Path(os.environ["BROWSER_USER_DATA_PATH"])
            if os.getenv("BROWSER_USER_DATA_PATH")
            else None
        ),
        browser_debug_port=int(os.getenv("BROWSER_DEBUG_PORT", "19222")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "outputs")),
    )
