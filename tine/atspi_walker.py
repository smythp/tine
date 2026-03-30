"""AT-SPI2 tree walker subprocess.

Run as: python -m tine.atspi_walker [app_name]

Walks the AT-SPI2 accessibility tree via D-Bus (pyatspi/gi) and outputs
a JSON dict to stdout. Designed to be called from cli.py as a subprocess
to avoid D-Bus cache staleness between invocations.

Output format matches what tree.py expects:
    {role, name, bbox: {x, y, w, h}, states, actions, children, child_index}
"""

import json
import sys

import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi

MAX_DEPTH = 12
MAX_NODES = 50000  # Safety cap to prevent runaway trees


def _walk(node, depth=0):
    """Recursively walk an AT-SPI2 node, returning a dict."""
    _walk._count += 1
    if _walk._count > MAX_NODES:
        return None
    if depth > MAX_DEPTH:
        return None

    role = node.get_role_name() or 'unknown'
    name = node.get_name() or ''

    # Bounding box — try SCREEN coords first (absolute, no offset needed).
    # GTK4 on Wayland returns (0,0) for SCREEN, so fall back to WINDOW
    # (relative to frame_rect, needs window offset added downstream).
    # The "coord" field tells downstream code which space we're in.
    #
    # Heuristic: trust SCREEN only if x or y is non-zero. GTK4 on Wayland
    # always returns (0,0,w,h) for SCREEN — valid dimensions but zeroed
    # position. An element genuinely at screen (0,0) would be under the
    # top-left panel pixel, which is never an interactive target.
    bbox = {'x': 0, 'y': 0, 'w': 0, 'h': 0, 'coord': 'window'}
    try:
        comp = node.get_component_iface()
        if comp:
            scr = comp.get_extents(Atspi.CoordType.SCREEN)
            if scr.x != 0 or scr.y != 0:
                bbox = {'x': scr.x, 'y': scr.y, 'w': scr.width, 'h': scr.height,
                        'coord': 'screen'}
            else:
                win = comp.get_extents(Atspi.CoordType.WINDOW)
                bbox = {'x': win.x, 'y': win.y, 'w': win.width, 'h': win.height,
                        'coord': 'window'}
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

    # Children
    children = []
    try:
        n_children = node.get_child_count()
        for i in range(n_children):
            if _walk._count > MAX_NODES:
                break
            try:
                child = node.get_child_at_index(i)
                if child:
                    child_dict = _walk(child, depth + 1)
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
            'bbox': {'x': 0, 'y': 0, 'w': 0, 'h': 0},
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
            'bbox': {'x': 0, 'y': 0, 'w': 0, 'h': 0},
            'states': [],
            'actions': [],
            'children': children,
        }
        json.dump(result, sys.stdout)


if __name__ == '__main__':
    main()
