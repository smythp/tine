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


@pytest.fixture
def fake_uinput(monkeypatch):
    """Patch _get_uinput to return a FakeUInput and reset cached device."""
    import tine.input
    fake = FakeUInput()
    monkeypatch.setattr(tine.input, '_uinput_device', fake)
    monkeypatch.setattr(tine.input, '_get_uinput', lambda: fake)
    return fake


@pytest.fixture
def default_scale(monkeypatch):
    """Ensure default scale/physical/shadow settings for predictable math."""
    import tine.input
    monkeypatch.setattr(tine.input, 'PHYSICAL_WIDTH', 1920)
    monkeypatch.setattr(tine.input, 'PHYSICAL_HEIGHT', 1080)
    monkeypatch.setattr(tine.input, 'SCALE_FACTOR', 1.0)


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
        import tine.input
        from tine.input import click_ref
        from evdev import ecodes

        monkeypatch.setattr(tine.input, 'PHYSICAL_WIDTH', 1920)
        monkeypatch.setattr(tine.input, 'PHYSICAL_HEIGHT', 1080)
        monkeypatch.setattr(tine.input, 'SCALE_FACTOR', 1.25)

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
        import tine.input
        from tine.input import click_ref
        from evdev import ecodes

        monkeypatch.setattr(tine.input, 'PHYSICAL_WIDTH', 1920)
        monkeypatch.setattr(tine.input, 'PHYSICAL_HEIGHT', 1080)
        monkeypatch.setattr(tine.input, 'SCALE_FACTOR', 1.25)

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
