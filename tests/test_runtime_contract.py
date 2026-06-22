from __future__ import annotations

from app.runtime.drission_runtime import _annotate_clickable_item_ranks
from app.runtime.drission_runtime import normalize_runtime_selector
from app.runtime.drission_runtime import DrissionRuntime
from app.config import Settings
from pathlib import Path


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
    def __call__(self, seconds: float) -> None:
        return None

    def ele_displayed(self, selector: str, timeout: int, raise_err: bool) -> bool:
        return selector == "@id=ok"


class FakeElement:
    def __init__(self, name: str = "ok", *, contenteditable: bool = False) -> None:
        self.name = name
        self.contenteditable = contenteditable
        self.drag_call: tuple | None = None
        self.focused = False
        self.clicks = 0
        self.input_calls: list[tuple[str, bool]] = []
        self.js_calls: list[str] = []
        self.time_fields = {
            "hours": "0",
            "minutes": "00",
            "seconds": "00",
            "milliseconds": "00",
        }

    def focus(self) -> None:
        self.focused = True

    def attr(self, name: str):
        if name == "contenteditable" and self.contenteditable:
            return "true"
        return None

    def click(self) -> None:
        self.clicks += 1

    def input(self, value: str, clear: bool = False) -> None:
        self.input_calls.append((value, clear))

    def run_js(self, script: str, value: str = ""):
        self.js_calls.append(script)
        if "const result = {}" in script:
            return dict(self.time_fields)
        return value

    def ele(self, selector: str, timeout: int = 1):
        for kind in self.time_fields:
            if f"input-{kind}" in selector:
                return FakeTimeField(self.time_fields, kind)
        return None

    def drag(self, *, offset_x: float, offset_y: float, duration: float) -> None:
        self.drag_call = (offset_x, offset_y, duration)


class FakeTimeField:
    def __init__(self, values: dict[str, str], kind: str) -> None:
        self.values = values
        self.kind = kind
        self.states = type("States", (), {"is_displayed": True})()

    @property
    def text(self) -> str:
        return self.values[self.kind]

    def input(self, value: str) -> None:
        self.values[self.kind] = value


class FakeActions:
    def __init__(self) -> None:
        self.keys: list[tuple[str, str]] = []

    def key_down(self, key: str):
        self.keys.append(("down", key))
        return self

    def key_up(self, key: str):
        self.keys.append(("up", key))
        return self


class FakePage:
    def __init__(self) -> None:
        self.wait = FakeWait()
        self.actions = FakeActions()
        self.element = FakeElement()
        self.cdp_calls: list[tuple[str, dict]] = []

    def _run_cdp(self, method: str, **kwargs) -> None:
        self.cdp_calls.append((method, kwargs))

    def ele(self, selector: str, timeout: int) -> FakeElement | None:
        if selector == "@id=ok":
            return self.element
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


def test_runtime_complex_editor_primitives() -> None:
    runtime = DrissionRuntime()
    runtime.page = FakePage()

    assert runtime.set_range(["@id=ok"], "1.5", "Speed") == "@id=ok"
    assert runtime.double_click(["@id=ok"], "media card") == "@id=ok"
    assert runtime.page.element.clicks == 1
    assert runtime.set_timecode(["@id=ok"], "00:02.00", "Trim start") == "@id=ok"
    assert runtime.drag(["@id=ok"], 40, -2, 0.8, "Trim handle") == "@id=ok"
    assert runtime.page.element.drag_call == (40, -2, 0.8)
    assert runtime.press_key("DELETE", "selected clip") == "key:DELETE"
    assert runtime.page.cdp_calls[0][1]["commands"] == ["deleteForward"]
    assert runtime.page.cdp_calls[1][1]["type"] == "keyUp"

    runtime.page.cdp_calls.clear()
    assert runtime.press_key("Control+A", "text object") == "key:CTRL+A"
    assert [call[1]["type"] for call in runtime.page.cdp_calls] == [
        "rawKeyDown",
        "rawKeyDown",
        "keyUp",
        "keyUp",
    ]
    assert runtime.page.cdp_calls[1][1]["code"] == "KeyA"
    assert runtime.page.cdp_calls[1][1]["modifiers"] == 2


def test_runtime_input_replaces_existing_editable_text() -> None:
    runtime = DrissionRuntime()
    runtime.page = FakePage()

    assert runtime.input(["@id=ok"], "短视频测试", "Title") == "@id=ok"
    assert runtime.page.element.focused is True
    assert runtime.page.element.input_calls == [("短视频测试", True)]
    assert any("this.blur()" in script for script in runtime.page.element.js_calls)


def test_runtime_contenteditable_input_uses_real_keyboard_replacement() -> None:
    runtime = DrissionRuntime()
    runtime.page = FakePage()
    runtime.page.element = FakeElement(contenteditable=True)

    assert runtime.input(["@id=ok"], "短视频测试", "Canvas title") == "@id=ok"

    assert runtime.page.element.input_calls == []
    assert [call[0] for call in runtime.page.cdp_calls][-1] == "Input.insertText"
    assert runtime.page.cdp_calls[-1][1]["text"] == "短视频测试"
    assert any("this.blur()" in script for script in runtime.page.element.js_calls)


def test_resolve_browser_path_uses_linux_path(monkeypatch, tmp_path: Path) -> None:
    chrome = tmp_path / "google-chrome"
    chrome.touch()
    settings = Settings(
        openai_api_key=None,
        openai_model="mock",
        openai_base_url=None,
        browser_type="chrome",
        browser_path=None,
        browser_user_data_path=None,
        browser_debug_port=19222,
        browser_headless=True,
        output_dir=tmp_path / "outputs",
    )
    monkeypatch.setattr("app.runtime.drission_runtime.shutil.which", lambda name: str(chrome) if name == "google-chrome" else None)

    assert DrissionRuntime(settings)._resolve_browser_path() == chrome.resolve()
