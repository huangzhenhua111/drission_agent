from __future__ import annotations

from pathlib import Path
import struct

from app.generation.capture_runner import CaptureRunner
from app.generation.capture_runner import _map_visual_click_to_selector
from app.generation.capture_runner import _should_use_visual_fallback
from app.generation.capture_runner import _visual_fallback_step
from app.generation.planner import ActionPlan, ActionStep
from app.generation.visual_fallback import VisualClick
from app.generation.visual_fallback import VisualFallbackGrounder
from app.generation.visual_fallback import _expected_visible_label
from app.generation.visual_fallback import _visible_labels_equivalent
from app.llm.client import LLMJsonResponse


class FailingGrounder:
    def ground(self, *, step: dict, candidates: list[dict], context: dict) -> dict:
        raise RuntimeError("selector grounding failed")


class NoCandidateGrounder:
    def ground(self, *, step: dict, candidates: list[dict], context: dict) -> dict:
        raise RuntimeError(
            f"No DOM candidate matched target: {step.get('target')}"
        )


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


class ExplodingVisualGrounder:
    def propose_click(self, **kwargs) -> VisualClick:
        raise AssertionError("visual fallback should not run before replan is attempted")


def test_default_visual_grounder_uses_vision_provider() -> None:
    grounder = VisualFallbackGrounder()

    assert grounder.client.provider == "vision"


class NormalizedVisionClient:
    def complete_json_with_image(self, **kwargs) -> LLMJsonResponse:
        return LLMJsonResponse(
            raw_text="{}",
            data={
                "action": "click_at",
                "x": 500,
                "y": 600,
                "coordinate_space": "normalized_1000",
                "visible_label": "Target",
                "reason": "normalized target",
            },
        )


def test_visual_grounder_converts_normalized_qwen_coordinates(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 800, 500)
    )
    click = VisualFallbackGrounder(client=NormalizedVisionClient()).propose_click(
        task="click target",
        failed_step={"type": "click", "target": "Target"},
        current_state={},
        completed_actions=[],
        screenshot_path=screenshot,
        error="DOM miss",
    )

    assert (click.x, click.y) == (400, 300)


class WrongVisibleLabelClient:
    def complete_json_with_image(self, **kwargs) -> LLMJsonResponse:
        return LLMJsonResponse(
            raw_text="{}",
            data={
                "action": "click_at",
                "x": 50,
                "y": 950,
                "coordinate_space": "normalized_1000",
                "visible_label": "Text to speech",
                "reason": "semantically related but wrong control",
            },
        )


def test_visual_grounder_rejects_similar_but_wrong_visible_label(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 800, 500)
    )

    import pytest

    with pytest.raises(ValueError, match="expected 'text'"):
        VisualFallbackGrounder(client=WrongVisibleLabelClient()).propose_click(
            task="add title",
            failed_step={"type": "click", "target": "Text tool"},
            current_state={},
            completed_actions=[],
            screenshot_path=screenshot,
            error="DOM miss",
        )


def test_descriptive_ranked_visual_target_does_not_require_literal_label() -> None:
    assert _expected_visible_label("second image in history library") == ""
    assert _expected_visible_label("时间线上的第二个视频片段") == ""
    assert _expected_visible_label("Text tool") == "text"
    assert _expected_visible_label("Open Sans text style") == "open sans"
    assert _expected_visible_label("Open Sans 文字样式") == "open sans"
    assert _visible_labels_equivalent("black color", "#000000")
    assert _visible_labels_equivalent("black color", "black color swatch")
    assert _visible_labels_equivalent("黑色", "#000")
    assert not _visible_labels_equivalent("Text", "Text to speech")


class QwenMislabeledCoordinateClient:
    provider = "vision"

    def _provider_config(self) -> tuple[str, str, str]:
        return ("key", "qwen3-vl-plus", "https://vision.example/v1")

    def complete_json_with_image(self, **kwargs) -> LLMJsonResponse:
        return LLMJsonResponse(
            raw_text="{}",
            data={
                "action": "click_at",
                "x": 43,
                "y": 476,
                "coordinate_space": "pixels",
                "visible_label": "Text",
                "reason": "Qwen mislabeled normalized coordinates as pixels",
            },
        )


def test_qwen_coordinates_are_normalized_even_when_labeled_pixels(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 780, 493)
    )
    click = VisualFallbackGrounder(client=QwenMislabeledCoordinateClient()).propose_click(
        task="add title",
        failed_step={"type": "click", "target": "Text tool"},
        current_state={},
        completed_actions=[],
        screenshot_path=screenshot,
        error="DOM miss",
    )

    assert (click.x, click.y) == (34, 235)


class ReplanningPlanner:
    def __init__(self) -> None:
        self.replan_calls = 0

    def replan(self, **kwargs) -> ActionPlan:
        self.replan_calls += 1
        return ActionPlan(
            task="replacement plan",
            steps=[ActionStep("wait", target="wait after replan", value="0.1")],
        )


class RepeatingUngroundedPlanner:
    def __init__(self) -> None:
        self.replan_calls = 0

    def replan(self, **kwargs) -> ActionPlan:
        self.replan_calls += 1
        return ActionPlan(
            task="invalid unchanged recovery",
            steps=[
                ActionStep("wait", value="2"),
                ActionStep("click", target="Visible fallback target"),
            ],
        )


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


def test_capture_runner_replans_before_visual_fallback(tmp_path: Path) -> None:
    runtime = VisualRuntime()
    planner = ReplanningPlanner()
    plan = ActionPlan(
        task="click visible fallback target",
        steps=[ActionStep("click", target="Visible fallback target")],
    )

    captured = CaptureRunner(
        runtime=runtime,
        grounder=FailingGrounder(),
        planner=planner,
        visual_grounder=ExplodingVisualGrounder(),
        output_dir=tmp_path,
        max_replans=1,
    ).run(plan)

    assert planner.replan_calls == 1
    assert runtime.clicked_at is None
    assert [action["type"] for action in captured] == ["wait"]


def test_repeated_ungrounded_replan_is_rejected_for_visual_fallback(
    tmp_path: Path,
) -> None:
    runtime = VisualRuntime()
    planner = RepeatingUngroundedPlanner()
    plan = ActionPlan(
        task="click visible fallback target",
        steps=[ActionStep("click", target="Visible fallback target")],
    )

    captured = CaptureRunner(
        runtime=runtime,
        grounder=NoCandidateGrounder(),
        planner=planner,
        visual_grounder=FakeVisualGrounder(),
        output_dir=tmp_path,
        max_replans=1,
    ).run(plan)

    assert planner.replan_calls == 1
    assert runtime.clicked_at == (123, 45)
    assert captured[0]["chosen_selector"] == "visual:123,45"
    rejected = tmp_path / "replans" / "step_00_rejected.json"
    assert rejected.exists()


def test_visible_target_without_dom_candidate_prefers_visual_after_replan() -> None:
    assert _should_use_visual_fallback(
        error=RuntimeError("No DOM candidate matched target: Create Project"),
        step={"type": "click", "target": "Create Project"},
        state={"text_excerpt": "Online Video Editor\nCreate Project\nor drag files here"},
        replan_count=1,
    )


def test_dom_miss_still_replans_before_first_visual_attempt() -> None:
    assert not _should_use_visual_fallback(
        error=RuntimeError("No DOM candidate matched target: Create Project"),
        step={"type": "click", "target": "Create Project"},
        state={"text_excerpt": "Create Project"},
        replan_count=0,
    )


def test_open_color_picker_swatch_dom_miss_prefers_visual_immediately() -> None:
    assert _should_use_visual_fallback(
        error=RuntimeError("No DOM candidate matched target: Black color"),
        step={"type": "click", "target": "Black color"},
        state={"text_excerpt": "Text Open Sans 32 Choose a color #FFFFFF"},
        replan_count=0,
    )


def test_ambiguous_click_uses_visual_after_replan_attempt() -> None:
    class AmbiguousClick(RuntimeError):
        reason = "ambiguous_target"

    assert _should_use_visual_fallback(
        error=AmbiguousClick("Ambiguous DOM target"),
        step={"type": "click", "target": "Add text button"},
        state={"text_excerpt": "Add text"},
        replan_count=1,
    )


def test_visual_fallback_preserves_named_style_from_task_for_generic_add_text() -> None:
    class AmbiguousClick(RuntimeError):
        reason = "ambiguous_target"

    step = _visual_fallback_step(
        "点击 Open Sans 文字样式创建标题",
        {"type": "click", "target": "Add text button"},
        AmbiguousClick("Ambiguous DOM target"),
    )

    assert step["target"] == "Open Sans text style"


def test_visual_click_fallback_never_changes_drag_semantics(tmp_path: Path) -> None:
    runtime = VisualRuntime()
    runner = CaptureRunner(
        runtime=runtime,
        visual_grounder=FakeVisualGrounder(),
        output_dir=tmp_path,
        max_replans=0,
    )

    result = runner._try_visual_fallback(
        task="drag slider",
        step_index=0,
        step={"type": "drag", "target": "slider", "delta_x": 20},
        error=RuntimeError("drag failed"),
        completed_actions=[],
        before_state=runtime.state(),
        started=0,
    )

    assert result is None
    assert runtime.clicked_at is None


def _visual_candidate(
    candidate_id: str,
    text: str,
    rect: dict,
    selectors: list[str],
    *,
    tag: str = "button",
    action_allowed: list[str] | None = None,
    visible: bool = True,
    match_count: int = 1,
    index: int = 0,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "tag": tag,
        "semantic_type": "button" if tag == "button" else "clickable_item",
        "action_allowed": ["click"] if action_allowed is None else action_allowed,
        "text": text,
        "is_visible": visible,
        "rect": rect,
        "selector_candidates": selectors,
        "selector_metadata": [
            {
                "selector": selector,
                "index": index,
                "match_count": match_count,
                "unique": match_count == 1,
            }
            for selector in selectors
        ],
    }


def _changed_states() -> tuple[dict, dict]:
    return (
        {"url": "https://editor.test", "title": "Editor", "text_excerpt": "Text"},
        {"url": "https://editor.test", "title": "Editor", "text_excerpt": "Text Open Sans"},
    )


def test_visual_open_sans_click_backfills_stable_selector() -> None:
    before_state, after_state = _changed_states()
    candidate = _visual_candidate(
        "font-card",
        "Open Sans",
        {"x": 100, "y": 100, "width": 180, "height": 60},
        ["text=Open Sans", "css:[data-font='open-sans']"],
    )

    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Open Sans text style"},
        x=150,
        y=120,
        before_candidates=[candidate],
        after_candidates=[candidate],
        before_state=before_state,
        after_state=after_state,
    )

    assert mapping["trusted"] is True
    assert mapping["candidate_id"] == "font-card"
    assert mapping["selectors"][0] == "text=Open Sans"


def test_visual_confirmed_label_can_backfill_unlabelled_clickable_parent() -> None:
    before_state, after_state = _changed_states()
    candidate = _visual_candidate(
        "font-card",
        "",
        {"x": 90, "y": 110, "width": 280, "height": 48},
        ["css:div.components_side-tools-text-add > button:nth-of-type(1)"],
    )
    candidate["context_text"] = "Add text"
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Open Sans style"},
        x=229,
        y=135,
        visual_label="Open Sans",
        before_candidates=[candidate],
        after_candidates=[candidate],
        before_state=before_state,
        after_state=after_state,
    )
    assert mapping["trusted"] is True
    assert mapping["selectors"] == [
        "css:div.components_side-tools-text-add > button:nth-of-type(1)"
    ]
    assert mapping["semantic_evidence"] == ["vision-confirmed-label:open sans"]


def test_visual_text_tool_never_backfills_text_to_speech_neighbor() -> None:
    before_state, after_state = _changed_states()
    candidate = _visual_candidate(
        "tts",
        "Text to speech",
        {"x": 0, "y": 0, "width": 140, "height": 60},
        ["text=Text to speech"],
    )
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Text tool"},
        x=40,
        y=30,
        before_candidates=[candidate],
        after_candidates=[candidate],
        before_state=before_state,
        after_state=after_state,
    )

    assert mapping["trusted"] is False
    assert mapping["selectors"] == []


def test_visual_canvas_click_without_dom_candidate_keeps_coordinate_only() -> None:
    before_state, after_state = _changed_states()
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "canvas text object"},
        x=400,
        y=300,
        before_candidates=[],
        after_candidates=[],
        before_state=before_state,
        after_state=after_state,
    )
    assert mapping["trusted"] is False
    assert "no visible DOM candidate" in mapping["reason"]


def test_visual_mapping_rejects_click_when_page_state_is_unchanged() -> None:
    state = {"url": "https://editor.test", "title": "Editor", "text_excerpt": "Open Sans"}
    candidate = _visual_candidate(
        "font-card", "Open Sans", {"x": 10, "y": 10, "width": 100, "height": 40}, ["text=Open Sans"]
    )
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Open Sans"},
        x=20,
        y=20,
        before_candidates=[candidate],
        after_candidates=[candidate],
        before_state=state,
        after_state=state,
    )
    assert mapping["trusted"] is False
    assert "no observable state" in mapping["reason"]


def test_visual_mapping_prefers_clickable_parent_over_nested_span() -> None:
    before_state, after_state = _changed_states()
    child = _visual_candidate(
        "child",
        "Open Sans",
        {"x": 20, "y": 20, "width": 60, "height": 20},
        ["css:span.font-name"],
        tag="span",
        action_allowed=[],
    )
    parent = _visual_candidate(
        "parent",
        "Open Sans",
        {"x": 10, "y": 10, "width": 160, "height": 60},
        ["css:button[data-font='open-sans']"],
    )
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Open Sans"},
        x=30,
        y=25,
        before_candidates=[child, parent],
        after_candidates=[child, parent],
        before_state=before_state,
        after_state=after_state,
    )
    assert mapping["trusted"] is True
    assert mapping["candidate_id"] == "parent"


def test_visual_mapping_keeps_index_for_non_unique_selector() -> None:
    before_state, after_state = _changed_states()
    candidate = _visual_candidate(
        "second-font",
        "Open Sans",
        {"x": 100, "y": 100, "width": 100, "height": 40},
        ["text=Open Sans"],
        match_count=2,
        index=1,
    )
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Open Sans"},
        x=120,
        y=120,
        before_candidates=[candidate],
        after_candidates=[candidate],
        before_state=before_state,
        after_state=after_state,
    )
    assert mapping["trusted"] is True
    assert mapping["selector_metadata"][0]["index"] == 1
    assert mapping["selector_metadata"][0]["unique"] is False


def test_visual_mapping_ignores_hidden_overlay_candidate() -> None:
    before_state, after_state = _changed_states()
    hidden = _visual_candidate(
        "hidden",
        "Open Sans",
        {"x": 10, "y": 10, "width": 100, "height": 40},
        ["css:.hidden-font"],
        visible=False,
    )
    visible = _visual_candidate(
        "visible",
        "Open Sans",
        {"x": 10, "y": 10, "width": 100, "height": 40},
        ["css:.visible-font"],
    )
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Open Sans"},
        x=30,
        y=20,
        before_candidates=[hidden, visible],
        after_candidates=[hidden, visible],
        before_state=before_state,
        after_state=after_state,
    )
    assert mapping["candidate_id"] == "visible"


def test_visual_mapping_prefers_smallest_clickable_overlap() -> None:
    before_state, after_state = _changed_states()
    large = _visual_candidate(
        "large",
        "Open Sans",
        {"x": 0, "y": 0, "width": 400, "height": 300},
        ["css:.font-panel"],
    )
    small = _visual_candidate(
        "small",
        "Open Sans",
        {"x": 90, "y": 100, "width": 180, "height": 50},
        ["css:.font-card"],
    )
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Open Sans"},
        x=120,
        y=120,
        before_candidates=[large, small],
        after_candidates=[large, small],
        before_state=before_state,
        after_state=after_state,
    )
    assert mapping["candidate_id"] == "small"


def test_visual_mapping_uses_viewport_rect_after_scroll_or_zoom() -> None:
    before_state, after_state = _changed_states()
    # Runtime snapshots expose getBoundingClientRect(), so the mapper consumes
    # the already transformed viewport rectangle rather than document offsets.
    candidate = _visual_candidate(
        "transformed",
        "Open Sans",
        {"x": 312.5, "y": 84.5, "width": 150.0, "height": 45.0},
        ["css:.zoomed-font-card"],
    )
    mapping = _map_visual_click_to_selector(
        step={"type": "click", "target": "Open Sans"},
        x=350,
        y=100,
        before_candidates=[candidate],
        after_candidates=[candidate],
        before_state=before_state,
        after_state=after_state,
    )
    assert mapping["trusted"] is True
    assert mapping["candidate_id"] == "transformed"
