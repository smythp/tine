"""Input injection via ydotool: click, type, key combos, mouse movement.

All input goes through ydotool which talks to ydotoold via /dev/uinput.
No sudo needed — ydotoold runs as a user service.
"""

import subprocess

from tine.refs import bbox_center, load_ref_cache

# Button codes for ydotool click (down + up)
BUTTON_CODES = {
    'left': '0xC0',
    'right': '0xC1',
    'middle': '0xC2',
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
    'plus': 13,  # KEY_EQUAL — plus is shift+equal but ydotool sends the keycode
}

# Keys that are modifiers (pressed first, released last)
MODIFIER_NAMES = {'ctrl', 'lctrl', 'rctrl', 'shift', 'lshift', 'rshift',
                   'alt', 'lalt', 'ralt', 'super', 'meta'}


def _run_ydotool(args):
    """Run a ydotool command."""
    cmd = ['ydotool'] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f'ydotool failed: {" ".join(cmd)}: {result.stderr.strip()}')
    return result


def move(x, y):
    """Move mouse to absolute screen position.

    Args:
        x: X pixel coordinate (non-negative).
        y: Y pixel coordinate (non-negative).

    Raises:
        ValueError: If coordinates are negative.
        RuntimeError: If ydotool fails.
    """
    x, y = int(x), int(y)
    if x < 0 or y < 0:
        raise ValueError(f'Coordinates must be non-negative: ({x}, {y})')
    _run_ydotool(['mousemove', '-a', '-x', str(x), '-y', str(y)])


def click(x, y, button='left', double=False):
    """Click at absolute screen position.

    Args:
        x: X pixel coordinate.
        y: Y pixel coordinate.
        button: 'left', 'right', or 'middle'.
        double: If True, double-click.

    Raises:
        ValueError: If button is invalid.
        RuntimeError: If ydotool fails.
    """
    if button not in BUTTON_CODES:
        raise ValueError(f'Invalid button: {button}. Must be one of: {", ".join(BUTTON_CODES)}')

    move(x, y)

    click_args = ['click']
    if double:
        click_args.extend(['--repeat', '2', '--next-delay', '50'])
    click_args.append(BUTTON_CODES[button])

    _run_ydotool(click_args)


def click_ref(ref_id, ref_cache, window_offset=(0, 0), button='left'):
    """Click at the center of a ref's bounding box.

    Args:
        ref_id: Ref ID (e.g. 'ref_1').
        ref_cache: Dict mapping ref_id -> {role, name, bbox}, or path to cache file.
        window_offset: (x, y) offset of the window on screen.
        button: 'left', 'right', or 'middle'.

    Raises:
        KeyError: If ref_id not in cache.
        ValueError: If ref has no bbox or bbox is invalid.
        RuntimeError: If ydotool fails.
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

    screen_x = window_offset[0] + center[0]
    screen_y = window_offset[1] + center[1]

    click(screen_x, screen_y, button=button)


def type_text(text, delay_ms=None):
    """Type text via ydotool.

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
    """Press a key combination.

    Parses human-readable combo strings like 'ctrl+c', 'alt+f4', 'ctrl+shift+t'.
    Modifiers are pressed first in order, then the key, then all released in reverse.

    Args:
        combo_str: Key combination string (case insensitive, '+' separated).

    Raises:
        ValueError: If any key name is unrecognized.
        RuntimeError: If ydotool fails.
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

    # Build keycode sequence: press modifiers, press keys, release keys, release modifiers (reverse)
    events = []
    for code in modifiers:
        events.append(f'{code}:1')
    for code in keys:
        events.append(f'{code}:1')
    for code in reversed(keys):
        events.append(f'{code}:0')
    for code in reversed(modifiers):
        events.append(f'{code}:0')

    _run_ydotool(['key'] + events)
