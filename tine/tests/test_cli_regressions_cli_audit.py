import types

import pytest

import tine.cli as cli


BUG_MARKER = "cli_audit_regression"


# BUG: _cmd_describe parses app name from tree text with a regex that expects
# single quotes, but the formatted tree output uses double quotes.
# This causes ref_cache['_app'] to be empty even when the application name is present.
# LINE: tine/cli.py:285-288
# WHY: re.match(r"application\s+'([^']*)'", tree_text) fails when tree contains
#      application \"Name\" (double quotes), dropping metadata used to resolve offsets.

def test_cmd_describe_extracts_app_name_from_tree_text_double_quotes(monkeypatch):
    saved = {}

    def fake_describe(app=None, annotate=False, scale=1.0):
        return {
            "tree": 'application "Calculator"\n  button "1"',
            "ref_cache": {"ref_1": {"bbox": {"x": 1, "y": 2, "w": 3, "h": 4}}},
        }

    def fake_format_for_agent(result):
        return "FORMATTED"

    def fake_save_ref_cache(ref_cache, path):
        saved["ref_cache"] = ref_cache
        saved["path"] = path

    monkeypatch.setattr(cli.tine.describe, "describe", fake_describe)
    monkeypatch.setattr(cli.tine.describe, "format_for_agent", fake_format_for_agent)
    monkeypatch.setattr(cli.tine.refs, "save_ref_cache", fake_save_ref_cache)
    monkeypatch.setattr(cli, "_ref_cache_path", lambda: "/tmp/tine-refs.json")

    args = types.SimpleNamespace(app=None, annotate=False, scale=1.0)
    cli._cmd_describe(args)

    assert saved["ref_cache"]["_app"] == "Calculator"


# BUG: _cmd_click coordinate parsing rejects negative coordinates.
# LINE: tine/cli.py:200
# WHY: regex '^\d+,\d+$' only allows positive ints; negative screen coordinates
#      can occur on multi-monitor setups with origin not at top-left of all displays.

def test_cmd_click_allows_negative_coordinates(monkeypatch):
    called = {}

    def fake_click(x, y, button="left", double=False):
        called["x"] = x
        called["y"] = y
        called["button"] = button
        called["double"] = double

    monkeypatch.setattr(cli.tine.input, "click", fake_click)

    args = types.SimpleNamespace(target="-1,20", button="left", double=False)

    # should not sys.exit; should parse and call tine.input.click
    cli._cmd_click(args)

    assert called == {"x": -1, "y": 20, "button": "left", "double": False}
