from __future__ import annotations

from app.generation.capture_runner import CaptureRunner
from app.generation.capture_runner import _is_security_challenge
from app.generation.capture_runner import _is_auth_blocked
from app.generation.planner import ActionPlan, ActionStep


class FakeRuntime:
    def __init__(self) -> None:
        self.page = object()
        self.url = ""
        self.wait_calls = 0
        self.clicked = False
        self.closed = False

    def goto(self, url: str) -> None:
        self.url = "https://example.test/login"

    def state(self) -> dict:
        title = "Login" if "/login" in self.url else "App"
        return {"url": self.url, "title": title, "html_excerpt": ""}

    def wait(self, seconds: float) -> None:
        self.wait_calls += 1
        if self.wait_calls >= 2:
            self.url = "https://example.test/app/mix"

    def snapshot(self) -> list[dict]:
        return [
            {
                "candidate_id": "e1",
                "dom_index": 1,
                "tag": "button",
                "semantic_type": "button",
                "action_allowed": ["click"],
                "text": "Continue",
                "is_visible": True,
                "rect": {"x": 1, "y": 1, "width": 80, "height": 30},
                "selector_candidates": ["text=Continue"],
                "selector_metadata": [
                    {
                        "selector": "text=Continue",
                        "index": 0,
                        "match_count": 1,
                        "unique": True,
                    }
                ],
            }
        ]

    def click(self, selectors: list[str], target: str, *, selector_indexes: dict[str, int] | None = None) -> str:
        self.clicked = True
        return selectors[0]

    def close(self) -> None:
        self.closed = True


class SignInRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.url = "https://example.test/app"
        self.sign_in_clicked = False

    def state(self) -> dict:
        if self.url.endswith("/app/mix"):
            return {"url": self.url, "title": "App", "text_excerpt": "Ready"}
        if self.sign_in_clicked:
            return {"url": "https://example.test/login", "title": "Login", "html_excerpt": ""}
        return {"url": self.url, "title": "App", "text_excerpt": "Sign In with Google"}

    def snapshot(self) -> list[dict]:
        if not self.sign_in_clicked:
            return [
                {
                    "candidate_id": "signin",
                    "tag": "button",
                    "text": "Sign In",
                    "accessible_name": "Sign In",
                    "selector_candidates": ["text=Sign In"],
                    "selector_metadata": [],
                }
            ]
        return super().snapshot()

    def click(self, selectors: list[str], target: str, *, selector_indexes: dict[str, int] | None = None) -> str:
        if target == "Sign In":
            self.sign_in_clicked = True
            return selectors[0]
        return super().click(selectors, target, selector_indexes=selector_indexes)


def test_wait_for_login_polls_until_auth_state_clears() -> None:
    runtime = FakeRuntime()
    plan = ActionPlan(
        task="open app then continue",
        steps=[
            ActionStep("goto", url="https://example.test/app", target="app"),
            ActionStep("click", target="Continue"),
        ],
    )

    captured = CaptureRunner(
        runtime=runtime,
        wait_for_login=True,
        login_timeout_seconds=10,
    ).run(plan)

    assert runtime.wait_calls >= 2
    assert runtime.clicked is True
    assert [action["type"] for action in captured] == ["goto", "click"]
    assert captured[0]["after_url"] == "https://example.test/app/mix"


def test_auth_flow_clicks_visible_sign_in_before_waiting() -> None:
    runtime = SignInRuntime()
    runner = CaptureRunner(runtime=runtime, wait_for_login=True, login_timeout_seconds=10)

    runtime.wait_calls = 0
    state = runner._ensure_authenticated(0, {"type": "goto"})

    assert runtime.sign_in_clicked is True
    assert state["url"] == "https://example.test/app/mix"


def test_cloudflare_verification_is_classified_as_security_challenge() -> None:
    assert _is_security_challenge(
        {"text_excerpt": "Performing security verification. Verify you are human."}
    )


def test_public_page_with_sign_in_button_is_not_auth_blocked() -> None:
    assert not _is_auth_blocked(
        {
            "url": "https://example.test/app",
            "title": "Public App",
            "text_excerpt": "Video Editor Create Project Sign In Pricing Help",
        }
    )
