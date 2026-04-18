"""Tests for the target overlay and click refinement feature.

target_overlay() draws a crosshair + 3x3 sub-grid on a screenshot.
target_subcell_center() computes the center of a numpad-layout sub-cell.
CLI: tine target E5 / tine target 450,320 / tine target E5 --pick 2
"""

import os
import subprocess

import pytest
from PIL import Image

from tine.screenshot import target_overlay, target_subcell_center


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_image(width=800, height=600):
    """Create a plain test image."""
    return Image.new('RGB', (width, height), color=(200, 200, 200))


# ---------------------------------------------------------------------------
# target_subcell_center() tests
# ---------------------------------------------------------------------------

class TestTargetSubcellCenter:
    """target_subcell_center(x, y, pick, region_size) -> (new_x, new_y)."""

    def test_pick_5_returns_center(self):
        """Pick 5 (center) returns the original position."""
        nx, ny = target_subcell_center(300, 300, 5, 100)
        assert nx == 300
        assert ny == 300

    def test_numpad_layout_directions(self):
        """Verify each pick goes in the expected direction from center."""
        cx, cy = 300, 300
        size = 99  # divisible by 3 for clean math

        # Pick 8 = above center
        _, y8 = target_subcell_center(cx, cy, 8, size)
        assert y8 < cy

        # Pick 2 = below center
        _, y2 = target_subcell_center(cx, cy, 2, size)
        assert y2 > cy

        # Pick 4 = left of center
        x4, _ = target_subcell_center(cx, cy, 4, size)
        assert x4 < cx

        # Pick 6 = right of center
        x6, _ = target_subcell_center(cx, cy, 6, size)
        assert x6 > cx

        # Pick 7 = top-left
        x7, y7 = target_subcell_center(cx, cy, 7, size)
        assert x7 < cx and y7 < cy

        # Pick 3 = bottom-right
        x3, y3 = target_subcell_center(cx, cy, 3, size)
        assert x3 > cx and y3 > cy

    def test_pick_1_bottom_left(self):
        """Pick 1 is bottom-left of the region."""
        x1, y1 = target_subcell_center(300, 300, 1, 99)
        # Bottom-left: x < center, y > center
        assert x1 < 300
        assert y1 > 300

    def test_pick_9_top_right(self):
        """Pick 9 is top-right of the region."""
        x9, y9 = target_subcell_center(300, 300, 9, 99)
        assert x9 > 300
        assert y9 < 300

    def test_region_size_affects_offset(self):
        """Larger region_size means sub-cells are farther from center."""
        x_small, _ = target_subcell_center(300, 300, 6, 60)
        x_large, _ = target_subcell_center(300, 300, 6, 120)
        # Both right of center, but large offset should be farther
        assert x_large - 300 > x_small - 300

    def test_invalid_pick_raises(self):
        with pytest.raises(ValueError, match='pick must be 1-9'):
            target_subcell_center(300, 300, 0, 100)
        with pytest.raises(ValueError, match='pick must be 1-9'):
            target_subcell_center(300, 300, 10, 100)

    def test_symmetry(self):
        """Opposite numpad picks should be symmetric around center."""
        cx, cy = 500, 400
        size = 90

        x7, y7 = target_subcell_center(cx, cy, 7, size)
        x3, y3 = target_subcell_center(cx, cy, 3, size)
        # 7 and 3 should be equidistant from center
        assert cx - x7 == x3 - cx
        assert cy - y7 == y3 - cy

        x4, y4 = target_subcell_center(cx, cy, 4, size)
        x6, y6 = target_subcell_center(cx, cy, 6, size)
        assert cx - x4 == x6 - cx
        assert y4 == y6


# ---------------------------------------------------------------------------
# target_overlay() tests
# ---------------------------------------------------------------------------

class TestTargetOverlay:
    """target_overlay(image_path, x, y, region_size, output_path) -> annotated path."""

    def test_returns_output_path(self, tmp_path):
        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'target.png')
        _make_test_image().save(img_path)

        result = target_overlay(img_path, 400, 300, output_path=out_path)
        assert result == out_path
        assert os.path.exists(out_path)

    def test_modifies_image(self, tmp_path):
        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'target.png')
        _make_test_image().save(img_path)

        target_overlay(img_path, 400, 300, output_path=out_path)

        original = Image.open(img_path)
        annotated = Image.open(out_path)
        assert list(original.getdata()) != list(annotated.getdata())

    def test_in_place(self, tmp_path):
        img_path = str(tmp_path / 'input.png')
        _make_test_image().save(img_path)

        result = target_overlay(img_path, 400, 300)
        assert result == img_path

    def test_crosshair_pixels_are_cyan(self, tmp_path):
        """The crosshair should draw cyan pixels at the target position."""
        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'target.png')
        _make_test_image(800, 600).save(img_path)

        target_overlay(img_path, 400, 300, output_path=out_path)
        img = Image.open(out_path)

        # Check a pixel on the vertical crosshair line, away from the grid area
        # (at x=400, y=10 should be on the crosshair line)
        px = img.getpixel((400, 10))
        assert px == (0, 255, 255), f'Expected cyan crosshair, got {px}'

    def test_custom_region_size(self, tmp_path):
        img_path = str(tmp_path / 'input.png')
        out_100 = str(tmp_path / 'target_100.png')
        out_200 = str(tmp_path / 'target_200.png')
        _make_test_image(800, 600).save(img_path)

        target_overlay(img_path, 400, 300, region_size=100, output_path=out_100)
        target_overlay(img_path, 400, 300, region_size=200, output_path=out_200)

        img_100 = Image.open(out_100)
        img_200 = Image.open(out_200)
        assert list(img_100.getdata()) != list(img_200.getdata())

    def test_rejects_zero_region_size(self, tmp_path):
        img_path = str(tmp_path / 'input.png')
        _make_test_image().save(img_path)

        with pytest.raises(ValueError, match='region_size'):
            target_overlay(img_path, 400, 300, region_size=0)
        with pytest.raises(ValueError, match='region_size'):
            target_overlay(img_path, 400, 300, region_size=-10)

    def test_near_edge(self, tmp_path):
        """target_overlay() near image edge should not crash."""
        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'target.png')
        _make_test_image(200, 200).save(img_path)

        # Near top-left corner
        target_overlay(img_path, 10, 10, region_size=100, output_path=out_path)
        assert os.path.exists(out_path)

    def test_preserves_image_size(self, tmp_path):
        img_path = str(tmp_path / 'input.png')
        out_path = str(tmp_path / 'target.png')
        _make_test_image(1920, 1080).save(img_path)

        target_overlay(img_path, 960, 540, output_path=out_path)
        img = Image.open(out_path)
        assert img.size == (1920, 1080)


# ---------------------------------------------------------------------------
# CLI integration tests (tine target subcommand)
# ---------------------------------------------------------------------------

class TestTargetCLI:
    """CLI integration for 'tine target' subcommand."""

    def test_target_with_coordinates(self, monkeypatch, tmp_path):
        """tine target 400,300 takes screenshot and draws overlay."""
        import tine.screenshot
        import tine.cli

        img_path = str(tmp_path / 'shot.png')
        _make_test_image(800, 600).save(img_path)

        monkeypatch.setattr(tine.screenshot, 'capture', lambda output_path=None: img_path)

        tine.cli.main(['target', '400,300'])

    def test_target_with_grid_cell(self, monkeypatch, tmp_path):
        """tine target E3 resolves grid cell and draws overlay."""
        import tine.screenshot
        import tine.display
        import tine.cli

        img_path = str(tmp_path / 'shot.png')
        _make_test_image(1920, 1080).save(img_path)

        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1080')
        monkeypatch.setenv('TINE_SCALE', '1.0')
        tine.display._cached_config = None
        monkeypatch.setattr(tine.screenshot, 'capture', lambda output_path=None: img_path)

        tine.cli.main(['target', 'E3'])

    def test_target_pick_clicks(self, monkeypatch, tmp_path):
        """tine target E3 --pick 2 clicks the sub-cell center."""
        import tine.display
        import tine.input
        import tine.cli

        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1080')
        monkeypatch.setenv('TINE_SCALE', '1.0')
        tine.display._cached_config = None

        clicks = []
        monkeypatch.setattr(tine.input, 'click', lambda x, y, **kw: clicks.append((x, y)))

        tine.cli.main(['target', 'E3', '--pick', '2'])

        assert len(clicks) == 1
        # 5x3 on 1920x1080: E3 center is (1728, 900). Pick 2 is below center.
        assert clicks[0][1] > 900

    def test_target_pick_refine(self, monkeypatch, tmp_path):
        """tine target E3 --pick 2 --refine clicks then takes new screenshot."""
        import tine.display
        import tine.screenshot
        import tine.input
        import tine.cli

        img_path = str(tmp_path / 'shot.png')
        _make_test_image(1920, 1080).save(img_path)

        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1080')
        monkeypatch.setenv('TINE_SCALE', '1.0')
        tine.display._cached_config = None

        clicks = []
        monkeypatch.setattr(tine.input, 'click', lambda x, y, **kw: clicks.append((x, y)))
        monkeypatch.setattr(tine.screenshot, 'capture', lambda output_path=None: img_path)

        tine.cli.main(['target', 'E3', '--pick', '2', '--refine'])

        assert len(clicks) == 1

    def test_target_invalid_position(self, monkeypatch):
        """tine target with invalid position prints error."""
        import tine.cli

        with pytest.raises(SystemExit):
            tine.cli.main(['target', '!!!invalid'])

    def test_target_invalid_pick(self, monkeypatch, tmp_path):
        """tine target E3 --pick 0 is rejected."""
        import tine.display
        import tine.input
        import tine.cli

        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1080')
        monkeypatch.setenv('TINE_SCALE', '1.0')
        tine.display._cached_config = None

        monkeypatch.setattr(tine.input, 'click', lambda x, y, **kw: None)

        with pytest.raises(SystemExit):
            tine.cli.main(['target', 'E3', '--pick', '0'])

    def test_target_prints_subcell_coordinates(self, monkeypatch, tmp_path, capsys):
        """tine target 400,300 prints image path, target coords, and subcell grid."""
        import tine.screenshot
        import tine.cli

        img_path = str(tmp_path / 'shot.png')
        _make_test_image(800, 600).save(img_path)

        monkeypatch.setattr(tine.screenshot, 'capture', lambda output_path=None: img_path)

        tine.cli.main(['target', '400,300'])

        out = capsys.readouterr().out
        lines = out.strip().split('\n')

        # First line is image path
        assert lines[0] == img_path
        # Second line is target coordinates
        assert lines[1] == 'target: 400,300'
        # Third line is subcells header
        assert lines[2] == 'subcells:'
        # Rows 7-8-9, 4-5-6, 1-2-3
        assert '7:' in lines[3] and '8:' in lines[3] and '9:' in lines[3]
        assert '4:' in lines[4] and '5:' in lines[4] and '6:' in lines[4]
        assert '1:' in lines[5] and '2:' in lines[5] and '3:' in lines[5]

    def test_target_grid_cell_prints_subcell_coordinates(self, monkeypatch, tmp_path, capsys):
        """tine target E3 prints subcell coordinates for the grid cell center."""
        import tine.display
        import tine.screenshot
        import tine.cli

        img_path = str(tmp_path / 'shot.png')
        _make_test_image(1920, 1080).save(img_path)

        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1080')
        monkeypatch.setenv('TINE_SCALE', '1.0')
        tine.display._cached_config = None
        monkeypatch.setattr(tine.screenshot, 'capture', lambda output_path=None: img_path)

        tine.cli.main(['target', 'E3'])

        out = capsys.readouterr().out
        lines = out.strip().split('\n')

        assert lines[0] == img_path
        assert lines[1].startswith('target:')
        assert lines[2] == 'subcells:'
        # Verify pick 5 (center) matches the target coords
        target_coords = lines[1].split(': ')[1]
        assert f'5: {target_coords}' in lines[4]

    def test_target_subcell_values_match(self, monkeypatch, tmp_path, capsys):
        """Subcell coordinates in output match target_subcell_center() values."""
        import tine.screenshot
        import tine.cli

        img_path = str(tmp_path / 'shot.png')
        _make_test_image(800, 600).save(img_path)

        monkeypatch.setattr(tine.screenshot, 'capture', lambda output_path=None: img_path)

        tine.cli.main(['target', '400,300', '--size', '90'])

        out = capsys.readouterr().out

        # Verify each subcell coord matches the function output
        for pick in range(1, 10):
            sx, sy = target_subcell_center(400, 300, pick, 90)
            assert f'{pick}: {sx},{sy}' in out

    def test_target_custom_size(self, monkeypatch, tmp_path):
        """tine target E3 --size 150 uses custom region size."""
        import tine.display
        import tine.screenshot
        import tine.cli

        img_path = str(tmp_path / 'shot.png')
        _make_test_image(1920, 1080).save(img_path)

        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1080')
        monkeypatch.setenv('TINE_SCALE', '1.0')
        tine.display._cached_config = None

        overlay_calls = []
        original_overlay = tine.screenshot.target_overlay

        def tracking_overlay(image_path, x, y, region_size=100, output_path=None):
            overlay_calls.append(region_size)
            return original_overlay(image_path, x, y, region_size=region_size, output_path=output_path)

        monkeypatch.setattr(tine.screenshot, 'capture', lambda output_path=None: img_path)
        monkeypatch.setattr(tine.screenshot, 'target_overlay', tracking_overlay)

        tine.cli.main(['target', 'E3', '--size', '150'])

        assert overlay_calls == [150]
