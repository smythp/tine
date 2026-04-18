"""AT-SPI2 tree walker subprocess.

Run as: python -m tine.atspi_walker [app_name]

Walks the AT-SPI2 accessibility tree via D-Bus (pyatspi/gi) and outputs
a JSON dict to stdout. Designed to be called from cli.py as a subprocess
to avoid D-Bus cache staleness between invocations.

Output format matches what tree.py expects:
    {role, name, bbox: {x, y, w, h, coord}, states, actions, children, child_index}

Bounding boxes are always window-relative (coord='window'). Downstream code
adds the compositor's window offset at click time. See finding-20260418-3ff1
for why AT-SPI CoordType.SCREEN is unreliable on Wayland (Firefox reports a
fake-frame origin; GTK4 returns (0, 0, w, h) as a placeholder).
"""

import json
import sys

import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi

MAX_DEPTH = 12
MAX_NODES = 50000  # Safety cap to prevent runaway trees


def _window_origin(node):
    """Get an AT-SPI node's WINDOW-coord origin, or (0, 0) on failure."""
    try:
        comp = node.get_component_iface()
        if comp:
            w = comp.get_extents(Atspi.CoordType.WINDOW)
            return (w.x, w.y)
    except Exception:
        pass
    return (0, 0)


def _walk(node, depth=0, frame_origin=(0, 0)):
    """Recursively walk an AT-SPI2 node, returning a dict.

    frame_origin is the WINDOW-coord origin of the current subtree's top-level
    window/frame. Element bboxes are computed relative to it so downstream can
    add the compositor's real window offset uniformly, regardless of whether
    the toolkit reports frame.WINDOW as (0, 0) (GTK4) or as a non-zero fake
    value (Firefox; see finding-20260418-3ff1).
    """
    _walk._count += 1
    if _walk._count > MAX_NODES:
        return None
    if depth > MAX_DEPTH:
        return None

    role = node.get_role_name() or 'unknown'
    name = node.get_name() or ''

    # Compute bbox as (element.WINDOW - frame_origin), tagged 'window'. The
    # subtraction is a no-op when frame_origin == (0, 0), so this preserves
    # the pre-fix behavior for well-behaved toolkits.
    bbox = {'x': 0, 'y': 0, 'w': 0, 'h': 0, 'coord': 'window'}
    try:
        comp = node.get_component_iface()
        if comp:
            w = comp.get_extents(Atspi.CoordType.WINDOW)
            bbox = {'x': w.x - frame_origin[0], 'y': w.y - frame_origin[1],
                    'w': w.width, 'h': w.height, 'coord': 'window'}
    except Exception:
        pass

    # States
    states = []
    try:
        ss = node.get_state_set()
        for s in range(100):
            try:
                st = Atspi.StateType(s)
                if ss.contains(st):
                    states.append(st.value_nick)
            except (ValueError, OverflowError):
                break
    except Exception:
        pass

    # Actions
    actions = []
    try:
        ai = node.get_action_iface()
        if ai:
            for i in range(ai.get_n_actions()):
                actions.append(ai.get_action_name(i))
    except Exception:
        pass

    # Children. Direct children of an 'application' node are top-level windows;
    # each one defines its own frame coordinate system for its subtree.
    children = []
    try:
        n_children = node.get_child_count()
        for i in range(n_children):
            if _walk._count > MAX_NODES:
                break
            try:
                child = node.get_child_at_index(i)
                if child:
                    child_frame_origin = (
                        _window_origin(child) if role == 'application'
                        else frame_origin
                    )
                    child_dict = _walk(child, depth + 1, child_frame_origin)
                    if child_dict is not None:
                        child_dict['child_index'] = i
                        children.append(child_dict)
            except Exception:
                pass
    except Exception:
        pass

    return {
        'role': role,
        'name': name,
        'bbox': bbox,
        'states': states,
        'actions': actions,
        'children': children,
    }


def main():
    Atspi.init()
    target = sys.argv[1] if len(sys.argv) > 1 else None
    desktop = Atspi.get_desktop(0)

    if target:
        # Find matching app
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if not app:
                continue
            app_name = app.get_name() or ''
            if target.lower() in app_name.lower():
                _walk._count = 0
                result = _walk(app)
                if result is not None:
                    json.dump(result, sys.stdout)
                    return
        # No match found — return empty tree for the target
        print(json.dumps({
            'role': 'application',
            'name': target,
            'bbox': {'x': 0, 'y': 0, 'w': 0, 'h': 0, 'coord': 'window'},
            'states': [],
            'actions': [],
            'children': [],
        }))
    else:
        # Walk entire desktop
        _walk._count = 0
        children = []
        for i in range(desktop.get_child_count()):
            if _walk._count > MAX_NODES:
                break
            app = desktop.get_child_at_index(i)
            if not app:
                continue
            app_dict = _walk(app)
            if app_dict is not None:
                children.append(app_dict)

        result = {
            'role': 'desktop',
            'name': 'desktop',
            'bbox': {'x': 0, 'y': 0, 'w': 0, 'h': 0, 'coord': 'window'},
            'states': [],
            'actions': [],
            'children': children,
        }
        json.dump(result, sys.stdout)


if __name__ == '__main__':
    main()
