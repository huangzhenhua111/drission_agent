from __future__ import annotations

from pathlib import Path

from app.generation.dom_snapshot import DomCandidate, extract_candidates_from_html


ROOT = Path(__file__).resolve().parents[1]


def by_id(candidates: list[DomCandidate], id_value: str) -> DomCandidate:
    for candidate in candidates:
        if candidate.id == id_value:
            return candidate
    raise AssertionError(f"candidate with id={id_value!r} not found")


def test_extracts_local_search_candidates() -> None:
    html = (ROOT / "examples/local_search/site/index.html").read_text(encoding="utf-8")
    candidates = extract_candidates_from_html(html)

    search_input = by_id(candidates, "search-input")
    search_button = by_id(candidates, "search-button")
    first_result = by_id(candidates, "first-result")

    assert search_input.tag == "input"
    assert search_input.name == "q"
    assert search_input.placeholder == "Type keyword"
    assert search_input.selector_candidates[:2] == [
        "@id=search-input",
        "css:#search-input",
    ]

    assert search_button.type == "submit"
    assert "text=Search" in search_button.selector_candidates

    assert first_result.text == "Alpha detail"
    assert "css:a[data-testid='result-alpha']" in first_result.selector_candidates


def test_extracts_local_form_candidates() -> None:
    html = (ROOT / "examples/local_form/site/index.html").read_text(encoding="utf-8")
    candidates = extract_candidates_from_html(html)

    name_input = by_id(candidates, "name-input")
    type_select = by_id(candidates, "type-select")
    upload_input = by_id(candidates, "fixture-file")
    submit_button = by_id(candidates, "submit-button")

    assert "@name=name" in name_input.selector_candidates
    assert "@name=type" in type_select.selector_candidates
    assert upload_input.type == "file"
    assert "css:input[type='file']" in upload_input.selector_candidates
    assert "text=Submit" in submit_button.selector_candidates


def test_hidden_nodes_are_marked_not_visible() -> None:
    html = """
    <button id="visible">Visible</button>
    <button id="hidden-button" class="hidden">Hidden</button>
    <input id="secret" type="hidden" name="secret">
    """
    candidates = extract_candidates_from_html(html)

    assert by_id(candidates, "visible").is_visible is True
    assert by_id(candidates, "hidden-button").is_visible is False
    assert by_id(candidates, "secret").is_visible is False

