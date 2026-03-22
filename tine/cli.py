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


def _ref_cache_path():
    """Return path for the ref cache file."""
    return os.environ.get('TINE_REF_CACHE', '/tmp/tine-refs.json')


def _walk_atspi(app=None):
    """Run AT-SPI2 walker in a subprocess to avoid D-Bus cache staleness.

    Returns the raw tree dict (JSON parsed from subprocess stdout).
    Raises RuntimeError on failure.
    """
    cmd = [sys.executable, '-m', 'tine.atspi_walker']
    if app:
        cmd.append(app)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError('AT-SPI2 walker timed out after 30s')
    if result.returncode != 0:
        raise RuntimeError(f'AT-SPI2 walker failed: {result.stderr.strip()}')
    return json.loads(result.stdout)


def _resolve_window_offset(app_name):
    """Match app name against window list to get (x, y) offset.

    Tries wm_class substring first (case-insensitive), then title.
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

    app_lower = app_name.lower()

    # Try wm_class substring match first
    for w in windows:
        wm_class = w.get('wm_class', '')
        if app_lower in wm_class.lower():
            return (w.get('x', 0), w.get('y', 0))

    # Fall back to title substring match
    for w in windows:
        title = w.get('title', '')
        if app_lower in title.lower():
            return (w.get('x', 0), w.get('y', 0))

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

    collapsed = tine.tree.collapse_containers(raw_tree)
    annotated, ref_cache = tine.tree.assign_refs(collapsed)
    output = tine.tree.format_tree(annotated)
    print(output)

    # Save ref cache with _app metadata
    app_name = raw_tree.get('name', '')
    ref_cache['_app'] = app_name
    tine.refs.save_ref_cache(ref_cache, _ref_cache_path())


def _cmd_screenshot(args):
    """Handle 'screenshot' subcommand."""
    path = tine.screenshot.capture()

    if args.annotate:
        cache = tine.refs.load_ref_cache(_ref_cache_path())
        # Only annotate if there are actual refs (not just _app or empty)
        real_refs = {k: v for k, v in cache.items() if k != '_app'}
        if real_refs:
            app_name = cache.get('_app', '')
            offset = _resolve_window_offset(app_name)

            # Create adjusted copy with screen coords
            adjusted = {}
            for ref_id, ref_data in real_refs.items():
                adjusted_data = copy.deepcopy(ref_data)
                bbox = adjusted_data.get('bbox', {})
                bbox['x'] = bbox.get('x', 0) + offset[0]
                bbox['y'] = bbox.get('y', 0) + offset[1]
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

        app_name = cache.get('_app', '')
        offset = _resolve_window_offset(app_name)
        kwargs = {'window_offset': offset, 'button': args.button}
        if args.double:
            kwargs['double'] = True
        tine.input.click_ref(target, cache, **kwargs)

    # Check if target is coordinates (x,y pattern)
    elif re.match(r'^\d+,\d+$', target):
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
    windows = tine.screenshot.list_windows()
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
    windows = tine.screenshot.list_windows()
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

    # Save ref cache with _app metadata
    ref_cache = result.get('ref_cache', {})
    tree_text = result.get('tree', '')

    # Extract app name from tree text (first line: "application 'Name'").
    # This is coupled to format_tree output — when describe() returns structured
    # data (post-RSP A integration), extract from the dict directly instead.
    app_name = ''
    if tree_text:
        m = re.match(r"application\s+'([^']*)'", tree_text)
        if m:
            app_name = m.group(1)
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
