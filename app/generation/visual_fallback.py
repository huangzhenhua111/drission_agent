from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.llm.client import LLMClient
from app.llm.client import OpenAIJsonClient


@dataclass(frozen=True)
class VisualClick:
    x: int
    y: int
    reason: str


class VisualFallbackGrounder:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or OpenAIJsonClient()

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
        prompt = build_visual_click_prompt(
            task=task,
            failed_step=failed_step,
            current_state=current_state,
            completed_actions=completed_actions,
            error=error,
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
        x = int(data["x"])
        y = int(data["y"])
        if x < 0 or y < 0:
            raise ValueError(f"Visual fallback returned invalid coordinates: ({x}, {y})")
        return VisualClick(x=x, y=y, reason=str(data.get("reason") or "visual fallback click"))


def build_visual_click_prompt(
    *,
    task: str,
    failed_step: dict,
    current_state: dict,
    completed_actions: list[dict],
    error: str,
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

Error:
{error}

Return exactly this JSON shape:
{{
  "action": "click_at",
  "x": 123,
  "y": 456,
  "reason": "brief reason"
}}

Rules:
- Return one click only.
- Do not return selectors, XPath, code, or prose.
- If the target is a ranked item such as first/second/third, count visible cards or rows in natural reading order.
- Avoid clicking login, account, upgrade, download, share, or destructive controls unless the failed step explicitly asks for that.
"""
