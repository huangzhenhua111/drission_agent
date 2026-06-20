from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.resilience.selectors import build_selector_candidates, normalize_text


INTERACTIVE_TAGS = {"a", "button", "input", "textarea", "select", "option", "label"}
INTERACTIVE_ATTRS = {"role", "onclick", "tabindex"}
SKIPPED_TAGS = {"script", "style", "noscript", "template", "meta", "link"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "source", "track", "wbr"}


@dataclass(frozen=True)
class DomCandidate:
    candidate_id: str
    tag: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    type: str | None = None
    role: str | None = None
    aria_label: str | None = None
    placeholder: str | None = None
    value: str | None = None
    data_attrs: dict[str, str] = field(default_factory=dict)
    is_visible: bool = True
    selector_candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "tag": self.tag,
            "text": self.text,
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "role": self.role,
            "aria_label": self.aria_label,
            "placeholder": self.placeholder,
            "value": self.value,
            "data_attrs": dict(self.data_attrs),
            "is_visible": self.is_visible,
            "selector_candidates": list(self.selector_candidates),
        }


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    hidden: bool
    text_parts: list[str] = field(default_factory=list)


class _CandidateHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[_Node] = []
        self._raw_candidates: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {name.lower(): value or "" for name, value in attrs}
        parent_hidden = self._stack[-1].hidden if self._stack else False
        hidden = parent_hidden or _is_hidden(tag, attr_map)
        node = _Node(tag=tag, attrs=attr_map, hidden=hidden)
        if tag in VOID_TAGS:
            if _is_candidate_node(node):
                self._raw_candidates.append(node)
            return
        self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in VOID_TAGS:
            return
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        tag = tag.lower()
        node = self._pop_until(tag)
        if node is None:
            return
        if _is_candidate_node(node):
            self._raw_candidates.append(node)
        if self._stack and node.text_parts:
            self._stack[-1].text_parts.extend(node.text_parts)

    def handle_data(self, data: str) -> None:
        if self._stack and data:
            self._stack[-1].text_parts.append(data)

    def close(self) -> None:
        super().close()
        while self._stack:
            node = self._stack.pop()
            if _is_candidate_node(node):
                self._raw_candidates.append(node)

    def candidates(self) -> list[DomCandidate]:
        result: list[DomCandidate] = []
        for index, node in enumerate(self._raw_candidates, start=1):
            candidate = _node_to_candidate(node, index)
            if candidate.selector_candidates:
                result.append(candidate)
        return result

    def _pop_until(self, tag: str) -> _Node | None:
        popped: list[_Node] = []
        while self._stack:
            node = self._stack.pop()
            popped.append(node)
            if node.tag == tag:
                for child in reversed(popped[:-1]):
                    node.text_parts.extend(child.text_parts)
                return node
        return None


def extract_candidates_from_html(html: str) -> list[DomCandidate]:
    parser = _CandidateHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.candidates()


def _is_hidden(tag: str, attrs: dict[str, str]) -> bool:
    if tag in SKIPPED_TAGS:
        return True
    if "hidden" in attrs:
        return True
    if attrs.get("type", "").lower() == "hidden":
        return True
    style = attrs.get("style", "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    classes = {part.strip().lower() for part in attrs.get("class", "").split()}
    return "hidden" in classes or "sr-only" in classes


def _is_candidate_node(node: _Node) -> bool:
    if node.tag in SKIPPED_TAGS:
        return False
    if node.tag in INTERACTIVE_TAGS:
        return True
    if any(attr in node.attrs for attr in INTERACTIVE_ATTRS):
        return True
    return any(name.startswith("data-") for name in node.attrs)


def _node_to_candidate(node: _Node, index: int) -> DomCandidate:
    data_attrs = {name: value for name, value in node.attrs.items() if name.startswith("data-")}
    text = normalize_text(" ".join(node.text_parts))
    selectors = build_selector_candidates(
        tag=node.tag,
        text=text,
        id_value=node.attrs.get("id"),
        name=node.attrs.get("name"),
        type_value=node.attrs.get("type"),
        role=node.attrs.get("role"),
        aria_label=node.attrs.get("aria-label"),
        placeholder=node.attrs.get("placeholder"),
        value=node.attrs.get("value"),
        data_attrs=data_attrs,
    )
    return DomCandidate(
        candidate_id=f"e{index}",
        tag=node.tag,
        text=text,
        id=node.attrs.get("id"),
        name=node.attrs.get("name"),
        type=node.attrs.get("type"),
        role=node.attrs.get("role"),
        aria_label=node.attrs.get("aria-label"),
        placeholder=node.attrs.get("placeholder"),
        value=node.attrs.get("value"),
        data_attrs=data_attrs,
        is_visible=not node.hidden,
        selector_candidates=selectors,
    )
