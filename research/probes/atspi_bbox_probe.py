"""Dump raw AT-SPI extents for named probe elements in SCREEN, WINDOW, PARENT coord types.

Designed to be pointed at bbox-probe.html loaded in Firefox. Walks the Firefox
AT-SPI tree, finds elements whose name starts with 'tgt', and prints a side-by-
side comparison of all three coord types. Compare to the known CSS positions
(encoded in the element names, e.g. 'tgtA 20,20') and to ground-truth screen
pixels via content origin (320, 253) on archgnome.

Usage: python3 atspi_bbox_probe.py [app_name_fragment]
       default app_name_fragment = 'firefox'
"""
import sys
import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi

Atspi.init()

def find_app(fragment):
    desktop = Atspi.get_desktop(0)
    fragment_lower = fragment.lower()
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app and fragment_lower in (app.get_name() or '').lower():
            return app
    return None

def walk_collect(node, depth=0, out=None, max_depth=20):
    if out is None:
        out = []
    if depth > max_depth:
        return out
    name = node.get_name() or ''
    role = node.get_role_name() or ''
    # Collect tgt* (Firefox probe) OR any push-button with a non-empty name (general apps).
    if name.lower().startswith('tgt') or (role == 'push button' and name):
        out.append((name, role, node, depth))
    try:
        nc = node.get_child_count()
    except Exception:
        nc = 0
    for i in range(nc):
        try:
            c = node.get_child_at_index(i)
            if c:
                walk_collect(c, depth+1, out, max_depth)
        except Exception:
            pass
    return out

def extents(node, coord_type):
    try:
        comp = node.get_component_iface()
        if comp:
            r = comp.get_extents(coord_type)
            return (r.x, r.y, r.width, r.height)
    except Exception as e:
        return f'ERR:{e}'
    return None

def main():
    fragment = sys.argv[1] if len(sys.argv) > 1 else 'firefox'
    app = find_app(fragment)
    if not app:
        print(f'No app matching "{fragment}" in AT-SPI desktop.')
        return
    print(f'App: {app.get_name()!r}  child_count={app.get_child_count()}\n')

    tgts = walk_collect(app)
    if not tgts:
        print('No tgt* elements found in the AT-SPI tree.')
        return

    print(f'Found {len(tgts)} tgt* elements:\n')
    print(f'  {"name":<20s} {"role":<15s} {"SCREEN":<24s} {"WINDOW":<24s} {"PARENT":<24s}')
    for name, role, node, depth in tgts:
        scr = extents(node, Atspi.CoordType.SCREEN)
        win = extents(node, Atspi.CoordType.WINDOW)
        par = extents(node, Atspi.CoordType.PARENT)
        def fmt(e):
            if e is None: return 'None'
            if isinstance(e, str): return e
            return f'{e[0]:4d},{e[1]:4d},{e[2]:3d},{e[3]:3d}'
        print(f'  {name:<20s} {role:<15s} {fmt(scr):<24s} {fmt(win):<24s} {fmt(par):<24s}')

    # Also walk up the ancestry of the first tgt to find the frame/window and its extents
    print('\nAncestor extents (SCREEN) for first tgt:')
    n = tgts[0][2]
    hops = 0
    while n and hops < 20:
        name = (n.get_name() or '')[:40]
        role = n.get_role_name() or ''
        scr = extents(n, Atspi.CoordType.SCREEN)
        win = extents(n, Atspi.CoordType.WINDOW)
        def fmt(e):
            if e is None: return 'None'
            if isinstance(e, str): return e[:20]
            return f'{e[0]:5d},{e[1]:5d},{e[2]:4d},{e[3]:4d}'
        print(f'  depth={hops}  role={role:<15s} name={name!r:<40s} SCREEN={fmt(scr):<26s} WINDOW={fmt(win):<26s}')
        n = n.get_parent()
        hops += 1

if __name__ == '__main__':
    main()
