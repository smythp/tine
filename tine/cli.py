"""Tine CLI: AI agent interface to a GNOME Wayland desktop.

Entry point: main(argv). Subcommands: tree, screenshot, click, type, key,
windows, focus, describe.
"""

import argparse
import copy
import json
import os
import re
import subprocess
import sys

import tine.describe
import tine.input
import tine.refs
import tine.screenshot
import tine.tree


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


def _walk_atspi(app=None):
    """Run AT-SPI2 walker in a subprocess to avoid D-Bus cache staleness.

    Returns the raw tree dict (JSON parsed from subprocess stdout).
    Raises RuntimeError on failure.
    """
    cmd = [sys.executable, '-m', 'tine.atspi_walker']
    if app is not None:
        cmd.append(app)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError('AT-SPI2 walker timed out after 30s')
    if result.returncode != 0:
        raise RuntimeError(f'AT-SPI2 walker failed: {result.stderr.strip()}')
    _ATSPI_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
    if len(result.stdout) > _ATSPI_MAX_BYTES:
        raise RuntimeError(
            f'AT-SPI2 walker output exceeds 50 MB ({len(result.stdout)} bytes); '
            'aborting to prevent OOM'
        )
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'AT-SPI2 walker returned invalid JSON: {e}') from e
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f'AT-SPI2 walker returned unexpected type: {type(parsed).__name__}'
        )
    return parsed


def _normalize_app_name(name):
    """Normalize an app name/wm_class/app_id for cross-format matching.

    AT-SPI2 reports app names with hyphens (e.g. "gnome-calculator") while
    GNOME's wm_class and app_id use reverse-domain notation with dots
    (e.g. "org.gnome.Calculator").  Removing separators and common prefixes
    lets substring matching work across both formats.

    Examples:
        "gnome-calculator"      → "gnomecalculator"
        "org.gnome.Calculator"  → "gnomecalculator"
        "org.gnome.TextEditor"  → "gnometexteditor"
        "gnome-text-editor"     → "gnometexteditor"
        "firefox"               → "firefox"
    """
    if not name:
        return ''
    s = name.lower()
    # Strip common reverse-domain prefixes used in wm_class and app_id
    for prefix in ('org.', 'com.', 'net.', 'io.'):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # Remove separators so hyphens and dots compare equal
    s = s.replace('-', '').replace('.', '')
    return s


def _resolve_window_offset(app_name):
    """Match app name against window list to get (x, y) offset.

    Matching order:
    1. Normalized wm_class substring (handles AT-SPI2 hyphens vs reverse-domain dots)
    2. Normalized app_id substring (for GNOME extensions that expose this field)
    3. Raw title substring (case-insensitive, last resort)

    Falls back to (0, 0) with stderr warning.
    """
    if not app_name:
        print('Warning: empty _app, cannot resolve window offset — using (0, 0)',
              file=sys.stderr)
        return (0, 0)

    try:
        windows = tine.screenshot.list_windows()
    except Exception:
        print('Warning: could not list windows — using offset (0, 0)',
              file=sys.stderr)
        return (0, 0)

    app_norm = _normalize_app_name(app_name)

    # 1. Normalized wm_class substring match
    if app_norm:
        for w in windows:
            wm_class = w.get('wm_class', '')
            if wm_class and app_norm in _normalize_app_name(wm_class):
                return (_safe_coord(w.get('x')), _safe_coord(w.get('y')))

    # 2. Normalized app_id substring match
    if app_norm:
        for w in windows:
            app_id = w.get('app_id', '')
            if app_id and app_norm in _normalize_app_name(app_id):
                return (_safe_coord(w.get('x')), _safe_coord(w.get('y')))

    # 3. Fall back to raw title substring match (case-insensitive)
    app_lower = app_name.lower()
    for w in windows:
        title = w.get('title', '')
        if app_lower in title.lower():
            return (_safe_coord(w.get('x')), _safe_coord(w.get('y')))

    print(f'Warning: no window match for "{app_name}" — using offset (0, 0)',
          file=sys.stderr)
    return (0, 0)


def _focus_window(window):
    """Raise and focus a window via GNOME Shell D-Bus."""
    # TODO: implement via gdbus call to org.gnome.Shell to activate window
    title = window.get('title', '?')
    print(f'Warning: focus not yet implemented (would focus "{title}")',
          file=sys.stderr)


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

    if args.annotate:
        cache = tine.refs.load_ref_cache(_ref_cache_path())
        # Only annotate if there are actual refs (not just _app or empty)
        real_refs = {k: v for k, v in cache.items() if k != '_app'} if cache else {}
        if real_refs:
            app_name = cache.get('_app', '')
            offset = _resolve_window_offset(app_name)

            # Create adjusted copy with screen coords
            adjusted = {}
            for ref_id, ref_data in real_refs.items():
                if not isinstance(ref_data, dict):
                    # Corrupt/null cache entry — skip rather than crash
                    continue
                adjusted_data = copy.deepcopy(ref_data)
                bbox = adjusted_data.get('bbox') or {}
                # Write bbox back in case it was None or missing
                adjusted_data['bbox'] = bbox
                bbox['x'] = _safe_coord(bbox.get('x', 0)) + offset[0]
                bbox['y'] = _safe_coord(bbox.get('y', 0)) + offset[1]
                adjusted[ref_id] = adjusted_data

            tine.screenshot.annotate(path, adjusted, scale=args.scale)

    print(path)


def _cmd_click(args):
    """Handle 'click' subcommand."""
    target = args.target

    # Check if target is a ref (ref_N pattern)
    if re.match(r'^ref_\d+$', target):
        cache = tine.refs.load_ref_cache(_ref_cache_path())
        if not cache:
            print('Error: no ref cache found. Run "tine tree" first.',
                  file=sys.stderr)
            sys.exit(1)

        if target not in cache:
            print(f'Error: {target} not found in ref cache', file=sys.stderr)
            sys.exit(1)

        if not isinstance(cache[target], dict):
            print(f'Error: ref cache entry for {target} is corrupt (expected dict)',
                  file=sys.stderr)
            sys.exit(1)

        app_name = cache.get('_app', '')
        offset = _resolve_window_offset(app_name)
        kwargs = {'window_offset': offset, 'button': args.button}
        if args.double:
            kwargs['double'] = True
        tine.input.click_ref(target, cache, **kwargs)

    # Check if target is coordinates (x,y pattern) — ASCII flag ensures \d
    # matches only [0-9], excluding Unicode digit codepoints that pass the
    # regex but cause int() to raise ValueError. Negative values are allowed
    # for multi-monitor setups where the origin may not be top-left of all displays.
    elif re.match(r'^-?\d+,-?\d+$', target, re.ASCII):
        parts = target.split(',')
        x, y = int(parts[0]), int(parts[1])
        tine.input.click(x, y, button=args.button, double=args.double)

    else:
        print(f'Error: "{target}" is not a valid ref (ref_N) or coordinates (x,y)',
              file=sys.stderr)
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
            _focus_window(w)
            return

    # Fall back to title substring match
    for w in windows:
        title = w.get('title', '')
        if app_lower in title.lower():
            _focus_window(w)
            return

    print(f'Error: no window matching "{args.app}"', file=sys.stderr)
    sys.exit(1)


def _cmd_describe(args):
    """Handle 'describe' subcommand."""
    result = tine.describe.describe(
        app=getattr(args, 'app', None),
        annotate=args.annotate,
        scale=args.scale,
    )

    # Format output first, before mutating ref_cache with _app
    output = tine.describe.format_for_agent(result)

    # Save ref cache with _app metadata (copy to avoid mutating describe()'s result)
    ref_cache = dict(result.get('ref_cache') or {})
    tree_text = result.get('tree', '')

    # Extract app name from tree text (first line: "application 'Name'").
    # This is coupled to format_tree output — when describe() returns structured
    # data (post-RSP A integration), extract from the dict directly instead.
    app_name = ''
    if tree_text:
        # re.search (not match) handles leading whitespace/newlines.
        # Backreference (?P=q) ensures closing quote matches opening quote,
        # allowing apostrophes in double-quoted names and vice-versa.
        m = re.search(r"""application\s+(?P<q>['"])(?P<name>.*?)(?P=q)""", tree_text)
        if m:
            app_name = m.group('name')
    ref_cache['_app'] = app_name
    tine.refs.save_ref_cache(ref_cache, _ref_cache_path())

    print(output)


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog='tine', description='AI agent desktop driver')
    subparsers = parser.add_subparsers(dest='command')

    # tree
    tree_parser = subparsers.add_parser('tree', help='Walk AT-SPI2 tree')
    tree_parser.add_argument('--app', default=None, help='Filter to app name')

    # screenshot
    ss_parser = subparsers.add_parser('screenshot', help='Capture screenshot')
    ss_parser.add_argument('--annotate', action='store_true',
                           help='Add Set-of-Mark overlays')
    ss_parser.add_argument('--scale', type=float, default=1.0,
                           help='Display scale factor')
    ss_parser.add_argument('--output', '-o', default=None,
                           help='Output path for screenshot PNG')

    # click
    click_parser = subparsers.add_parser('click', help='Click by ref or coordinates')
    click_parser.add_argument('target', help='ref_N or x,y')
    click_parser.add_argument('--button', default='left',
                              choices=['left', 'right', 'middle'],
                              help='Mouse button: left, right, middle')
    click_parser.add_argument('--double', action='store_true',
                              help='Double click')

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

    # describe
    desc_parser = subparsers.add_parser('describe', help='Combined desktop state')
    desc_parser.add_argument('--app', default=None, help='Filter to app name')
    desc_parser.add_argument('--annotate', action='store_true',
                             help='Add Set-of-Mark overlays')
    desc_parser.add_argument('--scale', type=float, default=1.0,
                             help='Display scale factor')

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_usage(sys.stderr)
        sys.exit(2)

    commands = {
        'tree': _cmd_tree,
        'screenshot': _cmd_screenshot,
        'click': _cmd_click,
        'type': _cmd_type,
        'key': _cmd_key,
        'windows': _cmd_windows,
        'focus': _cmd_focus,
        'describe': _cmd_describe,
    }

    commands[args.command](args)
