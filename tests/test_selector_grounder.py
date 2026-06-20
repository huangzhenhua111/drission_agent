from __future__ import annotations

from pathlib import Path

import pytest

from app.generation.dom_snapshot import extract_candidates_from_html
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
