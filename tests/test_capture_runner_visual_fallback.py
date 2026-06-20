from __future__ import annotations

from pathlib import Path

from app.generation.capture_runner import CaptureRunner
from app.generation.planner import ActionPlan, ActionStep
from app.generation.visual_fallback import VisualClick


class FailingGrounder:
    def ground(self, *, step: dict, candidates: list[dict], context: dict) -> dict:
        raise RuntimeError("selector grounding failed")


class FakeVisualGrounder:
    def propose_click(
        self,
        *,
        task: str,
        failed_step: dict,
        current_state: dict,
        completed_actions: list[dict],
        screenshot_path: str | Path,
        error: str,
    ) -> VisualClick:
        assert Path(screenshot_path).exists()
        return VisualClick(x=123, y=45, reason="click visible fallback target")


class VisualRuntime:
    page = object()

    def __init__(self) -> None:
        self.clicked_at: tuple[int, int] | None = None
        self.closed = False

    def state(self) -> dict:
        return {
            "url": "https://example.test/app",
            "title": "App",
            "text_excerpt": "Visible fallback target",
            "html_excerpt": "",
        }

    def snapshot(self) -> list[dict]:
        return [
            {
                "candidate_id": "wrong",
                "tag": "button",
                "semantic_type": "button",
                "action_allowed": ["click"],
                "text": "Wrong",
                "is_visible": True,
                "rect": {"x": 1, "y": 1, "width": 40, "height": 20},
                "selector_candidates": ["text=Wrong"],
            }
        ]

    def wait(self, seconds: float) -> None:
        return None

    def screenshot(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake png bytes")

    def click_at(self, x: int, y: int, target: str) -> str:
        self.clicked_at = (x, y)
        return f"visual:{x},{y}"

    def close(self) -> None:
        self.closed = True


def test_capture_runner_uses_visual_fallback_after_grounding_failure(tmp_path: Path) -> None:
    runtime = VisualRuntime()
    plan = ActionPlan(
        task="click visible fallback target",
        steps=[ActionStep("click", target="Visible fallback target")],
    )

    captured = CaptureRunner(
        runtime=runtime,
        grounder=FailingGrounder(),
        visual_grounder=FakeVisualGrounder(),
        output_dir=tmp_path,
        max_replans=0,
    ).run(plan)

    assert runtime.clicked_at == (123, 45)
    assert captured[0]["chosen_selector"] == "visual:123,45"
    assert captured[0]["visual_position"] == {"x": 123, "y": 45}
    assert (tmp_path / "visual_fallbacks" / "step_00.json").exists()
