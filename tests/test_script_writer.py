from __future__ import annotations

from app.generation.script_writer import ScriptWriter
from app.validation.static_checks import validate_generated_script


def test_script_writer_renders_standalone_drission_script() -> None:
    script = ScriptWriter().render(
        [
            {
                "step_index": 0,
                "type": "goto",
                "target": "Example",
                "url": "https://example.com",
                "fallback_selectors": [],
            },
            {
                "step_index": 1,
                "type": "click",
                "target": "Submit",
                "chosen_selector": "text=Submit",
                "fallback_selectors": ["text=Submit", "css:button[type='submit']"],
                "selector_metadata": [
                    {"selector": "text=Submit", "index": 0, "match_count": 1, "unique": True}
                ],
            },
            {
                "step_index": 2,
                "type": "click",
                "target": "Visual fallback",
                "visual_position": {"x": 100, "y": 200},
                "fallback_selectors": [],
                "selector_metadata": [],
            },
            {
                "step_index": 3,
                "type": "upload",
                "target": "Add Image",
                "path": "fixture.png",
                "fallback_selectors": ["css:input[type='file']"],
                "selector_metadata": [
                    {
                        "selector": "css:input[type='file']",
                        "index": 1,
                        "match_count": 2,
                        "unique": False,
                    }
                ],
            },
        ],
        browser_user_data_path=r"C:\workspace\drission_agent\outputs\browser_profiles\chrome",
        browser_debug_port=19222,
        wait_for_login=True,
        login_timeout_seconds=123,
        action_delay_seconds=1.5,
    )

    compile(script, "generated_script.py", "exec")
    assert "from app" not in script
    assert "def find_first(" in script
    assert "selector_metadata" in script
    assert "css:input[type='file']" in script
    assert "DEFAULT_BROWSER_USER_DATA_PATH" in script
    assert "DEFAULT_BROWSER_DEBUG_PORT = 19222" in script
    assert "DEFAULT_WAIT_FOR_LOGIN = True" in script
    assert "DEFAULT_LOGIN_TIMEOUT_SECONDS = 123" in script
    assert "DEFAULT_ACTION_DELAY_SECONDS = 1.5" in script
    assert "ACTION_DELAY_SECONDS" in script
    assert "METRICS_PATH" in script
    assert '"llm_calls"' in script
    assert '"total": 0' in script
    assert "write_metrics(page, success=True" in script
    assert "visual_coordinate_clicks" in script
    assert "pure_execution_seconds" in script
    assert '"postconditions": []' in script
    assert "wait_until_timeline_media_present(page)" in script
    assert "wait_until_visible_text(page" in script
    assert "DEFAULT_FAST_SELECTOR_TIMEOUT = 0.3" in script
    assert "def ordered_selectors(" in script
    assert "def is_volatile_selector(" in script
    assert "selector_lookup_timeout(selector, timeout)" in script
    assert "def ensure_authenticated(" in script
    assert "def click_at(" in script
    assert "def preview_click_target(" in script
    assert "def verify_action_postcondition(" in script
    assert "Target ready:" in script
    assert "visual_position" in script
    assert "auto_port" not in script
    assert "browser_profiles" in script
    assert 'shutil.which(executable)' in script
    assert 'options.headless(env_bool("BROWSER_HEADLESS"))' in script
    assert "BASE_DIR / upload_path" in script
    assert validate_generated_script(script) == []


def test_script_writer_supports_complex_editor_actions() -> None:
    script = ScriptWriter().render(
        [
            {"type": "press_key", "target": "selected clip", "value": "DELETE"},
            {"type": "press_key", "target": "text object", "value": "Control+A"},
            {
                "type": "double_click",
                "target": "sample.mp4",
                "fallback_selectors": ["css:.media-card"],
            },
            {
                "type": "set_range",
                "target": "Speed",
                "value": "1.5",
                "fallback_selectors": ["css:#speed"],
            },
            {
                "type": "set_timecode",
                "target": "Trim start",
                "value": "00:02.00",
                "fallback_selectors": ["css:.time-stepper"],
            },
            {
                "type": "drag",
                "target": "Trim handle",
                "delta_x": 40,
                "delta_y": 0,
                "duration": 0.8,
                "fallback_selectors": ["css:.trim-handle"],
            },
        ]
    )

    compile(script, "generated_complex_script.py", "exec")
    assert 'action_type == "press_key"' in script
    assert 'action_type == "double_click"' in script
    assert 'action_type == "set_range"' in script
    assert 'action_type == "set_timecode"' in script
    assert 'action_type == "drag"' in script
    assert "def press_key(" in script
    assert "def dispatch_key_chord(" in script
    assert "normalize_key_chord(normalized)" in script
    assert "def set_timecode(" in script
    assert '"deleteBackward"' in script
    assert "this.dispatchEvent(new Event('input'" in script
    assert "ele.drag(" in script
    assert 'contenteditable == "true"' in script
    assert 'page._run_cdp("Input.insertText", text=value)' in script
    assert "ele.input(value, clear=True)" in script
