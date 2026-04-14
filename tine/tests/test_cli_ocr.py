"""Tests for --ocr CLI wiring on screenshot, describe, and click ref_tN."""

import json
import os

import pytest


def _save_cache(path, cache):
    with open(path, 'w') as f:
        json.dump(cache, f)


class TestClickRefT:
    def test_click_text_ref_uses_screen_coords_no_offset(self, monkeypatch, tmp_path):
        """ref_tN is always screen-absolute; click_ref should receive offset=(0,0)."""
        cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', cache_path)
        _save_cache(cache_path, {
            '_app': 'firefox',
            'ref_t1': {
                'text': 'Log In',
                'bbox': {'x': 920, 'y': 292, 'w': 79, 'h': 32,
                         'coord': 'screen'},
                'confidence': 0.99,
            },
        })

        click_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_calls.append({
                'ref_id': ref_id,
                'window_offset': window_offset,
            })

        import tine.cli as cli_mod
        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        # Stub out offset resolution so we don't actually hit list_windows.
        monkeypatch.setattr(cli_mod, '_resolve_window_offset',
                            lambda app, **kw: (999, 999))

        cli_mod.main(['click', 'ref_t1'])

        assert len(click_calls) == 1
        assert click_calls[0]['ref_id'] == 'ref_t1'
        # Critical: text refs are screen-absolute, so window_offset must be (0,0)
        assert click_calls[0]['window_offset'] == (0, 0)

    def test_click_ref_n_still_resolves_window_offset(self, monkeypatch, tmp_path):
        """Regression: AT-SPI2 refs keep their window-offset resolution."""
        cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', cache_path)
        _save_cache(cache_path, {
            '_app': 'calculator',
            'ref_1': {
                'role': 'button', 'name': '1',
                'bbox': {'x': 10, 'y': 10, 'w': 30, 'h': 30},
            },
        })

        click_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_calls.append({'window_offset': window_offset})

        import tine.cli as cli_mod
        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        monkeypatch.setattr(cli_mod, '_resolve_window_offset',
                            lambda app, **kw: (300, 200))

        cli_mod.main(['click', 'ref_1'])

        assert click_calls[0]['window_offset'] == (300, 200)


class TestDescribeOcrFlag:
    def test_ocr_flag_forwarded(self, monkeypatch, tmp_path):
        cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', cache_path)

        captured = {}

        def fake_describe(app=None, annotate=False, scale=1.0, ocr=False):
            captured['ocr'] = ocr
            return {
                'tree': '', 'screenshot': '/tmp/x.png',
                'windows': [], 'ref_cache': {}, 'app_name': '',
                'text_ref_order': [],
            }

        import tine.cli as cli_mod
        import tine.describe
        monkeypatch.setattr(tine.describe, 'describe', fake_describe)

        cli_mod.main(['describe', '--ocr'])

        assert captured['ocr'] is True


class TestScreenshotOcrFlag:
    def test_screenshot_ocr_merges_ref_tn_into_cache(self, monkeypatch, tmp_path):
        cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', cache_path)
        _save_cache(cache_path, {'_app': 'firefox'})

        shot_path = str(tmp_path / 'shot.png')

        import tine.cli as cli_mod
        import tine.screenshot
        import tine.ocr as ocr_mod

        monkeypatch.setattr(tine.screenshot, 'capture',
                            lambda output_path=None: shot_path)
        monkeypatch.setattr(cli_mod, '_resolve_window_offset',
                            lambda app, **kw: (0, 0))

        def fake_detect(image_path, scale=1.0, min_confidence=0.8):
            return [{
                'text': 'Welcome',
                'bbox': {'x': 10, 'y': 20, 'w': 80, 'h': 20,
                         'coord': 'screen'},
                'confidence': 0.97,
            }]

        monkeypatch.setattr(ocr_mod, 'detect_text', fake_detect)

        cli_mod.main(['screenshot', '--ocr', '--scale', '1.0'])

        with open(cache_path) as f:
            saved = json.load(f)
        assert 'ref_t1' in saved
        assert saved['ref_t1']['text'] == 'Welcome'
        # _app preserved
        assert saved['_app'] == 'firefox'

    def test_screenshot_ocr_without_existing_cache_creates_one(
            self, monkeypatch, tmp_path):
        cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', cache_path)

        shot_path = str(tmp_path / 'shot.png')

        import tine.cli as cli_mod
        import tine.screenshot
        import tine.ocr as ocr_mod

        monkeypatch.setattr(tine.screenshot, 'capture',
                            lambda output_path=None: shot_path)

        def fake_detect(image_path, scale=1.0, min_confidence=0.8):
            return [{
                'text': 'hello',
                'bbox': {'x': 0, 'y': 0, 'w': 40, 'h': 20, 'coord': 'screen'},
                'confidence': 0.9,
            }]

        monkeypatch.setattr(ocr_mod, 'detect_text', fake_detect)

        cli_mod.main(['screenshot', '--ocr', '--scale', '1.0'])

        with open(cache_path) as f:
            saved = json.load(f)
        assert 'ref_t1' in saved


class TestRefIdPattern:
    def test_matches_atspi_ref(self):
        from tine.refs import REF_ID_PATTERN, is_text_ref
        assert REF_ID_PATTERN.match('ref_1')
        assert REF_ID_PATTERN.match('ref_99')
        assert not is_text_ref('ref_1')

    def test_matches_text_ref(self):
        from tine.refs import REF_ID_PATTERN, is_text_ref
        assert REF_ID_PATTERN.match('ref_t1')
        assert REF_ID_PATTERN.match('ref_t42')
        assert is_text_ref('ref_t1')

    def test_rejects_bad_formats(self):
        from tine.refs import REF_ID_PATTERN
        assert not REF_ID_PATTERN.match('ref_')
        assert not REF_ID_PATTERN.match('refs_1')
        assert not REF_ID_PATTERN.match('ref_1_')
        assert not REF_ID_PATTERN.match('ref_t')
