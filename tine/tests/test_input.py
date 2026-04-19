"""Tests for tine input: click, click_ref, type_text, key_combo, move.

All input operations use evdev UInput. Tests mock the UInput device via FakeUInput.
"""

import json
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ref_cache():
    """Ref cache with a few test entries."""
    return {
        'ref_1': {
            'role': 'button',
            'name': 'Wi-Fi',
            'bbox': {'x': 6, 'y': 136, 'w': 233, 'h': 43},
        },
        'ref_2': {
            'role': 'entry',
            'name': 'Search',
            'bbox': {'x': 100, 'y': 50, 'w': 200, 'h': 30},
        },
        'ref_3': {
            'role': 'button',
            'name': 'BadBbox',
            'bbox': {'x': -2147483648, 'y': 0, 'w': 80, 'h': 30},
        },
    }


class FakeUInput:
    """Records UInput write/syn calls for assertion."""

    def __init__(self, capabilities=None, name=None):
        self.events = []
        self.syn_count = 0
        self.capabilities = capabilities
        self.name = name

    def write(self, etype, ecode, value):
        self.events.append((etype, ecode, value))

    def syn(self):
        self.syn_count += 1

    @property
    def abs_writes(self):
        """Return list of (ecode, value) for EV_ABS events."""
        from evdev import ecodes
        return [(ecode, value) for etype, ecode, value in self.events
                if etype == ecodes.EV_ABS]

    @property
    def key_writes(self):
        """Return list of (ecode, value) for EV_KEY events."""
        from evdev import ecodes
        return [(ecode, value) for etype, ecode, value in self.events
                if etype == ecodes.EV_KEY]


class FakeSubprocess:
    """Records subprocess.run calls for assertion."""

    def __init__(self):
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append(cmd)
        return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

    @property
    def last_cmd(self):
        return self.calls[-1] if self.calls else None

    def last_cmd_str(self):
        return ' '.join(str(c) for c in self.last_cmd) if self.last_cmd else ''


class _FakeUInputPair:
    """Aggregates pointer + keyboard fakes and forwards the event views.

    Lets existing tests continue to query ``abs_writes`` and ``key_writes``
    on a single object while the underlying split-device architecture is
    exercised. Tests that care about which device saw an event can read
    ``pair.pointer`` or ``pair.keyboard`` directly.
    """

    def __init__(self):
        self.pointer = FakeUInput()
        self.keyboard = FakeUInput()

    @property
    def events(self):
        return self.pointer.events + self.keyboard.events

    @property
    def syn_count(self):
        return self.pointer.syn_count + self.keyboard.syn_count

    @property
    def abs_writes(self):
        return self.pointer.abs_writes + self.keyboard.abs_writes

    @property
    def key_writes(self):
        return self.pointer.key_writes + self.keyboard.key_writes


@pytest.fixture
def fake_uinput(monkeypatch):
    """Patch pointer + keyboard getters to return FakeUInput instances.

    Returns a ``_FakeUInputPair`` so tests can assert aggregated
    ``abs_writes`` / ``key_writes`` or inspect the individual devices.
    """
    import tine.input
    pair = _FakeUInputPair()
    monkeypatch.setattr(tine.input, '_pointer_device', pair.pointer)
    monkeypatch.setattr(tine.input, '_keyboard_device', pair.keyboard)
    monkeypatch.setattr(tine.input, '_get_pointer_uinput', lambda: pair.pointer)
    monkeypatch.setattr(tine.input, '_get_keyboard_uinput', lambda: pair.keyboard)
    # Disable daemon so tests use direct UInput path
    monkeypatch.setattr(tine.input, '_use_daemon', False)
    return pair


@pytest.fixture
def default_scale(monkeypatch):
    """Ensure default scale/physical settings for predictable math."""
    import tine.display
    monkeypatch.setattr(tine.display, '_cached_config', {
        'scale': 1.0, 'physical_width': 1920, 'physical_height': 1080,
    })


# ---------------------------------------------------------------------------
# Device capability regression — BTN_TOUCH must NOT appear
# ---------------------------------------------------------------------------

class TestUinputCapabilities:
    """Regression tests on the shape of tine's uinput devices.

    Three invariants matter for Wayland/libinput to deliver events correctly:

    1. No BTN_TOUCH. Devices advertising BTN_TOUCH route events through
       wl_touch in libinput's internal dispatch, and Firefox's Wayland
       pointer binding ignores wl_touch. See finding-20260414-0y45 and
       brief-20260415-6yjt.

    2. Pointer and keyboard must be separate uinput devices. Combining
       them makes libinput classify the result as cap:kp and silently
       drop EV_ABS motion events — only EV_REL is accepted for pointer
       aspects of kp devices. See finding-20260417-k7j5.

    3. Unique vendor/product IDs. evdev's default 0x0001:0x0001 collides
       with other common virtual uinput devices (hotkey daemons,
       passthrough scripts). libinput groups them and routes pointer
       events to whichever it picked first — often not ours.
    """

    def _captured_pair(self, monkeypatch):
        """Intercept UInput() and return the two capability dicts passed in.

        Returns a list of dicts, one per UInput() call, in creation order
        (pointer first, keyboard second).
        """
        captured = []

        class FakeDev:
            def close(self): pass

        def fake_uinput_ctor(capabilities, name=None, input_props=None,
                             vendor=None, product=None, version=None):
            captured.append({
                'capabilities': capabilities,
                'name': name,
                'input_props': input_props,
                'vendor': vendor,
                'product': product,
            })
            return FakeDev()

        import tine.input
        monkeypatch.setattr(tine.input, 'UInput', fake_uinput_ctor)
        monkeypatch.setattr(tine.input, '_pointer_device', None)
        monkeypatch.setattr(tine.input, '_keyboard_device', None)
        import time
        monkeypatch.setattr(time, 'sleep', lambda _s: None)
        tine.input._ensure_devices()
        assert len(captured) == 2, 'expected two UInput devices (pointer, keyboard)'
        return captured

    def test_pointer_device_has_no_btn_touch(self, monkeypatch):
        from evdev import ecodes

        pointer, _ = self._captured_pair(monkeypatch)
        key_caps = pointer['capabilities'].get(ecodes.EV_KEY, [])
        assert ecodes.BTN_TOUCH not in key_caps, (
            'tine-pointer must not advertise BTN_TOUCH — breaks Firefox clicks'
        )

    def test_pointer_device_has_mouse_buttons(self, monkeypatch):
        from evdev import ecodes

        pointer, _ = self._captured_pair(monkeypatch)
        key_caps = pointer['capabilities'].get(ecodes.EV_KEY, [])
        assert ecodes.BTN_LEFT in key_caps
        assert ecodes.BTN_RIGHT in key_caps
        assert ecodes.BTN_MIDDLE in key_caps

    def test_pointer_device_has_abs_axes(self, monkeypatch):
        from evdev import ecodes

        pointer, _ = self._captured_pair(monkeypatch)
        abs_caps = pointer['capabilities'].get(ecodes.EV_ABS, [])
        abs_codes = [code for code, info in abs_caps]
        assert ecodes.ABS_X in abs_codes
        assert ecodes.ABS_Y in abs_codes

    def test_pointer_device_has_no_keyboard_keys(self, monkeypatch):
        """cap:kp dispatch drops EV_ABS. Pointer must advertise only mouse buttons."""
        from evdev import ecodes
        from tine.input import KEYCODES

        pointer, _ = self._captured_pair(monkeypatch)
        key_caps = set(pointer['capabilities'].get(ecodes.EV_KEY, []))
        mouse_btns = {ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE}
        extra = key_caps - mouse_btns
        assert not extra, (
            f'tine-pointer must only advertise mouse buttons; found extra: {extra!r}'
        )
        # Cross-check: no tine keyboard keycode appears on the pointer device
        keyboard_codes = set(KEYCODES.values())
        overlap = key_caps & keyboard_codes - mouse_btns
        assert not overlap, (
            f'keyboard keycodes leaked onto pointer device: {overlap!r}'
        )

    def test_pointer_device_has_input_prop_direct(self, monkeypatch):
        from evdev import ecodes

        pointer, _ = self._captured_pair(monkeypatch)
        assert ecodes.INPUT_PROP_DIRECT in (pointer['input_props'] or [])

    def test_keyboard_device_has_no_mouse_buttons(self, monkeypatch):
        """Keyboard must not advertise mouse buttons — would flip it to cap:kp."""
        from evdev import ecodes

        _, keyboard = self._captured_pair(monkeypatch)
        key_caps = set(keyboard['capabilities'].get(ecodes.EV_KEY, []))
        mouse_btns = {ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE}
        assert not (key_caps & mouse_btns), (
            'tine-keyboard must not advertise mouse buttons'
        )

    def test_keyboard_device_has_no_abs_axes(self, monkeypatch):
        from evdev import ecodes

        _, keyboard = self._captured_pair(monkeypatch)
        assert ecodes.EV_ABS not in keyboard['capabilities'], (
            'tine-keyboard must not advertise absolute axes'
        )

    def test_keyboard_device_has_no_input_prop_direct(self, monkeypatch):
        from evdev import ecodes

        _, keyboard = self._captured_pair(monkeypatch)
        assert ecodes.INPUT_PROP_DIRECT not in (keyboard['input_props'] or [])

    def test_devices_have_unique_vendor_product(self, monkeypatch):
        """Default evdev 0x0001:0x0001 causes libinput group collisions.

        Each tine device must set a non-default vendor/product ID and the
        two devices must differ (so they can't accidentally collapse into
        each other within libinput's grouping logic).
        """
        pointer, keyboard = self._captured_pair(monkeypatch)
        assert pointer['vendor'] is not None and pointer['product'] is not None
        assert keyboard['vendor'] is not None and keyboard['product'] is not None
        assert pointer['vendor'] != 0x0001 or pointer['product'] != 0x0001
        assert keyboard['vendor'] != 0x0001 or keyboard['product'] != 0x0001
        assert (pointer['vendor'], pointer['product']) != (
            keyboard['vendor'], keyboard['product'])

    def test_inputd_creates_split_devices(self, monkeypatch):
        """The daemon must create the same pointer + keyboard pair."""
        from evdev import ecodes

        captured = []

        class FakeDev:
            def close(self): pass

        def fake_uinput_ctor(capabilities, name=None, input_props=None,
                             vendor=None, product=None, version=None):
            captured.append({
                'capabilities': capabilities, 'name': name,
                'input_props': input_props,
                'vendor': vendor, 'product': product,
            })
            return FakeDev()

        import tine.inputd
        monkeypatch.setattr(tine.inputd, 'UInput', fake_uinput_ctor)
        import time
        monkeypatch.setattr(time, 'sleep', lambda _s: None)
        pointer, keyboard = tine.inputd._create_devices()

        assert len(captured) == 2
        # Pointer is created first
        p_caps = captured[0]['capabilities']
        k_caps = captured[1]['capabilities']
        assert ecodes.BTN_TOUCH not in p_caps.get(ecodes.EV_KEY, [])
        assert ecodes.BTN_TOUCH not in k_caps.get(ecodes.EV_KEY, [])
        assert ecodes.EV_ABS in p_caps
        assert ecodes.EV_ABS not in k_caps
        p_keys = set(p_caps.get(ecodes.EV_KEY, []))
        assert p_keys == {ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE}
        # Unique, non-default IDs
        assert (captured[0]['vendor'], captured[0]['product']) != (0x0001, 0x0001)
        assert (captured[1]['vendor'], captured[1]['product']) != (0x0001, 0x0001)
        assert ((captured[0]['vendor'], captured[0]['product'])
                != (captured[1]['vendor'], captured[1]['product']))


# ---------------------------------------------------------------------------
# move() tests
# ---------------------------------------------------------------------------

class TestMove:
    """tine.input.move(x, y) -> moves mouse to absolute screen position via evdev."""

    def test_move_writes_abs_events(self, fake_uinput, default_scale):
        from tine.input import move
        from evdev import ecodes

        move(500, 300)

        abs_writes = fake_uinput.abs_writes
        # Should have X and Y writes
        x_vals = [v for code, v in abs_writes if code == ecodes.ABS_X]
        y_vals = [v for code, v in abs_writes if code == ecodes.ABS_Y]
        assert len(x_vals) == 1
        assert len(y_vals) == 1
        # 500 * 65535 / 1920 = 17067
        assert x_vals[0] == int(500 * 65535 / 1920)
        # 300 * 65535 / 1080 = 18198
        assert y_vals[0] == int(300 * 65535 / 1080)

    def test_move_calls_syn(self, fake_uinput, default_scale):
        from tine.input import move

        move(100, 100)
        assert fake_uinput.syn_count >= 1

    def test_move_rejects_negative_coords(self, fake_uinput, default_scale):
        from tine.input import move

        with pytest.raises(ValueError):
            move(-1, 100)
        with pytest.raises(ValueError):
            move(100, -1)

    def test_move_rejects_non_numeric(self, fake_uinput, default_scale):
        from tine.input import move

        with pytest.raises(TypeError):
            move('abc', 100)
        with pytest.raises(TypeError):
            move(100, None)

    def test_move_float_coords_truncated(self, fake_uinput, default_scale):
        """move() accepts floats and truncates to int."""
        from tine.input import move
        from evdev import ecodes

        move(100.7, 200.3)

        abs_writes = fake_uinput.abs_writes
        x_vals = [v for code, v in abs_writes if code == ecodes.ABS_X]
        y_vals = [v for code, v in abs_writes if code == ecodes.ABS_Y]
        # 100 * 65535 / 1920 = 3413
        assert x_vals[0] == int(100 * 65535 / 1920)
        # 200 * 65535 / 1080 = 12136
        assert y_vals[0] == int(200 * 65535 / 1080)


# ---------------------------------------------------------------------------
# click() tests
# ---------------------------------------------------------------------------

class TestClick:
    """tine.input.click(x, y, button='left') -> move + button press/release."""

    def test_click_writes_move_and_button(self, fake_uinput, default_scale):
        from tine.input import click
        from evdev import ecodes

        click(500, 300)

        # Should have ABS events (from move) and KEY events (button press/release)
        assert len(fake_uinput.abs_writes) >= 2  # X and Y
        key_writes = fake_uinput.key_writes
        # press (1) then release (0) for BTN_LEFT
        assert (ecodes.BTN_LEFT, 1) in key_writes
        assert (ecodes.BTN_LEFT, 0) in key_writes

    def test_click_left_button(self, fake_uinput, default_scale):
        from tine.input import click
        from evdev import ecodes

        click(100, 100, button='left')

        key_writes = fake_uinput.key_writes
        assert (ecodes.BTN_LEFT, 1) in key_writes
        assert (ecodes.BTN_LEFT, 0) in key_writes

    def test_click_right_button(self, fake_uinput, default_scale):
        from tine.input import click
        from evdev import ecodes

        click(100, 100, button='right')

        key_writes = fake_uinput.key_writes
        assert (ecodes.BTN_RIGHT, 1) in key_writes
        assert (ecodes.BTN_RIGHT, 0) in key_writes

    def test_click_middle_button(self, fake_uinput, default_scale):
        from tine.input import click
        from evdev import ecodes

        click(100, 100, button='middle')

        key_writes = fake_uinput.key_writes
        assert (ecodes.BTN_MIDDLE, 1) in key_writes
        assert (ecodes.BTN_MIDDLE, 0) in key_writes

    def test_click_double(self, fake_uinput, default_scale):
        from tine.input import click
        from evdev import ecodes

        click(100, 100, double=True)

        key_writes = fake_uinput.key_writes
        # Double click: two press+release cycles
        presses = [(c, v) for c, v in key_writes if c == ecodes.BTN_LEFT and v == 1]
        releases = [(c, v) for c, v in key_writes if c == ecodes.BTN_LEFT and v == 0]
        assert len(presses) == 2
        assert len(releases) == 2

    def test_click_rejects_invalid_button(self, fake_uinput, default_scale):
        from tine.input import click

        with pytest.raises(ValueError):
            click(100, 100, button='invalid')


# ---------------------------------------------------------------------------
# click_ref() tests
# ---------------------------------------------------------------------------

class TestClickRef:
    """tine.input.click_ref(ref_id, ref_cache, window_offset) -> resolves ref then clicks."""

    def test_click_ref_resolves_bbox_center(self, fake_uinput, default_scale):
        from tine.input import click_ref
        from evdev import ecodes

        cache = _make_ref_cache()
        # ref_1: bbox x=6, y=136, w=233, h=43
        # center: (6 + 233//2, 136 + 43//2) = (122, 157)
        # with window offset (400, 184), shadow=0, scale=1.0:
        # physical = (522, 341)
        click_ref('ref_1', cache, window_offset=(400, 184))

        abs_writes = fake_uinput.abs_writes
        x_vals = [v for code, v in abs_writes if code == ecodes.ABS_X]
        y_vals = [v for code, v in abs_writes if code == ecodes.ABS_Y]
        assert x_vals[0] == int(522 * 65535 / 1920)
        assert y_vals[0] == int(341 * 65535 / 1080)

    def test_click_ref_with_scale_window_coords(self, fake_uinput, monkeypatch):
        """Window-relative coords: window_offset + center, then scale."""
        import tine.display
        from tine.input import click_ref
        from evdev import ecodes

        monkeypatch.setattr(tine.display, '_cached_config', {
            'scale': 1.25, 'physical_width': 1920, 'physical_height': 1080,
        })

        cache = _make_ref_cache()
        # ref_2: bbox x=100, y=50, w=200, h=30 (coord=window by default)
        # center: (200, 65)
        # logical = window_offset + center = (10+200, 20+65) = (210, 85)
        # physical = logical * 1.25 = (262.5, 106.25) -> (262, 106)
        click_ref('ref_2', cache, window_offset=(10, 20))

        abs_writes = fake_uinput.abs_writes
        x_vals = [v for code, v in abs_writes if code == ecodes.ABS_X]
        y_vals = [v for code, v in abs_writes if code == ecodes.ABS_Y]
        assert x_vals[0] == int(262 * 65535 / 1920)
        assert y_vals[0] == int(106 * 65535 / 1080)

    def test_click_ref_screen_coords_ignores_offset(self, fake_uinput, monkeypatch):
        """Screen-space coords: offset is ignored, center used directly."""
        import tine.display
        from tine.input import click_ref
        from evdev import ecodes

        monkeypatch.setattr(tine.display, '_cached_config', {
            'scale': 1.25, 'physical_width': 1920, 'physical_height': 1080,
        })

        cache = {
            'ref_1': {
                'role': 'link',
                'name': 'Learn more',
                'bbox': {'x': 282, 'y': 311, 'w': 86, 'h': 19, 'coord': 'screen'},
            },
        }
        # center: (282+43, 311+9) = (325, 320)
        # screen coords — offset ignored
        # physical = (325*1.25, 320*1.25) = (406.25, 400.0) -> (406, 400)
        click_ref('ref_1', cache, window_offset=(128, 60))

        abs_writes = fake_uinput.abs_writes
        x_vals = [v for code, v in abs_writes if code == ecodes.ABS_X]
        y_vals = [v for code, v in abs_writes if code == ecodes.ABS_Y]
        assert x_vals[0] == int(406 * 65535 / 1920)
        assert y_vals[0] == int(400 * 65535 / 1080)

    def test_click_ref_unknown_ref_raises(self, fake_uinput, default_scale):
        from tine.input import click_ref

        cache = _make_ref_cache()
        with pytest.raises(KeyError):
            click_ref('ref_99', cache, window_offset=(0, 0))

    def test_click_ref_invalid_bbox_raises(self, fake_uinput, default_scale):
        from tine.input import click_ref

        cache = _make_ref_cache()
        # ref_3 has INT_MIN bbox
        with pytest.raises(ValueError):
            click_ref('ref_3', cache, window_offset=(0, 0))

    def test_click_ref_zero_offset(self, fake_uinput, default_scale):
        from tine.input import click_ref
        from evdev import ecodes

        cache = _make_ref_cache()
        # ref_2: bbox x=100, y=50, w=200, h=30
        # center: (200, 65), offset (0,0), shadow=0, scale=1.0 -> physical (200, 65)
        click_ref('ref_2', cache, window_offset=(0, 0))

        abs_writes = fake_uinput.abs_writes
        x_vals = [v for code, v in abs_writes if code == ecodes.ABS_X]
        y_vals = [v for code, v in abs_writes if code == ecodes.ABS_Y]
        assert x_vals[0] == int(200 * 65535 / 1920)
        assert y_vals[0] == int(65 * 65535 / 1080)

    def test_click_ref_right_button(self, fake_uinput, default_scale):
        """click_ref forwards button parameter to click()."""
        from tine.input import click_ref
        from evdev import ecodes

        cache = _make_ref_cache()
        click_ref('ref_2', cache, window_offset=(0, 0), button='right')

        key_writes = fake_uinput.key_writes
        assert (ecodes.BTN_RIGHT, 1) in key_writes

    def test_click_ref_from_cache_file(self, fake_uinput, default_scale, tmp_path):
        """click_ref can load ref cache from file path."""
        from tine.input import click_ref
        from tine.refs import save_ref_cache

        cache = _make_ref_cache()
        cache_path = str(tmp_path / 'refs.json')
        save_ref_cache(cache, cache_path)

        # Pass path string instead of dict
        click_ref('ref_1', cache_path, window_offset=(0, 0))

        assert len(fake_uinput.abs_writes) >= 2  # X and Y from move

    def test_click_ref_missing_bbox_raises_valueerror(self, fake_uinput, default_scale):
        """click_ref raises ValueError (not KeyError) for refs without bbox key."""
        from tine.input import click_ref

        cache = {'ref_1': {'role': 'button', 'name': 'OK'}}  # no bbox
        with pytest.raises(ValueError, match='no bbox'):
            click_ref('ref_1', cache, window_offset=(0, 0))


# ---------------------------------------------------------------------------
# type_text() tests
# ---------------------------------------------------------------------------

class TestTypeText:
    """tine.input.type_text(text) -> types text via evdev key events."""

    def test_type_text_basic(self, fake_uinput, default_scale):
        from tine.input import type_text, KEYCODES

        type_text('hello')

        key_writes = fake_uinput.key_writes
        # Each char: press (value=1) + release (value=0)
        expected_keycodes = [KEYCODES[c] for c in 'hello']
        presses = [(code, val) for code, val in key_writes if val == 1]
        releases = [(code, val) for code, val in key_writes if val == 0]
        assert len(presses) == 5
        assert len(releases) == 5
        assert [code for code, _ in presses] == expected_keycodes

    def test_type_text_empty_string(self, fake_uinput, default_scale):
        from tine.input import type_text

        type_text('')

        assert len(fake_uinput.events) == 0
        assert fake_uinput.syn_count == 0

    def test_type_text_special_chars(self, fake_uinput, default_scale):
        """Shifted punctuation produces shift press/release around the keycode."""
        from tine.input import type_text, KEYCODES

        type_text('A!')

        key_writes = fake_uinput.key_writes
        shift = KEYCODES['shift']
        # 'A' needs shift+a, '!' needs shift+1
        # Expect: shift down, a down, a up, shift up, shift down, 1 down, 1 up, shift up
        assert key_writes[0] == (shift, 1)   # shift press for A
        assert key_writes[1] == (KEYCODES['a'], 1)
        assert key_writes[2] == (KEYCODES['a'], 0)
        assert key_writes[3] == (shift, 0)   # shift release for A
        assert key_writes[4] == (shift, 1)   # shift press for !
        assert key_writes[5] == (KEYCODES['1'], 1)
        assert key_writes[6] == (KEYCODES['1'], 0)
        assert key_writes[7] == (shift, 0)   # shift release for !

    def test_type_text_with_delay(self, fake_uinput, default_scale, monkeypatch):
        import time
        sleeps = []
        monkeypatch.setattr(time, 'sleep', lambda s: sleeps.append(s))
        from tine.input import type_text

        type_text('ab', delay_ms=50)

        assert all(s == 0.05 for s in sleeps)
        assert len(sleeps) == 2  # one delay per character

    def test_type_text_unshifted_punctuation(self, fake_uinput, default_scale):
        """Unshifted punctuation (dash, dot, etc.) sends keycode without shift."""
        from tine.input import type_text, KEYCODES

        type_text('-.')

        key_writes = fake_uinput.key_writes
        shift = KEYCODES['shift']
        # No shift events
        assert (shift, 1) not in key_writes
        assert key_writes[0] == (KEYCODES['minus'], 1)
        assert key_writes[1] == (KEYCODES['minus'], 0)
        assert key_writes[2] == (KEYCODES['dot'], 1)
        assert key_writes[3] == (KEYCODES['dot'], 0)

    def test_type_text_unmapped_char_raises(self, fake_uinput, default_scale):
        """Characters not in CHAR_MAP raise ValueError."""
        from tine.input import type_text

        with pytest.raises(ValueError, match='No keycode mapping'):
            type_text('\x00')

    def test_type_text_none_raises_valueerror(self, monkeypatch):
        from tine.input import type_text

        with pytest.raises(ValueError, match='string'):
            type_text(None)

    def test_type_text_int_raises_valueerror(self, monkeypatch):
        from tine.input import type_text

        with pytest.raises(ValueError, match='string'):
            type_text(42)

    def test_type_text_negative_delay_raises(self, monkeypatch):
        from tine.input import type_text

        with pytest.raises(ValueError, match='delay_ms'):
            type_text('hello', delay_ms=-1)


# ---------------------------------------------------------------------------
# key_combo() tests
# ---------------------------------------------------------------------------

class TestKeyCombo:
    """tine.input.key_combo(combo_str) -> presses key combination via evdev."""

    def test_key_combo_ctrl_c(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('ctrl+c')

        key_writes = fake_uinput.key_writes
        # ctrl=29, c=46 — should see press and release
        assert (29, 1) in key_writes  # ctrl press
        assert (46, 1) in key_writes  # c press
        assert (46, 0) in key_writes  # c release
        assert (29, 0) in key_writes  # ctrl release

    def test_key_combo_single_key(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('enter')

        key_writes = fake_uinput.key_writes
        assert (28, 1) in key_writes  # KEY_ENTER press
        assert (28, 0) in key_writes  # KEY_ENTER release

    def test_key_combo_alt_f4(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('alt+f4')

        key_writes = fake_uinput.key_writes
        assert (56, 1) in key_writes   # KEY_LEFTALT press
        assert (62, 1) in key_writes   # KEY_F4 press
        assert (62, 0) in key_writes   # KEY_F4 release
        assert (56, 0) in key_writes   # KEY_LEFTALT release

    def test_key_combo_shift_a(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('shift+a')

        key_writes = fake_uinput.key_writes
        assert (42, 1) in key_writes  # KEY_LEFTSHIFT press
        assert (30, 1) in key_writes  # KEY_A press

    def test_key_combo_ctrl_shift_t(self, fake_uinput, default_scale):
        """Three-key combo: ctrl+shift+t."""
        from tine.input import key_combo

        key_combo('ctrl+shift+t')

        key_writes = fake_uinput.key_writes
        assert (29, 1) in key_writes   # ctrl down
        assert (42, 1) in key_writes   # shift down
        assert (20, 1) in key_writes   # t down
        assert (20, 0) in key_writes   # t up
        assert (42, 0) in key_writes   # shift up
        assert (29, 0) in key_writes   # ctrl up

    def test_key_combo_super(self, fake_uinput, default_scale):
        """Super/meta key."""
        from tine.input import key_combo

        key_combo('super')

        key_writes = fake_uinput.key_writes
        assert (125, 1) in key_writes  # KEY_LEFTMETA press
        assert (125, 0) in key_writes  # KEY_LEFTMETA release

    def test_key_combo_tab(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('tab')

        key_writes = fake_uinput.key_writes
        assert (15, 1) in key_writes
        assert (15, 0) in key_writes

    def test_key_combo_escape(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('escape')

        key_writes = fake_uinput.key_writes
        assert (1, 1) in key_writes   # KEY_ESC press
        assert (1, 0) in key_writes   # KEY_ESC release

    def test_key_combo_unknown_key_raises(self, fake_uinput, default_scale):
        from tine.input import key_combo

        with pytest.raises(ValueError):
            key_combo('nonexistent_key')

    def test_key_combo_case_insensitive(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Ctrl+C')

        key_writes = fake_uinput.key_writes
        assert (29, 1) in key_writes
        assert (46, 1) in key_writes

    def test_key_combo_release_order(self, fake_uinput, default_scale):
        """Modifiers released in reverse order of pressing."""
        from tine.input import key_combo

        key_combo('ctrl+alt+delete')

        key_writes = fake_uinput.key_writes
        # Find indices of press/release events
        events = [(code, val) for code, val in key_writes]
        ctrl_press_idx = events.index((29, 1))
        alt_press_idx = events.index((56, 1))
        del_press_idx = events.index((111, 1))
        del_release_idx = events.index((111, 0))
        alt_release_idx = events.index((56, 0))
        ctrl_release_idx = events.index((29, 0))

        assert ctrl_press_idx < alt_press_idx < del_press_idx
        assert del_release_idx < alt_release_idx < ctrl_release_idx

    def test_key_combo_function_keys(self, fake_uinput, default_scale):
        """F1 through F12."""
        from tine.input import key_combo

        key_combo('f1')
        assert (59, 1) in fake_uinput.key_writes

        # Reset events for next combo
        fake_uinput.events.clear()
        key_combo('f12')
        assert (88, 1) in fake_uinput.key_writes

    def test_key_combo_space(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('space')
        assert (57, 1) in fake_uinput.key_writes  # KEY_SPACE

    def test_key_combo_arrow_keys(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('up')
        assert (103, 1) in fake_uinput.key_writes

        fake_uinput.events.clear()
        key_combo('down')
        assert (108, 1) in fake_uinput.key_writes

        fake_uinput.events.clear()
        key_combo('left')
        assert (105, 1) in fake_uinput.key_writes

        fake_uinput.events.clear()
        key_combo('right')
        assert (106, 1) in fake_uinput.key_writes

    def test_key_combo_plus_key(self, fake_uinput, default_scale):
        """'plus' alias allows typing the + key."""
        from tine.input import key_combo

        key_combo('ctrl+plus')
        key_writes = fake_uinput.key_writes
        assert (29, 1) in key_writes   # ctrl
        assert (13, 1) in key_writes   # KEY_EQUAL (plus key)

    def test_key_combo_none_raises(self, fake_uinput, default_scale):
        from tine.input import key_combo

        with pytest.raises(ValueError, match='string'):
            key_combo(None)

    def test_key_combo_empty_raises(self, fake_uinput, default_scale):
        from tine.input import key_combo

        with pytest.raises(ValueError, match='Empty'):
            key_combo('')

    def test_key_combo_trailing_plus_handled(self, fake_uinput, default_scale):
        """'ctrl+' (trailing delimiter) doesn't crash — empty segment filtered."""
        from tine.input import key_combo

        key_combo('ctrl+')
        assert (29, 1) in fake_uinput.key_writes  # just ctrl

    def test_key_combo_calls_syn(self, fake_uinput, default_scale):
        """key_combo should call syn() to flush events."""
        from tine.input import key_combo

        key_combo('enter')
        assert fake_uinput.syn_count >= 1


# -----------------------------------------------------------------------------
# Key name normalization — accept X11/XKB keysym forms
# (brief-20260418-x7yz)
# -----------------------------------------------------------------------------


class TestNormalizeKeyName:
    """tine.input._normalize_key_name — X11/XKB keysym acceptance."""

    def test_canonical_name_unchanged(self):
        from tine.input import _normalize_key_name

        assert _normalize_key_name('pagedown') == 'pagedown'
        assert _normalize_key_name('backspace') == 'backspace'
        assert _normalize_key_name('ctrl') == 'ctrl'

    def test_strips_underscore(self):
        from tine.input import _normalize_key_name

        assert _normalize_key_name('Page_Down') == 'pagedown'
        assert _normalize_key_name('Page_Up') == 'pageup'
        assert _normalize_key_name('Caps_Lock') == 'capslock'
        assert _normalize_key_name('Back_Space') == 'backspace'

    def test_camelcase_normalizes(self):
        from tine.input import _normalize_key_name

        assert _normalize_key_name('PageDown') == 'pagedown'
        assert _normalize_key_name('BackSpace') == 'backspace'
        assert _normalize_key_name('CapsLock') == 'capslock'

    def test_strips_spaces_and_dashes(self):
        from tine.input import _normalize_key_name

        assert _normalize_key_name('Page Down') == 'pagedown'
        assert _normalize_key_name('page-down') == 'pagedown'

    def test_lr_modifier_aliases(self):
        from tine.input import _normalize_key_name

        assert _normalize_key_name('Left_Shift') == 'lshift'
        assert _normalize_key_name('Right_Shift') == 'rshift'
        assert _normalize_key_name('Shift_L') == 'lshift'
        assert _normalize_key_name('Shift_R') == 'rshift'
        assert _normalize_key_name('Control_L') == 'lctrl'
        assert _normalize_key_name('Control_R') == 'rctrl'
        assert _normalize_key_name('Alt_L') == 'lalt'
        assert _normalize_key_name('Alt_R') == 'ralt'

    def test_bare_control_maps_to_ctrl(self):
        from tine.input import _normalize_key_name

        assert _normalize_key_name('Control') == 'ctrl'
        assert _normalize_key_name('CONTROL') == 'ctrl'

    def test_common_abbreviations(self):
        from tine.input import _normalize_key_name

        assert _normalize_key_name('Del') == 'delete'
        assert _normalize_key_name('Ins') == 'insert'

    def test_unknown_returns_normalized_form(self):
        """Caller does the final KEYCODES lookup; normalizer just prepares it."""
        from tine.input import _normalize_key_name

        assert _normalize_key_name('Nonexistent_Key') == 'nonexistentkey'


class TestKeyComboXkbAliases:
    """key_combo accepts X11/XKB keysym names that were previously rejected."""

    def test_page_down_xkb_form(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Page_Down')
        assert (109, 1) in fake_uinput.key_writes  # KEY_PAGEDOWN

    def test_page_down_camelcase(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('PageDown')
        assert (109, 1) in fake_uinput.key_writes

    def test_backspace_xkb_form(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('BackSpace')
        assert (14, 1) in fake_uinput.key_writes  # KEY_BACKSPACE

    def test_caps_lock_xkb_form(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Caps_Lock')
        assert (58, 1) in fake_uinput.key_writes  # KEY_CAPSLOCK

    def test_left_shift_maps_to_lshift(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Left_Shift')
        assert (42, 1) in fake_uinput.key_writes  # KEY_LEFTSHIFT

    def test_right_shift_maps_to_rshift(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Right_Shift')
        assert (54, 1) in fake_uinput.key_writes  # KEY_RIGHTSHIFT

    def test_shift_l_xkb_keysym(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Shift_L')
        assert (42, 1) in fake_uinput.key_writes

    def test_control_l_xkb_keysym(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Control_L')
        assert (29, 1) in fake_uinput.key_writes  # KEY_LEFTCTRL

    def test_alias_works_inside_combo(self, fake_uinput, default_scale):
        """XKB aliases must work when combined with modifiers."""
        from tine.input import key_combo

        key_combo('Ctrl+Page_Down')
        assert (29, 1) in fake_uinput.key_writes   # ctrl
        assert (109, 1) in fake_uinput.key_writes  # pagedown

    def test_genuinely_unknown_still_raises(self, fake_uinput, default_scale):
        """Normalization must not mask truly unknown keys."""
        from tine.input import key_combo

        with pytest.raises(ValueError, match='Unknown key'):
            key_combo('Not_A_Real_Key')

    def test_daemon_receives_normalized_combo(self, fake_uinput, default_scale, monkeypatch):
        """key_combo forwards normalized combo to daemon so daemon parsers
        downstream don't need to repeat the X11/XKB translation."""
        from tine import input as tine_input

        captured = {}

        def fake_daemon(cmd):
            captured['cmd'] = cmd
            return {'ok': True}

        monkeypatch.setattr(tine_input, '_try_daemon', fake_daemon)
        tine_input.key_combo('Ctrl+Page_Down')

        assert captured['cmd']['action'] == 'key'
        assert captured['cmd']['combo'] == 'ctrl+pagedown'

    def test_super_l_maps_to_super(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Super_L')
        assert (125, 1) in fake_uinput.key_writes  # KEY_LEFTMETA

    def test_super_r_maps_to_super(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Super_R')
        assert (125, 1) in fake_uinput.key_writes

    def test_meta_l_maps_to_meta(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Meta_L')
        assert (125, 1) in fake_uinput.key_writes

    def test_meta_r_maps_to_meta(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Meta_R')
        assert (125, 1) in fake_uinput.key_writes

    def test_super_left_prefix_form(self, fake_uinput, default_scale):
        from tine.input import key_combo

        key_combo('Left_Super')
        assert (125, 1) in fake_uinput.key_writes

    def test_super_combo_with_letter(self, fake_uinput, default_scale):
        """Super+A (GNOME overview) via XKB Super_L form."""
        from tine.input import key_combo

        key_combo('Super_L+a')
        assert (125, 1) in fake_uinput.key_writes  # super
        assert (30, 1) in fake_uinput.key_writes   # a
