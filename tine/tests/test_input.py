"""Tests for tine input: click, click_ref, type_text, key_combo, move.

All input injection goes through ydotool. These tests mock subprocess calls
to verify correct command construction. Integration tests on archgnome
verify actual input delivery.
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


# ---------------------------------------------------------------------------
# move() tests
# ---------------------------------------------------------------------------

class TestMove:
    """tine.input.move(x, y) -> moves mouse to absolute screen position."""

    def test_move_calls_ydotool_absolute(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import move

        move(500, 300)

        cmd = fake.last_cmd_str()
        assert 'ydotool' in cmd
        assert 'mousemove' in cmd
        # Must use absolute flag
        assert '-a' in fake.last_cmd or '--absolute' in fake.last_cmd

    def test_move_passes_coordinates(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import move

        move(1234, 567)

        cmd = fake.last_cmd
        # x and y values should appear in the command
        cmd_str = ' '.join(str(c) for c in cmd)
        assert '1234' in cmd_str
        assert '567' in cmd_str

    def test_move_rejects_negative_coords(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import move

        with pytest.raises(ValueError):
            move(-1, 100)
        with pytest.raises(ValueError):
            move(100, -1)

    def test_move_raises_on_ydotool_failure(self, monkeypatch):
        import subprocess

        def fail(cmd, **kwargs):
            return type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'err'})()

        monkeypatch.setattr(subprocess, 'run', fail)
        from tine.input import move

        with pytest.raises(RuntimeError):
            move(100, 100)


# ---------------------------------------------------------------------------
# click() tests
# ---------------------------------------------------------------------------

class TestClick:
    """tine.input.click(x, y, button='left') -> move + click."""

    def test_click_moves_then_clicks(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click

        click(500, 300)

        # Should have at least 2 calls: mousemove + click
        assert len(fake.calls) >= 2
        move_cmd = ' '.join(str(c) for c in fake.calls[0])
        click_cmd = ' '.join(str(c) for c in fake.calls[1])
        assert 'mousemove' in move_cmd
        assert 'click' in click_cmd

    def test_click_left_button(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click

        click(100, 100, button='left')

        click_cmd = ' '.join(str(c) for c in fake.calls[-1])
        # 0xC0 is left click
        assert '0xC0' in click_cmd or 'C0' in click_cmd

    def test_click_right_button(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click

        click(100, 100, button='right')

        click_cmd = ' '.join(str(c) for c in fake.calls[-1])
        assert '0xC1' in click_cmd or 'C1' in click_cmd

    def test_click_middle_button(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click

        click(100, 100, button='middle')

        click_cmd = ' '.join(str(c) for c in fake.calls[-1])
        assert '0xC2' in click_cmd or 'C2' in click_cmd

    def test_click_double(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click

        click(100, 100, double=True)

        click_cmd = fake.calls[-1]
        assert '--repeat' in click_cmd
        repeat_idx = click_cmd.index('--repeat')
        assert click_cmd[repeat_idx + 1] == '2'
        assert '--next-delay' in click_cmd
        delay_idx = click_cmd.index('--next-delay')
        assert click_cmd[delay_idx + 1] == '50'

    def test_click_rejects_invalid_button(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click

        with pytest.raises(ValueError):
            click(100, 100, button='invalid')


# ---------------------------------------------------------------------------
# click_ref() tests
# ---------------------------------------------------------------------------

class TestClickRef:
    """tine.input.click_ref(ref_id, ref_cache, window_offset) -> resolves ref then clicks."""

    def test_click_ref_resolves_bbox_center(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click_ref

        cache = _make_ref_cache()
        # ref_1: bbox x=6, y=136, w=233, h=43
        # center: (6 + 233//2, 136 + 43//2) = (122, 157)
        # with window offset (400, 184): screen = (522, 341)
        click_ref('ref_1', cache, window_offset=(400, 184))

        move_cmd = ' '.join(str(c) for c in fake.calls[0])
        assert '522' in move_cmd
        assert '341' in move_cmd

    def test_click_ref_unknown_ref_raises(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click_ref

        cache = _make_ref_cache()
        with pytest.raises(KeyError):
            click_ref('ref_99', cache, window_offset=(0, 0))

    def test_click_ref_invalid_bbox_raises(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click_ref

        cache = _make_ref_cache()
        # ref_3 has INT_MIN bbox
        with pytest.raises(ValueError):
            click_ref('ref_3', cache, window_offset=(0, 0))

    def test_click_ref_zero_offset(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click_ref

        cache = _make_ref_cache()
        # ref_2: bbox x=100, y=50, w=200, h=30
        # center: (200, 65), offset (0,0) → (200, 65)
        click_ref('ref_2', cache, window_offset=(0, 0))

        move_cmd = ' '.join(str(c) for c in fake.calls[0])
        assert '200' in move_cmd
        assert '65' in move_cmd

    def test_click_ref_right_button(self, monkeypatch):
        """click_ref forwards button parameter to click()."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click_ref

        cache = _make_ref_cache()
        click_ref('ref_2', cache, window_offset=(0, 0), button='right')

        click_cmd = ' '.join(str(c) for c in fake.calls[-1])
        assert '0xC1' in click_cmd

    def test_click_ref_from_cache_file(self, monkeypatch, tmp_path):
        """click_ref can load ref cache from file path."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click_ref
        from tine.refs import save_ref_cache

        cache = _make_ref_cache()
        cache_path = str(tmp_path / 'refs.json')
        save_ref_cache(cache, cache_path)

        # Pass path string instead of dict
        click_ref('ref_1', cache_path, window_offset=(0, 0))

        assert len(fake.calls) >= 2  # move + click

    def test_click_ref_missing_bbox_raises_valueerror(self, monkeypatch):
        """click_ref raises ValueError (not KeyError) for refs without bbox key."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import click_ref

        cache = {'ref_1': {'role': 'button', 'name': 'OK'}}  # no bbox
        with pytest.raises(ValueError, match='no bbox'):
            click_ref('ref_1', cache, window_offset=(0, 0))


# ---------------------------------------------------------------------------
# type_text() tests
# ---------------------------------------------------------------------------

class TestTypeText:
    """tine.input.type_text(text) -> types text via ydotool."""

    def test_type_text_calls_ydotool(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import type_text

        type_text('hello world')

        cmd = fake.last_cmd_str()
        assert 'ydotool' in cmd
        assert 'type' in cmd
        assert 'hello world' in cmd

    def test_type_text_empty_string(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import type_text

        # Empty string is valid — ydotool handles it
        type_text('')
        assert len(fake.calls) == 1

    def test_type_text_special_chars(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import type_text

        # Should not crash on special characters
        type_text('echo "hello" > /tmp/test.txt && cat /tmp/test.txt')
        assert len(fake.calls) == 1

    def test_type_text_with_delay(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import type_text

        type_text('hello', delay_ms=50)

        cmd = fake.last_cmd
        assert '--key-delay' in cmd
        delay_idx = cmd.index('--key-delay')
        assert cmd[delay_idx + 1] == '50'

    def test_type_text_dash_prefix(self, monkeypatch):
        """type_text uses -- separator so text starting with - isn't parsed as flag."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import type_text

        type_text('--help')

        cmd = fake.last_cmd
        # '--' must appear before the text to prevent flag parsing
        separator_idx = cmd.index('--')
        text_idx = cmd.index('--help')
        assert separator_idx < text_idx

    def test_type_text_none_raises_valueerror(self, monkeypatch):
        """type_text(None) raises ValueError, not TypeError from subprocess."""
        from tine.input import type_text

        with pytest.raises(ValueError, match='string'):
            type_text(None)

    def test_type_text_int_raises_valueerror(self, monkeypatch):
        """type_text(42) raises ValueError."""
        from tine.input import type_text

        with pytest.raises(ValueError, match='string'):
            type_text(42)

    def test_type_text_negative_delay_raises(self, monkeypatch):
        """Negative delay_ms raises ValueError."""
        from tine.input import type_text

        with pytest.raises(ValueError, match='delay_ms'):
            type_text('hello', delay_ms=-1)

    def test_type_text_raises_on_failure(self, monkeypatch):
        import subprocess

        def fail(cmd, **kwargs):
            return type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'err'})()

        monkeypatch.setattr(subprocess, 'run', fail)
        from tine.input import type_text

        with pytest.raises(RuntimeError):
            type_text('hello')


# ---------------------------------------------------------------------------
# key_combo() tests
# ---------------------------------------------------------------------------

class TestKeyCombo:
    """tine.input.key_combo(combo_str) -> presses key combination via ydotool."""

    def test_key_combo_ctrl_c(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('ctrl+c')

        cmd = fake.last_cmd_str()
        assert 'ydotool' in cmd
        assert 'key' in cmd
        # ctrl=29, c=46 — should see press and release
        assert '29:1' in cmd
        assert '46:1' in cmd
        assert '46:0' in cmd
        assert '29:0' in cmd

    def test_key_combo_single_key(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('enter')

        cmd = fake.last_cmd_str()
        assert '28:1' in cmd  # KEY_ENTER
        assert '28:0' in cmd

    def test_key_combo_alt_f4(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('alt+f4')

        cmd = fake.last_cmd_str()
        assert '56:1' in cmd  # KEY_LEFTALT
        assert '62:1' in cmd  # KEY_F4
        assert '62:0' in cmd
        assert '56:0' in cmd

    def test_key_combo_shift_a(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('shift+a')

        cmd = fake.last_cmd_str()
        assert '42:1' in cmd  # KEY_LEFTSHIFT
        assert '30:1' in cmd  # KEY_A

    def test_key_combo_ctrl_shift_t(self, monkeypatch):
        """Three-key combo: ctrl+shift+t."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('ctrl+shift+t')

        cmd = fake.last_cmd_str()
        assert '29:1' in cmd   # ctrl down
        assert '42:1' in cmd   # shift down
        assert '20:1' in cmd   # t down
        assert '20:0' in cmd   # t up
        assert '42:0' in cmd   # shift up
        assert '29:0' in cmd   # ctrl up

    def test_key_combo_super(self, monkeypatch):
        """Super/meta key."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('super')

        cmd = fake.last_cmd_str()
        # KEY_LEFTMETA = 125
        assert '125:1' in cmd
        assert '125:0' in cmd

    def test_key_combo_tab(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('tab')

        cmd = fake.last_cmd_str()
        assert '15:1' in cmd
        assert '15:0' in cmd

    def test_key_combo_escape(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('escape')

        cmd = fake.last_cmd_str()
        assert '1:1' in cmd  # KEY_ESC
        assert '1:0' in cmd

    def test_key_combo_unknown_key_raises(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        with pytest.raises(ValueError):
            key_combo('nonexistent_key')

    def test_key_combo_case_insensitive(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('Ctrl+C')

        cmd = fake.last_cmd_str()
        assert '29:1' in cmd
        assert '46:1' in cmd

    def test_key_combo_release_order(self, monkeypatch):
        """Modifiers released in reverse order of pressing."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('ctrl+alt+delete')

        cmd_parts = fake.last_cmd
        # Find positions of press/release events
        cmd_str = ' '.join(str(c) for c in cmd_parts)
        # ctrl press should come before alt press
        ctrl_press = cmd_str.index('29:1')
        alt_press = cmd_str.index('56:1')
        del_press = cmd_str.index('111:1')
        del_release = cmd_str.index('111:0')
        alt_release = cmd_str.index('56:0')
        ctrl_release = cmd_str.index('29:0')

        assert ctrl_press < alt_press < del_press
        assert del_release < alt_release < ctrl_release

    def test_key_combo_raises_on_failure(self, monkeypatch):
        import subprocess

        def fail(cmd, **kwargs):
            return type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'err'})()

        monkeypatch.setattr(subprocess, 'run', fail)
        from tine.input import key_combo

        with pytest.raises(RuntimeError):
            key_combo('enter')

    def test_key_combo_function_keys(self, monkeypatch):
        """F1 through F12."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('f1')
        cmd = fake.last_cmd_str()
        assert '59:1' in cmd

        key_combo('f12')
        cmd = fake.last_cmd_str()
        assert '88:1' in cmd

    def test_key_combo_space(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('space')
        cmd = fake.last_cmd_str()
        assert '57:1' in cmd  # KEY_SPACE

    def test_key_combo_arrow_keys(self, monkeypatch):
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('up')
        assert '103:1' in fake.last_cmd_str()

        key_combo('down')
        assert '108:1' in fake.last_cmd_str()

        key_combo('left')
        assert '105:1' in fake.last_cmd_str()

        key_combo('right')
        assert '106:1' in fake.last_cmd_str()

    def test_key_combo_plus_key(self, monkeypatch):
        """'plus' alias allows typing the + key (can't use literal '+' as delimiter)."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('ctrl+plus')
        cmd = fake.last_cmd_str()
        assert '29:1' in cmd  # ctrl
        assert '13:1' in cmd  # KEY_EQUAL (plus key)

    def test_key_combo_none_raises(self, monkeypatch):
        """key_combo(None) raises ValueError, not AttributeError."""
        from tine.input import key_combo

        with pytest.raises(ValueError, match='string'):
            key_combo(None)

    def test_key_combo_empty_raises(self, monkeypatch):
        """Empty combo string raises ValueError."""
        from tine.input import key_combo

        with pytest.raises(ValueError, match='Empty'):
            key_combo('')

    def test_key_combo_trailing_plus_handled(self, monkeypatch):
        """'ctrl+' (trailing delimiter) doesn't crash — empty segment filtered."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import key_combo

        key_combo('ctrl+')
        cmd = fake.last_cmd_str()
        assert '29:1' in cmd  # just ctrl

    def test_move_float_coords_truncated(self, monkeypatch):
        """move() accepts floats and truncates to int."""
        import subprocess
        fake = FakeSubprocess()
        monkeypatch.setattr(subprocess, 'run', fake.run)
        from tine.input import move

        move(100.7, 200.3)
        cmd = fake.last_cmd_str()
        assert '100' in cmd
        assert '200' in cmd
