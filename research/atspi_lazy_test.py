"""Test GTK4 lazy AT-SPI2 realization.
Walk tree twice with a pause between to let GTK4 realize objects."""
import gi, sys, time
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi, GLib
Atspi.init()

def count_and_walk(node, depth=0, max_depth=12):
    """Walk tree, triggering GetChildren on each node."""
    if depth > max_depth:
        return 0
    count = 1
    n = node.get_child_count()
    for i in range(n):
        try:
            child = node.get_child_at_index(i)
            if child:
                count += count_and_walk(child, depth + 1, max_depth)
        except:
            pass
    return count

def dump_tree(node, depth=0, max_depth=12):
    if depth > max_depth:
        return 0
    count = 1
    role = node.get_role_name()
    name = node.get_name() or ''
    x, y, w, h = 0, 0, 0, 0
    try:
        comp = node.get_component_iface()
        if comp:
            rect = comp.get_extents(Atspi.CoordType.SCREEN)
            x, y, w, h = rect.x, rect.y, rect.width, rect.height
    except:
        pass
    actions = []
    try:
        ai = node.get_action_iface()
        if ai:
            for i in range(ai.get_n_actions()):
                actions.append(ai.get_action_name(i))
    except:
        pass

    indent = '  ' * depth
    name_str = ' "' + name + '"' if name else ''
    bbox_str = ' @(' + str(x) + ',' + str(y) + ' ' + str(w) + 'x' + str(h) + ')' if w > 0 or h > 0 else ''
    action_str = ' [' + ', '.join(actions) + ']' if actions else ''
    # skip printing frame-level GAction spam
    if role == 'frame' and len(actions) > 5:
        action_str = ' [' + str(len(actions)) + ' actions]'
    print(indent + '[' + role + ']' + name_str + bbox_str + action_str)

    n = node.get_child_count()
    for i in range(n):
        try:
            child = node.get_child_at_index(i)
            if child:
                count += dump_tree(child, depth + 1, max_depth)
        except:
            pass
    return count

desktop = Atspi.get_desktop(0)
target = sys.argv[1] if len(sys.argv) > 1 else 'gnome-control-center'

for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    if not app:
        continue
    app_name = app.get_name() or ''
    if target.lower() not in app_name.lower():
        continue

    # Pass 1: walk to trigger realization
    print('Pass 1 (trigger realization)...')
    t0 = time.time()
    c1 = count_and_walk(app, max_depth=12)
    print('  ' + str(c1) + ' elements in ' + format(time.time() - t0, '.3f') + 's')

    # Give GTK4 time to realize objects
    print('Waiting 2s for lazy realization...')
    time.sleep(2)

    # Pass 2: walk again
    print('Pass 2 (after realization)...')
    t0 = time.time()
    c2 = count_and_walk(app, max_depth=12)
    print('  ' + str(c2) + ' elements in ' + format(time.time() - t0, '.3f') + 's')

    # Wait more and try pass 3
    time.sleep(1)
    print('Pass 3...')
    t0 = time.time()
    c3 = count_and_walk(app, max_depth=12)
    print('  ' + str(c3) + ' elements in ' + format(time.time() - t0, '.3f') + 's')

    # Full dump on final pass
    print()
    print('=== Full tree dump ===')
    total = dump_tree(app, max_depth=12)
    print('--- ' + str(total) + ' elements ---')
