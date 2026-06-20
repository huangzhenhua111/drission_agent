from __future__ import annotations

from app.runtime.drission_runtime import _annotate_clickable_item_ranks
from app.runtime.drission_runtime import normalize_runtime_selector
from app.runtime.drission_runtime import DrissionRuntime


def test_normalize_runtime_selector_supports_internal_text_convention() -> None:
    assert normalize_runtime_selector("text=Submit") == "text:Submit"
    assert normalize_runtime_selector("@id=submit-button") == "@id=submit-button"
    assert normalize_runtime_selector("css:button[type='submit']") == "css:button[type='submit']"


def test_clickable_item_rank_annotation_only_links_item_action_buttons() -> None:
    candidates = [
        {
            "candidate_id": "card_1",
            "dom_index": 10,
            "tag": "button",
            "semantic_type": "clickable_item",
            "text": "Tom Aura",
            "is_visible": True,
            "rect": {"x": 100, "y": 100, "width": 200, "height": 200},
        },
        {
            "candidate_id": "background_library",
            "dom_index": 11,
            "tag": "button",
            "semantic_type": "library_button",
            "text": "Library",
            "accessible_name": "Library",
            "is_visible": True,
            "rect": {"x": 130, "y": 130, "width": 60, "height": 30},
        },
        {
            "candidate_id": "reuse",
            "dom_index": 12,
            "tag": "button",
            "semantic_type": "button",
            "aria_label": "Reuse",
            "accessible_name": "Reuse",
            "is_visible": True,
            "rect": {"x": 240, "y": 240, "width": 32, "height": 32},
        },
    ]

    _annotate_clickable_item_ranks(candidates)

    assert candidates[0]["result_rank"] == 1
    assert "related_item_rank" not in candidates[1]
    assert candidates[2]["related_item_rank"] == 1


class FakeWait:
    def ele_displayed(self, selector: str, timeout: int, raise_err: bool) -> bool:
        return selector == "@id=ok"


class FakeElement:
    def __init__(self, name: str = "ok") -> None:
        self.name = name


class FakePage:
    def __init__(self) -> None:
        self.wait = FakeWait()

    def ele(self, selector: str, timeout: int) -> FakeElement | None:
        if selector == "@id=ok":
            return FakeElement()
        return None

    def eles(self, selector: str) -> list[FakeElement]:
        if selector == "css:input[type='file']":
            return [FakeElement("motion"), FakeElement("image")]
        return [FakeElement()] if selector == "@id=ok" else []


def test_find_first_tries_fallback_selectors() -> None:
    runtime = DrissionRuntime()
    runtime.page = FakePage()

    element, selector = runtime.find_first(["@id=missing", "@id=ok"], target="OK button")

    assert isinstance(element, FakeElement)
    assert selector == "@id=ok"


def test_find_first_uses_selector_specific_index() -> None:
    runtime = DrissionRuntime()
    runtime.page = FakePage()

    element, selector = runtime.find_first(
        ["css:input[type='file']"],
        target="image upload",
        selector_indexes={"css:input[type='file']": 1},
        require_displayed=False,
    )

    assert isinstance(element, FakeElement)
    assert element.name == "image"
    assert selector == "css:input[type='file']"
