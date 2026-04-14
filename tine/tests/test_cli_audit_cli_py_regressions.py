import argparse
import pytest

import tine.cli as cli


def test_cmd_describe_copies_app_name_from_describe_result(monkeypatch, tmp_path):
    """_cmd_describe should copy app_name from describe()'s result into _app.

    Originally this regression test covered a re.match bug that failed on
    trees with a leading newline. That regex parse has been removed:
    describe() now returns app_name directly from the walker. The invariant
    is the same — _app must land in the saved ref cache — but it rides on a
    structured field instead of a text parse.
    """
    cache_path = tmp_path / "refs.json"
    monkeypatch.setenv("TINE_REF_CACHE", str(cache_path))

    def fake_describe(app, annotate, scale, ocr=False, window_offset=(0, 0)):
        return {
            "tree": "\napplication 'MyApp'\n  window 'x'",
            "ref_cache": {"ref_1": {"bbox": {"x": 1, "y": 2, "w": 3, "h": 4}}},
            "app_name": "MyApp",
            "windows": [],
            "screenshot": "/tmp/x.png",
        }

    monkeypatch.setattr(cli.tine.describe, "describe", fake_describe)
    monkeypatch.setattr(cli.tine.describe, "format_for_agent", lambda result: "OUT")

    saved = {}

    def fake_save_ref_cache(cache, path):
        saved["cache"] = cache
        saved["path"] = path

    monkeypatch.setattr(cli.tine.refs, "save_ref_cache", fake_save_ref_cache)

    args = argparse.Namespace(app=None, annotate=False, scale=1.0, ocr=False)
    cli._cmd_describe(args)

    assert saved["cache"]["_app"] == "MyApp"


def test_cmd_tree_metadata_app_name_should_not_be_empty(monkeypatch, tmp_path):
    """BUG: _cmd_tree stores raw_tree['name'] under _app.

    AT-SPI trees typically use 'application'/'app' naming; if walker returns
    no 'name' field, _app becomes empty and later offset resolution silently
    degrades.

    Expected behavior: _app should be derived from application node name when present.
    """
    cache_path = tmp_path / "refs.json"
    monkeypatch.setenv("TINE_REF_CACHE", str(cache_path))

    raw_tree = {
        "application": {"name": "RealApp"},
        # intentionally no top-level 'name'
    }

    monkeypatch.setattr(cli, "_walk_atspi", lambda app=None: raw_tree)
    monkeypatch.setattr(cli.tine.tree, "collapse_containers", lambda t: t)
    monkeypatch.setattr(cli.tine.tree, "assign_refs", lambda t: (t, {"ref_1": {"bbox": {}}}))
    monkeypatch.setattr(cli.tine.tree, "format_tree", lambda t: "TREE")

    saved = {}

    def fake_save_ref_cache(cache, path):
        saved["cache"] = cache

    monkeypatch.setattr(cli.tine.refs, "save_ref_cache", fake_save_ref_cache)

    args = argparse.Namespace(app=None)
    cli._cmd_tree(args)

    # Fails today: stores '' because raw_tree.get('name','') is empty.
    assert saved["cache"]["_app"] == "RealApp"
