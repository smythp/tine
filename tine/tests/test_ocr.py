"""Tests for tine.ocr — RapidOCR wrapper and text ref helpers.

RapidOCR itself isn't imported here; its engine is monkeypatched. Tests cover:
- quad → axis-aligned bbox conversion
- physical → logical coord scaling
- confidence thresholding
- ref_tN assignment
- text normalization for match-time lookup
- dedup against AT-SPI2 refs
"""

import pytest

import tine.ocr as ocr


class _FakeResult:
    """Shape returned by RapidOCR 3.x — boxes, txts, scores attributes."""
    def __init__(self, boxes, txts, scores):
        self.boxes = boxes
        self.txts = txts
        self.scores = scores


class _FakeEngine:
    def __init__(self, boxes, txts, scores):
        self.result = _FakeResult(boxes, txts, scores)
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        return self.result


def _patch_engine(monkeypatch, engine):
    monkeypatch.setattr(ocr, '_engine', engine)


# ---------------------------------------------------------------------------
# _quad_to_bbox
# ---------------------------------------------------------------------------

class TestQuadToBbox:
    def test_axis_aligned_rect(self):
        quad = [[10, 20], [110, 20], [110, 45], [10, 45]]
        assert ocr._quad_to_bbox(quad) == {'x': 10, 'y': 20, 'w': 100, 'h': 25}

    def test_unordered_points(self):
        """Points may come back in any order; bbox uses min/max."""
        quad = [[110, 45], [10, 20], [110, 20], [10, 45]]
        assert ocr._quad_to_bbox(quad) == {'x': 10, 'y': 20, 'w': 100, 'h': 25}

    def test_fractional_points_floor_to_int(self):
        quad = [[10.7, 20.3], [110.2, 20.3], [110.2, 45.9], [10.7, 45.9]]
        bbox = ocr._quad_to_bbox(quad)
        assert isinstance(bbox['x'], int)
        assert isinstance(bbox['y'], int)
        assert isinstance(bbox['w'], int)
        assert isinstance(bbox['h'], int)


# ---------------------------------------------------------------------------
# _scale_bbox
# ---------------------------------------------------------------------------

class TestScaleBbox:
    def test_scale_one_passthrough(self):
        bbox = {'x': 100, 'y': 200, 'w': 50, 'h': 30}
        assert ocr._scale_bbox(bbox, 1.0) == bbox

    def test_scale_1_25(self):
        bbox = {'x': 100, 'y': 200, 'w': 50, 'h': 30}
        result = ocr._scale_bbox(bbox, 1.25)
        assert result == {'x': 80, 'y': 160, 'w': 40, 'h': 24}

    def test_scale_two(self):
        bbox = {'x': 200, 'y': 400, 'w': 100, 'h': 60}
        result = ocr._scale_bbox(bbox, 2.0)
        assert result == {'x': 100, 'y': 200, 'w': 50, 'h': 30}


# ---------------------------------------------------------------------------
# detect_text
# ---------------------------------------------------------------------------

class TestDetectText:
    def test_returns_list_of_dicts(self, monkeypatch):
        engine = _FakeEngine(
            boxes=[[[10, 20], [90, 20], [90, 40], [10, 40]]],
            txts=['Log In'],
            scores=[0.99],
        )
        _patch_engine(monkeypatch, engine)
        result = ocr.detect_text('/tmp/unused.png')
        assert len(result) == 1
        assert result[0]['text'] == 'Log In'
        assert result[0]['confidence'] == 0.99
        assert result[0]['bbox']['coord'] == 'screen'

    def test_bbox_in_logical_coords(self, monkeypatch):
        """detect_text divides by scale so OCR and AT-SPI2 share one coord space."""
        engine = _FakeEngine(
            boxes=[[[100, 200], [200, 200], [200, 250], [100, 250]]],
            txts=['Submit'],
            scores=[0.95],
        )
        _patch_engine(monkeypatch, engine)
        result = ocr.detect_text('/tmp/unused.png', scale=2.0)
        bbox = result[0]['bbox']
        assert bbox['x'] == 50
        assert bbox['y'] == 100
        assert bbox['w'] == 50
        assert bbox['h'] == 25

    def test_confidence_filter(self, monkeypatch):
        engine = _FakeEngine(
            boxes=[
                [[0, 0], [10, 0], [10, 10], [0, 10]],
                [[0, 20], [10, 20], [10, 30], [0, 30]],
            ],
            txts=['good', 'garbage'],
            scores=[0.95, 0.4],
        )
        _patch_engine(monkeypatch, engine)
        result = ocr.detect_text('/tmp/unused.png', min_confidence=0.8)
        assert len(result) == 1
        assert result[0]['text'] == 'good'

    def test_empty_result(self, monkeypatch):
        engine = _FakeEngine(boxes=None, txts=None, scores=None)
        _patch_engine(monkeypatch, engine)
        assert ocr.detect_text('/tmp/unused.png') == []

    def test_drops_zero_size_bboxes(self, monkeypatch):
        engine = _FakeEngine(
            boxes=[[[10, 10], [10, 10], [10, 10], [10, 10]]],
            txts=['phantom'],
            scores=[0.95],
        )
        _patch_engine(monkeypatch, engine)
        assert ocr.detect_text('/tmp/unused.png') == []

    def test_drops_zero_size_bbox_after_scaling(self, monkeypatch):
        """A 1px-wide detection at scale=2.0 yields w=0 after int truncation."""
        engine = _FakeEngine(
            boxes=[[[100, 200], [101, 200], [101, 220], [100, 220]]],
            txts=['thin'],
            scores=[0.99],
        )
        _patch_engine(monkeypatch, engine)
        result = ocr.detect_text('/tmp/unused.png', scale=2.0)
        assert result == []

    def test_coord_marked_screen(self, monkeypatch):
        """Every OCR bbox carries coord=screen so click_ref skips window offset."""
        engine = _FakeEngine(
            boxes=[[[0, 0], [10, 0], [10, 10], [0, 10]]],
            txts=['x'],
            scores=[0.99],
        )
        _patch_engine(monkeypatch, engine)
        result = ocr.detect_text('/tmp/unused.png')
        assert result[0]['bbox']['coord'] == 'screen'


# ---------------------------------------------------------------------------
# assign_text_refs
# ---------------------------------------------------------------------------

class TestAssignTextRefs:
    def test_assigns_ref_t_prefix(self):
        dets = [{'text': 'a', 'bbox': {'x': 0, 'y': 0, 'w': 1, 'h': 1},
                 'confidence': 0.9}]
        cache = ocr.assign_text_refs(dets)
        assert 'ref_t1' in cache

    def test_starts_from_custom_index(self):
        dets = [
            {'text': 'a', 'bbox': {'x': 0, 'y': 0, 'w': 1, 'h': 1},
             'confidence': 0.9},
            {'text': 'b', 'bbox': {'x': 0, 'y': 0, 'w': 1, 'h': 1},
             'confidence': 0.9},
        ]
        cache = ocr.assign_text_refs(dets, start_index=5)
        assert list(cache.keys()) == ['ref_t5', 'ref_t6']

    def test_entries_have_text_and_bbox(self):
        dets = [{'text': 'Log In', 'bbox': {'x': 10, 'y': 20, 'w': 40, 'h': 15},
                 'confidence': 0.95}]
        cache = ocr.assign_text_refs(dets)
        entry = cache['ref_t1']
        assert entry['text'] == 'Log In'
        assert entry['bbox']['x'] == 10
        assert entry['confidence'] == 0.95


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_casefold(self):
        assert ocr.normalize_text('Log In') == ocr.normalize_text('LOG IN')

    def test_strips_whitespace_so_ppocr_merged_words_still_match(self):
        """PP-OCR concatenates words in a line without spaces; agents type the
        natural form. Both sides strip all non-alphanumeric so they match."""
        assert ocr.normalize_text('Continue with Google') == \
               ocr.normalize_text('ContinueWithGoogle')

    def test_strips_punctuation(self):
        assert ocr.normalize_text('Forgot password?') == 'forgotpassword'

    def test_non_string_returns_empty(self):
        assert ocr.normalize_text(None) == ''
        assert ocr.normalize_text(42) == ''


# ---------------------------------------------------------------------------
# dedup_against_atspi
# ---------------------------------------------------------------------------

class TestDedupAgainstAtspi:
    def _det(self, text, x, y, w, h):
        return {
            'text': text,
            'bbox': {'x': x, 'y': y, 'w': w, 'h': h, 'coord': 'screen'},
            'confidence': 0.95,
        }

    def test_drops_duplicate_inside_atspi_bbox(self):
        """OCR detection suppressed when its center falls inside a named
        AT-SPI2 ref with matching normalized text."""
        atspi = {
            'ref_1': {'role': 'button', 'name': 'Log In',
                      'bbox': {'x': 100, 'y': 100, 'w': 100, 'h': 50,
                               'coord': 'screen'}},
        }
        dets = [self._det('Log In', 120, 115, 60, 20)]
        result = ocr.dedup_against_atspi(dets, atspi)
        assert result == []

    def test_keeps_detection_with_different_text(self):
        atspi = {
            'ref_1': {'role': 'button', 'name': 'Log In',
                      'bbox': {'x': 100, 'y': 100, 'w': 100, 'h': 50,
                               'coord': 'screen'}},
        }
        dets = [self._det('Sign Up', 120, 115, 60, 20)]
        result = ocr.dedup_against_atspi(dets, atspi)
        assert len(result) == 1

    def test_keeps_matching_text_outside_bbox(self):
        """Two 'OK' buttons at different positions should both survive."""
        atspi = {
            'ref_1': {'role': 'button', 'name': 'OK',
                      'bbox': {'x': 100, 'y': 100, 'w': 50, 'h': 30,
                               'coord': 'screen'}},
        }
        dets = [self._det('OK', 500, 500, 50, 30)]
        result = ocr.dedup_against_atspi(dets, atspi)
        assert len(result) == 1

    def test_applies_window_offset_to_window_relative_atspi(self):
        """Window-relative AT-SPI2 bboxes get offset before containment check."""
        atspi = {
            'ref_1': {'role': 'button', 'name': 'Submit',
                      'bbox': {'x': 10, 'y': 10, 'w': 100, 'h': 50,
                               'coord': 'window'}},
        }
        # With window_offset (300, 200), the AT-SPI2 bbox in screen coords is
        # (310, 210, 100x50). OCR detection center at (360, 230) falls inside.
        dets = [self._det('Submit', 340, 220, 40, 20)]
        result = ocr.dedup_against_atspi(dets, atspi, window_offset=(300, 200))
        assert result == []

    def test_ignores_app_metadata_key(self):
        atspi = {
            '_app': 'firefox',
            'ref_1': {'role': 'button', 'name': 'Log In',
                      'bbox': {'x': 100, 'y': 100, 'w': 100, 'h': 50,
                               'coord': 'screen'}},
        }
        dets = [self._det('Log In', 120, 115, 60, 20)]
        result = ocr.dedup_against_atspi(dets, atspi)
        assert result == []

    def test_normalized_match_across_whitespace_eaten_output(self):
        """PP-OCR emits 'LogIn' but AT-SPI2 has 'Log In'; normalization matches both."""
        atspi = {
            'ref_1': {'role': 'button', 'name': 'Log In',
                      'bbox': {'x': 100, 'y': 100, 'w': 100, 'h': 50,
                               'coord': 'screen'}},
        }
        dets = [self._det('LogIn', 120, 115, 60, 20)]
        result = ocr.dedup_against_atspi(dets, atspi)
        assert result == []

    def test_empty_atspi_cache(self):
        dets = [self._det('Anything', 0, 0, 10, 10)]
        result = ocr.dedup_against_atspi(dets, {})
        assert len(result) == 1
