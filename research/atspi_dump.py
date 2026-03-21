import gi, sys, time
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi
Atspi.init()

def dump_tree(node, depth=0, max_depth=8):
    if depth > max_depth:
        return 0
    count = 1
    role = node.get_role_name()
    name = node.get_name() or ''

    # bounding box
    x, y, w, h = 0, 0, 0, 0
    try:
        comp = node.get_component_iface()
        if comp:
            rect = comp.get_extents(Atspi.CoordType.SCREEN)
            x, y, w, h = rect.x, rect.y, rect.width, rect.height
    except:
        pass

    # states
    states = []
    try:
        ss = node.get_state_set()
        for s in range(100):
            try:
                if ss.contains(Atspi.StateType(s)):
                    st = Atspi.StateType(s)
                    states.append(st.value_nick)
            except:
                break
    except:
        pass

    # actions
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
    state_str = ' (' + ', '.join(states) + ')' if states else ''
    action_str = ' [actions: ' + ', '.join(actions) + ']' if actions else ''

    print(indent + '[' + role + ']' + name_str + state_str + bbox_str + action_str)

    n_children = node.get_child_count()
    for i in range(n_children):
        try:
            child = node.get_child_at_index(i)
            if child:
                count += dump_tree(child, depth + 1, max_depth)
        except:
            pass
    return count

desktop = Atspi.get_desktop(0)
target = sys.argv[1] if len(sys.argv) > 1 else None

for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    if not app:
        continue
    app_name = app.get_name() or ''
    if target and target.lower() not in app_name.lower():
        continue

    print('=== ' + app_name + ' ===')
    t0 = time.time()
    total = dump_tree(app, max_depth=8)
    elapsed = time.time() - t0
    print('--- ' + str(total) + ' elements, ' + format(elapsed, '.3f') + 's ---')
    print()
