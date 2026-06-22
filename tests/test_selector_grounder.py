from __future__ import annotations

from pathlib import Path

import pytest

from app.generation.dom_snapshot import extract_candidates_from_html
from app.generation.selector_grounder import AmbiguousTargetError
from app.generation.selector_grounder import SelectorGrounder


ROOT = Path(__file__).resolve().parents[1]


def test_grounder_selects_search_input_and_first_result() -> None:
    html = (ROOT / "examples/local_search/site/index.html").read_text(encoding="utf-8")
    candidates = [candidate.to_dict() for candidate in extract_candidates_from_html(html)]
    grounder = SelectorGrounder()

    input_grounding = grounder.ground(
        step={"type": "input", "target": "Search keyword 搜索输入框", "value": "alpha"},
        candidates=candidates,
        context={},
    )
    result_grounding = grounder.ground(
        step={"type": "click", "target": "第一条搜索结果 Alpha detail"},
        candidates=candidates,
        context={},
    )

    assert input_grounding["candidate"]["id"] == "search-input"
    assert result_grounding["candidate"]["id"] == "first-result"


def test_grounder_selects_selenium_web_form_controls() -> None:
    candidates = [
        {
            "candidate_id": "e1",
            "tag": "input",
            "id": "my-text-id",
            "name": "my-text",
            "type": "text",
            "text": None,
            "is_visible": True,
            "data_attrs": {},
            "selector_candidates": ["@id=my-text-id"],
        },
        {
            "candidate_id": "e2",
            "tag": "select",
            "id": None,
            "name": "my-select",
            "type": None,
            "text": "One Two Three",
            "is_visible": True,
            "data_attrs": {},
            "selector_candidates": ["@name=my-select"],
        },
        {
            "candidate_id": "e3",
            "tag": "button",
            "id": None,
            "name": None,
            "type": "submit",
            "text": "Submit",
            "is_visible": True,
            "data_attrs": {},
            "selector_candidates": ["css:button[type='submit']", "text=Submit"],
        },
    ]
    grounder = SelectorGrounder()

    assert grounder.ground(
        step={"type": "input", "target": "Text input 文本框", "value": "hello"},
        candidates=candidates,
        context={},
    )["candidate_id"] == "e1"
    assert grounder.ground(
        step={"type": "select", "target": "Dropdown select 下拉框", "value": "Two"},
        candidates=candidates,
        context={},
    )["candidate_id"] == "e2"
    assert grounder.ground(
        step={"type": "click", "target": "Submit 按钮"},
        candidates=candidates,
        context={},
    )["candidate_id"] == "e3"


def test_grounder_uses_upload_semantic_context_for_duplicate_file_inputs() -> None:
    candidates = [
        {
            "candidate_id": "motion_file",
            "tag": "input",
            "type": "file",
            "semantic_type": "file_input",
            "action_allowed": ["upload"],
            "is_visible": False,
            "upload_label": "Add Motion",
            "upload_kind": "video",
            "nearest_card_text": "Add Motion Drag and drop a video here or click to upload Library",
            "selector_candidates": ["css:input[type='file']"],
        },
        {
            "candidate_id": "image_file",
            "tag": "input",
            "type": "file",
            "semantic_type": "file_input",
            "action_allowed": ["upload"],
            "is_visible": False,
            "upload_label": "Add Image",
            "upload_kind": "image",
            "nearest_card_text": "Add Image Drag and drop an image here or click to upload Library",
            "selector_candidates": ["css:input[type='file']"],
        },
    ]
    grounder = SelectorGrounder()

    motion = grounder.ground(
        step={"type": "upload", "target": "Upload motion video", "path": "motion.mp4"},
        candidates=candidates,
        context={},
    )
    image = grounder.ground(
        step={"type": "upload", "target": "Upload image", "path": "image.png"},
        candidates=candidates,
        context={},
    )

    assert motion["candidate_id"] == "motion_file"
    assert image["candidate_id"] == "image_file"


def test_grounder_prefers_exact_click_label_over_context_match() -> None:
    candidates = [
        {
            "candidate_id": "library",
            "tag": "button",
            "semantic_type": "library_button",
            "action_allowed": ["click"],
            "text": "Library",
            "context_text": "Library | Add Image Drag and drop an image here",
            "is_visible": True,
            "selector_candidates": ["text=Library"],
        },
        {
            "candidate_id": "history",
            "tag": "button",
            "semantic_type": "button",
            "action_allowed": ["click"],
            "text": "History",
            "is_visible": True,
            "selector_candidates": ["text=History"],
        },
    ]

    grounding = SelectorGrounder().ground(
        step={"type": "click", "target": "History"},
        candidates=candidates,
        context={},
    )

    assert grounding["candidate_id"] == "history"


def test_grounder_prefers_second_ranked_clickable_item_over_library_button() -> None:
    grounding = SelectorGrounder().ground(
        step={"type": "click", "target": "second image in history library"},
        candidates=[
            {
                "candidate_id": "library",
                "tag": "button",
                "type": "button",
                "semantic_type": "library_button",
                "action_allowed": ["click"],
                "text": "Library",
                "context_text": "Add Image Drag and drop an image here Library",
                "is_visible": True,
                "selector_candidates": ["text=Library"],
            },
            {
                "candidate_id": "item_1",
                "tag": "div",
                "role": "button",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "accessible_name": "Open item preview",
                "context_text": "History item",
                "result_rank": 1,
                "is_visible": True,
                "selector_candidates": ["@aria-label=Open item preview"],
            },
            {
                "candidate_id": "item_2",
                "tag": "div",
                "role": "button",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "accessible_name": "Open item preview",
                "context_text": "History item",
                "result_rank": 2,
                "is_visible": True,
                "selector_candidates": ["@aria-label=Open item preview"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "item_2"


def test_grounder_raises_ambiguous_target_when_candidates_are_too_close() -> None:
    with pytest.raises(AmbiguousTargetError) as exc_info:
        SelectorGrounder().ground(
            step={"type": "click", "target": "Open"},
            candidates=[
                {
                    "candidate_id": "open_a",
                    "tag": "button",
                    "semantic_type": "button",
                    "action_allowed": ["click"],
                    "text": "Open",
                    "is_visible": True,
                    "selector_candidates": ["css:button[data-id='a']"],
                },
                {
                    "candidate_id": "open_b",
                    "tag": "button",
                    "semantic_type": "button",
                    "action_allowed": ["click"],
                    "text": "Open",
                    "is_visible": True,
                    "selector_candidates": ["css:button[data-id='b']"],
                },
            ],
            context={},
        )

    assert exc_info.value.reason == "ambiguous_target"
    assert [candidate["candidate_id"] for candidate in exc_info.value.candidates] == [
        "open_a",
        "open_b",
    ]


def test_grounder_rejects_unrelated_click_buttons_without_target_evidence() -> None:
    with pytest.raises(RuntimeError, match="No DOM candidate matched target"):
        SelectorGrounder().ground(
            step={"type": "click", "target": "Apply trim"},
            candidates=[
                {
                    "candidate_id": "language",
                    "tag": "button",
                    "semantic_type": "button",
                    "action_allowed": ["click"],
                    "text": "EN",
                    "accessible_name": "EN",
                    "context_text": "Trim Video Online Video Editor",
                    "ancestor_text": "Video Audio PDF Converters Trim Video",
                    "is_visible": True,
                    "selector_candidates": ["@id=language-link-compact"],
                },
                {
                    "candidate_id": "signin",
                    "tag": "button",
                    "semantic_type": "button",
                    "action_allowed": ["click"],
                    "text": "Sign In",
                    "accessible_name": "Sign In",
                    "is_visible": True,
                    "selector_candidates": ["@id=sign-in"],
                },
            ],
            context={},
        )


def test_grounder_does_not_treat_short_label_substrings_as_evidence() -> None:
    with pytest.raises(RuntimeError, match="No DOM candidate matched target"):
        SelectorGrounder().ground(
            step={"type": "click", "target": "Trim button", "comment": "Open trim tool"},
            candidates=[
                {
                    "candidate_id": "language",
                    "tag": "button",
                    "semantic_type": "button",
                    "action_allowed": ["click"],
                    "text": "EN",
                    "id": "language-link-compact",
                    "accessible_name": "EN",
                    "is_visible": True,
                    "selector_candidates": ["@id=language-link-compact"],
                }
            ],
            context={},
        )


def test_grounder_requires_multiple_keyword_evidence_for_long_click_targets() -> None:
    with pytest.raises(RuntimeError, match="No DOM candidate matched target"):
        SelectorGrounder().ground(
            step={"type": "click", "target": "Video clip in timeline"},
            candidates=[
                {
                    "candidate_id": "video_nav",
                    "tag": "a",
                    "semantic_type": "link",
                    "action_allowed": ["click"],
                    "text": "Video",
                    "accessible_name": "Video",
                    "is_visible": True,
                    "selector_candidates": ["text=Video"],
                }
            ],
            context={},
        )


def test_grounder_deduplicates_target_tokens_before_evidence_check() -> None:
    with pytest.raises(RuntimeError, match="No DOM candidate matched target"):
        SelectorGrounder().ground(
            step={"type": "click", "target": "Video Editor link", "comment": "Click Video Editor"},
            candidates=[
                {
                    "candidate_id": "video_nav",
                    "tag": "a",
                    "semantic_type": "link",
                    "action_allowed": ["click"],
                    "text": "Video",
                    "accessible_name": "Video",
                    "is_visible": True,
                    "selector_candidates": ["text=Video"],
                }
            ],
            context={},
        )


def test_grounder_allows_ranked_target_to_disambiguate_close_candidates() -> None:
    grounding = SelectorGrounder().ground(
        step={"type": "click", "target": "second image"},
        candidates=[
            {
                "candidate_id": "image_1",
                "tag": "button",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "accessible_name": "Image",
                "result_rank": 1,
                "is_visible": True,
                "selector_candidates": ["css:button[data-rank='1']"],
            },
            {
                "candidate_id": "image_2",
                "tag": "button",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "accessible_name": "Image",
                "result_rank": 2,
                "is_visible": True,
                "selector_candidates": ["css:button[data-rank='2']"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "image_2"


def test_grounder_prefers_related_reuse_button_for_selecting_ranked_item() -> None:
    grounding = SelectorGrounder().ground(
        step={
            "type": "click",
            "target": "second image in history library",
            "comment": "Select the second image from the user's history",
        },
        candidates=[
            {
                "candidate_id": "item_2_preview",
                "tag": "div",
                "role": "button",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "accessible_name": "Open generation preview",
                "context_text": "June 17, 2026",
                "result_rank": 2,
                "is_visible": True,
                "selector_candidates": ["@aria-label=Open generation preview"],
            },
            {
                "candidate_id": "item_2_reuse",
                "tag": "button",
                "semantic_type": "button",
                "action_allowed": ["click"],
                "accessible_name": "Reuse",
                "aria_label": "Reuse",
                "context_text": "Reuse | June 17, 2026 | Open generation preview",
                "related_item_rank": 2,
                "related_item_candidate_id": "item_2_preview",
                "related_item_context": "Open generation preview | June 17, 2026",
                "is_visible": True,
                "selector_candidates": ["@aria-label=Reuse"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "item_2_reuse"


def test_grounder_prefers_role_tab_for_tab_targets() -> None:
    grounding = SelectorGrounder().ground(
        step={"type": "click", "target": "My Library tab", "comment": "Switch to user's library"},
        candidates=[
            {
                "candidate_id": "background_library",
                "tag": "button",
                "type": "button",
                "semantic_type": "library_button",
                "action_allowed": ["click"],
                "text": "Library",
                "context_text": "Add Image Library",
                "is_visible": True,
                "selector_candidates": ["text=Library"],
            },
            {
                "candidate_id": "my_library_tab",
                "tag": "button",
                "role": "tab",
                "type": "button",
                "semantic_type": "button",
                "action_allowed": ["click"],
                "text": "My Library",
                "accessible_name": "My Library",
                "is_visible": True,
                "selector_candidates": ["text=My Library"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "my_library_tab"


def test_grounder_rejects_different_tab_when_explicit_tab_is_missing() -> None:
    with pytest.raises(RuntimeError, match="No DOM candidate matched"):
        SelectorGrounder().ground(
            step={
                "type": "click",
                "target": "My Library tab",
                "comment": "Switch to user's library",
            },
            candidates=[
                {
                    "candidate_id": "history_tab",
                    "tag": "button",
                    "role": "tab",
                    "text": "History",
                    "accessible_name": "History",
                    "is_visible": True,
                    "selector_candidates": ["text=History"],
                }
            ],
            context={},
        )


def test_grounder_requires_add_image_library_control_not_image_related_card() -> None:
    grounding = SelectorGrounder().ground(
        step={
            "type": "click",
            "target": "添加图片的库按钮",
            "comment": "点击添加图片区域的库按钮",
        },
        candidates=[
            {
                "candidate_id": "model_card",
                "tag": "button",
                "semantic_type": "clickable_item",
                "text": "V4.5 Lite",
                "upload_label": "Add Image",
                "is_visible": True,
                "selector_candidates": ["text=V4.5 Lite"],
            },
            {
                "candidate_id": "image_library",
                "tag": "button",
                "semantic_type": "library_button",
                "text": "Library",
                "upload_label": "Add Image",
                "upload_kind": "image",
                "is_visible": True,
                "selector_candidates": ["text=Library"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "image_library"


def test_grounder_treats_nested_pointer_labels_as_one_visual_control() -> None:
    candidates = [
        {
            "candidate_id": "outer",
            "tag": "div",
            "semantic_type": "clickable_item",
            "action_allowed": ["click"],
            "text": "Create Project",
            "accessible_name": "Create Project",
            "rect": {"x": 260, "y": 267, "width": 245, "height": 60},
            "selector_candidates": ["css:.project-button"],
        },
        {
            "candidate_id": "inner",
            "tag": "span",
            "semantic_type": "clickable_item",
            "action_allowed": ["click"],
            "text": "Create Project",
            "accessible_name": "Create Project",
            "rect": {"x": 320, "y": 286, "width": 126, "height": 22},
            "selector_candidates": ["css:.project-button span"],
        },
    ]

    grounding = SelectorGrounder().ground(
        step={"type": "click", "target": "Create Project"},
        candidates=candidates,
        context={},
    )

    assert grounding["candidate_id"] == "outer"


def test_grounder_does_not_let_comment_noun_override_exact_target() -> None:
    grounding = SelectorGrounder().ground(
        step={
            "type": "click",
            "target": "Create Project button",
            "comment": "Start editing project with the uploaded video",
        },
        candidates=[
            {
                "candidate_id": "video_nav",
                "tag": "a",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "text": "Video",
                "accessible_name": "Video",
                "rect": {"x": 20, "y": 8, "width": 80, "height": 36},
                "selector_candidates": ["text=Video"],
            },
            {
                "candidate_id": "create_project",
                "tag": "div",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "text": "Create Project",
                "accessible_name": "Create Project",
                "rect": {"x": 260, "y": 267, "width": 245, "height": 60},
                "selector_candidates": ["css:.create-project"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "create_project"


def test_grounder_prefers_file_input_with_real_upload_hit_area() -> None:
    grounding = SelectorGrounder().ground(
        step={"type": "upload", "target": "Add files area", "path": "/tmp/sample.mp4"},
        candidates=[
            {
                "candidate_id": "real_drop_area",
                "tag": "input",
                "type": "file",
                "semantic_type": "file_input",
                "action_allowed": ["upload"],
                "context_text": "Add files or drag and drop files here",
                "rect": {"x": 187, "y": 56, "width": 480, "height": 253},
                "selector_candidates": ["css:.real-upload"],
            },
            {
                "candidate_id": "hidden_duplicate",
                "tag": "input",
                "type": "file",
                "semantic_type": "file_input",
                "action_allowed": ["upload"],
                "context_text": "Add files",
                "rect": {"x": 0, "y": 52, "width": 0, "height": 0},
                "selector_candidates": ["css:.hidden-upload"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "real_drop_area"


def test_grounder_prefers_larger_drop_area_between_valid_file_inputs() -> None:
    grounding = SelectorGrounder().ground(
        step={"type": "upload", "target": "Upload media area", "path": "/tmp/sample.mp4"},
        candidates=[
            {
                "candidate_id": "small_media_button",
                "tag": "input",
                "type": "file",
                "semantic_type": "file_input",
                "action_allowed": ["upload"],
                "context_text": "Add files",
                "rect": {"x": 94, "y": 115, "width": 278, "height": 46},
                "selector_candidates": ["css:.small-upload"],
            },
            {
                "candidate_id": "main_drop_area",
                "tag": "input",
                "type": "file",
                "semantic_type": "file_input",
                "action_allowed": ["upload"],
                "context_text": "Add files or drag and drop files here",
                "rect": {"x": 397, "y": 56, "width": 379, "height": 253},
                "selector_candidates": ["css:.main-upload"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "main_drop_area"


def test_grounder_restricts_range_drag_to_range_control() -> None:
    grounding = SelectorGrounder().ground(
        step={"type": "drag", "target": "Example range", "delta_x": 20},
        candidates=[
            {
                "candidate_id": "submit",
                "tag": "button",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "text": "Submit",
                "is_visible": True,
                "selector_candidates": ["text=Submit"],
            },
            {
                "candidate_id": "range",
                "tag": "input",
                "type": "range",
                "semantic_type": "input_range",
                "action_allowed": ["input", "click"],
                "accessible_name": "Example range",
                "is_visible": True,
                "selector_candidates": ["@name=my-range"],
            },
        ],
        context={},
    )

    assert grounding["candidate_id"] == "range"
def test_double_click_prefers_requested_file_card_over_library_navigation() -> None:
    candidates = [
        {
            "candidate_id": "nav",
            "tag": "div",
            "semantic_type": "clickable_item",
            "action_allowed": ["click"],
            "text": "My files",
            "accessible_name": "My files",
            "selector_candidates": ["css:.files-nav"],
        },
        {
            "candidate_id": "asset",
            "tag": "div",
            "semantic_type": "clickable_item",
            "action_allowed": ["click"],
            "text": "00:26 sample.mp4",
            "accessible_name": "00:26 sample.mp4",
            "selector_candidates": ["css:div.thumb-item"],
        },
    ]

    result = SelectorGrounder().ground(
        step={"type": "double_click", "target": "sample.mp4 in My files"},
        candidates=candidates,
        context={},
    )

    assert result["candidate_id"] == "asset"
def test_input_requires_target_evidence_and_rejects_unrelated_search_box() -> None:
    candidates = [
        {
            "candidate_id": "search",
            "tag": "input",
            "type": "text",
            "semantic_type": "input_text",
            "action_allowed": ["input", "click"],
            "placeholder": "Search",
            "context_text": "My files",
            "selector_candidates": ["css:#search"],
        },
        {
            "candidate_id": "speed",
            "tag": "input",
            "type": "text",
            "semantic_type": "input_text",
            "action_allowed": ["input", "click"],
            "value": "1x",
            "context_text": "Speed",
            "selector_candidates": ["css:#speed"],
        },
    ]

    result = SelectorGrounder().ground(
        step={"type": "input", "target": "Speed", "value": "1.5"},
        candidates=candidates,
        context={},
    )

    assert result["candidate_id"] == "speed"


def test_set_range_can_target_visible_numeric_text_field_with_semantic_context() -> None:
    result = SelectorGrounder().ground(
        step={"type": "set_range", "target": "Speed slider", "value": "1.5"},
        candidates=[
            {
                "candidate_id": "search",
                "tag": "input",
                "type": "text",
                "semantic_type": "input_text",
                "action_allowed": ["input", "click"],
                "value": "",
                "context_text": "My files",
                "selector_candidates": ["css:#search"],
            },
            {
                "candidate_id": "speed",
                "tag": "input",
                "type": "text",
                "semantic_type": "input_text",
                "action_allowed": ["input", "click"],
                "value": "1x",
                "context_text": "Speed",
                "selector_candidates": ["css:input[value='1x']"],
            },
        ],
        context={},
    )

    assert result["candidate_id"] == "speed"


def test_title_input_does_not_match_project_name_contenteditable() -> None:
    with pytest.raises(RuntimeError, match="No DOM candidate matched"):
        SelectorGrounder().ground(
            step={"type": "input", "target": "Title text input", "value": "Demo"},
            candidates=[
                {
                    "candidate_id": "project-name",
                    "tag": "div",
                    "type": "text",
                    "semantic_type": "contenteditable",
                    "action_allowed": ["input", "click"],
                    "text": "sample",
                    "accessible_name": "sample",
                    "selector_candidates": ["css:.project-name"],
                }
            ],
            context={},
        )


def test_first_button_rank_cannot_override_missing_core_label_evidence() -> None:
    result = SelectorGrounder().ground(
        step={
            "type": "click",
            "target": "first Add text button for Open Sans style",
        },
        candidates=[
            {
                "candidate_id": "projects",
                "tag": "a",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "text": "My projects",
                "accessible_name": "My projects",
                "result_rank": 1,
                "selector_candidates": ["text=My projects"],
            },
            {
                "candidate_id": "open-sans",
                "tag": "div",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "text": "Open Sans",
                "accessible_name": "Open Sans",
                "selector_candidates": ["text=Open Sans"],
            },
        ],
        context={},
    )

    assert result["candidate_id"] == "open-sans"


def test_font_target_with_cjk_qualifier_does_not_match_plain_noto_sans() -> None:
    result = SelectorGrounder().ground(
        step={
            "type": "click",
            "target": "Noto Sans CJK SC",
        },
        candidates=[
            {
                "candidate_id": "plain-noto",
                "tag": "li",
                "role": "option",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "text": "Noto Sans",
                "accessible_name": "Noto Sans",
                "selector_candidates": ["text=Noto Sans"],
            },
            {
                "candidate_id": "cjk-noto",
                "tag": "li",
                "role": "option",
                "semantic_type": "clickable_item",
                "action_allowed": ["click"],
                "text": "Noto Sans CJK SC",
                "accessible_name": "Noto Sans CJK SC",
                "selector_candidates": ["text=Noto Sans CJK SC"],
            },
        ],
        context={},
    )

    assert result["candidate_id"] == "cjk-noto"


def test_font_target_with_cjk_qualifier_rejects_only_plain_noto_sans() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="No DOM candidate matched target"):
        SelectorGrounder().ground(
            step={
                "type": "click",
                "target": "Noto Sans CJK SC",
            },
            candidates=[
                {
                    "candidate_id": "plain-noto",
                    "tag": "li",
                    "role": "option",
                    "semantic_type": "clickable_item",
                    "action_allowed": ["click"],
                    "text": "Noto Sans",
                    "accessible_name": "Noto Sans",
                    "selector_candidates": ["text=Noto Sans"],
                }
            ],
            context={},
        )


def test_double_click_canvas_text_can_ground_unique_contenteditable() -> None:
    result = SelectorGrounder().ground(
        step={"type": "double_click", "target": "New text box on canvas"},
        candidates=[
            {
                "candidate_id": "canvas-title",
                "tag": "div",
                "semantic_type": "contenteditable",
                "class_name": "element text-renderer wrap-content-inner",
                "placeholder": "Sample Text",
                "action_allowed": ["input", "click", "double_click"],
                "is_visible": True,
                "rect": {"x": 400, "y": 120, "width": 180, "height": 50},
                "selector_candidates": ["css:.text-renderer"],
            },
            {
                "candidate_id": "project-name",
                "tag": "div",
                "semantic_type": "contenteditable",
                "class_name": "name-input transparent color-border",
                "text": "sample",
                "action_allowed": ["input", "click", "double_click"],
                "is_visible": True,
                "rect": {"x": 280, "y": 8, "width": 60, "height": 36},
                "selector_candidates": ["css:.name-input"],
            },
        ],
        context={},
    )

    assert result["candidate_id"] == "canvas-title"


def test_text_color_target_prefers_font_property_picker_over_text_navigation() -> None:
    result = SelectorGrounder().ground(
        step={"type": "click", "target": "Text color button"},
        candidates=[
            {
                "candidate_id": "text-tool",
                "tag": "div",
                "semantic_type": "clickable_item",
                "class_name": "component_side-menu-item text",
                "action_allowed": ["click"],
                "text": "Text",
                "accessible_name": "Text",
                "is_visible": True,
                "selector_candidates": ["css:.side-menu.text"],
            },
            {
                "candidate_id": "font-color",
                "tag": "div",
                "role": "button",
                "semantic_type": "clickable_item",
                "class_name": "el-color-picker",
                "action_allowed": ["click"],
                "aria_label": "color picker",
                "accessible_name": "color picker",
                "context_text": "color picker | Open Sans 32",
                "is_visible": True,
                "selector_candidates": ["@aria-label=color picker"],
            },
        ],
        context={},
    )

    assert result["candidate_id"] == "font-color"
