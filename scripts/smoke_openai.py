from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm.client import OpenAIJsonClient


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()


def main() -> int:
    load_env()
    response = OpenAIJsonClient().complete_json(
        prompt='Return exactly this JSON object: {"status":"ok"}',
        schema_name="ProviderSmoke",
    )
    if response.data.get("status") != "ok":
        print(f"Unexpected provider response: {response.data}", file=sys.stderr)
        return 1
    print("OpenAI-compatible provider smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
