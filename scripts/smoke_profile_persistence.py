from __future__ import annotations

import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PROFILE_SMOKE_HTTP_PORT", "8765"))
DEBUG_PORT = int(os.environ.get("PROFILE_SMOKE_DEBUG_PORT", "19223"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.debug.runner import DebugRunner
from app.generation.script_writer import ScriptWriter
from app.runtime.drission_runtime import DrissionRuntime


OUTPUT_DIR = ROOT / "outputs" / "profile_persistence_smoke"
PROFILE_DIR = OUTPUT_DIR / "profile"


class AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/app"):
            self._send_html(_app_html(self.headers.get("Cookie", "")))
        else:
            self._send_html(LOGIN_HTML)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_html(self, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


LOGIN_HTML = """<!doctype html>
<html>
<head><title>Login Smoke</title></head>
<body>
  <h1>Sign In</h1>
  <button id="login" type="button"
    onclick="document.cookie='auth=1; path=/; max-age=3600'; location.href='/app';">
    Sign In
  </button>
</body>
</html>
"""


def _app_html(cookie: str) -> str:
    if "auth=1" not in cookie:
        return """<!doctype html>
<html>
<head><title>Login Smoke</title></head>
<body>
  <h1>Sign In</h1>
  <a href="/">Sign In</a>
</body>
</html>
"""
    return """<!doctype html>
<html>
<head><title>Dashboard Smoke</title></head>
<body>
  <h1>Logged In Dashboard</h1>
  <button id="dashboard-action" type="button"
    onclick="document.body.setAttribute('data-action-clicked', '1');">
    Dashboard Action
  </button>
</body>
</html>
"""


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    browser_path = os.environ.get("BROWSER_PATH")
    settings = Settings(
        openai_api_key=None,
        openai_model="mock",
        openai_base_url=None,
        browser_type="chrome",
        browser_path=browser_path,
        browser_user_data_path=PROFILE_DIR,
        browser_debug_port=DEBUG_PORT,
        output_dir=OUTPUT_DIR,
    )

    try:
        runtime = DrissionRuntime(settings)
        try:
            runtime.goto(f"http://127.0.0.1:{PORT}/")
            runtime.click(["@id=login", "text=Sign In"], "Sign In")
            runtime.wait(1)
            state = runtime.state()
            if "Logged In Dashboard" not in state.get("text_excerpt", ""):
                raise RuntimeError(f"Login smoke setup failed: {state}")
        finally:
            runtime.close()

        actions = [
            {
                "step_index": 0,
                "type": "goto",
                "target": "Dashboard",
                "url": f"http://127.0.0.1:{PORT}/app",
                "fallback_selectors": [],
            },
            {
                "step_index": 1,
                "type": "click",
                "target": "Dashboard Action",
                "fallback_selectors": ["@id=dashboard-action", "text=Dashboard Action"],
                "selector_metadata": [
                    {
                        "selector": "@id=dashboard-action",
                        "index": 0,
                        "match_count": 1,
                        "unique": True,
                    }
                ],
            },
        ]
        script = ScriptWriter().render(
            actions,
            browser_user_data_path=str(PROFILE_DIR.resolve()),
            browser_debug_port=DEBUG_PORT,
            wait_for_login=False,
        )
        script_path = OUTPUT_DIR / "generated_profile_smoke.py"
        script_path.write_text(script, encoding="utf-8")
        result = DebugRunner().run_script(
            str(script_path),
            env={
                "BROWSER_USER_DATA_PATH": str(PROFILE_DIR.resolve()),
                "BROWSER_DEBUG_PORT": str(DEBUG_PORT),
            },
        )
        (OUTPUT_DIR / "script_run.json").write_text(
            __import__("json").dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"profile_dir={PROFILE_DIR.resolve()}")
        print(f"generated_script_returncode={result['returncode']}")
        if not result["success"]:
            print(result["stdout"])
            print(result["stderr"])
            return 1
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
