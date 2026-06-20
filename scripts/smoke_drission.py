from __future__ import annotations

import os
import sys
from pathlib import Path


def build_page():
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "DrissionPage is not installed. Run: pip install -r requirements.txt"
        ) from exc

    browser_path = os.getenv("BROWSER_PATH")
    user_data_path = Path(
        os.getenv("BROWSER_USER_DATA_PATH", "outputs/browser_profiles/smoke")
    ).resolve()
    user_data_path.mkdir(parents=True, exist_ok=True)
    options = ChromiumOptions()
    if hasattr(options, "set_user_data_path"):
        options.set_user_data_path(str(user_data_path))
    if hasattr(options, "auto_port"):
        options.auto_port()
    if browser_path:
        if hasattr(options, "set_browser_path"):
            options.set_browser_path(browser_path)
        return ChromiumPage(options)

    return ChromiumPage(options)


def close_page(page) -> None:
    if hasattr(page, "quit"):
        page.quit()
    elif hasattr(page, "close"):
        page.close()


def main() -> int:
    page = build_page()
    try:
        page.get("https://example.com")
        page.wait.doc_loaded(timeout=10)
        print(page.title)
        return 0
    except Exception as exc:
        print(f"DrissionPage smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_page(page)


if __name__ == "__main__":
    raise SystemExit(main())
