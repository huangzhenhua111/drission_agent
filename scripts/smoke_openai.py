from __future__ import annotations

import os
import sys


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()


def main() -> int:
    load_env()
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    if not api_key:
        print("OPENAI_API_KEY is not set. Create .env from .env.example first.", file=sys.stderr)
        return 2

    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise SystemExit("openai is not installed. Run: pip install -r requirements.txt") from exc

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input="Reply with exactly: openai smoke ok",
    )
    print(getattr(response, "output_text", str(response)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

