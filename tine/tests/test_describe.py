"""Tests for tine describe: combined tree + screenshot for agent orientation.

describe() gives an agent a complete picture of the current desktop state:
- AT-SPI2 tree (text, with refs)
- Screenshot (optionally annotated with Set-of-Mark overlays)
- Window list (positions, focus state)

The output is structured for LLM consumption.
"""

import json
import os
import tempfile

import pytest
from PIL import Image


def test_capture_screenshot_annotate_true_but_empty_cache_still_annotates(monkeypatch, tmp_path):
    """_capture_screenshot should honor annotate=True even with empty ref_cache.

    describe._capture_screenshot currently gates annotation with:
        if annotate and ref_cache:
    which skips calling annotate() when ref_cache == {}.

    Expected behavior: if annotate=True, call annotate() even for an empty cache
    (a no-op overlay is still a valid operation and makes behavior consistent).
    """
    import tine.describe as desc

    calls = []

    def fake_capture(path):
        _make_screenshot(path)
        return path

    def fake_annotate(path, ref_cache, scale=1.0):
        calls.append((path, ref_cache, scale))
        return path

    import tine.screenshot as ss
    monkeypatch.setattr(ss, 'capture', fake_capture)
    monkeypatch.setattr(ss, 'annotate', fake_annotate)

    path = str(tmp_path / 'shot.png')
    desc._capture_screenshot(path, annotate=True, ref_cache={}, scale=2.0)

    assert calls == [(path, {}, 2.0)]


def test_capture_screenshot_annotate_true_with_none_cache_should_not_crash(monkeypatch, tmp_path):
    """If caller passes annotate=True but ref_cache=None, should not TypeError.

    The current code checks truthiness, so None skips annotation; but it still
    should succeed and return the path.
    """
    import tine.describe as desc
    import tine.screenshot as ss

    def fake_capture(path):
        _make_screenshot(path)
        return path

    monkeypatch.setattr(ss, 'capture', fake_capture)

    path = str(tmp_path / 'shot.png')
    assert desc._capture_screenshot(path, annotate=True, ref_cache=None, scale=1.0) == path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_tree_output():
    """Simulated format_tree output."""
    return (
        "frame 'Settings' [ref_1]\n"
        "  list 'Settings categories' [ref_2]\n"
        "    button 'Wi-Fi' [ref_3]\n"
        "    button 'Bluetooth' [ref_4]\n"
        "    button 'Network' [ref_5]"
    )


def _fake_ref_cache():
    return {
        'ref_1': {'role': 'frame', 'name': 'Settings', 'bbox': {'x': 0, 'y': 0, 'w': 980, 'h': 640}},
        'ref_2': {'role': 'list', 'name': 'Settings categories', 'bbox': {'x': 10, 'y': 50, 'w': 200, 'h': 500}},
        'ref_3': {'role': 'button', 'name': 'Wi-Fi', 'bbox': {'x': 10, 'y': 50, 'w': 200, 'h': 40}},
        'ref_4': {'role': 'button', 'name': 'Bluetooth', 'bbox': {'x': 10, 'y': 90, 'w': 200, 'h': 40}},
        'ref_5': {'role': 'button', 'name': 'Network', 'bbox': {'x': 10, 'y': 130, 'w': 200, 'h': 40}},
    }


def _fake_windows():
    return [
        {'title': 'Settings', 'wm_class': 'org.gnome.Settings',
         'app_id': 'org.gnome.Settings', 'x': 278, 'y': 112,
         'width': 980, 'height': 640, 'focused': True},
    ]


# ---------------------------------------------------------------------------
# describe() tests
# ---------------------------------------------------------------------------

class TestDescribe:
    """tine.describe.describe() -> dict with tree, screenshot, windows."""

    def test_describe_returns_tree(self, monkeypatch, tmp_path):
        """describe() includes the AT-SPI2 tree text."""
        # Mock the dependencies
        import tine.describe as desc

        monkeypatch.setattr(desc, '_get_tree', lambda app: _fake_tree_output())
        monkeypatch.setattr(desc, '_get_ref_cache', lambda: _fake_ref_cache())
        monkeypatch.setattr(desc, '_get_windows', lambda: _fake_windows())
        monkeypatch.setattr(desc, '_capture_screenshot', lambda path, **kw: _make_screenshot(path))

        result = desc.describe()

        assert 'tree' in result
        assert 'Settings' in result['tree']
        assert 'ref_3' in result['tree']

    def test_describe_returns_screenshot_path(self, monkeypatch, tmp_path):
        """describe() includes path to screenshot file."""
        import tine.describe as desc

        monkeypatch.setattr(desc, '_get_tree', lambda app: _fake_tree_output())
        monkeypatch.setattr(desc, '_get_ref_cache', lambda: _fake_ref_cache())
        monkeypatch.setattr(desc, '_get_windows', lambda: _fake_windows())
        monkeypatch.setattr(desc, '_capture_screenshot', lambda path, **kw: _make_screenshot(path))

        result = desc.describe()

        assert 'screenshot' in result
        assert result['screenshot'].endswith('.png')

    def test_describe_returns_windows(self, monkeypatch, tmp_path):
        """describe() includes window list."""
        import tine.describe as desc

        monkeypatch.setattr(desc, '_get_tree', lambda app: _fake_tree_output())
        monkeypatch.setattr(desc, '_get_ref_cache', lambda: _fake_ref_cache())
        monkeypatch.setattr(desc, '_get_windows', lambda: _fake_windows())
        monkeypatch.setattr(desc, '_capture_screenshot', lambda path, **kw: _make_screenshot(path))

        result = desc.describe()

        assert 'windows' in result
        assert len(result['windows']) == 1
        assert result['windows'][0]['title'] == 'Settings'
        assert result['windows'][0]['focused'] is True

    def test_describe_returns_ref_cache(self, monkeypatch, tmp_path):
        """describe() includes the ref cache for downstream use."""
        import tine.describe as desc

        monkeypatch.setattr(desc, '_get_tree', lambda app: _fake_tree_output())
        monkeypatch.setattr(desc, '_get_ref_cache', lambda: _fake_ref_cache())
        monkeypatch.setattr(desc, '_get_windows', lambda: _fake_windows())
        monkeypatch.setattr(desc, '_capture_screenshot', lambda path, **kw: _make_screenshot(path))

        result = desc.describe()

        assert 'ref_cache' in result
        assert 'ref_3' in result['ref_cache']

    def test_describe_with_app_filter(self, monkeypatch, tmp_path):
        """describe(app='Settings') passes app filter to tree walker."""
        import tine.describe as desc

        captured_app = []
        def fake_tree(app=None):
            captured_app.append(app)
            return _fake_tree_output()

        monkeypatch.setattr(desc, '_get_tree', fake_tree)
        monkeypatch.setattr(desc, '_get_ref_cache', lambda: _fake_ref_cache())
        monkeypatch.setattr(desc, '_get_windows', lambda: _fake_windows())
        monkeypatch.setattr(desc, '_capture_screenshot', lambda path, **kw: _make_screenshot(path))

        desc.describe(app='Settings')

        assert captured_app == ['Settings']

    def test_describe_with_annotate(self, monkeypatch, tmp_path):
        """describe(annotate=True) forwards annotate, ref_cache, and scale."""
        import tine.describe as desc

        capture_kwargs = []
        def fake_capture(path, **kw):
            capture_kwargs.append(kw)
            return _make_screenshot(path)

        monkeypatch.setattr(desc, '_get_tree', lambda app: _fake_tree_output())
        monkeypatch.setattr(desc, '_get_ref_cache', lambda: _fake_ref_cache())
        monkeypatch.setattr(desc, '_get_windows', lambda: _fake_windows())
        monkeypatch.setattr(desc, '_capture_screenshot', fake_capture)

        desc.describe(annotate=True, scale=2.0)

        assert len(capture_kwargs) == 1
        assert capture_kwargs[0]['annotate'] is True
        assert capture_kwargs[0]['scale'] == 2.0
        assert capture_kwargs[0]['ref_cache'] == _fake_ref_cache()

    def test_describe_no_windows(self, monkeypatch, tmp_path):
        """describe() works when no windows are open."""
        import tine.describe as desc

        monkeypatch.setattr(desc, '_get_tree', lambda app: '')
        monkeypatch.setattr(desc, '_get_ref_cache', lambda: {})
        monkeypatch.setattr(desc, '_get_windows', lambda: [])
        monkeypatch.setattr(desc, '_capture_screenshot', lambda path, **kw: _make_screenshot(path))

        result = desc.describe()

        assert result['windows'] == []
        assert result['tree'] == ''
        assert result['ref_cache'] == {}


# ---------------------------------------------------------------------------
# format_for_agent() tests
# ---------------------------------------------------------------------------

class TestFormatForAgent:
    """tine.describe.format_for_agent(describe_result) -> string for LLM."""

    def test_format_includes_tree(self):
        from tine.describe import format_for_agent

        result = {
            'tree': _fake_tree_output(),
            'screenshot': '/tmp/test.png',
            'windows': _fake_windows(),
            'ref_cache': _fake_ref_cache(),
        }

        text = format_for_agent(result)

        assert 'Settings' in text
        assert 'ref_3' in text
        assert 'Wi-Fi' in text

    def test_format_includes_windows(self):
        from tine.describe import format_for_agent

        result = {
            'tree': _fake_tree_output(),
            'screenshot': '/tmp/test.png',
            'windows': _fake_windows(),
            'ref_cache': _fake_ref_cache(),
        }

        text = format_for_agent(result)

        assert 'Settings' in text
        assert '980' in text or 'width' in text.lower()
        assert 'focused' in text.lower()

    def test_format_includes_screenshot_path(self):
        from tine.describe import format_for_agent

        result = {
            'tree': '',
            'screenshot': '/tmp/shot.png',
            'windows': [],
            'ref_cache': {},
        }

        text = format_for_agent(result)
        assert '/tmp/shot.png' in text

    def test_format_empty_state(self):
        """format_for_agent handles empty desktop state."""
        from tine.describe import format_for_agent

        result = {
            'tree': '',
            'screenshot': '/tmp/shot.png',
            'windows': [],
            'ref_cache': {},
        }

        text = format_for_agent(result)
        # Should produce valid output, not crash
        assert isinstance(text, str)
        assert len(text) > 0

    def test_format_partial_window_dict(self):
        """format_for_agent tolerates window dicts with missing keys."""
        from tine.describe import format_for_agent

        result = {
            'tree': '',
            'screenshot': '/tmp/shot.png',
            'windows': [{'app_id': 'org.gnome.Terminal'}],  # missing most keys
            'ref_cache': {},
        }

        text = format_for_agent(result)
        assert 'Terminal' in text
        assert '?' in text  # missing dimensions shown as ?


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_screenshot(path):
    """Create a tiny test screenshot."""
    Image.new('RGB', (100, 100)).save(path)
    return path
