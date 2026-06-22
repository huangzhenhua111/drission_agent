from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import os

from app.llm.client import LLMClient
from app.llm.client import OpenAIJsonClient


@dataclass(frozen=True)
class VisualClick:
    x: int
    y: int
    reason: str
    visible_label: str | None = None


@dataclass(frozen=True)
class VisualTextCheck:
    readable: bool
    exact_text_visible: bool
    observed_text: str | None
    issue: str
    reason: str


class VisualFallbackGrounder:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or OpenAIJsonClient.for_vision()

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
        image_width, image_height = _png_dimensions(Path(screenshot_path))
        prompt = build_visual_click_prompt(
            task=task,
            failed_step=failed_step,
            current_state=current_state,
            completed_actions=completed_actions,
            error=error,
            image_width=image_width,
            image_height=image_height,
        )
        response = self.client.complete_json_with_image(
            prompt=prompt,
            image_path=screenshot_path,
            schema_name="VisualClick",
        )
        data = response.data
        action = str(data.get("action") or "").strip().lower()
        if action not in {"click", "click_at", "tap"}:
            raise ValueError(f"Visual fallback returned unsupported action: {action!r}")
        raw_x, raw_y = _raw_coordinates(data)
        coordinate_space = _effective_coordinate_space(data, self.client)
        if coordinate_space in {"normalized_1000", "normalized", "0-1000"}:
            x = round(raw_x * image_width / 1000)
            y = round(raw_y * image_height / 1000)
        elif coordinate_space == "pixels":
            x, y = raw_x, raw_y
            if (x >= image_width or y >= image_height) and 0 <= x <= 1000 and 0 <= y <= 1000:
                x = round(raw_x * image_width / 1000)
                y = round(raw_y * image_height / 1000)
        else:
            raise ValueError(f"Visual fallback returned unknown coordinate space: {coordinate_space!r}")
        if x < 0 or y < 0:
            raise ValueError(f"Visual fallback returned invalid coordinates: ({x}, {y})")
        if x >= image_width or y >= image_height:
            raise ValueError(
                f"Visual fallback coordinates are outside the screenshot: ({x}, {y}) "
                f"not within {image_width}x{image_height}"
            )
        visible_label = str(data.get("visible_label") or "").strip() or None
        expected_label = _expected_visible_label(failed_step.get("target"))
        if expected_label and not visible_label:
            raise ValueError(
                f"Visual fallback did not report the visible label for named target {expected_label!r}"
            )
        if expected_label and not _visible_labels_equivalent(expected_label, visible_label):
            raise ValueError(
                f"Visual fallback chose visible label {visible_label!r}, expected {expected_label!r}"
            )
        return VisualClick(
            x=x,
            y=y,
            reason=str(data.get("reason") or "visual fallback click"),
            visible_label=visible_label,
        )


class VisualTextVerifier:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or OpenAIJsonClient.for_vision()

    def verify_rendered_text(
        self,
        *,
        expected_text: str,
        screenshot_path: str | Path,
        context: dict | None = None,
    ) -> VisualTextCheck:
        response = self.client.complete_json_with_image(
            prompt=build_visual_text_check_prompt(
                expected_text=expected_text,
                context=context or {},
            ),
            image_path=screenshot_path,
            schema_name="VisualTextCheck",
        )
        data = response.data
        issue = str(data.get("issue") or "unknown").strip().lower()
        return VisualTextCheck(
            readable=bool(data.get("readable")),
            exact_text_visible=bool(data.get("exact_text_visible")),
            observed_text=str(data.get("observed_text")).strip()
            if data.get("observed_text") is not None
            else None,
            issue=issue or "unknown",
            reason=str(data.get("reason") or "").strip(),
        )


def build_visual_click_prompt(
    *,
    task: str,
    failed_step: dict,
    current_state: dict,
    completed_actions: list[dict],
    error: str,
    image_width: int | None = None,
    image_height: int | None = None,
) -> str:
    state = {
        "url": current_state.get("url"),
        "title": current_state.get("title"),
        "text_excerpt": current_state.get("text_excerpt"),
    }
    return f"""The DOM selector-based browser automation step failed.

Use the screenshot to choose the next single click needed to continue the user's task.
Return coordinates in screenshot pixels relative to the top-left corner.

Original task:
{task}

Failed step JSON:
{failed_step}

Completed actions JSON:
{completed_actions[-6:]}

Current page state JSON:
{state}

Screenshot dimensions:
{image_width}x{image_height} pixels

Error:
{error}

Return exactly this JSON shape:
{{
  "action": "click_at",
  "x": 123,
  "y": 456,
  "coordinate_space": "pixels",
  "visible_label": "exact text visibly printed on the chosen control",
  "reason": "brief reason"
}}

Rules:
- Return one click only.
- First match the failed step target against the exact text visibly printed in the screenshot. Do not substitute a semantically related control. For example, target "Text tool" means the visible label "Text", never "Text to speech".
- Put the exact printed label of the chosen control in visible_label, and place the click point inside that exact control's bounds.
- Prefer actual screenshot pixels and set coordinate_space to "pixels". If your vision system inherently uses coordinates normalized to a 0-1000 grid, return those values unchanged and set coordinate_space to "normalized_1000".
- Do not return selectors, XPath, code, or prose.
- If the target is a ranked item such as first/second/third, count visible cards or rows in natural reading order.
- In a canvas video editor, if the intent is to reveal editing properties for a selected timeline clip, click the corresponding object in the preview/canvas when clicking the timeline body would only select the clip without opening properties.
- A scissors icon in a playback toolbar usually means split/cut at the playhead; do not treat it as a named Trim settings panel unless the failed target explicitly asks for that toolbar operation.
- Avoid clicking login, account, upgrade, download, share, or destructive controls unless the failed step explicitly asks for that.
"""


def build_visual_text_check_prompt(
    *,
    expected_text: str,
    context: dict,
) -> str:
    compact_context = {
        "step": context.get("step"),
        "url": context.get("url"),
        "title": context.get("title"),
        "text_excerpt": context.get("text_excerpt"),
    }
    return f"""Inspect this browser screenshot and verify whether the expected text is visibly rendered and human-readable.

Expected text:
{expected_text}

Context JSON:
{compact_context}

Return exactly this JSON shape:
{{
  "readable": true,
  "exact_text_visible": true,
  "observed_text": "{expected_text}",
  "issue": "none",
  "reason": "brief reason"
}}

Rules:
- Judge only the screenshot pixels, not DOM text, page state, or the user's instruction.
- The expected text must be visible as readable glyphs in the editor/canvas/preview/timeline area.
- If the expected text appears as square boxes, tofu glyphs, missing-glyph placeholders, question marks, blank text, or unreadable marks, set readable=false, exact_text_visible=false, and issue="tofu_boxes" or "unreadable".
- If different text is visible, set exact_text_visible=false and issue="wrong_text".
- If no matching text is visible, set exact_text_visible=false and issue="missing".
- Use issue="unknown" only when the screenshot is too unclear to decide.
- Return JSON only. Do not include markdown or prose.
"""


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as image_file:
        header = image_file.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Visual fallback screenshot is not a valid PNG: {path}")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(f"Visual fallback screenshot has invalid dimensions: {width}x{height}")
    return width, height


def _expected_visible_label(target: object) -> str:
    raw = str(target or "").lower().replace("_", " ")
    drop_text_descriptor = any(
        word in raw.split() for word in {"style", "preset", "font"}
    )
    for descriptor in ["文字样式", "文本样式", "样式预设", "字体样式"]:
        raw = raw.replace(descriptor, " ")
    descriptive_markers = {
        "first", "second", "third", "fourth", "last", "next", "previous",
        "in", "on", "at", "inside", "visible", "large", "small",
    }
    raw_words = raw.split()
    if any(word in descriptive_markers for word in raw_words):
        return ""
    if any(marker in raw for marker in ["第", "时间线", "画布", "区域中的", "列表中的"]):
        return ""
    words = [
        word
        for word in raw_words
        if word not in {
            "button", "tool", "tab", "control", "link", "area", "slider", "input", "field",
            "style", "preset", "font",
        }
        and not (drop_text_descriptor and word == "text")
    ]
    return " ".join(words).strip() if 0 < len(words) <= 3 else ""


def _normalized_label(label: object) -> str:
    return " ".join(str(label or "").lower().split())


def _visible_labels_equivalent(expected: object, actual: object) -> bool:
    expected_label = _normalized_label(expected)
    actual_label = _normalized_label(actual)
    if expected_label == actual_label:
        return True

    color_aliases = {
        "black": {"black", "#000", "#000000", "rgb(0, 0, 0)", "黑色"},
        "white": {"white", "#fff", "#ffffff", "rgb(255, 255, 255)", "白色"},
        "red": {"red", "#f00", "#ff0000", "rgb(255, 0, 0)", "红色"},
    }

    def color_key(label: str) -> str | None:
        simplified = label
        for word in ("color", "colour", "swatch", "option", "色块", "颜色", "选项"):
            simplified = simplified.replace(word, " ")
        simplified = " ".join(simplified.split())
        for key, aliases in color_aliases.items():
            if simplified in aliases or any(alias in label for alias in aliases):
                return key
        return None

    expected_color = color_key(expected_label)
    return expected_color is not None and color_key(actual_label) == expected_color


def _raw_coordinates(data: dict) -> tuple[int, int]:
    if "x" not in data:
        raise ValueError("Visual fallback response is missing x coordinate")
    raw_x = int(data["x"])
    if "y" in data:
        return raw_x, int(data["y"])
    ignored = {"action", "x", "coordinate_space", "visible_label", "reason"}
    numeric_extras = [
        int(value)
        for key, value in data.items()
        if key not in ignored and isinstance(value, (int, float))
    ]
    if len(numeric_extras) == 1:
        return raw_x, numeric_extras[0]
    raise ValueError("Visual fallback response is missing y coordinate")


def _effective_coordinate_space(data: dict, client: LLMClient) -> str:
    configured = os.getenv("VISION_LLM_COORDINATE_SPACE")
    if configured:
        return configured.strip().lower()
    provider_config = getattr(client, "_provider_config", None)
    provider = getattr(client, "provider", None)
    if provider == "vision" and callable(provider_config):
        model = str(provider_config()[1] or "").lower()
        if "qwen" in model and "vl" in model:
            # Qwen-VL grounding coordinates use a 0-1000 grid in practice,
            # even when smaller variants occasionally label them as pixels.
            return "normalized_1000"
    return str(data.get("coordinate_space") or "pixels").strip().lower()
