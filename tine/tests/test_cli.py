"""Tests for tine CLI: argument parsing, subcommand dispatch, output formatting.

The CLI is a thin layer that parses arguments and dispatches to library
functions. These tests mock the library layer and verify:
- Correct argument parsing for each subcommand
- Correct library function calls with expected arguments
- Output formatting (stdout/stderr, exit codes)
- Error handling (missing args, bad refs, subprocess failures)
- Ref cache lifecycle (save after tree/describe, load for click/screenshot)
- Window offset resolution (AT-SPI2 coords are surface-local on Wayland)

Import convention: the CLI must use `import tine.module` style (not
`from tine.module import func`) so that monkeypatch on the source module
takes effect at call time.
"""

import json
import os
import sys
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ref_cache(app='gnome-control-center'):
    """Ref cache with _app metadata and test entries."""
    return {
        '_app': app,
        'ref_1': {
            'role': 'button',
            'name': 'Wi-Fi',
            'bbox': {'x': 6, 'y': 136, 'w': 233, 'h': 43},
        },
        'ref_2': {
            'role': 'entry',
            'name': 'Search',
            'bbox': {'x': 100, 'y': 50, 'w': 200, 'h': 30},
        },
    }


def _make_windows():
    """Window list where the focused window is NOT the same as the ref app.

    Terminal is focused (first), Settings is not — disambiguates
    "use focused window" from "match by _app".
    """
    return [
        {
            'title': 'Terminal',
            'wm_class': 'gnome-terminal-server',
            'app_id': 'org.gnome.Terminal',
            'x': 200, 'y': 100, 'width': 800, 'height': 600,
            'focused': True,
        },
        {
            'title': 'Settings',
            'wm_class': 'gnome-control-center',
            'app_id': 'org.gnome.Settings',
            'x': 72, 'y': 32, 'width': 1024, 'height': 768,
            'focused': False,
        },
    ]


TREE_OUTPUT = (
    "application 'Settings'\n"
    "  button 'Wi-Fi' [ref_1]\n"
    "  entry 'Search' [ref_2]"
)


def _write_ref_cache(path, cache):
    with open(path, 'w') as f:
        json.dump(cache, f)


def _read_ref_cache(path):
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Argument parsing — tree
# ---------------------------------------------------------------------------

class TestTreeCommand:
    """tine tree [--app NAME] — walk AT-SPI2 tree, format, save ref cache."""

    def test_tree_no_args(self, monkeypatch, capsys, tmp_path):
        """Basic tree call prints formatted tree and saves ref cache with _app."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        raw_tree = {
            'role': 'application', 'name': 'Settings',
            'bbox': {'x': 0, 'y': 0, 'w': 1024, 'h': 768},
            'states': ['sensitive', 'showing', 'visible'],
            'actions': [], 'children': [
                {
                    'role': 'button', 'name': 'Wi-Fi',
                    'bbox': {'x': 6, 'y': 136, 'w': 233, 'h': 43},
                    'states': ['sensitive', 'showing', 'visible'],
                    'actions': ['click'], 'children': [],
                },
            ],
        }

        def fake_walk_atspi(app=None):
            return raw_tree

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_walk_atspi', fake_walk_atspi)

        cli_mod.main(['tree'])

        out = capsys.readouterr().out
        assert 'application' in out
        assert 'Settings' in out
        assert 'button' in out
        assert 'Wi-Fi' in out
        assert '[ref_' in out

        # Ref cache saved with _app metadata from root node
        assert os.path.exists(ref_cache_path)
        cache = _read_ref_cache(ref_cache_path)
        assert cache['_app'] == 'Settings'
        assert 'ref_1' in cache
        assert cache['ref_1']['name'] == 'Wi-Fi'

    def test_tree_with_app_filter(self, monkeypatch, capsys, tmp_path):
        """--app NAME passes the app filter to the walker."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        received_args = {}

        def fake_walk_atspi(app=None):
            received_args['app'] = app
            return {
                'role': 'application', 'name': 'Firefox',
                'bbox': {'x': 0, 'y': 0, 'w': 800, 'h': 600},
                'states': [], 'actions': [], 'children': [],
            }

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_walk_atspi', fake_walk_atspi)

        cli_mod.main(['tree', '--app', 'Firefox'])
        assert received_args['app'] == 'Firefox'

    def test_tree_empty_tree(self, monkeypatch, capsys, tmp_path):
        """Empty tree (no children) still prints the root and saves cache."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        def fake_walk_atspi(app=None):
            return {
                'role': 'desktop frame', 'name': '',
                'bbox': {'x': 0, 'y': 0, 'w': 1920, 'h': 1080},
                'states': [], 'actions': [], 'children': [],
            }

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_walk_atspi', fake_walk_atspi)

        cli_mod.main(['tree'])

        out = capsys.readouterr().out
        assert 'desktop frame' in out
        assert os.path.exists(ref_cache_path)


# ---------------------------------------------------------------------------
# Argument parsing — screenshot
# ---------------------------------------------------------------------------

class TestScreenshotCommand:
    """tine screenshot [--annotate] [--scale F] — capture screenshot."""

    def test_screenshot_basic(self, monkeypatch, capsys, tmp_path):
        """Basic screenshot prints path to stdout."""
        capture_calls = []

        def fake_capture(output_path=None):
            capture_calls.append(output_path)
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)

        import tine.cli as cli_mod
        cli_mod.main(['screenshot'])

        out = capsys.readouterr().out.strip()
        assert out.endswith('.png')
        assert len(capture_calls) == 1

    def test_screenshot_annotate_adjusts_coords(self, monkeypatch, capsys, tmp_path):
        """--annotate adjusts surface-local bboxes to screen coords using window offset."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        annotate_calls = []

        def fake_capture(output_path=None):
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        def fake_annotate(image_path, ref_cache, output_path=None, scale=1.0):
            annotate_calls.append({
                'ref_cache': ref_cache,
                'scale': scale,
            })
            return output_path or image_path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)
        monkeypatch.setattr(tine.screenshot, 'annotate', fake_annotate)
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())
        monkeypatch.setenv('TINE_SHADOW_OFFSET', '0')  # isolate window-offset behaviour

        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '--annotate'])

        assert len(annotate_calls) == 1
        adjusted = annotate_calls[0]['ref_cache']
        # _app metadata stripped from the cache passed to annotate
        assert '_app' not in adjusted
        # Settings window is at (72, 32). Original ref_1 bbox: x=6, y=136.
        # Adjusted for screen: x=6+72=78, y=136+32=168.
        assert adjusted['ref_1']['bbox']['x'] == 6 + 72
        assert adjusted['ref_1']['bbox']['y'] == 136 + 32
        # Dimensions unchanged
        assert adjusted['ref_1']['bbox']['w'] == 233
        assert adjusted['ref_1']['bbox']['h'] == 43

        # On-disk cache must NOT be mutated (would cause double-offset on next call)
        ondisk = _read_ref_cache(ref_cache_path)
        assert ondisk['ref_1']['bbox']['x'] == 6
        assert ondisk['ref_1']['bbox']['y'] == 136

    def test_screenshot_annotate_no_ref_cache(self, monkeypatch, capsys, tmp_path):
        """--annotate without a ref cache still captures, skips annotation."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        annotate_calls = []

        def fake_capture(output_path=None):
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        def fake_annotate(image_path, ref_cache, output_path=None, scale=1.0):
            annotate_calls.append(True)
            return output_path or image_path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)
        monkeypatch.setattr(tine.screenshot, 'annotate', fake_annotate)
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '--annotate'])

        out = capsys.readouterr().out.strip()
        assert out.endswith('.png')
        # No refs to draw — annotate should not be called
        assert len(annotate_calls) == 0

    def test_screenshot_output_path(self, monkeypatch, capsys, tmp_path):
        """--output passes the path to capture()."""
        capture_calls = []

        def fake_capture(output_path=None):
            capture_calls.append(output_path)
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)

        out_path = str(tmp_path / 'custom.png')
        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '--output', out_path])

        assert capture_calls == [out_path]
        out = capsys.readouterr().out.strip()
        assert out == out_path

    def test_screenshot_output_path_short_flag(self, monkeypatch, capsys, tmp_path):
        """-o is accepted as an alias for --output."""
        capture_calls = []

        def fake_capture(output_path=None):
            capture_calls.append(output_path)
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)

        out_path = str(tmp_path / 'short.png')
        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '-o', out_path])

        assert capture_calls == [out_path]

    def test_screenshot_scale(self, monkeypatch, capsys, tmp_path):
        """--scale passes through to annotate."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        annotate_calls = []

        def fake_capture(output_path=None):
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        def fake_annotate(image_path, ref_cache, output_path=None, scale=1.0):
            annotate_calls.append({'scale': scale})
            return output_path or image_path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)
        monkeypatch.setattr(tine.screenshot, 'annotate', fake_annotate)
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '--annotate', '--scale', '1.25'])

        assert annotate_calls[0]['scale'] == 1.25

    def test_screenshot_grid(self, monkeypatch, capsys, tmp_path):
        """--grid calls grid_overlay on the captured screenshot."""
        grid_calls = []

        def fake_capture(output_path=None):
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        def fake_grid_overlay(image_path, grid_size=100, output_path=None):
            grid_calls.append({'image_path': image_path, 'grid_size': grid_size})
            return output_path or image_path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)
        monkeypatch.setattr(tine.screenshot, 'grid_overlay', fake_grid_overlay)

        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '--grid'])

        assert len(grid_calls) == 1
        assert grid_calls[0]['grid_size'] == 100

    def test_screenshot_grid_custom_size(self, monkeypatch, capsys, tmp_path):
        """--grid --grid-size 200 passes custom size to grid_overlay."""
        grid_calls = []

        def fake_capture(output_path=None):
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        def fake_grid_overlay(image_path, grid_size=100, output_path=None):
            grid_calls.append({'grid_size': grid_size})
            return output_path or image_path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)
        monkeypatch.setattr(tine.screenshot, 'grid_overlay', fake_grid_overlay)

        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '--grid', '--grid-size', '200'])

        assert grid_calls[0]['grid_size'] == 200

    def test_screenshot_grid_and_annotate(self, monkeypatch, capsys, tmp_path):
        """--grid and --annotate can be combined — both get called."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        annotate_calls = []
        grid_calls = []

        def fake_capture(output_path=None):
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        def fake_annotate(image_path, ref_cache, output_path=None, scale=1.0):
            annotate_calls.append(True)
            return output_path or image_path

        def fake_grid_overlay(image_path, grid_size=100, output_path=None):
            grid_calls.append(True)
            return output_path or image_path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)
        monkeypatch.setattr(tine.screenshot, 'annotate', fake_annotate)
        monkeypatch.setattr(tine.screenshot, 'grid_overlay', fake_grid_overlay)
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '--annotate', '--grid'])

        assert len(annotate_calls) == 1
        assert len(grid_calls) == 1

    def test_screenshot_no_grid_by_default(self, monkeypatch, capsys, tmp_path):
        """Without --grid, grid_overlay is not called."""
        grid_calls = []

        def fake_capture(output_path=None):
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        def fake_grid_overlay(image_path, grid_size=100, output_path=None):
            grid_calls.append(True)
            return output_path or image_path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)
        monkeypatch.setattr(tine.screenshot, 'grid_overlay', fake_grid_overlay)

        import tine.cli as cli_mod
        cli_mod.main(['screenshot'])

        assert len(grid_calls) == 0


# ---------------------------------------------------------------------------
# Argument parsing — click
# ---------------------------------------------------------------------------

class TestClickCommand:
    """tine click REF_OR_COORDS — click by ref or pixel coordinates."""

    def test_click_ref(self, monkeypatch, tmp_path):
        """tine click ref_1 delegates to click_ref with correct window offset."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({
                'ref_id': ref_id,
                'window_offset': window_offset,
                'button': button,
                'double': double,
            })

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        assert len(click_ref_calls) == 1
        assert click_ref_calls[0]['ref_id'] == 'ref_1'
        # Settings window at (72, 32), NOT Terminal at (200, 100)
        assert click_ref_calls[0]['window_offset'] == (72, 32)
        assert click_ref_calls[0]['button'] == 'left'
        assert click_ref_calls[0]['double'] is False

    def test_click_coordinates(self, monkeypatch, tmp_path):
        """tine click 450,320 calls click at pixel coords with defaults."""
        click_calls = []

        def fake_click(x, y, button='left', double=False):
            click_calls.append({'x': x, 'y': y, 'button': button, 'double': double})

        import tine.input
        monkeypatch.setattr(tine.input, 'click', fake_click)

        import tine.cli as cli_mod
        cli_mod.main(['click', '450,320'])

        assert len(click_calls) == 1
        assert click_calls[0]['x'] == 450
        assert click_calls[0]['y'] == 320
        assert click_calls[0]['button'] == 'left'
        assert click_calls[0]['double'] is False

    def test_click_ref_right_button(self, monkeypatch, tmp_path):
        """tine click --button right ref_1 passes button to click_ref."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'button': button})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', '--button', 'right', 'ref_1'])

        assert click_ref_calls[0]['button'] == 'right'

    def test_click_coords_right_button(self, monkeypatch, tmp_path):
        """tine click --button right 450,320 passes button to click."""
        click_calls = []

        def fake_click(x, y, button='left', double=False):
            click_calls.append({'button': button})

        import tine.input
        monkeypatch.setattr(tine.input, 'click', fake_click)

        import tine.cli as cli_mod
        cli_mod.main(['click', '--button', 'right', '450,320'])

        assert click_calls[0]['button'] == 'right'

    def test_click_double_coordinates(self, monkeypatch, tmp_path):
        """tine click --double 450,320 sends double click."""
        click_calls = []

        def fake_click(x, y, button='left', double=False):
            click_calls.append({'double': double})

        import tine.input
        monkeypatch.setattr(tine.input, 'click', fake_click)

        import tine.cli as cli_mod
        cli_mod.main(['click', '--double', '450,320'])

        assert click_calls[0]['double'] is True

    def test_click_double_ref(self, monkeypatch, tmp_path):
        """tine click --double ref_1 sends double click via click_ref."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        # click_ref accepts double parameter (added for RSP C)
        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'ref_id': ref_id, 'double': double})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', '--double', 'ref_1'])

        assert click_ref_calls[0]['double'] is True

    def test_click_unknown_ref(self, monkeypatch, tmp_path, capsys):
        """tine click ref_99 with no such ref exits with error. click_ref not called."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append(ref_id)

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(['click', 'ref_99'])
        assert exc_info.value.code != 0

        err = capsys.readouterr().err
        assert 'ref_99' in err
        # CLI catches unknown refs before delegating
        assert len(click_ref_calls) == 0

    def test_click_no_ref_cache(self, monkeypatch, tmp_path, capsys):
        """tine click ref_1 with no cache file exits with error about missing cache."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        # Mock list_windows to isolate the "no cache" error path
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(['click', 'ref_1'])
        assert exc_info.value.code != 0

        err = capsys.readouterr().err
        assert 'ref cache' in err.lower() or 'tine tree' in err.lower()

    def test_click_bad_coords_format(self, monkeypatch, capsys):
        """tine click 'abc' is neither ref_N, grid cell, nor x,y — exits with error."""
        import tine.cli as cli_mod
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(['click', 'abc'])
        assert exc_info.value.code != 0

    def test_click_grid_cell(self, monkeypatch, tmp_path):
        """tine click B3 resolves grid cell center and clicks there."""
        click_calls = []

        def fake_click(x, y, button='left', double=False):
            click_calls.append({'x': x, 'y': y, 'button': button, 'double': double})

        import tine.input
        monkeypatch.setattr(tine.input, 'click', fake_click)

        import tine.cli as cli_mod
        cli_mod.main(['click', 'B3'])

        assert len(click_calls) == 1
        # B=col 1, 3=row 2 (zero-based). Default grid_size=100.
        # Center: (1*100+50, 2*100+50) = (150, 250)
        assert click_calls[0]['x'] == 150
        assert click_calls[0]['y'] == 250
        assert click_calls[0]['button'] == 'left'

    def test_click_grid_cell_custom_size(self, monkeypatch, tmp_path):
        """tine click --grid-size 200 A1 uses custom grid spacing."""
        click_calls = []

        def fake_click(x, y, button='left', double=False):
            click_calls.append({'x': x, 'y': y})

        import tine.input
        monkeypatch.setattr(tine.input, 'click', fake_click)

        import tine.cli as cli_mod
        cli_mod.main(['click', '--grid-size', '200', 'A1'])

        assert click_calls[0]['x'] == 100
        assert click_calls[0]['y'] == 100

    def test_click_grid_cell_case_insensitive(self, monkeypatch, tmp_path):
        """tine click b3 works (case insensitive)."""
        click_calls = []

        def fake_click(x, y, button='left', double=False):
            click_calls.append({'x': x, 'y': y})

        import tine.input
        monkeypatch.setattr(tine.input, 'click', fake_click)

        import tine.cli as cli_mod
        cli_mod.main(['click', 'b3'])

        assert click_calls[0]['x'] == 150
        assert click_calls[0]['y'] == 250

    def test_click_grid_cell_right_button(self, monkeypatch, tmp_path):
        """tine click --button right A1 passes button through."""
        click_calls = []

        def fake_click(x, y, button='left', double=False):
            click_calls.append({'button': button})

        import tine.input
        monkeypatch.setattr(tine.input, 'click', fake_click)

        import tine.cli as cli_mod
        cli_mod.main(['click', '--button', 'right', 'A1'])

        assert click_calls[0]['button'] == 'right'

    def test_click_grid_cell_double(self, monkeypatch, tmp_path):
        """tine click --double A1 sends double click."""
        click_calls = []

        def fake_click(x, y, button='left', double=False):
            click_calls.append({'double': double})

        import tine.input
        monkeypatch.setattr(tine.input, 'click', fake_click)

        import tine.cli as cli_mod
        cli_mod.main(['click', '--double', 'A1'])

        assert click_calls[0]['double'] is True


# ---------------------------------------------------------------------------
# Argument parsing — type
# ---------------------------------------------------------------------------

class TestTypeCommand:
    """tine type TEXT — type text via ydotool."""

    def test_type_basic(self, monkeypatch):
        """tine type 'hello world' calls type_text."""
        type_calls = []

        def fake_type_text(text, delay_ms=None):
            type_calls.append({'text': text, 'delay_ms': delay_ms})

        import tine.input
        monkeypatch.setattr(tine.input, 'type_text', fake_type_text)

        import tine.cli as cli_mod
        cli_mod.main(['type', 'hello world'])

        assert type_calls[0]['text'] == 'hello world'

    def test_type_with_delay(self, monkeypatch):
        """tine type --delay 50 'text' passes delay_ms."""
        type_calls = []

        def fake_type_text(text, delay_ms=None):
            type_calls.append({'delay_ms': delay_ms})

        import tine.input
        monkeypatch.setattr(tine.input, 'type_text', fake_type_text)

        import tine.cli as cli_mod
        cli_mod.main(['type', '--delay', '50', 'text'])

        assert type_calls[0]['delay_ms'] == 50

    def test_type_empty_string(self, monkeypatch):
        """tine type '' passes empty string through."""
        type_calls = []

        def fake_type_text(text, delay_ms=None):
            type_calls.append({'text': text})

        import tine.input
        monkeypatch.setattr(tine.input, 'type_text', fake_type_text)

        import tine.cli as cli_mod
        cli_mod.main(['type', ''])

        assert type_calls[0]['text'] == ''


# ---------------------------------------------------------------------------
# Argument parsing — key
# ---------------------------------------------------------------------------

class TestKeyCommand:
    """tine key COMBO — press key combination."""

    def test_key_basic(self, monkeypatch):
        """tine key ctrl+c calls key_combo."""
        key_calls = []

        def fake_key_combo(combo_str):
            key_calls.append(combo_str)

        import tine.input
        monkeypatch.setattr(tine.input, 'key_combo', fake_key_combo)

        import tine.cli as cli_mod
        cli_mod.main(['key', 'ctrl+c'])

        assert key_calls[0] == 'ctrl+c'

    def test_key_multi_modifier(self, monkeypatch):
        """tine key ctrl+shift+t passes full combo string."""
        key_calls = []

        def fake_key_combo(combo_str):
            key_calls.append(combo_str)

        import tine.input
        monkeypatch.setattr(tine.input, 'key_combo', fake_key_combo)

        import tine.cli as cli_mod
        cli_mod.main(['key', 'ctrl+shift+t'])

        assert key_calls[0] == 'ctrl+shift+t'

    def test_key_no_argument(self):
        """tine key with no argument exits with error."""
        import tine.cli as cli_mod
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(['key'])
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Argument parsing — windows
# ---------------------------------------------------------------------------

class TestWindowsCommand:
    """tine windows — list windows formatted for agent."""

    def test_windows_basic(self, monkeypatch, capsys):
        """Lists windows one per line with title, app, geometry, focus."""
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['windows'])

        out = capsys.readouterr().out
        assert 'Settings' in out
        assert 'Terminal' in out
        assert 'focused' in out
        assert '1024' in out
        assert '768' in out

    def test_windows_empty(self, monkeypatch, capsys):
        """No windows open prints informative message."""
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: [])

        import tine.cli as cli_mod
        cli_mod.main(['windows'])

        out = capsys.readouterr().out
        assert 'no windows' in out.lower() or out.strip() == ''


# ---------------------------------------------------------------------------
# Argument parsing — focus
# ---------------------------------------------------------------------------

class TestFocusCommand:
    """tine focus APP — raise and focus a window by app name."""

    def test_focus_by_wm_class(self, monkeypatch, capsys):
        """Matches by wm_class substring — uses a term that only matches wm_class."""
        focus_calls = []

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_focus_window', lambda w: focus_calls.append(w))

        # "gnome-terminal" matches wm_class "gnome-terminal-server" but
        # does NOT match title "Terminal" as a substring of wm_class
        cli_mod.main(['focus', 'gnome-terminal'])

        assert len(focus_calls) == 1
        assert focus_calls[0]['wm_class'] == 'gnome-terminal-server'

    def test_focus_by_title(self, monkeypatch, capsys):
        """Falls back to title match if wm_class doesn't match."""
        focus_calls = []

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_focus_window', lambda w: focus_calls.append(w))

        # "Settings" matches title but not wm_class "gnome-control-center"
        cli_mod.main(['focus', 'Settings'])

        assert len(focus_calls) == 1
        assert focus_calls[0]['title'] == 'Settings'

    def test_focus_no_match(self, monkeypatch, capsys):
        """No matching window exits with error."""
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_focus_window', lambda w: None)

        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(['focus', 'NonexistentApp'])
        assert exc_info.value.code != 0

    def test_focus_case_insensitive(self, monkeypatch, capsys):
        """Matching is case insensitive."""
        focus_calls = []

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_focus_window', lambda w: focus_calls.append(w))

        cli_mod.main(['focus', 'settings'])

        assert len(focus_calls) == 1


# ---------------------------------------------------------------------------
# _focus_window implementation — extension FocusWindow D-Bus method
# ---------------------------------------------------------------------------

class TestFocusWindowImpl:
    """_focus_window(window) — calls extension FocusWindow D-Bus method."""

    def _make_window(self, title='Firefox', wm_class='firefox'):
        return {
            'title': title,
            'wm_class': wm_class,
            'app_id': 'org.mozilla.firefox',
            'x': 0, 'y': 0, 'width': 1280, 'height': 800,
            'focused': False,
        }

    def test_calls_focus_window_method(self, monkeypatch):
        """Calls _gdbus_call('FocusWindow', title) to activate window."""
        calls = []

        def fake_gdbus(method, *args):
            calls.append((method, args))
            return '(true,)'

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, '_gdbus_call', fake_gdbus)

        import tine.cli as cli_mod
        cli_mod._focus_window(self._make_window('Firefox'))

        assert len(calls) == 1
        assert calls[0] == ('FocusWindow', ('Firefox',))

    def test_uses_title_as_query(self, monkeypatch):
        """Passes title to FocusWindow when both title and wm_class are present."""
        calls = []

        def fake_gdbus(method, *args):
            calls.append((method, args))
            return '(true,)'

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, '_gdbus_call', fake_gdbus)

        import tine.cli as cli_mod
        cli_mod._focus_window(self._make_window(title='Firefox', wm_class='firefox'))

        assert calls[0][1] == ('Firefox',)

    def test_falls_back_to_wm_class_when_no_title(self, monkeypatch):
        """Uses wm_class as query when title is empty."""
        calls = []

        def fake_gdbus(method, *args):
            calls.append((method, args))
            return '(true,)'

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, '_gdbus_call', fake_gdbus)

        import tine.cli as cli_mod
        cli_mod._focus_window({'title': '', 'wm_class': 'gnome-terminal-server'})

        assert calls[0][1] == ('gnome-terminal-server',)

    def test_focus_window_false_raises(self, monkeypatch):
        """FocusWindow returning (false, ...) raises RuntimeError."""
        def fake_gdbus(method, *args):
            return '(false,)'

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, '_gdbus_call', fake_gdbus)

        import tine.cli as cli_mod
        with pytest.raises(RuntimeError, match='false'):
            cli_mod._focus_window(self._make_window())

    def test_dbus_error_raises(self, monkeypatch):
        """RuntimeError from _gdbus_call propagates out of _focus_window."""
        def fake_gdbus(method, *args):
            raise RuntimeError('D-Bus call FocusWindow failed: No such name')

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, '_gdbus_call', fake_gdbus)

        import tine.cli as cli_mod
        with pytest.raises(RuntimeError):
            cli_mod._focus_window(self._make_window())

    def test_gdbus_not_found_raises(self, monkeypatch):
        """Missing gdbus binary raises RuntimeError."""
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        import subprocess
        monkeypatch.setattr(subprocess, 'run', fake_run)

        import tine.cli as cli_mod
        with pytest.raises(RuntimeError, match='gdbus not found'):
            cli_mod._focus_window(self._make_window())

    def test_gdbus_timeout_raises(self, monkeypatch):
        """gdbus timeout raises RuntimeError."""
        import subprocess as _subprocess

        def fake_run(cmd, **kwargs):
            raise _subprocess.TimeoutExpired(cmd, 10)

        import subprocess
        monkeypatch.setattr(subprocess, 'run', fake_run)

        import tine.cli as cli_mod
        with pytest.raises(RuntimeError, match='timed out'):
            cli_mod._focus_window(self._make_window())

    def test_no_title_or_wm_class_raises(self, monkeypatch):
        """Window with neither title nor wm_class raises RuntimeError."""
        import tine.cli as cli_mod
        with pytest.raises(RuntimeError, match='no title or wm_class'):
            cli_mod._focus_window({'title': '', 'wm_class': ''})

    def test_focus_error_propagates_to_cmd(self, monkeypatch, capsys):
        """_cmd_focus exits 1 when _focus_window raises RuntimeError."""
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod

        def raising_focus(w):
            raise RuntimeError('FocusWindow failed')

        monkeypatch.setattr(cli_mod, '_focus_window', raising_focus)

        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(['focus', 'Settings'])
        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert 'FocusWindow failed' in err


# ---------------------------------------------------------------------------
# Argument parsing — describe
# ---------------------------------------------------------------------------

class TestDescribeCommand:
    """tine describe [--annotate] [--scale F] — combined desktop state."""

    def test_describe_basic(self, monkeypatch, capsys, tmp_path):
        """Prints formatted describe output to stdout."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        describe_result = {
            'tree': TREE_OUTPUT,
            'screenshot': '/tmp/tine-describe-abc.png',
            'windows': _make_windows(),
            'ref_cache': _make_ref_cache(),
        }

        import tine.describe
        monkeypatch.setattr(tine.describe, 'describe', lambda **kw: describe_result)

        import tine.cli as cli_mod
        cli_mod.main(['describe'])

        out = capsys.readouterr().out
        assert 'Settings' in out
        assert 'Terminal' in out

    def test_describe_annotate(self, monkeypatch, capsys, tmp_path):
        """--annotate passes through to describe()."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        describe_calls = []

        def fake_describe(app=None, annotate=False, scale=1.0):
            describe_calls.append({'annotate': annotate, 'scale': scale})
            return {
                'tree': '', 'screenshot': '/tmp/x.png',
                'windows': [], 'ref_cache': {},
            }

        import tine.describe
        monkeypatch.setattr(tine.describe, 'describe', fake_describe)

        import tine.cli as cli_mod
        cli_mod.main(['describe', '--annotate', '--scale', '1.25'])

        assert describe_calls[0]['annotate'] is True
        assert describe_calls[0]['scale'] == 1.25

    def test_describe_saves_ref_cache_with_app(self, monkeypatch, capsys, tmp_path):
        """describe saves ref_cache and injects _app from the tree root."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        # describe() returns ref_cache without _app — CLI must inject it
        # from the tree content (root application name)
        raw_ref_cache = {
            'ref_1': {
                'role': 'button', 'name': 'Wi-Fi',
                'bbox': {'x': 6, 'y': 136, 'w': 233, 'h': 43},
            },
        }
        describe_result = {
            'tree': "application 'Settings'\n  button 'Wi-Fi' [ref_1]",
            'screenshot': '/tmp/tine-describe-abc.png',
            'windows': _make_windows(),
            'ref_cache': raw_ref_cache,
        }

        import tine.describe
        monkeypatch.setattr(tine.describe, 'describe', lambda **kw: describe_result)

        import tine.cli as cli_mod
        cli_mod.main(['describe'])

        assert os.path.exists(ref_cache_path)
        saved = _read_ref_cache(ref_cache_path)
        # CLI must inject _app so subsequent clicks resolve window offset
        assert '_app' in saved
        assert 'ref_1' in saved
        assert saved['ref_1']['name'] == 'Wi-Fi'


# ---------------------------------------------------------------------------
# Ref cache lifecycle
# ---------------------------------------------------------------------------

class TestRefCacheLifecycle:
    """Ref cache is saved by tree/describe and loaded by click/screenshot."""

    def test_tree_saves_click_loads_with_correct_coords(self, monkeypatch, tmp_path):
        """Full lifecycle: tree saves cache with _app, click uses it with correct offset."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        # Step 1: tree saves cache
        raw_tree = {
            'role': 'application', 'name': 'Settings',
            'bbox': {'x': 0, 'y': 0, 'w': 1024, 'h': 768},
            'states': [], 'actions': [], 'children': [
                {
                    'role': 'button', 'name': 'Wi-Fi',
                    'bbox': {'x': 6, 'y': 136, 'w': 233, 'h': 43},
                    'states': ['sensitive', 'showing', 'visible'],
                    'actions': ['click'], 'children': [],
                },
            ],
        }

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_walk_atspi', lambda app=None: raw_tree)
        cli_mod.main(['tree'])

        # Verify cache has _app and ref
        cache = _read_ref_cache(ref_cache_path)
        assert cache['_app'] == 'Settings'
        assert 'ref_1' in cache

        # Step 2: click loads cache and passes correct offset to click_ref
        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({
                'ref_id': ref_id,
                'window_offset': window_offset,
            })

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)

        # Settings matches title "Settings" — at (72, 32)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        cli_mod.main(['click', 'ref_1'])

        assert len(click_ref_calls) == 1
        assert click_ref_calls[0]['ref_id'] == 'ref_1'
        assert click_ref_calls[0]['window_offset'] == (72, 32)

    def test_default_cache_path(self, monkeypatch, tmp_path):
        """Without TINE_REF_CACHE env var, uses /tmp/tine-refs.json."""
        monkeypatch.delenv('TINE_REF_CACHE', raising=False)

        import tine.cli as cli_mod
        path = cli_mod._ref_cache_path()
        assert path == '/tmp/tine-refs.json'


# ---------------------------------------------------------------------------
# Window offset resolution
# ---------------------------------------------------------------------------

class TestWindowOffsetResolution:
    """AT-SPI2 coords on Wayland are surface-local.
    screen_x = window_x + element_local_x.
    The CLI matches _app in ref cache to windows by wm_class/title."""

    def test_offset_matches_app_not_focused(self, monkeypatch, tmp_path):
        """Offset comes from the _app-matched window, not the focused window."""
        ref_cache_path = str(tmp_path / 'refs.json')
        # _app is gnome-control-center → matches Settings window at (72, 32)
        # Terminal is focused at (200, 100) — should NOT be used
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        # Must be Settings offset (72, 32), NOT Terminal (200, 100)
        assert click_ref_calls[0]['window_offset'] == (72, 32)

    def test_offset_zero_when_no_match(self, monkeypatch, tmp_path):
        """When no window matches _app, offset defaults to (0, 0)."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('unknown-app-xyz'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        assert click_ref_calls[0]['window_offset'] == (0, 0)

    def test_offset_matches_by_title_fallback(self, monkeypatch, tmp_path):
        """If _app doesn't match any wm_class, falls back to title match."""
        ref_cache_path = str(tmp_path / 'refs.json')
        # _app is "Settings" — doesn't match any wm_class, but matches title
        _write_ref_cache(ref_cache_path, _make_ref_cache('Settings'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        # Settings window at (72, 32)
        assert click_ref_calls[0]['window_offset'] == (72, 32)

    def test_annotation_offset_matches_click_offset(self, monkeypatch, tmp_path):
        """Screenshot annotation uses the same offset resolution as click."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        annotate_calls = []

        def fake_capture(output_path=None):
            path = output_path or str(tmp_path / 'auto.png')
            open(path, 'w').close()
            return path

        def fake_annotate(image_path, ref_cache, output_path=None, scale=1.0):
            annotate_calls.append({'ref_cache': ref_cache})
            return output_path or image_path

        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'capture', fake_capture)
        monkeypatch.setattr(tine.screenshot, 'annotate', fake_annotate)
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())
        monkeypatch.setenv('TINE_SHADOW_OFFSET', '0')  # isolate window-offset behaviour

        import tine.cli as cli_mod
        cli_mod.main(['screenshot', '--annotate'])

        adjusted = annotate_calls[0]['ref_cache']
        # ref_2 original: x=100, y=50. Settings offset: (72, 32).
        assert adjusted['ref_2']['bbox']['x'] == 100 + 72
        assert adjusted['ref_2']['bbox']['y'] == 50 + 32

    def test_offset_substring_match(self, monkeypatch, tmp_path):
        """_app substring matches wm_class, consistent with focus behavior."""
        ref_cache_path = str(tmp_path / 'refs.json')
        # "gnome-control" is a substring of "gnome-control-center"
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        assert click_ref_calls[0]['window_offset'] == (72, 32)

    def test_offset_case_insensitive(self, monkeypatch, tmp_path):
        """_app matching is case insensitive."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('GNOME-CONTROL-CENTER'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        assert click_ref_calls[0]['window_offset'] == (72, 32)

    def test_offset_empty_app_warns(self, monkeypatch, tmp_path, capsys):
        """Empty _app (unfiltered tree walk) defaults to (0,0) with stderr warning."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache(''))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        assert click_ref_calls[0]['window_offset'] == (0, 0)
        err = capsys.readouterr().err
        assert 'window' in err.lower() and 'offset' in err.lower()

    def test_offset_missing_app_key(self, monkeypatch, tmp_path):
        """Cache with no _app key (legacy/hand-edited) defaults to (0,0), no crash."""
        ref_cache_path = str(tmp_path / 'refs.json')
        cache_no_app = {
            'ref_1': {
                'role': 'button', 'name': 'OK',
                'bbox': {'x': 10, 'y': 20, 'w': 80, 'h': 30},
            },
        }
        _write_ref_cache(ref_cache_path, cache_no_app)
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        assert click_ref_calls[0]['window_offset'] == (0, 0)

    def test_offset_list_windows_failure(self, monkeypatch, tmp_path, capsys):
        """list_windows failure falls back to (0,0) offset with warning."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache('gnome-control-center'))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        click_ref_calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            click_ref_calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)

        import tine.screenshot
        def failing_list_windows():
            raise RuntimeError('D-Bus call ListWindows failed')
        monkeypatch.setattr(tine.screenshot, 'list_windows', failing_list_windows)

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])

        # Falls back to (0,0) rather than crashing
        assert click_ref_calls[0]['window_offset'] == (0, 0)
        err = capsys.readouterr().err
        assert 'window' in err.lower()

    # --- Normalization tests (AT-SPI2 hyphens vs reverse-domain dots) -------

    def _click_ref_calls_for(self, monkeypatch, tmp_path, app_name, windows):
        """Helper: run 'click ref_1' with given _app and windows, return calls."""
        ref_cache_path = str(tmp_path / 'refs.json')
        _write_ref_cache(ref_cache_path, _make_ref_cache(app_name))
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        calls = []

        def fake_click_ref(ref_id, ref_cache, window_offset=(0, 0),
                           button='left', double=False):
            calls.append({'window_offset': window_offset})

        import tine.input
        monkeypatch.setattr(tine.input, 'click_ref', fake_click_ref)
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: windows)

        import tine.cli as cli_mod
        cli_mod.main(['click', 'ref_1'])
        return calls

    def test_offset_atspi_hyphen_matches_reverse_domain_wm_class(
            self, monkeypatch, tmp_path):
        """AT-SPI2 'gnome-calculator' matches wm_class 'org.gnome.Calculator'."""
        windows = [
            {
                'title': 'Calculator',
                'wm_class': 'org.gnome.Calculator',
                'app_id': 'org.gnome.Calculator',
                'x': 300, 'y': 200, 'width': 400, 'height': 500,
                'focused': True,
            },
        ]
        calls = self._click_ref_calls_for(
            monkeypatch, tmp_path, 'gnome-calculator', windows)
        assert calls[0]['window_offset'] == (300, 200)

    def test_offset_multiword_component_atspi_vs_camelcase_wm_class(
            self, monkeypatch, tmp_path):
        """'gnome-text-editor' matches 'org.gnome.TextEditor' (camelCase component)."""
        windows = [
            {
                'title': 'Text Editor',
                'wm_class': 'org.gnome.TextEditor',
                'app_id': 'org.gnome.TextEditor',
                'x': 50, 'y': 75, 'width': 800, 'height': 600,
                'focused': True,
            },
        ]
        calls = self._click_ref_calls_for(
            monkeypatch, tmp_path, 'gnome-text-editor', windows)
        assert calls[0]['window_offset'] == (50, 75)

    def test_offset_simple_name_no_normalization_needed(
            self, monkeypatch, tmp_path):
        """'firefox' matches wm_class 'firefox' — no normalization required."""
        windows = [
            {
                'title': 'Mozilla Firefox',
                'wm_class': 'firefox',
                'app_id': '',
                'x': 0, 'y': 0, 'width': 1200, 'height': 800,
                'focused': True,
            },
        ]
        calls = self._click_ref_calls_for(
            monkeypatch, tmp_path, 'firefox', windows)
        assert calls[0]['window_offset'] == (0, 0)

    def test_offset_gnome_shell_matches_reverse_domain_wm_class(
            self, monkeypatch, tmp_path):
        """'gnome-shell' matches wm_class 'org.gnome.Shell'."""
        windows = [
            {
                'title': 'GNOME Shell',
                'wm_class': 'org.gnome.Shell',
                'app_id': 'org.gnome.Shell',
                'x': 10, 'y': 20, 'width': 1920, 'height': 1080,
                'focused': True,
            },
        ]
        calls = self._click_ref_calls_for(
            monkeypatch, tmp_path, 'gnome-shell', windows)
        assert calls[0]['window_offset'] == (10, 20)

    def test_offset_via_app_id_when_wm_class_absent(
            self, monkeypatch, tmp_path):
        """Falls through to app_id match when wm_class doesn't match."""
        windows = [
            {
                'title': 'Calculator',
                'wm_class': '',
                'app_id': 'org.gnome.Calculator',
                'x': 100, 'y': 150, 'width': 400, 'height': 500,
                'focused': True,
            },
        ]
        calls = self._click_ref_calls_for(
            monkeypatch, tmp_path, 'gnome-calculator', windows)
        assert calls[0]['window_offset'] == (100, 150)

    def test_offset_none_app_name_returns_zero(self, monkeypatch, tmp_path, capsys):
        """None _app (missing key) falls back to (0, 0) without crashing."""
        import tine.cli as cli_mod
        result = cli_mod._resolve_window_offset(None)
        assert result == (0, 0)
        err = capsys.readouterr().err
        assert 'offset' in err.lower()

    def test_offset_prefers_focused_among_duplicates(
            self, monkeypatch, tmp_path):
        """When multiple windows match the same wm_class, prefer the focused one.

        GNOME Shell can return duplicate entries for a single app window
        (e.g. shadow/buffer actor + real actor). The focused window has
        the correct frame_rect position.
        """
        windows = [
            {
                'title': 'Calculator',
                'wm_class': 'org.gnome.Calculator',
                'app_id': 'org.gnome.Calculator',
                'x': 588, 'y': 140, 'width': 360, 'height': 616,
                'focused': False,
            },
            {
                'title': 'Calculator',
                'wm_class': 'org.gnome.Calculator',
                'app_id': 'org.gnome.Calculator',
                'x': 638, 'y': 190, 'width': 360, 'height': 616,
                'focused': True,
            },
        ]
        calls = self._click_ref_calls_for(
            monkeypatch, tmp_path, 'gnome-calculator', windows)
        assert calls[0]['window_offset'] == (638, 190)


# ---------------------------------------------------------------------------
# _normalize_app_name unit tests
# ---------------------------------------------------------------------------

class TestNormalizeAppName:
    """Unit tests for _normalize_app_name() normalization helper."""

    def _norm(self, name):
        import tine.cli as cli_mod
        return cli_mod._normalize_app_name(name)

    def test_atspi_hyphen_name(self):
        assert self._norm('gnome-calculator') == 'gnomecalculator'

    def test_reverse_domain_wm_class(self):
        assert self._norm('org.gnome.Calculator') == 'gnomecalculator'

    def test_both_normalize_to_same(self):
        assert self._norm('gnome-calculator') == self._norm('org.gnome.Calculator')

    def test_multiword_camelcase_wm_class(self):
        assert self._norm('org.gnome.TextEditor') == 'gnometexteditor'

    def test_multiword_hyphenated_atspi(self):
        assert self._norm('gnome-text-editor') == 'gnometexteditor'

    def test_multiword_both_normalize_to_same(self):
        assert self._norm('gnome-text-editor') == self._norm('org.gnome.TextEditor')

    def test_simple_no_prefix(self):
        assert self._norm('firefox') == 'firefox'

    def test_gnome_shell(self):
        assert self._norm('gnome-shell') == self._norm('org.gnome.Shell')

    def test_empty_string(self):
        assert self._norm('') == ''

    def test_none(self):
        assert self._norm(None) == ''

    def test_com_prefix_stripped(self):
        assert self._norm('com.example.MyApp') == 'examplemyapp'

    def test_case_insensitive(self):
        assert self._norm('GNOME-CALCULATOR') == self._norm('org.gnome.Calculator')


# ---------------------------------------------------------------------------
# AT-SPI2 subprocess walker
# ---------------------------------------------------------------------------

class TestAtspiWalker:
    """_walk_atspi runs AT-SPI2 queries in a subprocess to avoid D-Bus cache."""

    def test_walker_returns_tree_dict(self, monkeypatch):
        """The walker subprocess returns a JSON tree dict."""
        expected_tree = {
            'role': 'application', 'name': 'Settings',
            'bbox': {'x': 0, 'y': 0, 'w': 1024, 'h': 768},
            'states': [], 'actions': [], 'children': [],
        }

        def fake_subprocess_run(cmd, **kwargs):
            return type('R', (), {
                'returncode': 0,
                'stdout': json.dumps(expected_tree),
                'stderr': '',
            })()

        import subprocess
        monkeypatch.setattr(subprocess, 'run', fake_subprocess_run)

        import tine.cli as cli_mod
        result = cli_mod._walk_atspi()
        assert result['role'] == 'application'
        assert result['name'] == 'Settings'

    def test_walker_accepts_app_name_even_if_empty_string(self, monkeypatch):
        """BUG: _walk_atspi drops app filter when app='' due to truthiness check.

        Empty string can be a legitimate filter value (e.g. user explicitly passes
        --app '' from a higher-level wrapper). Implementation uses `if app:` so
        it fails to pass through empty-string values.
        """
        received_cmds = []

        def fake_subprocess_run(cmd, **kwargs):
            received_cmds.append(cmd)
            return type('R', (), {
                'returncode': 0,
                'stdout': json.dumps({'role': 'application', 'name': '', 'bbox': {}, 'states': [], 'actions': [], 'children': []}),
                'stderr': '',
            })()

        import subprocess
        monkeypatch.setattr(subprocess, 'run', fake_subprocess_run)

        import tine.cli as cli_mod
        cli_mod._walk_atspi(app='')

        # Expect the empty app argument to be forwarded as a positional arg
        assert received_cmds, 'subprocess.run should be called'
        assert received_cmds[0][-1] == ''

    def test_walker_subprocess_failure(self, monkeypatch):
        """Subprocess failure raises RuntimeError."""
        def fake_subprocess_run(cmd, **kwargs):
            return type('R', (), {
                'returncode': 1,
                'stdout': '',
                'stderr': 'AT-SPI2 connection failed',
            })()

        import subprocess
        monkeypatch.setattr(subprocess, 'run', fake_subprocess_run)

        import tine.cli as cli_mod
        with pytest.raises(RuntimeError, match='AT-SPI2'):
            cli_mod._walk_atspi()

    def test_walker_empty_stdout_is_wrapped_as_runtime_error(self, monkeypatch):
        """BUG: _walk_atspi lets JSONDecodeError escape on empty stdout.

        If subprocess returns 0 but stdout is empty (common for misconfigured
        AT-SPI sessions), json.loads('') raises JSONDecodeError, not RuntimeError.
        """

        def fake_subprocess_run(cmd, **kwargs):
            return type('R', (), {
                'returncode': 0,
                'stdout': '',
                'stderr': '',
            })()

        import subprocess
        monkeypatch.setattr(subprocess, 'run', fake_subprocess_run)

        import tine.cli as cli_mod
        with pytest.raises(RuntimeError):
            cli_mod._walk_atspi()

    def test_tree_command_handles_non_dict_tree_gracefully(self, monkeypatch, tmp_path):
        """BUG: _cmd_tree assumes _walk_atspi returns dict; list crashes with AttributeError."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_walk_atspi', lambda app=None: [])

        with pytest.raises(SystemExit):
            cli_mod.main(['tree'])

    def test_walker_passes_app_filter(self, monkeypatch):
        """App filter is passed as argument to the subprocess."""
        received_cmds = []

        def fake_subprocess_run(cmd, **kwargs):
            received_cmds.append(cmd)
            return type('R', (), {
                'returncode': 0,
                'stdout': json.dumps({
                    'role': 'application', 'name': 'Firefox',
                    'bbox': {}, 'states': [], 'actions': [], 'children': [],
                }),
                'stderr': '',
            })()

        import subprocess
        monkeypatch.setattr(subprocess, 'run', fake_subprocess_run)

        import tine.cli as cli_mod
        cli_mod._walk_atspi(app='Firefox')

        cmd_str = ' '.join(str(c) for c in received_cmds[0])
        assert 'Firefox' in cmd_str


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """CLI exits cleanly with useful error messages on stderr."""

    def test_no_subcommand(self, capsys):
        """No subcommand prints usage and exits."""
        import tine.cli as cli_mod
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main([])
        assert exc_info.value.code != 0

    def test_unknown_subcommand(self, capsys):
        """Unknown subcommand exits with error."""
        import tine.cli as cli_mod
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(['bogus'])
        assert exc_info.value.code != 0

    def test_tree_walker_error_message_on_stderr(self, monkeypatch, capsys, tmp_path):
        """When walker fails, error message goes to stderr."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        def fake_walk_atspi(app=None):
            raise RuntimeError('Cannot connect to AT-SPI2 registry')

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_walk_atspi', fake_walk_atspi)

        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(['tree'])
        assert exc_info.value.code != 0

        err = capsys.readouterr().err
        assert 'AT-SPI2' in err

    def test_click_target_required(self):
        """tine click with no target exits with error."""
        import tine.cli as cli_mod
        with pytest.raises(SystemExit):
            cli_mod.main(['click'])

    def test_type_text_required(self):
        """tine type with no text exits with error."""
        import tine.cli as cli_mod
        with pytest.raises(SystemExit):
            cli_mod.main(['type'])

    def test_focus_app_required(self):
        """tine focus with no app exits with error."""
        import tine.cli as cli_mod
        with pytest.raises(SystemExit):
            cli_mod.main(['focus'])


# ---------------------------------------------------------------------------
# Output formatting for agents
# ---------------------------------------------------------------------------

class TestOutputFormatting:
    """Output is plain text optimized for LLM consumption."""

    def test_windows_format_plain_text(self, monkeypatch, capsys):
        """Windows output is one line per window, no tables."""
        import tine.screenshot
        monkeypatch.setattr(tine.screenshot, 'list_windows', lambda: _make_windows())

        import tine.cli as cli_mod
        cli_mod.main(['windows'])

        out = capsys.readouterr().out
        lines = [l for l in out.strip().split('\n') if l.strip()]
        assert len(lines) >= 2
        # No markdown table formatting
        assert '|' not in out
        assert '---' not in out

    def test_tree_output_indented_text(self, monkeypatch, capsys, tmp_path):
        """Tree output uses indented text format from format_tree."""
        ref_cache_path = str(tmp_path / 'refs.json')
        monkeypatch.setenv('TINE_REF_CACHE', ref_cache_path)

        raw_tree = {
            'role': 'application', 'name': 'App',
            'bbox': {'x': 0, 'y': 0, 'w': 800, 'h': 600},
            'states': [], 'actions': [], 'children': [
                {
                    'role': 'button', 'name': 'OK',
                    'bbox': {'x': 10, 'y': 10, 'w': 80, 'h': 30},
                    'states': ['sensitive', 'showing', 'visible'],
                    'actions': ['click'], 'children': [],
                },
            ],
        }

        import tine.cli as cli_mod
        monkeypatch.setattr(cli_mod, '_walk_atspi', lambda app=None: raw_tree)
        cli_mod.main(['tree'])

        out = capsys.readouterr().out
        assert '  button' in out
        assert "'OK'" in out
        assert '[ref_1]' in out
