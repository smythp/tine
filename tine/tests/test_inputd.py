"""Tests for tine input daemon (inputd).

Tests the daemon server, client communication, command handling,
timeout behavior, stale socket cleanup, and fallback to direct UInput.
"""

import json
import os
import socket
import tempfile
import threading
import time

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeDaemonUInput:
    """Fake UInput device that records calls for assertion."""

    def __init__(self):
        self.events = []
        self.syn_count = 0

    def write(self, etype, ecode, value):
        self.events.append((etype, ecode, value))

    def syn(self):
        self.syn_count += 1

    @property
    def key_writes(self):
        from evdev import ecodes
        return [(ecode, value) for etype, ecode, value in self.events
                if etype == ecodes.EV_KEY]

    @property
    def abs_writes(self):
        from evdev import ecodes
        return [(ecode, value) for etype, ecode, value in self.events
                if etype == ecodes.EV_ABS]


@pytest.fixture
def tmp_sock(tmp_path):
    """Return a temporary socket path."""
    return str(tmp_path / 'test-input.sock')


@pytest.fixture
def fake_dev():
    """Create a fake UInput device."""
    return FakeDaemonUInput()


@pytest.fixture
def daemon_thread(tmp_sock, fake_dev):
    """Start daemon in a background thread, yield sock path, stop on teardown."""
    from tine.inputd import run_daemon

    thread = threading.Thread(
        target=run_daemon,
        kwargs={
            'create_uinput': lambda: fake_dev,
            'sock_path': tmp_sock,
            'timeout': 30,
        },
        daemon=True,
    )
    thread.start()

    # Wait for socket to appear
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if os.path.exists(tmp_sock):
            # Try connecting to verify it's ready
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(1.0)
                s.connect(tmp_sock)
                s.sendall(json.dumps({'action': 'ping'}).encode() + b'\n')
                resp = s.recv(4096)
                s.close()
                if resp:
                    break
            except (ConnectionRefusedError, OSError):
                pass
        time.sleep(0.05)

    yield tmp_sock

    # Stop daemon
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(tmp_sock)
        s.sendall(json.dumps({'action': 'shutdown'}).encode() + b'\n')
        s.recv(4096)
        s.close()
    except (ConnectionRefusedError, OSError):
        pass

    thread.join(timeout=3.0)


def _send(sock_path, cmd):
    """Send a command dict to daemon, return response dict."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect(sock_path)
    s.sendall(json.dumps(cmd).encode() + b'\n')
    data = b''
    while b'\n' not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data.strip())


# ---------------------------------------------------------------------------
# Daemon ping / lifecycle
# ---------------------------------------------------------------------------

class TestDaemonPing:

    def test_ping_responds_ok(self, daemon_thread):
        resp = _send(daemon_thread, {'action': 'ping'})
        assert resp == {'ok': True}

    def test_unknown_action_returns_error(self, daemon_thread):
        resp = _send(daemon_thread, {'action': 'nonexistent'})
        assert resp['ok'] is False
        assert 'Unknown action' in resp['error']

    def test_invalid_json_returns_error(self, daemon_thread):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(daemon_thread)
        s.sendall(b'not json\n')
        data = s.recv(4096)
        s.close()
        resp = json.loads(data.strip())
        assert resp['ok'] is False
        assert 'Invalid JSON' in resp['error']


# ---------------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------------

class TestDaemonCommands:

    def test_move_command(self, daemon_thread, fake_dev):
        resp = _send(daemon_thread, {'action': 'move', 'x': 500, 'y': 300})
        assert resp['ok'] is True
        assert len(fake_dev.abs_writes) >= 2
        assert fake_dev.syn_count >= 1

    def test_click_command(self, daemon_thread, fake_dev):
        from evdev import ecodes
        resp = _send(daemon_thread, {'action': 'click', 'x': 100, 'y': 200, 'button': 'left'})
        assert resp['ok'] is True
        assert (ecodes.BTN_LEFT, 1) in fake_dev.key_writes
        assert (ecodes.BTN_LEFT, 0) in fake_dev.key_writes

    def test_click_right_button(self, daemon_thread, fake_dev):
        from evdev import ecodes
        resp = _send(daemon_thread, {'action': 'click', 'x': 100, 'y': 200, 'button': 'right'})
        assert resp['ok'] is True
        assert (ecodes.BTN_RIGHT, 1) in fake_dev.key_writes

    def test_click_double(self, daemon_thread, fake_dev):
        from evdev import ecodes
        resp = _send(daemon_thread, {
            'action': 'click', 'x': 100, 'y': 200, 'button': 'left', 'double': True
        })
        assert resp['ok'] is True
        presses = [(c, v) for c, v in fake_dev.key_writes
                   if c == ecodes.BTN_LEFT and v == 1]
        assert len(presses) == 2

    def test_click_invalid_button(self, daemon_thread):
        resp = _send(daemon_thread, {'action': 'click', 'x': 100, 'y': 200, 'button': 'invalid'})
        assert resp['ok'] is False
        assert 'Invalid button' in resp['error']

    def test_key_command(self, daemon_thread, fake_dev):
        resp = _send(daemon_thread, {'action': 'key', 'combo': 'ctrl+c'})
        assert resp['ok'] is True
        assert (29, 1) in fake_dev.key_writes  # ctrl press
        assert (46, 1) in fake_dev.key_writes  # c press

    def test_key_unknown_key(self, daemon_thread):
        resp = _send(daemon_thread, {'action': 'key', 'combo': 'nonexistent'})
        assert resp['ok'] is False
        assert 'Unknown key' in resp['error']

    def test_key_empty_combo(self, daemon_thread):
        resp = _send(daemon_thread, {'action': 'key', 'combo': ''})
        assert resp['ok'] is False

    def test_type_command(self, daemon_thread, fake_dev):
        resp = _send(daemon_thread, {'action': 'type', 'text': 'hi'})
        assert resp['ok'] is True
        assert len(fake_dev.key_writes) >= 4  # h: press+release, i: press+release

    def test_type_shifted_chars(self, daemon_thread, fake_dev):
        from tine.input import KEYCODES
        resp = _send(daemon_thread, {'action': 'type', 'text': 'A'})
        assert resp['ok'] is True
        shift = KEYCODES['shift']
        assert (shift, 1) in fake_dev.key_writes

    def test_type_unmapped_char(self, daemon_thread):
        resp = _send(daemon_thread, {'action': 'type', 'text': '\x00'})
        assert resp['ok'] is False
        assert 'No keycode mapping' in resp['error']


# ---------------------------------------------------------------------------
# Timeout auto-exit
# ---------------------------------------------------------------------------

class TestDaemonTimeout:

    def test_daemon_exits_on_timeout(self, tmp_sock, fake_dev):
        from tine.inputd import run_daemon

        thread = threading.Thread(
            target=run_daemon,
            kwargs={
                'create_uinput': lambda: fake_dev,
                'sock_path': tmp_sock,
                'timeout': 1,  # 1 second timeout for fast test
            },
            daemon=True,
        )
        thread.start()

        # Wait for socket
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if os.path.exists(tmp_sock):
                break
            time.sleep(0.05)

        # Verify it's running
        assert os.path.exists(tmp_sock)

        # Wait for timeout + buffer
        thread.join(timeout=5.0)

        # Socket should be cleaned up
        assert not os.path.exists(tmp_sock)


# ---------------------------------------------------------------------------
# Client helpers (send_command, daemon_available)
# ---------------------------------------------------------------------------

class TestClientHelpers:

    def test_send_command_ping(self, daemon_thread):
        from tine.inputd import send_command
        resp = send_command({'action': 'ping'}, sock_path=daemon_thread)
        assert resp['ok'] is True

    def test_send_command_no_daemon_raises(self, tmp_path):
        from tine.inputd import send_command
        fake_path = str(tmp_path / 'nonexistent.sock')
        with pytest.raises((ConnectionError, OSError)):
            send_command({'action': 'ping'}, sock_path=fake_path)

    def test_daemon_available_true(self, daemon_thread):
        from tine.inputd import daemon_available
        assert daemon_available(sock_path=daemon_thread) is True

    def test_daemon_available_false(self, tmp_path):
        from tine.inputd import daemon_available
        fake_path = str(tmp_path / 'nonexistent.sock')
        assert daemon_available(sock_path=fake_path) is False


# ---------------------------------------------------------------------------
# Stale socket cleanup
# ---------------------------------------------------------------------------

class TestStaleSocket:

    def test_stale_socket_cleaned_on_start(self, tmp_sock, fake_dev):
        """If a stale socket exists (no process), daemon should clean it up and start."""
        from tine.inputd import run_daemon

        # Create a stale socket file
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(tmp_sock)
        s.close()

        # Create a stale PID file with a definitely-dead PID
        pid_path = tmp_sock.replace('.sock', '.pid')
        with open(pid_path, 'w') as f:
            f.write('999999999')

        # Daemon should clean up and start
        thread = threading.Thread(
            target=run_daemon,
            kwargs={
                'create_uinput': lambda: fake_dev,
                'sock_path': tmp_sock,
                'timeout': 2,
            },
            daemon=True,
        )
        thread.start()

        # Wait for socket to become available
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.settimeout(1.0)
                conn.connect(tmp_sock)
                conn.sendall(json.dumps({'action': 'ping'}).encode() + b'\n')
                resp = conn.recv(4096)
                conn.close()
                if resp:
                    break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.05)

        resp = _send(tmp_sock, {'action': 'ping'})
        assert resp['ok'] is True

        # Cleanup
        _send(tmp_sock, {'action': 'shutdown'})
        thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Fallback to direct UInput when daemon unavailable
# ---------------------------------------------------------------------------

class TestFallback:

    def test_move_falls_back_without_daemon(self, monkeypatch):
        """When daemon is unavailable, move() should use direct UInput."""
        import tine.input
        from tine.tests.test_input import FakeUInput

        fake = FakeUInput()
        monkeypatch.setattr(tine.input, '_uinput_device', fake)
        monkeypatch.setattr(tine.input, '_get_uinput', lambda: fake)
        monkeypatch.setattr(tine.input, 'PHYSICAL_WIDTH', 1920)
        monkeypatch.setattr(tine.input, 'PHYSICAL_HEIGHT', 1080)
        monkeypatch.setattr(tine.input, 'SCALE_FACTOR', 1.0)
        # Disable daemon so it doesn't try to auto-start
        monkeypatch.setattr(tine.input, '_use_daemon', False)

        tine.input.move(500, 300)

        from evdev import ecodes
        assert len(fake.abs_writes) >= 2

    def test_click_falls_back_without_daemon(self, monkeypatch):
        """When daemon is unavailable, click() should use direct UInput."""
        import tine.input
        from tine.tests.test_input import FakeUInput

        fake = FakeUInput()
        monkeypatch.setattr(tine.input, '_uinput_device', fake)
        monkeypatch.setattr(tine.input, '_get_uinput', lambda: fake)
        monkeypatch.setattr(tine.input, 'PHYSICAL_WIDTH', 1920)
        monkeypatch.setattr(tine.input, 'PHYSICAL_HEIGHT', 1080)
        monkeypatch.setattr(tine.input, 'SCALE_FACTOR', 1.0)
        monkeypatch.setattr(tine.input, '_use_daemon', False)

        tine.input.click(100, 200)

        from evdev import ecodes
        assert (ecodes.BTN_LEFT, 1) in fake.key_writes

    def test_type_falls_back_without_daemon(self, monkeypatch):
        """When daemon is unavailable, type_text() should use direct UInput."""
        import tine.input
        from tine.tests.test_input import FakeUInput

        fake = FakeUInput()
        monkeypatch.setattr(tine.input, '_uinput_device', fake)
        monkeypatch.setattr(tine.input, '_get_uinput', lambda: fake)
        monkeypatch.setattr(tine.input, '_use_daemon', False)

        tine.input.type_text('a')

        from tine.input import KEYCODES
        assert (KEYCODES['a'], 1) in fake.key_writes

    def test_key_falls_back_without_daemon(self, monkeypatch):
        """When daemon is unavailable, key_combo() should use direct UInput."""
        import tine.input
        from tine.tests.test_input import FakeUInput

        fake = FakeUInput()
        monkeypatch.setattr(tine.input, '_uinput_device', fake)
        monkeypatch.setattr(tine.input, '_get_uinput', lambda: fake)
        monkeypatch.setattr(tine.input, '_use_daemon', False)

        tine.input.key_combo('enter')

        assert (28, 1) in fake.key_writes


# ---------------------------------------------------------------------------
# Shutdown command
# ---------------------------------------------------------------------------

class TestShutdown:

    def test_shutdown_stops_daemon(self, tmp_sock, fake_dev):
        from tine.inputd import run_daemon, send_command

        thread = threading.Thread(
            target=run_daemon,
            kwargs={
                'create_uinput': lambda: fake_dev,
                'sock_path': tmp_sock,
                'timeout': 30,
            },
            daemon=True,
        )
        thread.start()

        # Wait for ready
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if os.path.exists(tmp_sock):
                try:
                    resp = _send(tmp_sock, {'action': 'ping'})
                    if resp.get('ok'):
                        break
                except (ConnectionRefusedError, OSError):
                    pass
            time.sleep(0.05)

        # Send shutdown
        resp = _send(tmp_sock, {'action': 'shutdown'})
        assert resp['ok'] is True

        # Thread should exit
        thread.join(timeout=5.0)
        assert not thread.is_alive()

        # Socket should be cleaned up
        assert not os.path.exists(tmp_sock)


# ---------------------------------------------------------------------------
# PID file
# ---------------------------------------------------------------------------

class TestPidFile:

    def test_pid_file_created(self, daemon_thread, tmp_sock):
        pid_path = tmp_sock.replace('.sock', '.pid')
        assert os.path.exists(pid_path)
        with open(pid_path) as f:
            pid = int(f.read().strip())
        assert pid > 0

    def test_pid_file_cleaned_on_shutdown(self, tmp_sock, fake_dev):
        from tine.inputd import run_daemon

        thread = threading.Thread(
            target=run_daemon,
            kwargs={
                'create_uinput': lambda: fake_dev,
                'sock_path': tmp_sock,
                'timeout': 30,
            },
            daemon=True,
        )
        thread.start()

        # Wait for ready
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if os.path.exists(tmp_sock):
                try:
                    resp = _send(tmp_sock, {'action': 'ping'})
                    if resp.get('ok'):
                        break
                except (ConnectionRefusedError, OSError):
                    pass
            time.sleep(0.05)

        pid_path = tmp_sock.replace('.sock', '.pid')
        assert os.path.exists(pid_path)

        _send(tmp_sock, {'action': 'shutdown'})
        thread.join(timeout=5.0)

        assert not os.path.exists(pid_path)
