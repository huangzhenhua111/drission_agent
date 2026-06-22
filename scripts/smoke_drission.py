from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def build_page():
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "DrissionPage is not installed. Run: pip install -r requirements.txt"
        ) from exc

    browser_path = resolve_browser_path()
    user_data_path = Path(
        os.getenv("BROWSER_USER_DATA_PATH", "outputs/browser_profiles/smoke")
    ).resolve()
    user_data_path.mkdir(parents=True, exist_ok=True)
    options = ChromiumOptions()
    headless = os.getenv("BROWSER_HEADLESS", "0").lower() in {"1", "true", "yes", "on"}
    if hasattr(options, "headless"):
        options.headless(headless)
    if hasattr(options, "set_user_data_path"):
        options.set_user_data_path(str(user_data_path))
    if hasattr(options, "auto_port"):
        options.auto_port()
    if hasattr(options, "set_browser_path"):
        options.set_browser_path(str(browser_path))
    return ChromiumPage(options)


def resolve_browser_path() -> Path:
    configured = os.getenv("BROWSER_PATH")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if candidate.exists():
            return candidate
    for executable in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        discovered = shutil.which(executable)
        if discovered:
            return Path(discovered).resolve()
    for candidate in (
        Path.home() / ".local/bin/google-chrome",
        Path.home() / ".local/opt/google-chrome-deb/opt/google/chrome/google-chrome",
    ):
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "Chrome/Chromium was not found. Install google-chrome-stable or set BROWSER_PATH."
    )


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
        screenshot = Path("outputs/smoke_drission.png").resolve()
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.get_screenshot(path=str(screenshot))
        print(f"screenshot: {screenshot}")
        return 0
    except Exception as exc:
        print(f"DrissionPage smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close_page(page)


if __name__ == "__main__":
    raise SystemExit(main())
