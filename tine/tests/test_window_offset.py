"""Tests for tine.window_offset — shared window-origin lookup.

These helpers were originally private to cli.py; they moved to their own
module so describe.py could reuse them for OCR dedup. The original
regression tests in test_cli.py still cover cli's aliased re-exports; these
tests cover the module directly.
"""

import tine.window_offset as wo


class TestNormalizeAppName:
    def test_hyphen_name(self):
        assert wo.normalize_app_name('gnome-calculator') == 'gnomecalculator'

    def test_reverse_domain(self):
        assert wo.normalize_app_name('org.gnome.Calculator') == 'gnomecalculator'

    def test_strip_com_prefix(self):
        assert wo.normalize_app_name('com.github.Foo') == 'githubfoo'

    def test_empty_returns_empty(self):
        assert wo.normalize_app_name('') == ''
        assert wo.normalize_app_name(None) == ''


class TestResolveWindowOffset:
    def _make_windows(self, **overrides):
        w = {
            'title': 'Settings',
            'wm_class': 'org.gnome.Settings',
            'app_id': 'org.gnome.Settings',
            'x': 100, 'y': 50,
            'focused': True,
        }
        w.update(overrides)
        return [w]

    def test_matches_wm_class_substring(self, monkeypatch):
        windows = self._make_windows()
        assert wo.resolve_window_offset('Settings', windows=windows) == (100, 50)

    def test_empty_app_warns_and_returns_origin(self, monkeypatch, capsys):
        result = wo.resolve_window_offset('', windows=[])
        assert result == (0, 0)
        err = capsys.readouterr().err
        assert 'empty _app' in err

    def test_no_match_warns_and_returns_origin(self, capsys):
        windows = self._make_windows(title='Settings', wm_class='gnome-settings')
        result = wo.resolve_window_offset('Firefox', windows=windows)
        assert result == (0, 0)
        err = capsys.readouterr().err
        assert 'no window match' in err

    def test_prefers_focused_window(self):
        windows = [
            {'title': 'Firefox', 'wm_class': 'firefox', 'x': 0, 'y': 0,
             'focused': False},
            {'title': 'Firefox', 'wm_class': 'firefox', 'x': 300, 'y': 200,
             'focused': True},
        ]
        assert wo.resolve_window_offset('firefox', windows=windows) == (300, 200)

    def test_title_fallback(self):
        windows = [{'title': 'Welcome to Reddit — Mozilla Firefox',
                    'wm_class': '', 'app_id': '',
                    'x': 42, 'y': 24, 'focused': True}]
        assert wo.resolve_window_offset('Reddit', windows=windows) == (42, 24)
