"""Tests for describe() OCR integration and format_for_agent text-ref output."""

import os

import pytest
from PIL import Image


def _make_screenshot(path):
    Image.new('RGB', (100, 100), color='white').save(path)
    return path


def _fake_raw_tree(app_name='Calculator'):
    return {
        'role': 'application',
        'name': app_name,
        'bbox': {'x': 0, 'y': 0, 'w': 800, 'h': 600},
        'states': [], 'actions': [], 'children': [],
    }


class TestDescribeOcr:
    def test_describe_without_ocr_returns_empty_text_ref_order(self, monkeypatch, tmp_path):
        import tine.describe as desc

        monkeypatch.setattr(
            desc, '_get_tree',
            lambda app=None: ('', {}, 'Calculator'),
        )
        monkeypatch.setattr(desc, '_get_windows', lambda: [])
        monkeypatch.setattr(desc, '_capture_screenshot',
                            lambda path, **kw: _make_screenshot(path))

        result = desc.describe()

        assert result['text_ref_order'] == []
        assert result['app_name'] == 'Calculator'

    def test_describe_with_ocr_merges_ref_tn(self, monkeypatch, tmp_path):
        import tine.describe as desc
        import tine.ocr as ocr_mod
        import tine.window_offset

        monkeypatch.setattr(
            desc, '_get_tree',
            lambda app=None: ('', {}, 'Calculator'),
        )
        monkeypatch.setattr(desc, '_get_windows', lambda: [])
        monkeypatch.setattr(desc, '_capture_screenshot',
                            lambda path, **kw: _make_screenshot(path))
        monkeypatch.setattr(tine.window_offset, 'resolve_window_offset',
                            lambda app, **kw: (0, 0))

        def fake_detect(image_path, scale=1.0, min_confidence=0.8):
            return [{
                'text': 'Log In',
                'bbox': {'x': 920, 'y': 292, 'w': 79, 'h': 32,
                         'coord': 'screen'},
                'confidence': 0.99,
            }]

        monkeypatch.setattr(ocr_mod, 'detect_text', fake_detect)

        result = desc.describe(ocr=True)

        assert result['text_ref_order'] == ['ref_t1']
        assert 'ref_t1' in result['ref_cache']
        assert result['ref_cache']['ref_t1']['text'] == 'Log In'

    def test_describe_ocr_dedupes_against_atspi(self, monkeypatch, tmp_path):
        """A duplicated 'Log In' ref should not show up twice."""
        import tine.describe as desc
        import tine.ocr as ocr_mod
        import tine.window_offset

        atspi_cache = {
            'ref_1': {'role': 'button', 'name': 'Log In',
                      'bbox': {'x': 920, 'y': 292, 'w': 79, 'h': 32,
                               'coord': 'screen'}},
        }
        monkeypatch.setattr(
            desc, '_get_tree',
            lambda app=None: ('', dict(atspi_cache), 'Firefox'),
        )
        monkeypatch.setattr(desc, '_get_windows', lambda: [])
        monkeypatch.setattr(desc, '_capture_screenshot',
                            lambda path, **kw: _make_screenshot(path))
        monkeypatch.setattr(tine.window_offset, 'resolve_window_offset',
                            lambda app, **kw: (0, 0))

        def fake_detect(image_path, scale=1.0, min_confidence=0.8):
            return [{
                'text': 'LogIn',  # PP-OCR whitespace-eaten form
                'bbox': {'x': 940, 'y': 300, 'w': 40, 'h': 20,
                         'coord': 'screen'},
                'confidence': 0.99,
            }]

        monkeypatch.setattr(ocr_mod, 'detect_text', fake_detect)

        result = desc.describe(ocr=True)

        # AT-SPI2 ref survives, OCR ref is dropped
        assert 'ref_1' in result['ref_cache']
        assert result['text_ref_order'] == []

    def test_format_for_agent_emits_detected_text_section(self):
        from tine.describe import format_for_agent

        result = {
            'tree': '',
            'screenshot': '/tmp/x.png',
            'windows': [],
            'ref_cache': {
                'ref_t1': {'text': 'Log In',
                           'bbox': {'x': 920, 'y': 292, 'w': 79, 'h': 32},
                           'confidence': 0.99},
            },
            'text_ref_order': ['ref_t1'],
        }
        out = format_for_agent(result)
        assert '## Detected Text' in out
        assert 'ref_t1' in out
        assert 'Log In' in out

    def test_format_for_agent_omits_section_when_no_text_refs(self):
        from tine.describe import format_for_agent

        result = {
            'tree': '',
            'screenshot': '/tmp/x.png',
            'windows': [],
            'ref_cache': {},
            'text_ref_order': [],
        }
        out = format_for_agent(result)
        assert '## Detected Text' not in out


class TestNextTextRefIndex:
    def test_empty_cache_returns_one(self):
        import tine.ocr as ocr_mod
        assert ocr_mod.next_text_ref_index({}) == 1

    def test_ignores_atspi_refs(self):
        import tine.ocr as ocr_mod
        cache = {'ref_1': {}, 'ref_2': {}, '_app': 'foo'}
        assert ocr_mod.next_text_ref_index(cache) == 1

    def test_returns_max_plus_one(self):
        import tine.ocr as ocr_mod
        cache = {'ref_t1': {}, 'ref_t5': {}, 'ref_t3': {}}
        assert ocr_mod.next_text_ref_index(cache) == 6
