"""AT-SPI2 action invocation subprocess.

Run as: python -m tine.atspi_activate APP_NAME ROLE ELEMENT_NAME [ACTION]

Finds a matching element in the AT-SPI2 tree and invokes its action.
Designed to be called from cli.py as a subprocess (same pattern as
atspi_walker.py) to get a fresh D-Bus session per invocation.

Exit codes:
    0 = action invoked successfully
    1 = element not found or action failed
"""

import sys

import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi

MAX_DEPTH = 12


def _find_and_activate(node, target_role, target_name, action_name, depth=0):
    """Walk the tree looking for a matching element, invoke its action."""
    if depth > MAX_DEPTH:
        return False

    try:
        role = node.get_role_name() or ''
        name = node.get_name() or ''

        if role == target_role and name == target_name:
            ai = node.get_action_iface()
            if ai:
                n = ai.get_n_actions()
                for i in range(n):
                    if action_name is None or ai.get_action_name(i) == action_name:
                        ai.do_action(i)
                        return True
                # No matching action name, try the first action
                if n > 0 and action_name is not None:
                    ai.do_action(0)
                    return True
            return False

        for i in range(node.get_child_count()):
            child = node.get_child_at_index(i)
            if child and _find_and_activate(child, target_role, target_name,
                                            action_name, depth + 1):
                return True
    except Exception:
        pass

    return False


def main():
    if len(sys.argv) < 4:
        print('Usage: python -m tine.atspi_activate APP_NAME ROLE NAME [ACTION]',
              file=sys.stderr)
        sys.exit(2)

    app_name = sys.argv[1]
    target_role = sys.argv[2]
    target_name = sys.argv[3]
    action_name = sys.argv[4] if len(sys.argv) > 4 else None

    Atspi.init()
    desktop = Atspi.get_desktop(0)

    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if not app:
            continue
        name = app.get_name() or ''
        if app_name.lower() in name.lower():
            if _find_and_activate(app, target_role, target_name, action_name):
                sys.exit(0)
            else:
                print(f'Element not found or no action: {target_role} "{target_name}"',
                      file=sys.stderr)
                sys.exit(1)

    print(f'App not found: {app_name}', file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    main()
