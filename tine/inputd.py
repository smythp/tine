"""Persistent evdev input daemon for tine.

Keeps the UInput devices alive across CLI invocations, eliminating the 2s
compositor warmup per command. Listens on a Unix socket for JSON commands.

The daemon owns two uinput devices (pointer + keyboard) matching the
split-device shape in tine/input.py. See that module for the rationale
behind the split. Commands dispatch to the pointer device (move/click)
or the keyboard device (type/key) by action.

Protocol: newline-delimited JSON over Unix stream socket.
  Request:  {"action": "click", "x": 100, "y": 200, "button": "left"}
  Response: {"ok": true}\n  or  {"ok": false, "error": "message"}\n

Lifecycle:
  - Auto-exits after TINE_INPUTD_TIMEOUT seconds of inactivity (default 300).
  - PID file next to socket for stale detection.
  - Cleaned up on exit via atexit + signal handlers.
"""

import atexit
import json
import os
import signal
import socket
import selectors
import sys
import time

# These are imported lazily in _create_devices() so tests can mock them,
# but we keep the top-level import for type checking / IDE support.
import evdev
from evdev import UInput, ecodes, AbsInfo


def _socket_path():
    """Return the Unix socket path for the daemon."""
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime:
        return os.path.join(runtime, 'tine-input.sock')
    return '/tmp/tine-input.sock'


def _pid_path():
    """Return the PID file path (derived from socket path)."""
    sp = _socket_path()
    base, ext = os.path.splitext(sp)
    return base + '.pid' if ext else sp + '.pid'


# ---------------------------------------------------------------------------
# UInput device creation (pointer + keyboard, same shape as tine.input)
# ---------------------------------------------------------------------------

ABS_MAX = 65535


def _get_input_constants():
    """Lazy import of key tables from tine.input to avoid circular imports."""
    from tine.input import KEYCODES, CHAR_MAP, BUTTON_CODES, MODIFIER_NAMES
    from tine.display import get_display_config
    config = get_display_config()
    return KEYCODES, CHAR_MAP, BUTTON_CODES, MODIFIER_NAMES, config['physical_width'], config['physical_height']


def _create_devices():
    """Create the pointer + keyboard UInput devices.

    Mirrors tine.input's split-device design: a pure absolute pointer
    (ABS + mouse buttons + INPUT_PROP_DIRECT) and a pure keyboard (EV_KEY
    with keycodes). Combining both on one device triggers libinput's
    cap:kp classification and causes EV_ABS motion to be dropped.
    """
    from tine.input import (
        POINTER_VENDOR, POINTER_PRODUCT,
        KEYBOARD_VENDOR, KEYBOARD_PRODUCT,
    )
    keycodes, _, _, _, _, _ = _get_input_constants()

    pointer_caps = {
        ecodes.EV_ABS: [
            (ecodes.ABS_X, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
            (ecodes.ABS_Y, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
        ],
        ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
    }
    keyboard_caps = {
        ecodes.EV_KEY: [code for code in keycodes.values()],
    }

    pointer = UInput(
        pointer_caps, name='tine-pointer',
        vendor=POINTER_VENDOR, product=POINTER_PRODUCT, version=1,
        input_props=[ecodes.INPUT_PROP_DIRECT],
    )
    keyboard = UInput(
        keyboard_caps, name='tine-keyboard',
        vendor=KEYBOARD_VENDOR, product=KEYBOARD_PRODUCT, version=1,
    )
    # Single warm-up for both — compositor registers them in parallel.
    time.sleep(2.0)
    return pointer, keyboard


# ---------------------------------------------------------------------------
# Command handlers — operate on the UInput device
# ---------------------------------------------------------------------------

def _physical_to_abs(phys_x, phys_y, physical_width, physical_height):
    abs_x = int(phys_x * ABS_MAX / physical_width)
    abs_y = int(phys_y * ABS_MAX / physical_height)
    return abs_x, abs_y


def _handle_move(devices, cmd):
    pointer, _ = devices
    _, _, _, _, physical_width, physical_height = _get_input_constants()
    x, y = int(cmd['x']), int(cmd['y'])
    abs_x, abs_y = _physical_to_abs(x, y, physical_width, physical_height)
    pointer.write(ecodes.EV_ABS, ecodes.ABS_X, abs_x)
    pointer.write(ecodes.EV_ABS, ecodes.ABS_Y, abs_y)
    pointer.syn()
    return {'ok': True}


def _handle_click(devices, cmd):
    pointer, _ = devices
    _, _, button_codes, _, physical_width, physical_height = _get_input_constants()
    x, y = int(cmd['x']), int(cmd['y'])
    button = cmd.get('button', 'left')
    double = cmd.get('double', False)

    if button not in button_codes:
        return {'ok': False, 'error': f'Invalid button: {button}'}

    # Move first
    abs_x, abs_y = _physical_to_abs(x, y, physical_width, physical_height)
    pointer.write(ecodes.EV_ABS, ecodes.ABS_X, abs_x)
    pointer.write(ecodes.EV_ABS, ecodes.ABS_Y, abs_y)
    pointer.syn()
    time.sleep(0.1)

    btn_code = button_codes[button]
    pointer.write(ecodes.EV_KEY, btn_code, 1)
    pointer.syn()
    pointer.write(ecodes.EV_KEY, btn_code, 0)
    pointer.syn()

    if double:
        time.sleep(0.05)
        pointer.write(ecodes.EV_KEY, btn_code, 1)
        pointer.syn()
        pointer.write(ecodes.EV_KEY, btn_code, 0)
        pointer.syn()

    return {'ok': True}


def _handle_key(devices, cmd):
    _, keyboard = devices
    from tine.input import _normalize_key_name
    keycodes, _, _, modifier_names, _, _ = _get_input_constants()
    combo_str = cmd.get('combo', '')
    if not combo_str:
        return {'ok': False, 'error': 'Missing combo'}

    parts = [_normalize_key_name(p) for p in combo_str.split('+')]
    parts = [p for p in parts if p]

    if not parts:
        return {'ok': False, 'error': 'Empty key combo'}

    modifiers = []
    keys = []
    for part in parts:
        if part not in keycodes:
            return {'ok': False, 'error': f'Unknown key: {part}'}
        if part in modifier_names:
            modifiers.append(keycodes[part])
        else:
            keys.append(keycodes[part])

    for code in modifiers:
        keyboard.write(ecodes.EV_KEY, code, 1)
    for code in keys:
        keyboard.write(ecodes.EV_KEY, code, 1)
    for code in reversed(keys):
        keyboard.write(ecodes.EV_KEY, code, 0)
    for code in reversed(modifiers):
        keyboard.write(ecodes.EV_KEY, code, 0)
    keyboard.syn()

    return {'ok': True}


def _handle_type(devices, cmd):
    _, keyboard = devices
    keycodes, char_map, _, _, _, _ = _get_input_constants()
    text = cmd.get('text', '')
    if not isinstance(text, str):
        return {'ok': False, 'error': 'text must be a string'}

    delay_ms = cmd.get('delay_ms', 2)
    delay_s = delay_ms / 1000.0
    shift_code = keycodes['shift']

    for char in text:
        if char not in char_map:
            return {'ok': False, 'error': f'No keycode mapping for: {char!r}'}

        keycode, needs_shift = char_map[char]

        if needs_shift:
            keyboard.write(ecodes.EV_KEY, shift_code, 1)
            keyboard.syn()

        keyboard.write(ecodes.EV_KEY, keycode, 1)
        keyboard.syn()
        keyboard.write(ecodes.EV_KEY, keycode, 0)
        keyboard.syn()

        if needs_shift:
            keyboard.write(ecodes.EV_KEY, shift_code, 0)
            keyboard.syn()

        if delay_s > 0:
            time.sleep(delay_s)

    return {'ok': True}


HANDLERS = {
    'move': _handle_move,
    'click': _handle_click,
    'key': _handle_key,
    'type': _handle_type,
}


# ---------------------------------------------------------------------------
# Daemon server
# ---------------------------------------------------------------------------

def _cleanup(sock_path, pid_path, server_sock):
    """Remove socket and PID files, close server socket."""
    try:
        server_sock.close()
    except Exception:
        pass
    for path in (sock_path, pid_path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def run_daemon(create_devices=None, sock_path=None, timeout=None):
    """Run the input daemon. Blocks until shutdown or inactivity timeout.

    Args:
        create_devices: Callable that returns a (pointer, keyboard) tuple of
            UInput-like devices. Defaults to _create_devices.
        sock_path: Override socket path (for testing).
        timeout: Inactivity timeout in seconds. Defaults to TINE_INPUTD_TIMEOUT env or 300.
    """
    if sock_path is None:
        sock_path = _socket_path()
    base, ext = os.path.splitext(sock_path)
    pid_path = base + '.pid' if ext else sock_path + '.pid'

    if timeout is None:
        timeout = int(os.environ.get('TINE_INPUTD_TIMEOUT', '300'))

    if create_devices is None:
        create_devices = _create_devices

    # Clean up stale socket
    if os.path.exists(sock_path):
        # Check if another daemon is running
        if os.path.exists(pid_path):
            try:
                with open(pid_path) as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)  # Check if process exists
                raise RuntimeError(f'Daemon already running (pid {old_pid})')
            except (OSError, ValueError):
                pass  # Stale PID, clean up
        os.unlink(sock_path)

    # Create UInput devices (pointer, keyboard)
    devices = create_devices()

    # Create Unix socket
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    os.chmod(sock_path, 0o600)
    server.listen(5)
    server.setblocking(False)

    # Write PID file
    with open(pid_path, 'w') as f:
        f.write(str(os.getpid()))
    os.chmod(pid_path, 0o600)

    # Register cleanup
    def cleanup_handler():
        _cleanup(sock_path, pid_path, server)

    atexit.register(cleanup_handler)

    def signal_handler(signum, frame):
        cleanup_handler()
        sys.exit(0)

    # Signal handlers only work in the main thread
    import threading
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    sel = selectors.DefaultSelector()
    sel.register(server, selectors.EVENT_READ)

    last_activity = time.monotonic()
    shutdown_flag = [False]

    while not shutdown_flag[0]:
        remaining = timeout - (time.monotonic() - last_activity)
        if remaining <= 0:
            break

        events = sel.select(timeout=min(remaining, 1.0))

        for key, mask in events:
            if key.fileobj is server:
                conn, _ = server.accept()
                _handle_connection(conn, devices, shutdown_flag)
                last_activity = time.monotonic()

    # Clean exit (timeout or shutdown)
    cleanup_handler()


def _handle_connection(conn, devices, shutdown_flag):
    """Handle a single client connection. Reads one JSON line, responds, closes."""
    try:
        conn.settimeout(5.0)
        data = b''
        while b'\n' not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 65536:
                break

        if not data.strip():
            return

        try:
            cmd = json.loads(data.strip())
        except json.JSONDecodeError as e:
            response = {'ok': False, 'error': f'Invalid JSON: {e}'}
            conn.sendall(json.dumps(response).encode() + b'\n')
            return

        action = cmd.get('action', '')

        if action == 'ping':
            response = {'ok': True}
        elif action == 'shutdown':
            response = {'ok': True}
            conn.sendall(json.dumps(response).encode() + b'\n')
            shutdown_flag[0] = True
            return
        elif action in HANDLERS:
            try:
                response = HANDLERS[action](devices, cmd)
            except Exception as e:
                response = {'ok': False, 'error': str(e)}
        else:
            response = {'ok': False, 'error': f'Unknown action: {action}'}

        conn.sendall(json.dumps(response).encode() + b'\n')
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Client helpers (used by input.py to talk to the daemon)
# ---------------------------------------------------------------------------

def send_command(cmd, sock_path=None):
    """Send a command to the running daemon. Returns the response dict.

    Raises ConnectionError if daemon is not running or not responding.
    """
    if sock_path is None:
        sock_path = _socket_path()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10.0)
        sock.connect(sock_path)
        sock.sendall(json.dumps(cmd).encode() + b'\n')

        data = b''
        while b'\n' not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk

        if not data.strip():
            raise ConnectionError('Empty response from daemon')

        return json.loads(data.strip())
    finally:
        sock.close()


def daemon_available(sock_path=None):
    """Check if the daemon is running and responsive."""
    try:
        resp = send_command({'action': 'ping'}, sock_path=sock_path)
        return resp.get('ok', False)
    except (ConnectionError, OSError, json.JSONDecodeError):
        return False


def start_daemon(sock_path=None):
    """Fork the daemon into the background. Returns True if started successfully."""
    import subprocess

    if daemon_available(sock_path=sock_path):
        return True  # Already running

    # Clean up stale socket if present
    sp = sock_path or _socket_path()
    base, ext = os.path.splitext(sp)
    pp = base + '.pid' if ext else sp + '.pid'
    if os.path.exists(sp):
        if os.path.exists(pp):
            try:
                with open(pp) as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)
                return True  # Process exists
            except (OSError, ValueError):
                pass
        try:
            os.unlink(sp)
        except FileNotFoundError:
            pass

    # Start daemon as a subprocess
    cmd = [sys.executable, '-m', 'tine.inputd']
    if sock_path:
        cmd.extend(['--sock', sock_path])
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for daemon to become available (up to 5s for device warmup)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if daemon_available(sock_path=sock_path):
            return True
        time.sleep(0.1)

    return False


def stop_daemon(sock_path=None):
    """Send shutdown command to the daemon."""
    try:
        send_command({'action': 'shutdown'}, sock_path=sock_path)
        return True
    except (ConnectionError, OSError):
        return False


# ---------------------------------------------------------------------------
# Entry point for running as module: python -m tine.inputd
# ---------------------------------------------------------------------------

def _main():
    import argparse
    parser = argparse.ArgumentParser(description='tine input daemon')
    parser.add_argument('--sock', default=None, help='Socket path override')
    parser.add_argument('--timeout', type=int, default=None,
                        help='Inactivity timeout in seconds')
    args = parser.parse_args()
    try:
        run_daemon(sock_path=args.sock, timeout=args.timeout)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    _main()
