from __future__ import annotations

from app.generation.capture_runner import CaptureRunner
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
