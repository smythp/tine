"""Tests for tine screenshot: capture and Set-of-Mark annotation.

Capture uses the GNOME Shell extension D-Bus API (org.gnome.OCRScreenshot.Full).
Annotation draws numbered ref labels on bounding boxes using PIL.

These tests mock subprocess/D-Bus calls for unit testing. Integration tests
on a live system are separate.
"""

import json
import os
import tempfile

import pytest
from PIL import Image, ImageDraw

from tine.refs import save_ref_cache


# ---------------------------------------------------------------------------
# Regression tests for known parsing edge cases
# ---------------------------------------------------------------------------


def test_list_windows_json_with_parentheses_in_title(monkeypatch):
    """list_windows() should parse JSON even if title contains ")".

    gdbus wraps the returned JSON in a GVariant tuple: ('...json...',)
    The current regex in tine.screenshot.list_windows is greedy:
        re.search(r"\('(.*)'\s*,\s*\)", ...)

    If the JSON contains a ')' character, it can match past the real closing
    ")" and/or truncate incorrectly, causing json.loads to fail.
    """
    import subprocess

    windows_json = json.dumps([
        {
            'title': 'Hello ) world',
            'wm_class': 'org.gnome.TextEditor',
            'app_id': 'org.gnome.TextEditor',
            'x': 1,
            'y': 2,
            'width': 3,
            'height': 4,
            'focused': False,
        }
    ])

    def fake_run(cmd, **kwargs):
        return type('R', (), {
            'returncode': 0,
            'stdout': f"('{windows_json}',)",
            'stderr': '',
        })()

    monkeypatch.setattr(subprocess, 'run', fake_run)

    from tine.screenshot import list_windows

    # Regression expectation: should parse successfully
    windows = list_windows()
    assert windows[0]['title'] == 'Hello ) world'


def test_list_windows_json_with_trailing_parenthesis_in_title_breaks_regex(monkeypatch):
    """More direct regression: a title ending with ')' triggers regex ambiguity.

    This constructs a JSON string whose final character before the gdbus tuple
    close is ')'. A greedy (.*) can consume too much and include the closing
    tuple characters, which makes json.loads raise JSONDecodeError.

    Expected behavior: still returns parsed JSON.
    """
    import subprocess

    windows_json = json.dumps([
        {
            'title': 'EndsWithParen)',
            'wm_class': 'x',
            'app_id': 'x',
            'x': 0,
            'y': 0,
            'width': 10,
            'height': 10,
            'focused': False,
        }
    ])

    def fake_run(cmd, **kwargs):
        return type('R', (), {
            'returncode': 0,
            'stdout': f"('{windows_json}',)",
            'stderr': '',
        })()

    monkeypatch.setattr(subprocess, 'run', fake_run)

    from tine.screenshot import list_windows

    windows = list_windows()
    assert windows[0]['title'] == 'EndsWithParen)'


# ---------------------------------------------------------------------------
# capture() tests
# ---------------------------------------------------------------------------

class TestCapture:
    """tine.screenshot.capture(output_path) -> path to PNG."""

    def test_capture_calls_gdbus(self, monkeypatch):
        """capture() shells out to gdbus with the right D-Bus method."""
        import subprocess
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = type('R', (), {'returncode': 0, 'stdout': '(true,)\n', 'stderr': ''})()
            return result

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import capture

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'shot.png')
            capture(path)

        assert len(calls) == 1
        cmd = calls[0]
        assert 'gdbus' in cmd
        cmd_str = ' '.join(cmd)
        assert 'org.gnome.OCRScreenshot.Full' in cmd_str

    def test_capture_returns_path(self, monkeypatch):
        """capture() returns the output path on success."""
        import subprocess

        def fake_run(cmd, **kwargs):
            # Create a tiny PNG so the path exists
            path = None
            for i, arg in enumerate(cmd):
                if arg.endswith('.png'):
                    path = arg
                    break
            if path:
                Image.new('RGB', (10, 10)).save(path)
            return type('R', (), {'returncode': 0, 'stdout': '(true,)\n', 'stderr': ''})()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import capture

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'shot.png')
            result = capture(path)
            assert result == path

    def test_capture_raises_on_failure(self, monkeypatch):
        """capture() raises when gdbus returns false or non-zero exit."""
        import subprocess

        def fake_run(cmd, **kwargs):
            return type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'error'})()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import capture

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'shot.png')
            with pytest.raises(RuntimeError):
                capture(path)

    def test_capture_raises_on_dbus_false(self, monkeypatch):
        """capture() raises when gdbus succeeds but returns (false,)."""
        import subprocess

        def fake_run(cmd, **kwargs):
            return type('R', (), {'returncode': 0, 'stdout': '(false,)\n', 'stderr': ''})()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import capture

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'shot.png')
            with pytest.raises(RuntimeError):
                capture(path)

    def test_capture_default_path(self, monkeypatch):
        """capture() with no args uses a default temp path."""
        import subprocess

        def fake_run(cmd, **kwargs):
            for arg in cmd:
                if arg.endswith('.png'):
                    Image.new('RGB', (10, 10)).save(arg)
            return type('R', (), {'returncode': 0, 'stdout': '(true,)\n', 'stderr': ''})()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import capture

        result = capture()
        assert result.endswith('.png')
        assert 'tine-screenshot' in result

    def test_capture_default_path_unique(self, monkeypatch):
        """capture() generates unique temp paths to avoid collisions."""
        import subprocess

        def fake_run(cmd, **kwargs):
            for arg in cmd:
                if arg.endswith('.png'):
                    Image.new('RGB', (10, 10)).save(arg)
            return type('R', (), {'returncode': 0, 'stdout': '(true,)\n', 'stderr': ''})()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import capture

        path1 = capture()
        path2 = capture()
        assert path1 != path2


# ---------------------------------------------------------------------------
# annotate() tests
# ---------------------------------------------------------------------------

_TEST_BG_COLOR = (200, 200, 200)


def _make_test_image(width=800, height=600):
    """Create a plain test image."""
    return Image.new('RGB', (width, height), color=_TEST_BG_COLOR)


def _make_ref_cache(*entries):
    """Build a ref cache from (ref_id, role, name, x, y, w, h) tuples."""
    cache = {}
    for ref_id, role, name, x, y, w, h in entries:
        cache[ref_id] = {
            'role': role,
            'name': name,
            'bbox': {'x': x, 'y': y, 'w': w, 'h': h},
        }
    return cache


class TestAnnotate:
    """tine.screenshot.annotate(image_path, ref_cache, output_path) -> annotated image path."""

    def test_annotate_returns_image(self, tmp_path):
        """annotate() returns a valid image path."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'annotated.png')
        _make_test_image().save(img_path)

        cache = _make_ref_cache(
            ('ref_1', 'button', 'OK', 100, 100, 80, 30),
        )

        result = annotate(img_path, cache, out_path)
        assert result == out_path
        assert os.path.exists(out_path)

        # Output should be a valid image
        img = Image.open(out_path)
        assert img.size == (800, 600)

    def test_annotate_draws_on_image(self, tmp_path):
        """annotate() modifies the image (not identical to input)."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'annotated.png')
        _make_test_image().save(img_path)

        cache = _make_ref_cache(
            ('ref_1', 'button', 'OK', 100, 100, 80, 30),
            ('ref_2', 'entry', 'Search', 200, 200, 150, 25),
        )

        annotate(img_path, cache, out_path)

        original = Image.open(img_path)
        annotated = Image.open(out_path)
        # Images should differ (annotations were drawn)
        assert list(original.getdata()) != list(annotated.getdata())

    def test_annotate_empty_cache(self, tmp_path):
        """annotate() with empty cache returns unmodified image."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'annotated.png')
        _make_test_image().save(img_path)

        annotate(img_path, {}, out_path)
        assert os.path.exists(out_path)

    def test_annotate_multiple_refs(self, tmp_path):
        """annotate() handles many refs without error."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'annotated.png')
        _make_test_image(1920, 1080).save(img_path)

        # 20 refs scattered across the image
        entries = []
        for i in range(20):
            entries.append((f'ref_{i+1}', 'button', f'Btn {i+1}',
                          50 + i * 90, 50 + (i % 5) * 100, 80, 30))
        cache = _make_ref_cache(*entries)

        annotate(img_path, cache, out_path)
        assert os.path.exists(out_path)

    def test_annotate_skips_invalid_bbox(self, tmp_path):
        """annotate() skips refs with invalid bounding boxes (no crash)."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'annotated.png')
        _make_test_image().save(img_path)

        cache = {
            'ref_1': {'role': 'button', 'name': 'OK', 'bbox': {'x': 100, 'y': 100, 'w': 80, 'h': 30}},
            'ref_2': {'role': 'button', 'name': 'Bad', 'bbox': {'x': -2147483648, 'y': 0, 'w': 80, 'h': 30}},
            'ref_3': {'role': 'button', 'name': 'Neg', 'bbox': {'x': 100, 'y': 100, 'w': -10, 'h': 30}},
        }

        # Should not raise — just skip bad refs
        annotate(img_path, cache, out_path)
        assert os.path.exists(out_path)

    def test_annotate_skips_zero_dimension_bbox(self, tmp_path):
        """annotate() skips refs with zero width or height (bbox_center is valid but can't draw)."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'annotated.png')
        _make_test_image().save(img_path)

        cache = {
            'ref_1': {'role': 'button', 'name': 'ZeroW', 'bbox': {'x': 100, 'y': 100, 'w': 0, 'h': 30}},
            'ref_2': {'role': 'button', 'name': 'ZeroH', 'bbox': {'x': 200, 'y': 200, 'w': 80, 'h': 0}},
        }

        annotate(img_path, cache, out_path)

        # Image should be unmodified — both refs skipped
        original = Image.open(img_path)
        annotated = Image.open(out_path)
        assert list(original.getdata()) == list(annotated.getdata())

    def test_annotate_bbox_outside_image(self, tmp_path):
        """annotate() handles refs whose bbox is partially or fully outside the image."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'annotated.png')
        _make_test_image(400, 300).save(img_path)

        cache = _make_ref_cache(
            ('ref_1', 'button', 'OffRight', 380, 100, 80, 30),  # extends past right edge
            ('ref_2', 'button', 'OffBottom', 100, 280, 80, 30),  # extends past bottom
            ('ref_3', 'button', 'WayOff', 5000, 5000, 80, 30),  # completely outside
        )

        # Should not crash
        annotate(img_path, cache, out_path)
        assert os.path.exists(out_path)

    def test_annotate_rejects_zero_scale(self, tmp_path):
        """annotate() raises ValueError for scale <= 0."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        _make_test_image().save(img_path)
        cache = _make_ref_cache(('ref_1', 'button', 'OK', 100, 100, 80, 30))

        with pytest.raises(ValueError, match='Scale'):
            annotate(img_path, cache, scale=0)
        with pytest.raises(ValueError, match='Scale'):
            annotate(img_path, cache, scale=-1.0)

    def test_annotate_in_place(self, tmp_path):
        """annotate() with output_path=None writes over the input."""
        from tine.screenshot import annotate

        img_path = str(tmp_path / 'input.png')
        _make_test_image().save(img_path)

        cache = _make_ref_cache(
            ('ref_1', 'button', 'OK', 100, 100, 80, 30),
        )

        result = annotate(img_path, cache)
        assert result == img_path

    def test_annotate_scale_factor(self, tmp_path):
        """annotate() applies scale factor to bbox coordinates.

        AT-SPI2 gives logical coords but screenshots are physical pixels.
        At 2.0x scale, logical (100,100) = physical (200,200).
        We verify by annotating at scale=1.0 and scale=2.0 and checking
        that the drawn pixels differ at the expected locations.
        """
        from tine.screenshot import annotate

        # Use a large enough image that both scaled positions are in bounds
        img_path = str(tmp_path / 'input.png')
        out_1x = str(tmp_path / 'annotated_1x.png')
        out_2x = str(tmp_path / 'annotated_2x.png')
        _make_test_image(800, 600).save(img_path)

        cache = _make_ref_cache(
            ('ref_1', 'button', 'OK', 100, 100, 80, 30),  # logical coords
        )

        annotate(img_path, cache, out_1x, scale=1.0)
        annotate(img_path, cache, out_2x, scale=2.0)

        img_1x = Image.open(out_1x)
        img_2x = Image.open(out_2x)

        # At scale=1.0, bbox drawn at pixel (100,100)-(180,130)
        # At scale=2.0, bbox drawn at pixel (200,200)-(360,260)
        # So pixel (200,200) should be modified at 2x but not at 1x
        # (since the 1x bbox only extends to 180,130)
        px_1x = img_1x.getpixel((200, 200))
        px_2x = img_2x.getpixel((200, 200))
        assert px_1x != px_2x, "Scale factor should change where annotations are drawn"


# ---------------------------------------------------------------------------
# grid_overlay() tests
# ---------------------------------------------------------------------------

class TestGridOverlay:
    """tine.screenshot.grid_overlay(image_path, cols, rows, output_path) -> gridded image."""

    def test_grid_overlay_returns_path(self, tmp_path):
        """grid_overlay() returns a valid image path."""
        from tine.screenshot import grid_overlay

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'gridded.png')
        _make_test_image(800, 600).save(img_path)

        result = grid_overlay(img_path, output_path=out_path)
        assert result == out_path
        assert os.path.exists(out_path)

        img = Image.open(out_path)
        assert img.size == (800, 600)

    def test_grid_overlay_modifies_image(self, tmp_path):
        """grid_overlay() draws lines and labels (image differs from input)."""
        from tine.screenshot import grid_overlay

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'gridded.png')
        _make_test_image(800, 600).save(img_path)

        grid_overlay(img_path, output_path=out_path)

        original = Image.open(img_path)
        gridded = Image.open(out_path)
        assert list(original.getdata()) != list(gridded.getdata())

    def test_grid_overlay_in_place(self, tmp_path):
        """grid_overlay() with output_path=None overwrites the input."""
        from tine.screenshot import grid_overlay

        img_path = str(tmp_path / 'input.png')
        _make_test_image(400, 300).save(img_path)

        result = grid_overlay(img_path)
        assert result == img_path

    def test_grid_overlay_custom_dims(self, tmp_path):
        """grid_overlay() with custom cols/rows draws differently than default."""
        from tine.screenshot import grid_overlay

        img_path = str(tmp_path / 'input.png')
        out_default = str(tmp_path / 'grid_default.png')
        out_custom = str(tmp_path / 'grid_custom.png')
        _make_test_image(800, 600).save(img_path)

        grid_overlay(img_path, output_path=out_default)
        grid_overlay(img_path, cols=10, rows=6, output_path=out_custom)

        img_default = Image.open(out_default)
        img_custom = Image.open(out_custom)
        assert list(img_default.getdata()) != list(img_custom.getdata())

    def test_grid_overlay_rejects_zero_dims(self, tmp_path):
        """grid_overlay() raises ValueError for cols or rows < 1."""
        from tine.screenshot import grid_overlay

        img_path = str(tmp_path / 'input.png')
        _make_test_image().save(img_path)

        with pytest.raises(ValueError, match='cols'):
            grid_overlay(img_path, cols=0)
        with pytest.raises(ValueError, match='rows'):
            grid_overlay(img_path, rows=0)
        with pytest.raises(ValueError, match='cols'):
            grid_overlay(img_path, cols=-3)

    def test_grid_overlay_small_image(self, tmp_path):
        """grid_overlay() handles images smaller than a typical cell."""
        from tine.screenshot import grid_overlay

        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'gridded.png')
        _make_test_image(60, 60).save(img_path)

        result = grid_overlay(img_path, cols=2, rows=2, output_path=out_path)
        assert os.path.exists(result)
        assert Image.open(result).size == (60, 60)

    def test_grid_overlay_font_scales_with_cell_height(self, tmp_path):
        """Corner badge font-pt is proportional to cell height: a coarser grid
        (larger cells) produces a taller A1 badge than a finer grid on the
        same image. Proportional — we don't pin the absolute value."""
        from tine.screenshot import grid_overlay

        img_path = str(tmp_path / 'input.png')
        out_coarse = str(tmp_path / 'coarse.png')
        out_fine = str(tmp_path / 'fine.png')
        _make_test_image(1800, 1080).save(img_path)

        # 5x3 — cell_h = 360, font_pt = round(360 * 64 / 360) = 64
        grid_overlay(img_path, cols=5, rows=3, output_path=out_coarse)
        # 10x6 — cell_h = 180, font_pt = round(180 * 64 / 360) = 32
        grid_overlay(img_path, cols=10, rows=6, output_path=out_fine)

        coarse = Image.open(out_coarse)
        fine = Image.open(out_fine)

        def a1_badge_height(img):
            """Height of the top-left (A1) badge — the number of contiguous
            rows from y=0 whose first 30 columns contain a non-background
            pixel. The badge sits against the top-left corner; the next
            content downstream (A2 for fine, nothing nearby for coarse)
            only starts at y = cell_h, so a gap of 3 empty rows terminates
            the A1 badge. Background here is the test image's gray (200).
            """
            h = 0
            empty = 0
            for y in range(img.size[1]):
                row_has_content = any(
                    img.getpixel((x, y)) != _TEST_BG_COLOR
                    for x in range(30)
                )
                if row_has_content:
                    h = y + 1
                    empty = 0
                else:
                    empty += 1
                    if empty >= 3:
                        break
            return h

        coarse_h = a1_badge_height(coarse)
        fine_h = a1_badge_height(fine)

        # Both must have a visible badge (not zero); coarse must be larger.
        assert coarse_h > 0 and fine_h > 0, (
            f'Expected a visible A1 badge in both grids; '
            f'coarse_h={coarse_h}, fine_h={fine_h}'
        )
        assert coarse_h > fine_h, (
            f'Coarser grid should have a taller A1 badge (font scales with '
            f'cell height). coarse_h={coarse_h}, fine_h={fine_h}'
        )

    def test_grid_overlay_contrast_on_dark_and_light(self, tmp_path):
        """Grid lines must be visible on both pure-black and pure-white
        backgrounds. Sample one vertical and one horizontal line at points
        that cannot be occupied by corner badges (middle of a cell, away
        from any cell corner), and require >= 100/255 luminance delta.

        Contract: if the hazard-tape line-draw calls in grid_overlay were
        removed, this test must fail. Earlier sampling at x = round(w/cols)
        and full-height ranges also saw badge outlines, so a line-less
        render still passed — that was the exact visibility-test gap that
        let the original grid bug ship. Pin the samples off the badges.
        """
        from tine.screenshot import grid_overlay

        def luminance(px):
            r, g, b = px[:3]
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        for bg in [(0, 0, 0), (255, 255, 255)]:
            img_path = str(tmp_path / f'bg_{bg[0]}.png')
            out_path = str(tmp_path / f'grid_{bg[0]}.png')
            Image.new('RGB', (800, 600), color=bg).save(img_path)
            grid_overlay(img_path, cols=5, rows=3, output_path=out_path)
            gridded = Image.open(out_path)
            bg_y = luminance(bg)

            # 800x600 5x3: x_edges=[0,160,320,480,640,800], y_edges=[0,200,400,600].
            # Hazard-tape line is a 14px black casing with a 10px yellow core
            # on top, so the line occupies ±7 px around the edge coordinate
            # (black at the outer 2px on each side, yellow in the middle 10).
            # Sample ±8 so we catch the casing; yellow alone is only 18/255
            # delta on white, but the black casing pushes delta past 100 on
            # both bgs. This range is why the test has teeth: a renderer
            # that dropped the casing and kept only the core would fail on
            # white.

            # Row-0 badges sit against y=0; their height is ~50px at
            # font_pt=36. y=100 is the middle of row-0, well clear of any
            # badge. Sample across the vertical line at x=160.
            vline_pixels = [gridded.getpixel((160 + dx, 100))
                            for dx in range(-8, 9)]
            vline_delta = max(abs(luminance(p) - bg_y) for p in vline_pixels)
            assert vline_delta >= 100, (
                f'Vertical line contrast on bg={bg} was only '
                f'{vline_delta:.0f}/255; need >= 100. Pixels: {vline_pixels}'
            )

            # Row-1 badges sit at x_edges[c] with bw ~50px; they occupy
            # x in [x_edges[c], x_edges[c]+50]. x=240 is past the B2 badge
            # (which ends near x=210). Sample across the horizontal line
            # at y=200.
            hline_pixels = [gridded.getpixel((240, 200 + dy))
                            for dy in range(-8, 9)]
            hline_delta = max(abs(luminance(p) - bg_y) for p in hline_pixels)
            assert hline_delta >= 100, (
                f'Horizontal line contrast on bg={bg} was only '
                f'{hline_delta:.0f}/255; need >= 100. Pixels: {hline_pixels}'
            )

    def test_grid_overlay_ocr_roundtrip(self, tmp_path):
        """OCR must detect the vast majority of cell labels rendered by
        grid_overlay. On a solid-color (noise-free) synthetic background
        this should comfortably clear the witness's 14/15-on-real-screenshot
        benchmark — we require >= 13/15 to allow for OCR quirks on a couple
        of glyphs."""
        pytest.importorskip('rapidocr')
        from tine.screenshot import grid_overlay
        from tine.ocr import detect_text, normalize_text

        img_path = str(tmp_path / 'bg.png')
        out_path = str(tmp_path / 'grid.png')
        Image.new('RGB', (1920, 1080), color=(128, 128, 128)).save(img_path)

        grid_overlay(img_path, cols=5, rows=3, output_path=out_path)

        detections = detect_text(out_path)
        detected_norm = {normalize_text(d['text']) for d in detections}

        cols = ['A', 'B', 'C', 'D', 'E']
        rows = [1, 2, 3]
        expected = [f'{c}{r}' for r in rows for c in cols]
        found = [label for label in expected
                 if normalize_text(label) in detected_norm]

        assert len(found) >= 13, (
            f'OCR detected only {len(found)}/15 labels: '
            f'found={found} detected_norm={detected_norm}'
        )


# ---------------------------------------------------------------------------
# _col_label() tests
# ---------------------------------------------------------------------------

class TestColLabel:
    """tine.screenshot._col_label(index) -> column letter label."""

    def test_single_letters(self):
        from tine.screenshot import _col_label
        assert _col_label(0) == 'A'
        assert _col_label(1) == 'B'
        assert _col_label(25) == 'Z'

    def test_double_letters(self):
        from tine.screenshot import _col_label
        assert _col_label(26) == 'AA'
        assert _col_label(27) == 'AB'
        assert _col_label(51) == 'AZ'
        assert _col_label(52) == 'BA'


# ---------------------------------------------------------------------------
# parse_grid_cell() tests
# ---------------------------------------------------------------------------

class TestParseGridCell:
    """tine.screenshot.parse_grid_cell(cell_str) -> (col, row) zero-based."""

    def test_basic_cells(self):
        from tine.screenshot import parse_grid_cell
        assert parse_grid_cell('A1') == (0, 0)
        assert parse_grid_cell('B3') == (1, 2)
        assert parse_grid_cell('Z1') == (25, 0)

    def test_double_letter_columns(self):
        from tine.screenshot import parse_grid_cell
        assert parse_grid_cell('AA1') == (26, 0)
        assert parse_grid_cell('AB5') == (27, 4)

    def test_case_insensitive(self):
        from tine.screenshot import parse_grid_cell
        assert parse_grid_cell('b3') == (1, 2)
        assert parse_grid_cell('aA1') == (26, 0)

    def test_invalid_format(self):
        from tine.screenshot import parse_grid_cell
        with pytest.raises(ValueError):
            parse_grid_cell('3B')  # digits before letters
        with pytest.raises(ValueError):
            parse_grid_cell('')
        with pytest.raises(ValueError):
            parse_grid_cell('A')  # no row number
        with pytest.raises(ValueError):
            parse_grid_cell('123')  # no column letters

    def test_row_zero_invalid(self):
        from tine.screenshot import parse_grid_cell
        with pytest.raises(ValueError):
            parse_grid_cell('A0')  # row 0 is invalid (1-based)


# ---------------------------------------------------------------------------
# grid_cell_center() tests
# ---------------------------------------------------------------------------

class TestGridCellCenter:
    """tine.screenshot.grid_cell_center(cell_str, W, H, cols, rows) -> (x, y) center."""

    def test_a1_5x3_1920x1080(self):
        """Top-left cell on a 1920x1080 5x3 grid centers in the first cell."""
        from tine.screenshot import grid_cell_center
        # cell width = 384, height = 360. A1 center = (192, 180).
        assert grid_cell_center('A1', 1920, 1080) == (192, 180)

    def test_e3_5x3_1920x1080_onscreen(self):
        """Bottom-right cell on 1920x1080 5x3 must be inside the image."""
        from tine.screenshot import grid_cell_center
        x, y = grid_cell_center('E3', 1920, 1080)
        assert 0 <= x < 1920
        assert 0 <= y < 1080

    def test_b3_5x3_1920x1080(self):
        """B3 center on a 5x3 1920x1080 grid."""
        from tine.screenshot import grid_cell_center
        # B = col 1, row 3 = row 2. 5x3 cells: x in [384,768], y in [720,1080]
        # Centers: x=576, y=900.
        assert grid_cell_center('B3', 1920, 1080) == (576, 900)

    def test_custom_dims(self):
        from tine.screenshot import grid_cell_center
        # 10x6 on 1000x600 — cell w=100, h=100. A1 center = (50, 50).
        assert grid_cell_center('A1', 1000, 600, cols=10, rows=6) == (50, 50)
        # B2 center = (150, 150).
        assert grid_cell_center('B2', 1000, 600, cols=10, rows=6) == (150, 150)

    def test_never_offscreen_1920x1080(self):
        """No cell in a 5x3 grid on 1920x1080 resolves to an offscreen center."""
        from tine.screenshot import grid_cell_center
        W, H = 1920, 1080
        for col_letter in ['A', 'B', 'C', 'D', 'E']:
            for row_num in [1, 2, 3]:
                x, y = grid_cell_center(f'{col_letter}{row_num}', W, H)
                assert 0 <= x < W, f'{col_letter}{row_num} x={x} out of range'
                assert 0 <= y < H, f'{col_letter}{row_num} y={y} out of range'

    def test_never_offscreen_1366x768(self):
        """Same guarantee at a smaller common resolution."""
        from tine.screenshot import grid_cell_center
        W, H = 1366, 768
        for col_letter in ['A', 'B', 'C', 'D', 'E']:
            for row_num in [1, 2, 3]:
                x, y = grid_cell_center(f'{col_letter}{row_num}', W, H)
                assert 0 <= x < W, f'{col_letter}{row_num} x={x} out of range'
                assert 0 <= y < H, f'{col_letter}{row_num} y={y} out of range'

    def test_out_of_range_cell(self):
        """Cells outside the grid dims raise ValueError."""
        from tine.screenshot import grid_cell_center
        with pytest.raises(ValueError):
            grid_cell_center('F1', 1920, 1080)  # only A-E for 5 cols
        with pytest.raises(ValueError):
            grid_cell_center('A4', 1920, 1080)  # only rows 1-3


# ---------------------------------------------------------------------------
# list_windows() tests
# ---------------------------------------------------------------------------

class TestListWindows:
    """tine.screenshot.list_windows() -> list of window dicts."""

    def test_list_windows_parses_json(self, monkeypatch):
        """list_windows() parses the JSON from ListWindows D-Bus call."""
        import subprocess

        windows_json = json.dumps([
            {'title': 'Terminal', 'wm_class': 'org.gnome.Terminal',
             'app_id': 'org.gnome.Terminal', 'x': 400, 'y': 184,
             'width': 736, 'height': 529, 'focused': True},
            {'title': 'Calculator', 'wm_class': 'org.gnome.Calculator',
             'app_id': 'org.gnome.Calculator', 'x': 588, 'y': 140,
             'width': 360, 'height': 616, 'focused': False},
        ])

        def fake_run(cmd, **kwargs):
            return type('R', (), {
                'returncode': 0,
                'stdout': f"('{windows_json}',)\n",
                'stderr': '',
            })()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import list_windows

        windows = list_windows()
        assert len(windows) == 2
        assert windows[0]['title'] == 'Terminal'
        assert windows[0]['app_id'] == 'org.gnome.Terminal'
        assert windows[0]['x'] == 400
        assert windows[0]['focused'] is True
        assert windows[1]['title'] == 'Calculator'

    def test_list_windows_empty(self, monkeypatch):
        """list_windows() returns [] when no windows open."""
        import subprocess

        def fake_run(cmd, **kwargs):
            return type('R', (), {
                'returncode': 0,
                'stdout': "('[]',)\n",
                'stderr': '',
            })()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import list_windows

        assert list_windows() == []

    def test_list_windows_raises_on_failure(self, monkeypatch):
        """list_windows() raises when D-Bus call fails."""
        import subprocess

        def fake_run(cmd, **kwargs):
            return type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'err'})()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import list_windows

        with pytest.raises(RuntimeError):
            list_windows()

    def test_list_windows_apostrophe_in_title(self, monkeypatch):
        """list_windows() handles GVariant escaping for titles with apostrophes."""
        import subprocess

        # GVariant escapes ' as \' inside single-quoted strings
        # The actual JSON has a literal apostrophe, but gdbus wraps it
        def fake_run(cmd, **kwargs):
            # Simulate gdbus output for a title "Don't Panic"
            # gdbus wraps in (' ... ',) and escapes internal ' as \'
            raw = """('[{"title":"Don\\'t Panic","wm_class":"org.gnome.TextEditor","app_id":"org.gnome.TextEditor","x":100,"y":100,"width":800,"height":600,"focused":true}]',)"""
            return type('R', (), {
                'returncode': 0,
                'stdout': raw,
                'stderr': '',
            })()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import list_windows

        windows = list_windows()
        assert len(windows) == 1
        assert windows[0]['title'] == "Don't Panic"

    def test_list_windows_backslash_in_title(self, monkeypatch):
        """list_windows() handles GVariant escaping for backslashes."""
        import subprocess

        def fake_run(cmd, **kwargs):
            # JSON has \\, GVariant further escapes \ as \\
            # So JSON "C:\\Users" becomes C:\\\\Users in GVariant output
            raw = r"""('[{"title":"C:\\\\Users","wm_class":"x","app_id":"x","x":0,"y":0,"width":100,"height":100,"focused":false}]',)"""
            return type('R', (), {
                'returncode': 0,
                'stdout': raw,
                'stderr': '',
            })()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import list_windows

        windows = list_windows()
        assert len(windows) == 1
        assert windows[0]['title'] == 'C:\\Users'

    def test_list_windows_finds_by_app_id(self, monkeypatch):
        """Utility: find a window by app_id from list_windows result."""
        import subprocess

        windows_json = json.dumps([
            {'title': 'T', 'wm_class': 'org.gnome.Terminal',
             'app_id': 'org.gnome.Terminal', 'x': 10, 'y': 20,
             'width': 800, 'height': 600, 'focused': False},
        ])

        def fake_run(cmd, **kwargs):
            return type('R', (), {
                'returncode': 0,
                'stdout': f"('{windows_json}',)\n",
                'stderr': '',
            })()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        from tine.screenshot import list_windows

        windows = list_windows()
        match = [w for w in windows if 'Terminal' in w['app_id']]
        assert len(match) == 1
        assert match[0]['x'] == 10
