import types

import pytest

import tine.cli as cli


BUG_MARKER = "cli_audit_regression"


# Originally this regression test covered a regex bug that parsed app names
# from format_tree output with single quotes but failed on double quotes.
# That regex is gone: describe() now returns app_name directly from the
# walker. The same invariant — CLI must persist _app in the saved cache —
# rides on the structured field instead of a text parse.

def test_cmd_describe_persists_app_name_in_cache(monkeypatch):
    saved = {}

    def fake_describe(app=None, annotate=False, scale=1.0, ocr=False,
                      window_offset=(0, 0)):
        return {
            "tree": 'application "Calculator"\n  button "1"',
            "ref_cache": {"ref_1": {"bbox": {"x": 1, "y": 2, "w": 3, "h": 4}}},
            "app_name": "Calculator",
            "windows": [],
            "screenshot": "/tmp/x.png",
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

    args = types.SimpleNamespace(app=None, annotate=False, scale=1.0, ocr=False)
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
