"""Input injection via python-evdev (uinput) for mouse and ydotool for typing.

Mouse movement and clicking use direct EV_ABS writes to /dev/uinput via evdev.
Text typing still uses ydotool (reliable for text, only mouse positioning was broken).
Key combos use evdev key events directly.

The UInput device is created lazily on first use and cached for the process lifetime.
Device creation takes ~0.5s for the compositor to recognize it.
"""

import os
import subprocess
import time

import evdev
from evdev import UInput, ecodes, AbsInfo

from tine.refs import bbox_center, load_ref_cache

# Absolute axis range — uinput maps [0, ABS_MAX] to the full screen
ABS_MAX = 65535

# Display configuration from environment.
# archgnome: TINE_PHYSICAL_WIDTH=1920 TINE_PHYSICAL_HEIGHT=1080 TINE_SCALE=1.25
PHYSICAL_WIDTH = int(os.environ.get('TINE_PHYSICAL_WIDTH', '1920'))
PHYSICAL_HEIGHT = int(os.environ.get('TINE_PHYSICAL_HEIGHT', '1080'))
SCALE_FACTOR = float(os.environ.get('TINE_SCALE', '1.0'))
SHADOW_OFFSET = int(os.environ.get('TINE_SHADOW_OFFSET', '25'))

# Button mapping for evdev
BUTTON_CODES = {
    'left': ecodes.BTN_LEFT,
    'right': ecodes.BTN_RIGHT,
    'middle': ecodes.BTN_MIDDLE,
}

# Linux keycodes from input-event-codes.h
KEYCODES = {
    # Letters
    'a': 30, 'b': 48, 'c': 46, 'd': 32, 'e': 18, 'f': 33,
    'g': 34, 'h': 35, 'i': 23, 'j': 36, 'k': 37, 'l': 38,
    'm': 50, 'n': 49, 'o': 24, 'p': 25, 'q': 16, 'r': 19,
    's': 31, 't': 20, 'u': 22, 'v': 47, 'w': 17, 'x': 45,
    'y': 21, 'z': 44,
    # Numbers
    '1': 2, '2': 3, '3': 4, '4': 5, '5': 6,
    '6': 7, '7': 8, '8': 9, '9': 10, '0': 11,
    # Modifiers
    'ctrl': 29, 'lctrl': 29, 'rctrl': 97,
    'shift': 42, 'lshift': 42, 'rshift': 54,
    'alt': 56, 'lalt': 56, 'ralt': 100,
    'super': 125, 'meta': 125,
    # Special
    'esc': 1, 'escape': 1,
    'tab': 15,
    'enter': 28, 'return': 28,
    'backspace': 14,
    'delete': 111,
    'insert': 110,
    'home': 102,
    'end': 107,
    'pageup': 104, 'pgup': 104,
    'pagedown': 109, 'pgdn': 109,
    'space': 57,
    'capslock': 58,
    # Arrow keys
    'up': 103, 'down': 108, 'left': 105, 'right': 106,
    # Function keys
    'f1': 59, 'f2': 60, 'f3': 61, 'f4': 62,
    'f5': 63, 'f6': 64, 'f7': 65, 'f8': 66,
    'f9': 67, 'f10': 68, 'f11': 87, 'f12': 88,
    # Punctuation
    'minus': 12, 'equal': 13,
    'leftbrace': 26, 'rightbrace': 27,
    'semicolon': 39, 'apostrophe': 40,
    'grave': 41, 'backslash': 43,
    'comma': 51, 'dot': 52, 'slash': 53,
    'plus': 13,  # KEY_EQUAL — plus is shift+equal but we send the keycode
}

# Keys that are modifiers (pressed first, released last)
MODIFIER_NAMES = {'ctrl', 'lctrl', 'rctrl', 'shift', 'lshift', 'rshift',
                   'alt', 'lalt', 'ralt', 'super', 'meta'}

# Cached UInput device — created lazily on first use
_uinput_device = None


def _get_uinput():
    """Get or create the cached UInput device.

    The device is created once and reused. Creation takes ~0.5s for the
    compositor to recognize the new input device.
    """
    global _uinput_device
    if _uinput_device is not None:
        return _uinput_device

    capabilities = {
        ecodes.EV_ABS: [
            (ecodes.ABS_X, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
            (ecodes.ABS_Y, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
        ],
        ecodes.EV_KEY: [
            ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE,
        ] + [code for code in KEYCODES.values()],
    }
    _uinput_device = UInput(capabilities, name='tine-input')
    # Give compositor time to recognize the new device
    time.sleep(0.3)
    return _uinput_device


def _physical_to_abs(phys_x, phys_y):
    """Convert physical screen coordinates to absolute uinput values.

    The uinput absolute axis maps [0, ABS_MAX] to the full physical display.
    """
    abs_x = int(phys_x * ABS_MAX / PHYSICAL_WIDTH)
    abs_y = int(phys_y * ABS_MAX / PHYSICAL_HEIGHT)
    return abs_x, abs_y


def _run_ydotool(args):
    """Run a ydotool command (used only for text typing)."""
    cmd = ['ydotool'] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f'ydotool failed: {" ".join(cmd)}: {result.stderr.strip()}')
    return result


def move(x, y):
    """Move mouse to absolute screen position (physical pixel coordinates).

    Args:
        x: X pixel coordinate (non-negative).
        y: Y pixel coordinate (non-negative).

    Raises:
        ValueError: If coordinates are negative.
        TypeError: If coordinates are not numeric.
    """
    if not isinstance(x, (int, float)):
        raise TypeError(f'x must be numeric, got {type(x).__name__}')
    if not isinstance(y, (int, float)):
        raise TypeError(f'y must be numeric, got {type(y).__name__}')
    x, y = int(x), int(y)
    if x < 0 or y < 0:
        raise ValueError(f'Coordinates must be non-negative: ({x}, {y})')

    abs_x, abs_y = _physical_to_abs(x, y)
    ui = _get_uinput()
    ui.write(ecodes.EV_ABS, ecodes.ABS_X, abs_x)
    ui.write(ecodes.EV_ABS, ecodes.ABS_Y, abs_y)
    ui.syn()


def click(x, y, button='left', double=False):
    """Click at absolute screen position (physical pixel coordinates).

    Args:
        x: X pixel coordinate.
        y: Y pixel coordinate.
        button: 'left', 'right', or 'middle'.
        double: If True, double-click.

    Raises:
        ValueError: If button is invalid.
    """
    if button not in BUTTON_CODES:
        raise ValueError(f'Invalid button: {button}. Must be one of: {", ".join(BUTTON_CODES)}')

    move(x, y)

    ui = _get_uinput()
    btn_code = BUTTON_CODES[button]

    # Press and release
    ui.write(ecodes.EV_KEY, btn_code, 1)
    ui.syn()
    ui.write(ecodes.EV_KEY, btn_code, 0)
    ui.syn()

    if double:
        time.sleep(0.05)  # 50ms between clicks
        ui.write(ecodes.EV_KEY, btn_code, 1)
        ui.syn()
        ui.write(ecodes.EV_KEY, btn_code, 0)
        ui.syn()


def click_ref(ref_id, ref_cache, window_offset=(0, 0), button='left', double=False):
    """Click at the center of a ref's bounding box.

    Applies the full coordinate pipeline:
      logical = window_pos + shadow_offset + element_window_coords
      physical = logical * scale_factor
      abs_value = physical * 65535 / physical_display_dimension

    Args:
        ref_id: Ref ID (e.g. 'ref_1').
        ref_cache: Dict mapping ref_id -> {role, name, bbox}, or path to cache file.
        window_offset: (x, y) position of the window on screen (from list_windows()).
        button: 'left', 'right', or 'middle'.
        double: If True, double-click.

    Raises:
        KeyError: If ref_id not in cache.
        ValueError: If ref has no bbox or bbox is invalid.
    """
    if isinstance(ref_cache, str):
        ref_cache = load_ref_cache(ref_cache)

    if ref_id not in ref_cache:
        raise KeyError(f'Unknown ref: {ref_id}')

    ref = ref_cache[ref_id]
    if 'bbox' not in ref:
        raise ValueError(f'Ref {ref_id} has no bbox')
    center = bbox_center(ref['bbox'])
    if center is None:
        raise ValueError(f'Ref {ref_id} has invalid bbox: {ref["bbox"]}')

    # Element coordinates are relative to the window content area.
    # Window offset is the surface position (includes CSD shadow).
    # Shadow offset accounts for the CSD shadow margin.
    shadow = SHADOW_OFFSET

    # Logical screen coordinates (before scaling)
    screen_logical_x = window_offset[0] + shadow + center[0]
    screen_logical_y = window_offset[1] + shadow + center[1]

    # Physical screen coordinates (after scaling)
    screen_physical_x = screen_logical_x * SCALE_FACTOR
    screen_physical_y = screen_logical_y * SCALE_FACTOR

    click(int(screen_physical_x), int(screen_physical_y), button=button, double=double)


def type_text(text, delay_ms=None):
    """Type text via ydotool.

    ydotool type is reliable for text input — only mouse positioning was broken.

    Args:
        text: String to type.
        delay_ms: Optional delay between keystrokes in milliseconds.

    Raises:
        RuntimeError: If ydotool fails.
    """
    if not isinstance(text, str):
        raise ValueError(f'text must be a string, got {type(text).__name__}')
    args = ['type']
    if delay_ms is not None:
        delay_ms = int(delay_ms)
        if delay_ms < 0:
            raise ValueError(f'delay_ms must be non-negative, got {delay_ms}')
        args.extend(['--key-delay', str(delay_ms)])
    args.append('--')
    args.append(text)
    _run_ydotool(args)


def key_combo(combo_str):
    """Press a key combination via evdev.

    Parses human-readable combo strings like 'ctrl+c', 'alt+f4', 'ctrl+shift+t'.
    Modifiers are pressed first in order, then the key, then all released in reverse.

    Args:
        combo_str: Key combination string (case insensitive, '+' separated).

    Raises:
        ValueError: If any key name is unrecognized.
    """
    if not isinstance(combo_str, str):
        raise ValueError(f'combo_str must be a string, got {type(combo_str).__name__}')

    parts = [p.strip().lower() for p in combo_str.split('+')]
    parts = [p for p in parts if p]  # filter empty segments from 'ctrl+' or '+'

    if not parts:
        raise ValueError('Empty key combo')

    # Separate modifiers and regular keys
    modifiers = []
    keys = []
    for part in parts:
        if part not in KEYCODES:
            raise ValueError(f'Unknown key: {part}. Known keys: {", ".join(sorted(KEYCODES))}')
        if part in MODIFIER_NAMES:
            modifiers.append(KEYCODES[part])
        else:
            keys.append(KEYCODES[part])

    ui = _get_uinput()

    # Press modifiers, press keys, release keys (reverse), release modifiers (reverse)
    for code in modifiers:
        ui.write(ecodes.EV_KEY, code, 1)
    for code in keys:
        ui.write(ecodes.EV_KEY, code, 1)
    for code in reversed(keys):
        ui.write(ecodes.EV_KEY, code, 0)
    for code in reversed(modifiers):
        ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()
