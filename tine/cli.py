"""Tine CLI: AI agent interface to a GNOME Wayland desktop.

Entry point: main(argv). Subcommands: tree, screenshot, click, type, key,
windows, focus, describe, target.
"""

import argparse
import copy
import os
import re
import subprocess
import sys
import time

import tine.describe
import tine.input
import tine.inputd
import tine.refs
import tine.screenshot
import tine.tree
from tine.display import get_display_config
from tine.refs import REF_ID_PATTERN, is_text_ref
from tine.walker import walk_atspi as _walk_atspi
from tine.window_offset import (
    normalize_app_name as _normalize_app_name,
    resolve_window_offset as _resolve_window_offset,
)

# Post-click verification: settle delay between the click and the
# after-walk. Kept short because most UI state transitions (button
# toggles, focus changes, menu opens) update the AT-SPI2 tree within
# ~100ms; slower transitions like full Firefox navigation will produce
# spurious warnings but the brief accepts that as a warning-level
# signal the agent can filter.
_CLICK_VERIFY_SETTLE_SEC = 0.3


def _click_verify_enabled():
    """Post-click AT-SPI2 verification is on by default; disable with
    TINE_NO_VERIFY_CLICK=1 for benchmarking or when running against an
    app where the tree walk is pathologically slow.
    """
    return os.environ.get('TINE_NO_VERIFY_CLICK', '').strip() not in ('1', 'true', 'yes')


def _verify_click_before(app_name):
    """Walk AT-SPI2 before the click and return (app, hash) or None when
    verification can't run.

    Returns None in three situations:

      * verification disabled via ``TINE_NO_VERIFY_CLICK``
      * no ``_app`` available to scope the walk to — without it,
        ``_walk_atspi(app='')`` would fall back to walking the whole
        desktop, which hashes unrelated windows and produces misleading
        signals. Better to skip quietly
      * the walker raised. In that case we print the error to stderr so
        the user knows the safety layer isn't running, but we do not
        block the click itself
    """
    if not _click_verify_enabled():
        return None
    if not app_name:
        return None
    try:
        raw = _walk_atspi(app=app_name)
        return (app_name, tine.tree.hash_subtree(raw))
    except Exception as exc:
        print(
            f'warning: click verification skipped — pre-click walker error: {exc}',
            file=sys.stderr,
        )
        return None


def _verify_click_after(before):
    """Walk AT-SPI2 after the click and warn if the tree hash matches the
    before-hash captured by ``_verify_click_before``.

    Walker errors on the after-walk are reported (not swallowed) so a
    broken walker can't silently suppress the warning every time.
    """
    if before is None:
        return
    before_app, before_hash = before
    time.sleep(_CLICK_VERIFY_SETTLE_SEC)
    try:
        raw = _walk_atspi(app=before_app)
        after_hash = tine.tree.hash_subtree(raw)
    except Exception as exc:
        print(
            f'warning: click verification incomplete — post-click walker error: {exc}',
            file=sys.stderr,
        )
        return
    if after_hash == before_hash:
        print(
            'warning: click may not have landed — AT-SPI2 tree unchanged '
            f'after click (app={before_app!r}). The ref bbox may be wrong '
            'for this app; try `tine activate` or click via ref_tN / grid. '
            'Live-region content (tickers, spinners) can also mask this '
            'signal — treat as hint, not error.',
            file=sys.stderr,
        )


_GRID_DIMS_RE = re.compile(r'^\s*(\d+)\s*[xX]\s*(\d+)\s*$')


def _parse_grid_dims(spec):
    """Parse a ``COLSxROWS`` grid spec into a ``(cols, rows)`` tuple.

    Rejects malformed input with a clear message — agents pass this verbatim
    from prompt scaffolding so an opaque parse error costs a round trip.
    """
    if not isinstance(spec, str):
        raise ValueError(f'--grid expects COLSxROWS (e.g. 5x3), got {spec!r}')
    m = _GRID_DIMS_RE.match(spec)
    if not m:
        raise ValueError(
            f'--grid expects COLSxROWS (e.g. 5x3), got {spec!r}'
        )
    cols = int(m.group(1))
    rows = int(m.group(2))
    if cols < 1 or rows < 1:
        raise ValueError(
            f'--grid dimensions must be >= 1, got {cols}x{rows}'
        )
    return cols, rows


_ALLOWED_CACHE_DIRS = (
    '/tmp',
    os.path.expanduser('~/.cache/tine'),
)


def _safe_coord(v):
    """Safely cast a bounding box coordinate value to int, returning 0 on failure."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _ref_cache_path():
    """Return path for the ref cache file.

    Validates TINE_REF_CACHE is under an allowed directory to prevent
    arbitrary file writes via environment variable injection.
    """
    path = os.environ.get('TINE_REF_CACHE', '/tmp/tine-refs.json')
    # Resolve to absolute path (also strips null bytes on Linux)
    resolved = os.path.realpath(os.path.abspath(path))
    for allowed in _ALLOWED_CACHE_DIRS:
        allowed_abs = os.path.realpath(os.path.abspath(allowed))
        if resolved.startswith(allowed_abs + os.sep):
            return resolved
    raise ValueError(
        f'TINE_REF_CACHE path "{path}" is not under an allowed directory '
        f'({", ".join(_ALLOWED_CACHE_DIRS)})'
    )



def _focus_window(window):
    """Raise and focus a window via the extension's FocusWindow D-Bus method.

    Calls org.gnome.OCRScreenshot.FocusWindow with the window title (or
    wm_class as fallback).  The extension does case-insensitive substring
    matching and calls meta_window.activate().

    Raises RuntimeError if the D-Bus call fails or no window is matched.
    """
    title = window.get('title', '')
    wm_class = window.get('wm_class', '')
    query = title or wm_class
    if not query:
        raise RuntimeError('_focus_window: window has no title or wm_class to match on')

    try:
        stdout = tine.screenshot._gdbus_call('FocusWindow', query)
    except FileNotFoundError:
        raise RuntimeError('gdbus not found; cannot focus window')
    except subprocess.TimeoutExpired:
        raise RuntimeError('gdbus call timed out while trying to focus window')

    if '(false' in stdout:
        raise RuntimeError(
            f'FocusWindow returned false — no window matched "{query}"'
        )


def _cmd_tree(args):
    """Handle 'tree' subcommand."""
    try:
        raw_tree = _walk_atspi(app=args.app)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if not isinstance(raw_tree, dict):
        print(f'Error: AT-SPI2 walker returned unexpected type: {type(raw_tree).__name__}',
              file=sys.stderr)
        sys.exit(1)

    collapsed = tine.tree.collapse_containers(raw_tree)
    annotated, ref_cache = tine.tree.assign_refs(collapsed)
    output = tine.tree.format_tree(annotated)
    print(output)

    # Save ref cache with _app metadata.
    # Fall back to raw_tree['application']['name'] when the top-level 'name'
    # key is absent (atspi_walker may omit it if the root node has no name).
    app_name = (raw_tree.get('name', '')
                or raw_tree.get('application', {}).get('name', ''))
    ref_cache['_app'] = app_name
    tine.refs.save_ref_cache(ref_cache, _ref_cache_path())


def _cmd_screenshot(args):
    """Handle 'screenshot' subcommand."""
    path = tine.screenshot.capture(output_path=args.output)

    cache = tine.refs.load_ref_cache(_ref_cache_path())
    real_refs = {k: v for k, v in cache.items() if k != '_app'} if cache else {}
    app_name = cache.get('_app', '') if cache else ''

    if args.annotate and real_refs:
        offset = _resolve_window_offset(app_name)

        adjusted = {}
        for ref_id, ref_data in real_refs.items():
            if not isinstance(ref_data, dict):
                continue
            adjusted_data = copy.deepcopy(ref_data)
            bbox = adjusted_data.get('bbox') or {}
            adjusted_data['bbox'] = bbox
            coord_space = bbox.get('coord', 'window')
            if coord_space != 'screen':
                bbox['x'] = _safe_coord(bbox.get('x', 0)) + offset[0]
                bbox['y'] = _safe_coord(bbox.get('y', 0)) + offset[1]
            adjusted[ref_id] = adjusted_data

        tine.screenshot.annotate(path, adjusted, scale=args.scale)

    if args.grid is not None:
        try:
            cols, rows = _parse_grid_dims(args.grid)
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)
        tine.screenshot.grid_overlay(path, cols=cols, rows=rows)

    if getattr(args, 'ocr', False):
        from tine.ocr import (
            assign_text_refs, dedup_against_atspi, detect_text,
            next_text_ref_index,
        )
        offset = _resolve_window_offset(app_name) if app_name else (0, 0)
        detections = detect_text(path, scale=args.scale)
        detections = dedup_against_atspi(
            detections, cache or {}, window_offset=offset, scale=args.scale,
        )
        merged = dict(cache) if cache else {}
        start_index = next_text_ref_index(merged)
        text_refs = assign_text_refs(detections, start_index=start_index)
        merged.update(text_refs)
        if app_name:
            merged['_app'] = app_name
        tine.refs.save_ref_cache(merged, _ref_cache_path())
        for ref_id, entry in text_refs.items():
            bbox = entry.get('bbox') or {}
            print(
                f"{ref_id}: {entry.get('text', '')!r} "
                f"at ({bbox.get('x')},{bbox.get('y')}) "
                f"{bbox.get('w')}x{bbox.get('h')}",
                file=sys.stderr,
            )

    print(path)


def _cmd_click(args):
    """Handle 'click' subcommand."""
    target = args.target

    # Check if target is a ref (ref_N or ref_tN pattern)
    if REF_ID_PATTERN.match(target):
        cache = tine.refs.load_ref_cache(_ref_cache_path())
        if not cache:
            hint = 'tine describe --ocr' if is_text_ref(target) else 'tine tree'
            print(f'Error: no ref cache found. Run "{hint}" first.',
                  file=sys.stderr)
            sys.exit(1)

        if target not in cache:
            print(f'Error: {target} not found in ref cache', file=sys.stderr)
            sys.exit(1)

        if not isinstance(cache[target], dict):
            print(f'Error: ref cache entry for {target} is corrupt (expected dict)',
                  file=sys.stderr)
            sys.exit(1)

        # Text refs are always screen-absolute (coord=screen on the bbox);
        # click_ref reads that and skips the window-offset adjustment.
        # AT-SPI2 refs still need the offset resolved.
        if is_text_ref(target):
            offset = (0, 0)
            verify_before = None
        else:
            app_name = cache.get('_app', '')
            offset = _resolve_window_offset(app_name)
            # Capture a before-click AT-SPI2 hash so we can warn if the
            # click lands somewhere that doesn't change the tree at all.
            verify_before = _verify_click_before(app_name)
        kwargs = {'window_offset': offset, 'button': args.button}
        if args.double:
            kwargs['double'] = True
        tine.input.click_ref(target, cache, **kwargs)
        _verify_click_after(verify_before)

    # Check if target is a grid cell (e.g. B3, AA12)
    elif re.match(r'^[A-Za-z]+\d+$', target):
        try:
            cols, rows = _parse_grid_dims(args.grid)
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)
        config = get_display_config()
        width = config['physical_width']
        height = config['physical_height']
        try:
            x, y = tine.screenshot.grid_cell_center(
                target, width, height, cols=cols, rows=rows,
            )
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)
        tine.input.click(x, y, button=args.button, double=args.double)

    # Check if target is coordinates (x,y pattern) — ASCII flag ensures \d
    # matches only [0-9], excluding Unicode digit codepoints that pass the
    # regex but cause int() to raise ValueError. Negative values are allowed
    # for multi-monitor setups where the origin may not be top-left of all displays.
    elif re.match(r'^-?\d+,-?\d+$', target, re.ASCII):
        parts = target.split(',')
        x, y = int(parts[0]), int(parts[1])
        tine.input.click(x, y, button=args.button, double=args.double)

    else:
        print(f'Error: "{target}" is not a valid ref (ref_N), grid cell (e.g. B3), or coordinates (x,y)',
              file=sys.stderr)
        sys.exit(1)


def _cmd_target(args):
    """Handle 'target' subcommand — crosshair + 3x3 sub-grid for click refinement."""
    position = args.position

    # Resolve position to (x, y) pixel coordinates
    if re.match(r'^-?\d+,-?\d+$', position, re.ASCII):
        parts = position.split(',')
        x, y = int(parts[0]), int(parts[1])
        # region_size default for coord positions: a fixed fallback that's
        # roughly cell-sized on a 5x3 at 1920x1080.
        region_size = args.size if args.size is not None else 150
    elif re.match(r'^[A-Za-z]+\d+$', position):
        try:
            cols, rows = _parse_grid_dims(args.grid)
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)
        config = get_display_config()
        width = config['physical_width']
        height = config['physical_height']
        try:
            x, y = tine.screenshot.grid_cell_center(
                position, width, height, cols=cols, rows=rows,
            )
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)
        # Match region size to the resolved cell so the 3x3 sub-grid maps
        # cleanly onto the drawn cell when no explicit --size is given.
        cell_w = width // cols
        cell_h = height // rows
        region_size = args.size if args.size is not None else min(cell_w, cell_h)
    else:
        print(f'Error: "{position}" is not a valid grid cell (e.g. E5) or coordinates (x,y)',
              file=sys.stderr)
        sys.exit(1)

    # If --pick is specified, compute sub-cell center
    if args.pick is not None:
        try:
            x, y = tine.screenshot.target_subcell_center(x, y, args.pick, region_size)
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)

        # Click the target position
        tine.input.click(x, y)

        if not args.refine:
            print(f'{x},{y}')
            return

        # --refine: take new screenshot and show 3x3 at clicked position
        region_size = region_size // 3 or 1

    # Take screenshot and draw overlay
    path = tine.screenshot.capture(output_path=args.output)
    tine.screenshot.target_overlay(path, x, y, region_size=region_size)

    print(path)
    print(f'target: {x},{y}')
    print('subcells:')
    for row_picks in [(7, 8, 9), (4, 5, 6), (1, 2, 3)]:
        cells = []
        for p in row_picks:
            sx, sy = tine.screenshot.target_subcell_center(x, y, p, region_size)
            cells.append(f'{p}: {sx},{sy}')
        print(f'  {"  ".join(cells)}')


def _cmd_activate(args):
    """Handle 'activate' subcommand — invoke AT-SPI2 action on a ref element."""
    target = args.target
    if not re.match(r'^ref_\d+$', target):
        print(f'Error: activate requires a ref (ref_N), got "{target}"',
              file=sys.stderr)
        sys.exit(1)

    cache = tine.refs.load_ref_cache(_ref_cache_path())
    if not cache:
        print('Error: no ref cache found. Run "tine tree" first.',
              file=sys.stderr)
        sys.exit(1)

    if target not in cache:
        print(f'Error: {target} not found in ref cache', file=sys.stderr)
        sys.exit(1)

    ref = cache[target]
    if not isinstance(ref, dict):
        print(f'Error: ref cache entry for {target} is corrupt', file=sys.stderr)
        sys.exit(1)

    app_name = cache.get('_app', '')
    if not app_name:
        print('Error: no _app in ref cache — cannot locate element', file=sys.stderr)
        sys.exit(1)

    role = ref.get('role', '')
    name = ref.get('name', '')

    cmd = [sys.executable, '-m', 'tine.atspi_activate', app_name, role, name]
    if args.action:
        cmd.append(args.action)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        print('Error: AT-SPI2 activate timed out', file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f'Error: {result.stderr.strip()}', file=sys.stderr)
        sys.exit(1)


def _cmd_type(args):
    """Handle 'type' subcommand."""
    tine.input.type_text(args.text, delay_ms=args.delay)


def _cmd_key(args):
    """Handle 'key' subcommand."""
    tine.input.key_combo(args.combo)


def _cmd_windows(args):
    """Handle 'windows' subcommand."""
    try:
        windows = tine.screenshot.list_windows()
    except Exception as e:
        print(f'Error: could not list windows: {e}', file=sys.stderr)
        sys.exit(1)
    if not windows:
        print('No windows open.')
        return

    for w in windows:
        focused = ' (focused)' if w.get('focused') else ''
        title = w.get('title', '(untitled)')
        wm_class = w.get('wm_class', '')
        width = w.get('width', '?')
        height = w.get('height', '?')
        x = w.get('x', '?')
        y = w.get('y', '?')
        print(f'{title} [{wm_class}] {width}x{height} at ({x},{y}){focused}')


def _cmd_focus(args):
    """Handle 'focus' subcommand."""
    if not args.app:
        print('Error: app name cannot be empty', file=sys.stderr)
        sys.exit(1)
    try:
        windows = tine.screenshot.list_windows()
    except Exception as e:
        print(f'Error: could not list windows: {e}', file=sys.stderr)
        sys.exit(1)
    app_lower = args.app.lower()

    # Try wm_class substring match first
    for w in windows:
        wm_class = w.get('wm_class', '')
        if app_lower in wm_class.lower():
            try:
                _focus_window(w)
            except RuntimeError as e:
                print(f'Error: {e}', file=sys.stderr)
                sys.exit(1)
            return

    # Fall back to title substring match
    for w in windows:
        title = w.get('title', '')
        if app_lower in title.lower():
            try:
                _focus_window(w)
            except RuntimeError as e:
                print(f'Error: {e}', file=sys.stderr)
                sys.exit(1)
            return

    print(f'Error: no window matching "{args.app}"', file=sys.stderr)
    sys.exit(1)


def _cmd_inputd(args):
    """Handle 'inputd' subcommand — manage the persistent input daemon."""
    sub = args.inputd_action

    if sub == 'start':
        if tine.inputd.daemon_available():
            print('Daemon already running.')
        elif tine.inputd.start_daemon():
            print('Daemon started.')
        else:
            print('Error: failed to start daemon', file=sys.stderr)
            sys.exit(1)

    elif sub == 'stop':
        if tine.inputd.stop_daemon():
            print('Daemon stopped.')
        else:
            print('Daemon not running.', file=sys.stderr)

    elif sub == 'status':
        if tine.inputd.daemon_available():
            print('Daemon is running.')
        else:
            print('Daemon is not running.')

    else:
        print(f'Unknown inputd action: {sub}', file=sys.stderr)
        sys.exit(1)


def _cmd_describe(args):
    """Handle 'describe' subcommand."""
    try:
        result = tine.describe.describe(
            app=getattr(args, 'app', None),
            annotate=args.annotate,
            scale=args.scale,
            ocr=getattr(args, 'ocr', False),
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    output = tine.describe.format_for_agent(result)

    # Save ref cache with _app metadata. describe() now returns app_name
    # directly from the walker, so no tree-text parsing is needed.
    ref_cache = dict(result.get('ref_cache') or {})
    ref_cache['_app'] = result.get('app_name', '')
    tine.refs.save_ref_cache(ref_cache, _ref_cache_path())

    print(output)


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog='tine', description='AI agent desktop driver')
    subparsers = parser.add_subparsers(dest='command')
    _default_scale = get_display_config()['scale']

    # tree
    tree_parser = subparsers.add_parser('tree', help='Walk AT-SPI2 tree')
    tree_parser.add_argument('--app', default=None, help='Filter to app name')

    # screenshot
    ss_parser = subparsers.add_parser('screenshot', help='Capture screenshot')
    ss_parser.add_argument('--annotate', action='store_true',
                           help='Add Set-of-Mark overlays')
    ss_parser.add_argument('--grid', nargs='?', const='5x3', default=None,
                           metavar='COLSxROWS',
                           help='Add labeled coordinate grid overlay. '
                                'Optional COLSxROWS (default 5x3) sets the '
                                'grid dimensions; every cell is fully on-screen.')
    ss_parser.add_argument('--scale', type=float, default=_default_scale,
                           help='Display scale factor')
    ss_parser.add_argument('--ocr', action='store_true',
                           help='Run OCR on the screenshot and add ref_tN '
                                'entries to the ref cache')
    ss_parser.add_argument('--output', '-o', default=None,
                           help='Output path for screenshot PNG')

    # click
    click_parser = subparsers.add_parser('click', help='Click by ref or coordinates')
    click_parser.add_argument('target', help='ref_N, grid cell (e.g. B3), or x,y')
    click_parser.add_argument('--button', default='left',
                              choices=['left', 'right', 'middle'],
                              help='Mouse button: left, right, middle')
    click_parser.add_argument('--double', action='store_true',
                              help='Double click')
    click_parser.add_argument('--grid', default='5x3', metavar='COLSxROWS',
                              help='Grid dimensions for letter-cell targets '
                                   '(default 5x3, matches screenshot --grid)')

    # target
    target_parser = subparsers.add_parser('target',
                                          help='Crosshair + 3x3 sub-grid for click refinement')
    target_parser.add_argument('position', help='Grid cell (e.g. E5) or x,y coordinates')
    target_parser.add_argument('--pick', type=int, default=None,
                               help='Click sub-cell N (1-9, numpad layout)')
    target_parser.add_argument('--refine', action='store_true',
                               help='After clicking, show new 3x3 at clicked position')
    target_parser.add_argument('--size', type=int, default=None,
                               help='Region size in pixels (default: cell size '
                                    'for grid-cell positions, 150 for x,y)')
    target_parser.add_argument('--grid', default='5x3', metavar='COLSxROWS',
                               help='Grid dimensions for letter-cell positions '
                                    '(default 5x3, matches screenshot --grid)')
    target_parser.add_argument('--output', '-o', default=None,
                               help='Output path for screenshot PNG')

    # activate
    act_parser = subparsers.add_parser('activate',
                                       help='Activate ref via AT-SPI2 action')
    act_parser.add_argument('target', help='ref_N')
    act_parser.add_argument('--action', default=None,
                            help='Action name (default: first available)')

    # type
    type_parser = subparsers.add_parser('type', help='Type text')
    type_parser.add_argument('text', help='Text to type')
    type_parser.add_argument('--delay', type=int, default=None,
                             help='Delay between keystrokes (ms)')

    # key
    key_parser = subparsers.add_parser('key', help='Press key combination')
    key_parser.add_argument('combo', help='Key combo (e.g. ctrl+c)')

    # windows
    subparsers.add_parser('windows', help='List windows')

    # focus
    focus_parser = subparsers.add_parser('focus', help='Focus a window')
    focus_parser.add_argument('app', help='App name to focus')

    # inputd
    inputd_parser = subparsers.add_parser('inputd', help='Manage persistent input daemon')
    inputd_parser.add_argument('inputd_action', choices=['start', 'stop', 'status'],
                               help='start, stop, or status')

    # describe
    desc_parser = subparsers.add_parser('describe', help='Combined desktop state')
    desc_parser.add_argument('--app', default=None, help='Filter to app name')
    desc_parser.add_argument('--annotate', action='store_true',
                             help='Add Set-of-Mark overlays')
    desc_parser.add_argument('--scale', type=float, default=_default_scale,
                             help='Display scale factor')
    desc_parser.add_argument('--ocr', action='store_true',
                             help='Run OCR on the screenshot and include '
                                  'detected text regions as ref_tN entries')

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_usage(sys.stderr)
        sys.exit(2)

    commands = {
        'tree': _cmd_tree,
        'screenshot': _cmd_screenshot,
        'click': _cmd_click,
        'target': _cmd_target,
        'activate': _cmd_activate,
        'type': _cmd_type,
        'key': _cmd_key,
        'windows': _cmd_windows,
        'focus': _cmd_focus,
        'describe': _cmd_describe,
        'inputd': _cmd_inputd,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
