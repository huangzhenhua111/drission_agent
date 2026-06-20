from __future__ import annotations

from app.generation.candidate_compactor import build_grounding_candidates
from app.generation.candidate_compactor import compact_candidate


def test_compact_candidate_drops_debug_fields_and_dedupes_context() -> None:
    compact = compact_candidate(
        {
            "candidate_id": "e7",
            "dom_index": 254,
            "tag": "input",
            "type": "file",
            "semantic_type": "file_input",
            "action_allowed": ["upload"],
            "is_visible": False,
            "css_path": "div:nth-of-type(1) > input:nth-of-type(1)",
            "context_chain": [
                {"text": "Add Motion Drag and drop a video here or click to upload Library"}
            ],
            "primary_context_text": "Add Motion Drag and drop a video here or click to upload Library",
            "context_text": "Add Motion Drag and drop a video here or click to upload Library",
            "nearest_card_text": "Add Motion Drag and drop a video here or click to upload Library",
            "upload_label": "Add Motion",
            "upload_kind": "video",
            "selector_candidates": ["css:input[type='file']"],
            "selector_indexes": {"css:input[type='file']": 0},
            "selector_match_counts": {"css:input[type='file']": 2},
            "selector_metadata": [
                {
                    "selector": "css:input[type='file']",
                    "index": 0,
                    "match_count": 2,
                    "unique": False,
                }
            ],
        }
    )

    assert "css_path" not in compact
    assert "context_chain" not in compact
    assert "primary_context_text" not in compact
    assert "selector_indexes" not in compact
    assert "selector_match_counts" not in compact
    assert compact["context_text"] == "Add Motion Drag and drop a video here or click to upload Library"
    assert "nearest_card_text" not in compact
    assert compact["selector_metadata"][0]["index"] == 0
    assert compact["selector_metadata"][0]["match_count"] == 2


def test_build_grounding_candidates_filters_by_action_type() -> None:
    raw_candidates = [
        {
            "candidate_id": "search",
            "tag": "input",
            "type": "search",
            "semantic_type": "input_search",
            "action_allowed": ["input", "click"],
            "placeholder": "Search",
            "is_visible": True,
            "selector_candidates": ["css:input[type='search']"],
        },
        {
            "candidate_id": "submit",
            "tag": "button",
            "type": "submit",
            "semantic_type": "button",
            "action_allowed": ["click"],
            "text": "Search",
            "is_visible": True,
            "selector_candidates": ["text=Search"],
        },
        {
            "candidate_id": "type",
            "tag": "select",
            "semantic_type": "select",
            "action_allowed": ["select", "click"],
            "options": [{"text": "One"}, {"text": "Two"}],
            "is_visible": True,
            "selector_candidates": ["@name=type"],
        },
        {
            "candidate_id": "upload",
            "tag": "input",
            "type": "file",
            "semantic_type": "file_input",
            "action_allowed": ["upload"],
            "is_visible": False,
            "upload_label": "Add Image",
            "selector_candidates": ["css:input[type='file']"],
        },
    ]

    click_ids = [
        item["candidate_id"]
        for item in build_grounding_candidates({"type": "click", "target": "Search"}, raw_candidates)
    ]
    input_ids = [
        item["candidate_id"]
        for item in build_grounding_candidates({"type": "input", "target": "Search"}, raw_candidates)
    ]
    select_ids = [
        item["candidate_id"]
        for item in build_grounding_candidates({"type": "select", "target": "Type"}, raw_candidates)
    ]
    upload_ids = [
        item["candidate_id"]
        for item in build_grounding_candidates({"type": "upload", "target": "Add Image"}, raw_candidates)
    ]

    assert click_ids == ["submit"]
    assert input_ids == ["search"]
    assert select_ids == ["type"]
    assert upload_ids == ["upload"]


def test_select_view_keeps_options_and_drops_click_noise() -> None:
    candidates = build_grounding_candidates(
        {"type": "select", "target": "Type", "value": "Two"},
        [
            {
                "candidate_id": "type",
                "tag": "select",
                "semantic_type": "select",
                "action_allowed": ["select"],
                "is_visible": True,
                "options": [{"text": "One"}, {"text": "Two"}],
                "selector_candidates": ["@name=type"],
                "css_path": "body > div:nth-of-type(1) > select",
                "context_chain": [{"text": "Type One Two"}],
            }
        ],
    )

    assert candidates[0]["options"] == [{"text": "One"}, {"text": "Two"}]
    assert "css_path" not in candidates[0]
    assert "context_chain" not in candidates[0]


def test_click_view_prioritizes_matching_library_button_over_icon_button() -> None:
    candidates = build_grounding_candidates(
        {"type": "click", "target": "Library Add Image"},
        [
            {
                "candidate_id": "icon",
                "tag": "button",
                "semantic_type": "button",
                "action_allowed": ["click"],
                "aria_label": "TikTok",
                "context_text": "TikTok | Library | Add Motion Drag and drop a video here",
                "upload_label": "Add Motion",
                "upload_kind": "video",
                "is_visible": True,
                "selector_candidates": ["@aria-label=TikTok"],
                "selector_metadata": [
                    {"selector": "@aria-label=TikTok", "index": 0, "match_count": 1, "unique": True}
                ],
            },
            {
                "candidate_id": "image_library",
                "tag": "button",
                "semantic_type": "library_button",
                "action_allowed": ["click"],
                "text": "Library",
                "context_text": "Library | Add Image Drag and drop an image here",
                "upload_label": "Add Image",
                "upload_kind": "image",
                "is_visible": True,
                "selector_candidates": ["text=Library"],
                "selector_metadata": [
                    {"selector": "text=Library", "index": 1, "match_count": 2, "unique": False}
                ],
            },
        ],
    )

    assert candidates[0]["candidate_id"] == "image_library"


def test_click_view_prioritizes_requested_ranked_clickable_item() -> None:
    candidates = build_grounding_candidates(
        {"type": "click", "target": "second image in history library"},
        [
            {
                "candidate_id": "library",
                "tag": "button",
                "semantic_type": "library_button",
                "action_allowed": ["click"],
                "text": "Library",
                "context_text": "Add Image Library",
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
                "context_text": "June 17, 2026",
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
                "context_text": "June 17, 2026",
                "result_rank": 2,
                "is_visible": True,
                "selector_candidates": ["@aria-label=Open item preview"],
            },
        ],
    )

    assert candidates[0]["candidate_id"] == "item_2"


def test_click_view_prioritizes_related_ranked_item_action_when_selecting_item() -> None:
    candidates = build_grounding_candidates(
        {
            "type": "click",
            "target": "second image in history library",
            "comment": "Select the second image from the user's history",
        },
        [
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
    )

    assert candidates[0]["candidate_id"] == "item_2_reuse"
    assert candidates[0]["related_item_rank"] == 2


def test_click_view_prioritizes_tab_role_for_tab_targets() -> None:
    candidates = build_grounding_candidates(
        {"type": "click", "target": "My Library tab", "comment": "Switch to user's library"},
        [
            {
                "candidate_id": "background_library",
                "tag": "button",
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
                "semantic_type": "button",
                "action_allowed": ["click"],
                "text": "My Library",
                "accessible_name": "My Library",
                "is_visible": True,
                "selector_candidates": ["text=My Library"],
            },
        ],
    )

    assert candidates[0]["candidate_id"] == "my_library_tab"
