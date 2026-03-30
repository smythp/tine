"""Adversarial tests for tine/screenshot.py, tine/input.py, tine/describe.py.

Gremlin tests -- violate every assumption the code makes.
No happy paths. Only edges, nulls, NaNs, and things that should never happen.
"""

import json
import math
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ===========================================================================
# Helpers
# ===========================================================================

def make_ref_cache(*entries):
    """Build a ref_cache dict from (ref_id, bbox) pairs."""
    cache = {}
    for ref_id, bbox in entries:
        cache[ref_id] = {'role': 'button', 'name': 'X', 'bbox': bbox}
    return cache


def valid_bbox():
    return {'x': 10, 'y': 20, 'w': 100, 'h': 40}


class _FakeUInput:
    """Minimal fake UInput for adversarial tests."""
    def __init__(self):
        self.events = []
        self.syn_count = 0
    def write(self, etype, ecode, value):
        self.events.append((etype, ecode, value))
    def syn(self):
        self.syn_count += 1


def _patch_uinput():
    """Return a patch context manager that replaces _get_uinput with a FakeUInput."""
    fake = _FakeUInput()
    return patch('tine.input._get_uinput', return_value=fake), fake


def make_tiny_png(path):
    """Write a minimal valid 1x1 white PNG to path."""
    from PIL import Image
    img = Image.new('RGB', (200, 200), color=(255, 255, 255))
    img.save(path)
    return path


# ===========================================================================
# screenshot.py -- annotate()
# ===========================================================================

class TestAnnotateAdversarial:

    def test_scale_zero_raises(self, tmp_path):
        """scale=0 is rejected with ValueError (hardening: scale must be positive)."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = make_ref_cache(
            ('ref_1', {'x': 100, 'y': 100, 'w': 50, 'h': 50}),
        )
        with pytest.raises(ValueError, match='Scale must be positive'):
            annotate(img_path, ref_cache, scale=0)

    def test_scale_negative_raises(self, tmp_path):
        """scale=-1 is rejected with ValueError (hardening: scale must be positive)."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = make_ref_cache(
            ('ref_1', {'x': 100, 'y': 100, 'w': 50, 'h': 50}),
        )
        with pytest.raises(ValueError, match='Scale must be positive'):
            annotate(img_path, ref_cache, scale=-1)

    def test_scale_nan_bbox_coords(self, tmp_path):
        """NaN in bbox coords: int(NaN) raises ValueError, ref is skipped gracefully."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X',
                               'bbox': {'x': float('nan'), 'y': 10, 'w': 50, 'h': 50}}}
        # ValueError is caught internally; the ref is skipped, no crash
        result = annotate(img_path, ref_cache, scale=1.0)
        assert result == img_path

    def test_scale_inf_bbox_coords(self, tmp_path):
        """Infinity in bbox coords: int(inf) raises OverflowError, ref is skipped."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X',
                               'bbox': {'x': float('inf'), 'y': 10, 'w': 50, 'h': 50}}}
        # OverflowError is caught internally; the ref is skipped, no crash
        result = annotate(img_path, ref_cache, scale=1.0)
        assert result == img_path

    def test_bbox_string_coords(self, tmp_path):
        """String values in bbox fields: int('abc' * 1.0) -> TypeError."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X',
                               'bbox': {'x': 'fifty', 'y': 10, 'w': 50, 'h': 50}}}
        with pytest.raises(TypeError):
            annotate(img_path, ref_cache, scale=1.0)

    def test_ref_id_empty_suffix(self, tmp_path):
        """ref_id='ref_' -> label=''. Empty string label to PIL textbbox."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = {'ref_': {'role': 'button', 'name': 'X',
                              'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 50}}}
        # Should not crash -- empty label is valid for PIL
        annotate(img_path, ref_cache, scale=1.0)

    def test_ref_id_no_prefix(self, tmp_path):
        """ref_id without 'ref_' prefix: replace('ref_', '') returns original."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = {'button_42': {'role': 'button', 'name': 'X',
                                   'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 50}}}
        # label becomes 'button_42' -- no crash expected
        annotate(img_path, ref_cache, scale=1.0)

    def test_image_path_not_found(self, tmp_path):
        """Non-existent image path: Image.open should raise FileNotFoundError."""
        from tine.screenshot import annotate
        ref_cache = make_ref_cache(('ref_1', valid_bbox()))
        with pytest.raises((FileNotFoundError, OSError)):
            annotate(str(tmp_path / 'nonexistent.png'), ref_cache)

    def test_massive_ref_cache(self, tmp_path):
        """10,000 refs: memory/time exhaustion probe."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = {
            f'ref_{i}': {'role': 'button', 'name': 'X',
                         'bbox': {'x': i % 200, 'y': i % 200, 'w': 1, 'h': 1}}
            for i in range(10_000)
        }
        # Should complete without MemoryError or timeout (1x1 bbox gets skipped by w<=0 or h<=0)
        annotate(img_path, ref_cache, scale=1.0)

    def test_bbox_missing_key_direct_access(self, tmp_path):
        """ref_data has 'bbox' key but bbox is missing 'w'. bbox.get('w', 0) returns 0
        -> w <= 0, so skipped. No crash expected -- but verifying the skip logic."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X',
                               'bbox': {'x': 10, 'y': 10}}}
        # w and h default to 0, so the ref is skipped. No crash.
        annotate(img_path, ref_cache, scale=1.0)

    def test_scale_very_large(self, tmp_path):
        """Enormous scale factor overflows pixel coords."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = make_ref_cache(
            ('ref_1', {'x': 100, 'y': 100, 'w': 100, 'h': 100}),
        )
        # scale=1e18 -> int(100 * 1e18) = huge. PIL draws off-canvas. Does it crash?
        annotate(img_path, ref_cache, scale=1e18)

    def test_scale_string_raises(self, tmp_path):
        """scale='2.0' (string): scale <= 0 comparison raises TypeError.
        Hardening rejects non-numeric scale at the validation gate."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = make_ref_cache(('ref_1', valid_bbox()))
        with pytest.raises(TypeError):
            annotate(img_path, ref_cache, scale='2.0')

    def test_no_bbox_key_at_all(self, tmp_path):
        """ref_data has no 'bbox' key. ref_data.get('bbox', {}) returns {},
        bbox_center({}) returns None, so ref is skipped. No crash expected."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X'}}
        annotate(img_path, ref_cache, scale=1.0)

    def test_empty_ref_cache(self, tmp_path):
        """Empty ref_cache: no iterations, should return image path."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        result = annotate(img_path, {}, scale=1.0)
        assert result == img_path

    def test_ref_cache_not_dict(self, tmp_path):
        """ref_cache is a list, not a dict. .items() will fail."""
        from tine.screenshot import annotate
        img_path = str(tmp_path / 'img.png')
        make_tiny_png(img_path)
        with pytest.raises(AttributeError):
            annotate(img_path, [{'ref_1': valid_bbox()}], scale=1.0)


# ===========================================================================
# screenshot.py -- list_windows() regex and unescaper
# ===========================================================================

class TestListWindowsAdversarial:

    def _parse_stdout(self, stdout):
        """Drive the regex+unescape+json.loads pipeline from list_windows() directly."""
        import re
        match = re.search(r"\('(.*)'\s*,\s*\)", stdout, re.DOTALL)
        if match:
            json_str = match.group(1)
            unescaped = []
            i = 0
            while i < len(json_str):
                if json_str[i] == '\\' and i + 1 < len(json_str) and json_str[i + 1] in ('\\', "'"):
                    unescaped.append(json_str[i + 1])
                    i += 2
                else:
                    unescaped.append(json_str[i])
                    i += 1
            json_str = ''.join(unescaped)
        else:
            json_str = stdout
        return json.loads(json_str)

    def test_json_containing_single_quote_before_paren(self):
        """If the JSON value contains \', ) the greedy regex matches too early.

        BROKE: list_windows() regex match
        HOW: Window title containing the literal string ', )
        WHY IT HURTS: Truncates JSON, json.loads raises JSONDecodeError
        """
        # The regex r\('(.*)' ,  \) with re.DOTALL is GREEDY -- .* matches as MUCH as possible.
        # So it actually takes the LAST occurrence, not the first. This is correct.
        # But let's verify: a title with literal '), ' inside shouldn't trip the regex.
        json_data = json.dumps([{'title': "It\'s bad', )", 'x': 0, 'y': 0,
                                 'width': 800, 'height': 600}])
        # Manually simulate GVariant quoting: ' -> \'
        escaped = json_data.replace("'", "\\'")
        stdout = f"('{escaped}',  )"
        result = self._parse_stdout(stdout)
        assert result[0]['title'] == "It's bad', )"

    def test_backslash_newline_in_json(self):
        """\n in a JSON string value: unescaper passes it through as literal \\n.
        json.loads sees \\n as an escape sequence and decodes it to newline.
        This actually works -- but only because json.loads handles \\n.
        Verify no silent corruption.
        """
        json_data = json.dumps([{'title': 'Line1\nLine2', 'x': 0, 'y': 0,
                                 'width': 800, 'height': 600}])
        escaped = json_data.replace("'", "\\'")
        stdout = f"('{escaped}',  )"
        result = self._parse_stdout(stdout)
        assert result[0]['title'] == 'Line1\nLine2'

    def test_trailing_backslash_in_json_string(self):
        """JSON string ending with \\: unescaper sees \\ at i, checks i+1 which is ' (quote start).
        This depends on what follows. If \\" ends the JSON string, it becomes misparse."""
        # A window title ending in backslash
        json_data = json.dumps([{'title': 'ends_with_backslash\\', 'x': 0, 'y': 0,
                                 'width': 800, 'height': 600}])
        # After json.dumps, the title becomes 'ends_with_backslash\\\\' in JSON
        # GVariant escaping: ' -> \', \\ -> \\\\
        escaped = json_data.replace('\\', '\\\\').replace("'", "\\'")
        stdout = f"('{escaped}',  )"
        result = self._parse_stdout(stdout)
        assert result[0]['title'] == 'ends_with_backslash\\'

    def test_empty_json_array_stdout(self):
        """Valid gdbus output wrapping empty JSON array."""
        stdout = "('[]',  )"
        result = self._parse_stdout(stdout)
        assert result == []

    def test_malformed_stdout_no_match(self):
        """stdout doesn't match the regex: falls through to json.loads(stdout).
        If stdout is not JSON, raises JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            self._parse_stdout('ERROR: method not found')

    def test_stdout_empty_string(self):
        """Empty stdout: no regex match, json.loads('') -> JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            self._parse_stdout('')

    def test_deeply_nested_json_in_title(self):
        """Window title that itself looks like JSON. Regex must not get confused."""
        inner = json.dumps({'key': 'value'})
        json_data = json.dumps([{'title': inner, 'x': 0, 'y': 0,
                                 'width': 800, 'height': 600}])
        escaped = json_data.replace("'", "\\'")
        stdout = f"('{escaped}',  )"
        result = self._parse_stdout(stdout)
        assert result[0]['title'] == inner

    def test_thousand_windows(self):
        """1000 windows: regex catastrophe probe."""
        windows = [{'title': f'Window {i}', 'x': i, 'y': i,
                    'width': 800, 'height': 600} for i in range(1000)]
        json_data = json.dumps(windows)
        escaped = json_data.replace("'", "\\'")
        stdout = f"('{escaped}',  )"
        result = self._parse_stdout(stdout)
        assert len(result) == 1000

    def test_null_bytes_in_title(self):
        """Null bytes in window title. json.dumps handles them as \\u0000.
        Should parse without corruption."""
        json_data = json.dumps([{'title': 'before\x00after', 'x': 0, 'y': 0,
                                 'width': 800, 'height': 600}])
        escaped = json_data.replace("'", "\\'")
        stdout = f"('{escaped}',  )"
        result = self._parse_stdout(stdout)
        assert result[0]['title'] == 'before\x00after'


# ===========================================================================
# input.py -- move()
# ===========================================================================

class TestMoveAdversarial:

    def _move(self, x, y):
        from tine.input import move
        return move(x, y)

    def test_nan_bypasses_negative_check(self):
        """FIXED: move() now rejects NaN via int(NaN) -> ValueError.
        NaN is a float so it passes isinstance, but int(NaN) raises ValueError.
        """
        from tine.input import move
        with pytest.raises(ValueError):
            move(float('nan'), 0)

    def test_positive_inf_bypasses_check(self):
        """FIXED: move() now rejects infinity via int(inf) -> OverflowError."""
        from tine.input import move
        with pytest.raises((OverflowError, ValueError)):
            move(float('inf'), 0)

    def test_negative_nan_raises(self):
        """FIXED: move(-float('nan'), 0) now raises ValueError via int(NaN)."""
        from tine.input import move
        with pytest.raises(ValueError):
            move(-float('nan'), 0)

    def test_string_coord_raises_typeerror(self):
        """move('100', 200): '100' < 0 raises TypeError in Python 3."""
        from tine.input import move
        with pytest.raises(TypeError):
            move('100', 200)

    def test_none_coord_raises(self):
        """move(None, 0): None < 0 raises TypeError."""
        from tine.input import move
        with pytest.raises(TypeError):
            move(None, 0)

    def test_negative_zero_passes(self):
        """move(-0.0, 0): -0.0 < 0 is False in Python. Passes check."""
        from tine.input import move
        p, fake = _patch_uinput()
        with p:
            move(-0.0, 0)  # -0.0 is not < 0, passes validation
            assert fake.syn_count >= 1

    def test_float_coords_passed_as_string(self):
        """move(1.7, 2.3): float coords are truncated to int via int().
        int(1.7)=1, int(2.3)=2 sent as EV_ABS events."""
        from tine.input import move
        p, fake = _patch_uinput()
        with p:
            move(1.7, 2.3)
            assert fake.syn_count >= 1

    def test_very_large_coord(self):
        """move(2**64, 2**64): enormous but positive. Passes validation."""
        from tine.input import move
        p, fake = _patch_uinput()
        with p:
            move(2**64, 2**64)
            assert fake.syn_count >= 1


# ===========================================================================
# input.py -- type_text()
# ===========================================================================

class TestTypeTextAdversarial:

    @patch('tine.input._run_ydotool')
    def test_empty_string(self, mock_run):
        """type_text(''): empty string. Should not crash."""
        from tine.input import type_text
        type_text('')
        mock_run.assert_called_once()

    @patch('tine.input._run_ydotool')
    def test_null_byte_in_text(self, mock_run):
        """type_text('hello\x00world'): null byte in text passed to ydotool."""
        from tine.input import type_text
        type_text('hello\x00world')  # No error raised by tine
        mock_run.assert_called_once()
        # Null byte reaches ydotool -- undefined behavior
        call_args = mock_run.call_args[0][0]
        assert 'hello\x00world' in call_args

    @patch('tine.input._run_ydotool')
    def test_gigabyte_text(self, mock_run):
        """type_text('A' * 10_000_000): 10MB string. No validation, reaches subprocess."""
        from tine.input import type_text
        type_text('A' * 10_000_000)
        mock_run.assert_called_once()

    @patch('tine.input._run_ydotool')
    def test_negative_delay(self, mock_run):
        """FIXED: type_text('hi', delay_ms=-1) raises ValueError for negative delay."""
        from tine.input import type_text
        with pytest.raises(ValueError):
            type_text('hi', delay_ms=-1)

    @patch('tine.input._run_ydotool')
    def test_float_delay(self, mock_run):
        """type_text('hi', delay_ms=1.5): float delay truncated to int via int().
        int(1.5)=1 is sent to ydotool as '--key-delay 1'."""
        from tine.input import type_text
        type_text('hi', delay_ms=1.5)
        call_args = mock_run.call_args[0][0]
        assert '--key-delay' in call_args
        assert str(int(1.5)) in call_args

    @patch('tine.input._run_ydotool')
    def test_string_delay(self, mock_run):
        """FIXED: type_text('hi', delay_ms='fast') raises ValueError.
        int('fast') raises ValueError — string delays are now rejected."""
        from tine.input import type_text
        with pytest.raises(ValueError):
            type_text('hi', delay_ms='fast')

    @patch('tine.input._run_ydotool')
    def test_unicode_text(self, mock_run):
        """Unicode, emoji, RTL text passed through without issue."""
        from tine.input import type_text
        type_text('\u0645\u0631\u062d\u0628\u0627 \U0001F600 hello')
        mock_run.assert_called_once()

    @patch('tine.input._run_ydotool')
    def test_text_is_none(self, mock_run):
        """type_text(None): args.append(None) -- subprocess.run receives None as arg.
        Does subprocess raise TypeError?"""
        from tine.input import type_text
        # No explicit validation. None gets appended to the args list.
        # subprocess.run converts args to strings only if shell=True.
        # With list args, None will likely raise TypeError in subprocess.
        with pytest.raises((TypeError, AttributeError, ValueError)):
            type_text(None)

    @patch('tine.input._run_ydotool')
    def test_delay_ms_none_skipped(self, mock_run):
        """delay_ms=None: the if-branch is skipped, no --key-delay added."""
        from tine.input import type_text
        type_text('hello', delay_ms=None)
        call_args = mock_run.call_args[0][0]
        assert '--key-delay' not in call_args


# ===========================================================================
# input.py -- key_combo()
# ===========================================================================

class TestKeyComboAdversarial:

    def test_empty_string(self):
        """key_combo(''): empty parts filtered -> ValueError 'Empty key combo'."""
        from tine.input import key_combo
        with pytest.raises(ValueError, match='Empty key combo'):
            key_combo('')

    def test_trailing_plus(self):
        """key_combo('ctrl+'): empty part filtered out, only 'ctrl' pressed."""
        from tine.input import key_combo
        p, fake = _patch_uinput()
        with p:
            key_combo('ctrl+')
            assert fake.syn_count >= 1

    def test_leading_plus(self):
        """key_combo('+c'): empty part filtered out, only 'c' pressed."""
        from tine.input import key_combo
        p, fake = _patch_uinput()
        with p:
            key_combo('+c')
            assert fake.syn_count >= 1

    def test_just_plus(self):
        """key_combo('+'): all empty parts filtered -> ValueError 'Empty key combo'."""
        from tine.input import key_combo
        with pytest.raises(ValueError, match='Empty key combo'):
            key_combo('+')

    def test_unknown_key(self):
        """Completely unknown key name."""
        from tine.input import key_combo
        with pytest.raises(ValueError, match='Unknown key'):
            key_combo('ctrl+NOTAKEY')

    def test_only_modifiers(self):
        """key_combo('ctrl+shift'): both are modifiers, no regular keys.
        Modifiers are still pressed+released via evdev. Correct behavior."""
        from tine.input import key_combo
        p, fake = _patch_uinput()
        with p:
            key_combo('ctrl+shift')
            # Should have: 29 press, 42 press, 42 release, 29 release
            key_writes = [(c, v) for _, c, v in fake.events]
            assert (29, 1) in key_writes
            assert (42, 1) in key_writes
            assert (42, 0) in key_writes
            assert (29, 0) in key_writes

    def test_duplicate_key(self):
        """key_combo('a+a'): same key twice -> pressed twice, released twice."""
        from tine.input import key_combo
        p, fake = _patch_uinput()
        with p:
            key_combo('a+a')
            key_writes = [(c, v) for _, c, v in fake.events]
            # a=30, so (30, 1) appears twice, (30, 0) appears twice
            assert key_writes.count((30, 1)) == 2
            assert key_writes.count((30, 0)) == 2

    def test_massive_combo(self):
        """key_combo with 500 modifier repetitions.
        Now goes through evdev (no ARG_MAX risk), but still generates 1002 events."""
        from tine.input import key_combo
        p, fake = _patch_uinput()
        with p:
            huge_combo = '+'.join(['ctrl'] * 500 + ['c'])
            key_combo(huge_combo)
            # 500 ctrl:1 + 1 c:1 + 1 c:0 + 500 ctrl:0 = 1002 events
            assert len(fake.events) >= 1002

    def test_whitespace_in_combo(self):
        """key_combo('ctrl + c'): spaces around +, strip() handles them."""
        from tine.input import key_combo
        p, fake = _patch_uinput()
        with p:
            key_combo('ctrl + c')
            assert fake.syn_count >= 1

    def test_tab_in_combo(self):
        """key_combo('ctrl\\t+\\tc'): tabs, strip() handles them."""
        from tine.input import key_combo
        p, fake = _patch_uinput()
        with p:
            key_combo('ctrl\t+\tc')
            assert fake.syn_count >= 1

    def test_none_combo(self):
        """key_combo(None): type check raises ValueError."""
        from tine.input import key_combo
        with pytest.raises((AttributeError, ValueError)):
            key_combo(None)

    def test_integer_combo(self):
        """key_combo(42): type check raises ValueError."""
        from tine.input import key_combo
        with pytest.raises((AttributeError, ValueError)):
            key_combo(42)


# ===========================================================================
# input.py -- click_ref()
# ===========================================================================

class TestClickRefAdversarial:

    @patch('tine.input._run_ydotool')
    def test_missing_bbox_key(self, mock_run):
        """click_ref() with ref that has no 'bbox' key raises ValueError.
        The function checks 'if bbox not in ref: raise ValueError'."""
        from tine.input import click_ref
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X'}}
        with pytest.raises((KeyError, ValueError)):
            click_ref('ref_1', ref_cache)

    @patch('tine.input._run_ydotool')
    def test_bbox_is_none(self, mock_run):
        """ref['bbox'] = None: bbox_center(None) -> TypeError or returns None."""
        from tine.input import click_ref
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X', 'bbox': None}}
        with pytest.raises((TypeError, AttributeError, ValueError)):
            click_ref('ref_1', ref_cache)

    @patch('tine.input._run_ydotool')
    def test_window_offset_empty_tuple(self, mock_run):
        """BROKE: click_ref() with window_offset=()
        HOW: window_offset=() -> window_offset[0] raises IndexError
        WHY IT HURTS: No length validation on window_offset parameter.
        """
        from tine.input import click_ref
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X', 'bbox': valid_bbox()}}
        with pytest.raises(IndexError):
            click_ref('ref_1', ref_cache, window_offset=())

    @patch('tine.input._run_ydotool')
    def test_window_offset_one_element(self, mock_run):
        """window_offset=(5,): [0] works, [1] raises IndexError."""
        from tine.input import click_ref
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X', 'bbox': valid_bbox()}}
        with pytest.raises(IndexError):
            click_ref('ref_1', ref_cache, window_offset=(5,))

    def test_window_offset_list_works(self):
        """window_offset=[10, 20]: list indexing works same as tuple."""
        from tine.input import click_ref
        p, fake = _patch_uinput()
        with p:
            ref_cache = {'ref_1': {'role': 'button', 'name': 'X', 'bbox': valid_bbox()}}
            click_ref('ref_1', ref_cache, window_offset=[10, 20])
            assert fake.syn_count >= 1

    def test_unknown_ref_id(self):
        """click_ref with ref_id not in cache -> KeyError."""
        from tine.input import click_ref
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X', 'bbox': valid_bbox()}}
        with pytest.raises(KeyError, match='Unknown ref'):
            click_ref('ref_99', ref_cache)

    @patch('tine.input._run_ydotool')
    def test_invalid_button(self, mock_run):
        """click_ref with invalid button name -> ValueError from click()."""
        from tine.input import click_ref
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X', 'bbox': valid_bbox()}}
        with pytest.raises(ValueError, match='Invalid button'):
            click_ref('ref_1', ref_cache, button='hyperclick')

    @patch('tine.input._run_ydotool')
    def test_window_offset_nan(self, mock_run):
        """window_offset=(float('nan'), 0): NaN propagates to move() which rejects it.
        int(NaN) raises ValueError before ydotool is called."""
        from tine.input import click_ref
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X', 'bbox': valid_bbox()}}
        with pytest.raises(ValueError):
            click_ref('ref_1', ref_cache, window_offset=(float('nan'), 0))


# ===========================================================================
# input.py -- click()
# ===========================================================================

class TestClickAdversarial:

    def test_double_as_truthy_int(self):
        """click(x, y, double=5): truthy int triggers double-click (two press/release cycles)."""
        from tine.input import click
        from evdev import ecodes
        p, fake = _patch_uinput()
        with p:
            click(10, 10, double=5)
            key_writes = [(c, v) for _, c, v in fake.events if _ == ecodes.EV_KEY]
            presses = [(c, v) for c, v in key_writes if c == ecodes.BTN_LEFT and v == 1]
            assert len(presses) == 2

    def test_double_as_string_truthy(self):
        """click(x, y, double='yes'): truthy string triggers double-click."""
        from tine.input import click
        from evdev import ecodes
        p, fake = _patch_uinput()
        with p:
            click(10, 10, double='yes')
            key_writes = [(c, v) for _, c, v in fake.events if _ == ecodes.EV_KEY]
            presses = [(c, v) for c, v in key_writes if c == ecodes.BTN_LEFT and v == 1]
            assert len(presses) == 2

    def test_invalid_button_name(self):
        """click(10, 10, button='hyperclick'): not in BUTTON_CODES -> ValueError."""
        from tine.input import click
        with pytest.raises(ValueError, match='Invalid button'):
            click(10, 10, button='hyperclick')


# ===========================================================================
# describe.py -- format_for_agent()
# ===========================================================================

class TestFormatForAgentAdversarial:

    def _fmt(self, result):
        from tine.describe import format_for_agent
        return format_for_agent(result)

    def test_window_missing_title_key(self):
        """Window dict without 'title' key: uses .get() default '(untitled)'."""
        result = {'windows': [{'x': 0, 'y': 0, 'width': 800, 'height': 600}]}
        output = self._fmt(result)
        assert '(untitled)' in output

    def test_window_missing_width_key(self):
        """Window dict without 'width' key: uses .get() default '?'."""
        result = {'windows': [{'title': 'X', 'x': 0, 'y': 0, 'height': 600}]}
        output = self._fmt(result)
        assert 'X' in output

    def test_window_missing_height_key(self):
        """Window dict without 'height' key: uses .get() default '?'."""
        result = {'windows': [{'title': 'X', 'x': 0, 'y': 0, 'width': 800}]}
        output = self._fmt(result)
        assert 'X' in output

    def test_window_missing_x_key(self):
        """Window dict without 'x' key: uses .get() default '?'."""
        result = {'windows': [{'title': 'X', 'y': 0, 'width': 800, 'height': 600}]}
        output = self._fmt(result)
        assert 'X' in output

    def test_window_missing_y_key(self):
        """Window dict without 'y' key: uses .get() default '?'."""
        result = {'windows': [{'title': 'X', 'x': 0, 'width': 800, 'height': 600}]}
        output = self._fmt(result)
        assert 'X' in output

    def test_windows_is_none(self):
        """result['windows'] = None: result.get('windows', []) returns None.
        Then 'if windows:' is False (None is falsy). Falls to 'No windows open.' path.
        No crash.
        """
        result = {'windows': None, 'tree': '', 'screenshot': ''}
        output = self._fmt(result)  # Should not crash
        assert 'No windows open' in output

    def test_windows_is_string(self):
        """result['windows'] = 'bad': iterating over string gives chars as 'w'.
        Then w.get('title') on a char -> AttributeError.
        """
        result = {'windows': 'bad_string', 'tree': '', 'screenshot': ''}
        with pytest.raises((TypeError, KeyError, AttributeError)):
            self._fmt(result)

    def test_windows_is_dict(self):
        """result['windows'] = {}: dict is truthy, iterates over keys (strings).
        Then w.get('title') on a string key -> AttributeError.
        """
        result = {'windows': {'title': 'X'}, 'tree': '', 'screenshot': ''}
        with pytest.raises((TypeError, KeyError, AttributeError)):
            self._fmt(result)

    def test_empty_result_dict(self):
        """format_for_agent({}): all .get() calls return defaults."""
        result = self._fmt({})
        assert 'No windows open' in result
        assert 'No tree available' in result

    def test_result_is_none(self):
        """format_for_agent(None): None.get() -> AttributeError."""
        with pytest.raises(AttributeError):
            self._fmt(None)

    def test_result_is_list(self):
        """format_for_agent([]): [].get() -> AttributeError."""
        with pytest.raises(AttributeError):
            self._fmt([])

    def test_window_title_is_none(self):
        """w['title'] = None: f'{None}' in format string -> 'None'. No crash."""
        result = {'windows': [{'title': None, 'x': 0, 'y': 0,
                               'width': 800, 'height': 600}]}
        output = self._fmt(result)
        assert 'None' in output

    def test_window_coords_are_strings(self):
        """Coordinates are strings instead of numbers.
        f'{w["width"]}x{w["height"]}' works with strings via f-string."""
        result = {'windows': [{'title': 'X', 'x': 'left', 'y': 'top',
                               'width': 'wide', 'height': 'tall'}]}
        output = self._fmt(result)  # No crash, strings are formatted directly
        assert 'widextall' in output

    def test_thousand_windows(self):
        """1000 windows: no crash, output should be very long."""
        windows = [
            {'title': f'Win{i}', 'x': i, 'y': i, 'width': 800, 'height': 600}
            for i in range(1000)
        ]
        result = {'windows': windows}
        output = self._fmt(result)
        assert 'Win999' in output

    def test_window_with_xss_title(self):
        """Title with HTML/script injection: should pass through unescaped.
        If output is ever rendered as HTML, this is an XSS vector."""
        result = {'windows': [{
            'title': '<script>alert(1)</script>',
            'x': 0, 'y': 0, 'width': 800, 'height': 600
        }]}
        output = self._fmt(result)
        assert '<script>' in output  # Unescaped: XSS if ever rendered as HTML

    def test_window_focused_truthy_non_bool(self):
        """w['focused'] = 'yes': w.get('focused') is truthy. Appends ' (focused)'."""
        result = {'windows': [{
            'title': 'X', 'x': 0, 'y': 0, 'width': 800, 'height': 600,
            'focused': 'yes'
        }]}
        output = self._fmt(result)
        assert '(focused)' in output


# ===========================================================================
# Integration: NaN propagation from window_offset through move()
# ===========================================================================

class TestNaNPropagation:
    """NaN in window_offset propagates through click_ref -> click -> move -> ydotool.
    No guard catches NaN at any level."""

    @patch('tine.input._run_ydotool')
    def test_nan_traverses_full_stack(self, mock_run):
        """FIXED: NaN in window_offset is now rejected in move() via int(NaN).
        ValueError is raised before ydotool is called.
        """
        from tine.input import click_ref
        ref_cache = {'ref_1': {'role': 'button', 'name': 'X',
                               'bbox': {'x': 50, 'y': 50, 'w': 100, 'h': 40}}}
        with pytest.raises(ValueError):
            click_ref('ref_1', ref_cache, window_offset=(float('nan'), 0))


# ===========================================================================
# cli.py -- _cmd_screenshot bbox shadow offset
# ===========================================================================

class TestCmdScreenshotShadowOffset:
    """_cmd_screenshot must include shadow offset when adjusting annotation bboxes."""

    def _run_annotate(self, tmp_path, ref_cache, window_offset, shadow_env):
        """Run _cmd_screenshot --annotate and return the adjusted refs dict passed to annotate()."""
        import tine.cli
        img_path = str(tmp_path / 'screen.png')
        make_tiny_png(img_path)

        annotate_calls = []
        env_override = {'TINE_SHADOW_OFFSET': str(shadow_env)}

        with patch('tine.screenshot.capture', return_value=img_path), \
             patch('tine.refs.load_ref_cache', return_value=ref_cache), \
             patch('tine.cli._resolve_window_offset', return_value=window_offset), \
             patch('tine.screenshot.annotate',
                   side_effect=lambda p, refs, **kw: annotate_calls.append(refs)), \
             patch.dict('os.environ', env_override, clear=False):
            tine.cli.main(['screenshot', '--annotate'])

        assert annotate_calls, 'annotate() was not called'
        return annotate_calls[0]

    def test_default_shadow_25_applied(self, tmp_path):
        """Shadow default of 25px is added to window offset in annotation bbox."""
        ref_cache = {
            '_app': 'testapp',
            'ref_1': {'role': 'button', 'name': 'OK',
                      'bbox': {'x': 10, 'y': 20, 'w': 50, 'h': 30}},
        }
        adjusted = self._run_annotate(tmp_path, ref_cache, (100, 200), shadow_env=25)
        bbox = adjusted['ref_1']['bbox']
        assert bbox['x'] == 10 + 100 + 25   # element + window_x + shadow
        assert bbox['y'] == 20 + 200 + 25   # element + window_y + shadow

    def test_custom_shadow_env_var(self, tmp_path):
        """TINE_SHADOW_OFFSET env var is used instead of default 25px."""
        ref_cache = {
            '_app': 'testapp',
            'ref_1': {'role': 'button', 'name': 'X',
                      'bbox': {'x': 5, 'y': 5, 'w': 40, 'h': 20}},
        }
        adjusted = self._run_annotate(tmp_path, ref_cache, (50, 60), shadow_env=10)
        bbox = adjusted['ref_1']['bbox']
        assert bbox['x'] == 5 + 50 + 10
        assert bbox['y'] == 5 + 60 + 10

    def test_zero_shadow_no_extra_offset(self, tmp_path):
        """TINE_SHADOW_OFFSET=0 means only window offset is added, no shadow."""
        ref_cache = {
            '_app': 'testapp',
            'ref_1': {'role': 'button', 'name': 'X',
                      'bbox': {'x': 10, 'y': 20, 'w': 50, 'h': 30}},
        }
        adjusted = self._run_annotate(tmp_path, ref_cache, (100, 200), shadow_env=0)
        bbox = adjusted['ref_1']['bbox']
        assert bbox['x'] == 10 + 100
        assert bbox['y'] == 20 + 200

    def test_shadow_applied_to_all_refs(self, tmp_path):
        """Shadow offset is applied to every ref in the cache, not just the first."""
        ref_cache = {
            '_app': 'testapp',
            'ref_1': {'role': 'button', 'name': 'A',
                      'bbox': {'x': 0, 'y': 0, 'w': 10, 'h': 10}},
            'ref_2': {'role': 'button', 'name': 'B',
                      'bbox': {'x': 100, 'y': 100, 'w': 10, 'h': 10}},
        }
        adjusted = self._run_annotate(tmp_path, ref_cache, (0, 0), shadow_env=25)
        assert adjusted['ref_1']['bbox']['x'] == 0 + 0 + 25
        assert adjusted['ref_2']['bbox']['x'] == 100 + 0 + 25

    def test_original_cache_not_mutated(self, tmp_path):
        """_cmd_screenshot uses deepcopy — original cache bboxes are not modified."""
        ref_cache = {
            '_app': 'testapp',
            'ref_1': {'role': 'button', 'name': 'X',
                      'bbox': {'x': 10, 'y': 20, 'w': 50, 'h': 30}},
        }
        self._run_annotate(tmp_path, ref_cache, (100, 200), shadow_env=25)
        # Original bbox must be untouched
        assert ref_cache['ref_1']['bbox']['x'] == 10
        assert ref_cache['ref_1']['bbox']['y'] == 20


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
