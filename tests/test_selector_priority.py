from __future__ import annotations

from app.resilience.selectors import (
    build_selector_candidates,
    dedupe_selectors,
    is_fragile_xpath,
    is_overbroad_selector,
    is_volatile_id,
    normalize_text,
)


def test_build_selector_candidates_orders_stable_selectors_first() -> None:
    selectors = build_selector_candidates(
        tag="button",
        text="Submit",
        id_value="submit-button",
        name="submit",
        type_value="submit",
        data_attrs={"data-testid": "submit-primary"},
    )

    assert selectors[0] == "@id=submit-button"
    assert "@name=submit" in selectors
    assert "css:button[data-testid='submit-primary']" in selectors
    assert "text=Submit" in selectors
    assert "css:button" not in selectors


def test_fragile_xpath_and_overbroad_selectors_are_detected() -> None:
    assert is_fragile_xpath("/html/body/div[1]/button[1]")
    assert is_fragile_xpath("xpath:/html/body/main/button")
    assert not is_fragile_xpath("xpath://button[@type='submit']")
    assert is_overbroad_selector("css:button")
    assert not is_overbroad_selector("css:button[type='submit']")


def test_dedupe_selectors_preserves_order_and_drops_fragile_xpath() -> None:
    selectors = dedupe_selectors(
        ["@id=save", "@id=save", "/html/body/button[1]", "css:button[type='submit']"]
    )

    assert selectors == ["@id=save", "css:button[type='submit']"]


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  Alpha\n  detail\t ") == "Alpha detail"
    assert normalize_text("   ") is None


def test_build_selector_candidates_demotes_volatile_generated_ids() -> None:
    selectors = build_selector_candidates(
        tag="button",
        text="My Library",
        id_value="base-ui-_r_6c_",
        role="tab",
        type_value="button",
    )

    assert is_volatile_id("base-ui-_r_6c_")
    assert not is_volatile_id("submit-button")
    assert "text=My Library" in selectors
    assert selectors.index("text=My Library") < selectors.index("@id=base-ui-_r_6c_")
