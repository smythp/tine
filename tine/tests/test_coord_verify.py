"""Tests for the coord-verification layer in tine.ocr / tine.describe.

Three levels of behaviour are exercised here:

  * matched pair within tolerance → coord_verified = 'true'
  * matched pair outside tolerance → coord_verified = 'false' with
    a ``disputed_by`` pointer to the OCR ref_tN
  * ambiguous groups → coord_verified = 'unknown'
  * duplicate-name groups with matching counts pair by (y, x) sort

The fake bboxes deliberately mirror the Firefox bug from
finding-20260415-g4qq so we know the real failure mode is covered.
"""

from PIL import Image

import tine.describe as desc
import tine.ocr as ocr_mod
import tine.window_offset


def _make_screenshot(path):
    Image.new('RGB', (100, 100), color='white').save(path)
    return path


def _bbox(x, y, w=80, h=20, coord='screen'):
    return {'x': x, 'y': y, 'w': w, 'h': h, 'coord': coord}


def _det(text, x, y, w=80, h=20):
    return {'text': text, 'bbox': _bbox(x, y, w, h),
            'confidence': 0.99}


class TestVerifyAtspiCoordsUnit:
    def test_matched_pair_within_tolerance_is_true(self):
        cache = {
            'ref_1': {'role': 'link', 'name': 'Learn more',
                      'bbox': _bbox(100, 100)},
        }
        dets = [_det('Learn more', 105, 103)]
        disputes = ocr_mod.verify_atspi_coords(cache, dets)
        assert cache['ref_1']['coord_verified'] == 'true'
        assert disputes['ref_1'] is None

    def test_matched_pair_outside_tolerance_is_false(self):
        """Mirrors finding-20260415-g4qq — Firefox AT-SPI bbox far
        from the real pixel position.
        """
        cache = {
            'ref_40': {'role': 'link', 'name': 'Learn more',
                       'bbox': _bbox(282, 313, 86, 20)},
        }
        dets = [_det('Learn more', 579, 459, 80, 20)]
        disputes = ocr_mod.verify_atspi_coords(cache, dets)
        assert cache['ref_40']['coord_verified'] == 'false'
        assert disputes['ref_40'] is True
        # Disputed detection is stamped with the ref id it contradicts,
        # so dedup and disputed_by linking can use the field instead of
        # Python object identity.
        assert dets[0]['_disputed_ref_id'] == 'ref_40'

    def test_no_ocr_coverage_is_unknown(self):
        cache = {
            'ref_1': {'role': 'button', 'name': 'Submit',
                      'bbox': _bbox(100, 100)},
        }
        disputes = ocr_mod.verify_atspi_coords(cache, [])
        assert cache['ref_1']['coord_verified'] == 'unknown'
        assert disputes['ref_1'] is None

    def test_count_mismatch_is_unknown(self):
        cache = {
            'ref_1': {'role': 'link', 'name': 'Learn more',
                      'bbox': _bbox(100, 100)},
            'ref_2': {'role': 'link', 'name': 'Learn more',
                      'bbox': _bbox(100, 200)},
        }
        dets = [_det('Learn more', 105, 103)]
        ocr_mod.verify_atspi_coords(cache, dets)
        assert cache['ref_1']['coord_verified'] == 'unknown'
        assert cache['ref_2']['coord_verified'] == 'unknown'

    def test_duplicate_names_with_matching_counts_pair_by_sort(self):
        """Three 'Learn more' refs + three matching OCR hits, each
        consistently shifted by the same offset (Firefox-style), should
        all get tagged 'false' with the correct 1:1 pairing.
        """
        cache = {
            'ref_1': {'role': 'link', 'name': 'Learn more',
                      'bbox': _bbox(100, 100)},
            'ref_2': {'role': 'link', 'name': 'Learn more',
                      'bbox': _bbox(100, 200)},
            'ref_3': {'role': 'link', 'name': 'Learn more',
                      'bbox': _bbox(100, 300)},
        }
        dets = [
            _det('Learn more', 400, 300),
            _det('Learn more', 400, 100),
            _det('Learn more', 400, 200),
        ]
        ocr_mod.verify_atspi_coords(cache, dets)
        assert cache['ref_1']['coord_verified'] == 'false'
        assert cache['ref_2']['coord_verified'] == 'false'
        assert cache['ref_3']['coord_verified'] == 'false'
        # Pairing by (y, x): ref_1 (y=100) ↔ det (y=100) etc. Read
        # the stamped field on each det to confirm the pairing, not
        # the disputes dict (which is now a simple present/absent map).
        by_y = {d['bbox']['y']: d.get('_disputed_ref_id') for d in dets}
        assert by_y[100] == 'ref_1'
        assert by_y[200] == 'ref_2'
        assert by_y[300] == 'ref_3'

    def test_duplicate_names_all_aligned_are_true(self):
        cache = {
            'ref_1': {'role': 'link', 'name': 'Learn more',
                      'bbox': _bbox(100, 100)},
            'ref_2': {'role': 'link', 'name': 'Learn more',
                      'bbox': _bbox(100, 200)},
        }
        dets = [
            _det('Learn more', 102, 101),
            _det('Learn more', 103, 203),
        ]
        ocr_mod.verify_atspi_coords(cache, dets)
        assert cache['ref_1']['coord_verified'] == 'true'
        assert cache['ref_2']['coord_verified'] == 'true'

    def test_window_coord_refs_are_offset_before_compare(self):
        # window bbox (10, 10, 80, 20) + offset (280, 160) → absolute
        # (290, 170, 80, 20) with center (330, 180). OCR bbox at
        # (290, 170, 80, 20) centers at (330, 180) → distance 0 → true.
        cache = {
            'ref_1': {'role': 'button', 'name': 'Click',
                      'bbox': {'x': 10, 'y': 10, 'w': 80, 'h': 20,
                               'coord': 'window'}},
        }
        dets = [_det('Click', 290, 170)]
        ocr_mod.verify_atspi_coords(cache, dets, window_offset=(280, 160))
        assert cache['ref_1']['coord_verified'] == 'true'

    def test_unnamed_refs_are_left_alone(self):
        cache = {
            'ref_1': {'role': 'panel', 'name': '',
                      'bbox': _bbox(100, 100)},
        }
        ocr_mod.verify_atspi_coords(cache, [])
        assert 'coord_verified' not in cache['ref_1']


class TestDescribeCoordVerifyIntegration:
    def _install_stubs(self, monkeypatch, atspi_cache, detections,
                       app_name='Firefox'):
        monkeypatch.setattr(
            desc, '_get_tree',
            lambda app=None: ('', dict(atspi_cache), app_name),
        )
        monkeypatch.setattr(desc, '_get_windows', lambda: [])
        monkeypatch.setattr(desc, '_capture_screenshot',
                            lambda path, **kw: _make_screenshot(path))
        monkeypatch.setattr(tine.window_offset, 'resolve_window_offset',
                            lambda app, **kw: (0, 0))
        monkeypatch.setattr(ocr_mod, 'detect_text',
                            lambda image_path, scale=1.0, min_confidence=0.8:
                                list(detections))

    def test_firefox_bbox_bug_shows_disputed_link(self, monkeypatch):
        """Reproduces finding-20260415-g4qq end-to-end.

        AT-SPI says 'Learn more' is at (282, 313); OCR says it's at
        (579, 459). The ref_N should be tagged false and linked to
        the surviving ref_tN.
        """
        self._install_stubs(
            monkeypatch,
            atspi_cache={
                'ref_40': {'role': 'link', 'name': 'Learn more',
                           'bbox': _bbox(282, 313, 86, 20)},
            },
            detections=[_det('Learn more', 579, 459, 80, 20)],
        )

        result = desc.describe(ocr=True)

        ref = result['ref_cache']['ref_40']
        assert ref['coord_verified'] == 'false'
        disputed_by = ref.get('disputed_by')
        assert disputed_by and disputed_by.startswith('ref_t')
        assert disputed_by in result['ref_cache']
        # Make sure the disputing OCR ref actually survived dedup.
        assert disputed_by in result['text_ref_order']

        output = desc.format_for_agent(result)
        assert '## Coord Verification' in output
        assert f"ref_40: 'Learn more' coord_verified=false" in output
        assert f"disputed by {disputed_by}" in output

    def test_matched_click_target_is_true_and_dedupes_ocr(self, monkeypatch):
        """Happy path: AT-SPI and OCR agree, so dedup drops the OCR side
        and the ref_N carries coord_verified=true.
        """
        self._install_stubs(
            monkeypatch,
            atspi_cache={
                'ref_1': {'role': 'button', 'name': 'Log In',
                          'bbox': _bbox(920, 292, 79, 32)},
            },
            detections=[_det('LogIn', 940, 300, 40, 20)],
        )

        result = desc.describe(ocr=True)

        assert result['ref_cache']['ref_1']['coord_verified'] == 'true'
        assert result['text_ref_order'] == []
        output = desc.format_for_agent(result)
        assert '## Coord Verification' not in output

    def test_unmatched_named_ref_is_unknown(self, monkeypatch):
        self._install_stubs(
            monkeypatch,
            atspi_cache={
                'ref_1': {'role': 'button', 'name': 'Submit',
                          'bbox': _bbox(100, 100, 80, 20)},
            },
            detections=[_det('Cancel', 200, 200, 60, 20)],
        )

        result = desc.describe(ocr=True)

        assert result['ref_cache']['ref_1']['coord_verified'] == 'unknown'
        output = desc.format_for_agent(result)
        assert '## Coord Verification' in output
        assert "coord_verified=unknown" in output
