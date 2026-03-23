import argparse
import os
import re
import pytest

import tine.cli as cli


BUG_APP_RE = re.compile(r"""application\s+['\"]([^'\"]*)['\"]""")


def test_cmd_describe_extracts_app_name_even_with_leading_newline(monkeypatch, tmp_path):
    """BUG: _cmd_describe uses re.match, so it fails if tree starts with a newline.

    Expected behavior: app name extraction should work on typical multi-line trees
    even if there is a leading newline/whitespace before the first line.
    """
    cache_path = tmp_path / "refs.json"
    monkeypatch.setenv("TINE_REF_CACHE", str(cache_path))

    def fake_describe(app, annotate, scale):
        return {
            "tree": "\napplication 'MyApp'\n  window 'x'",
            "ref_cache": {"ref_1": {"bbox": {"x": 1, "y": 2, "w": 3, "h": 4}}},
        }

    monkeypatch.setattr(cli.tine.describe, "describe", fake_describe)
    monkeypatch.setattr(cli.tine.describe, "format_for_agent", lambda result: "OUT")

    saved = {}

    def fake_save_ref_cache(cache, path):
        saved["cache"] = cache
        saved["path"] = path

    monkeypatch.setattr(cli.tine.refs, "save_ref_cache", fake_save_ref_cache)

    args = argparse.Namespace(app=None, annotate=False, scale=1.0)
    cli._cmd_describe(args)

    # Fails today: app_name becomes '' because re.match doesn't match after leading newline.
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
