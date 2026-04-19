"""Input injection via python-evdev (uinput) for mouse, keyboard, and typing.

Input goes through two separate UInput devices:

  tine-pointer  — EV_ABS (ABS_X/Y) + BTN_LEFT/RIGHT/MIDDLE + INPUT_PROP_DIRECT
  tine-keyboard — EV_KEY with keyboard keycodes only

The split is required because libinput classifies a single device that has
both keyboard keys and an absolute pointer as cap:kp (keyboard + pointer)
and then silently drops all EV_ABS motion events — pointer devices are
expected to emit EV_REL (relative) motion. The symptom was: clicks would
register at whatever position the cursor happened to be at, never where
the caller asked, because the ABS_X/ABS_Y writes were discarded before
reaching wl_pointer. See finding-20260417-k7j5.

Both devices also use a unique vendor/product ID (0xFEED:0x7001 and
0xFEED:0x7002) rather than evdev's default 0x0001:0x0001. The default
collides with other common userspace uinput devices, which libinput groups
together and then routes pointer events through whichever device it picked
as the group's pointer aspect — often not ours.

BTN_TOUCH must NOT appear on either device: Firefox's Wayland pointer
binding ignores events from devices that advertise BTN_TOUCH because
libinput's internal dispatch routes them as wl_touch rather than wl_pointer.
See brief-20260415-6yjt and finding-20260414-0y45.

Devices are created lazily on first use and cached for the process lifetime.
Creation blocks ~2s for the compositor to recognize them (both warm up in
parallel to amortize the delay).
"""

import time

import evdev
from evdev import UInput, ecodes, AbsInfo

from tine.display import get_display_config
from tine.refs import bbox_center, load_ref_cache

# Absolute axis range — uinput maps [0, ABS_MAX] to the full screen
ABS_MAX = 65535

# Unique vendor/product IDs so libinput does not group tine's devices
# with other virtual uinput devices that default to 0x0001:0x0001.
# The IDs are arbitrary but kept stable so users and tests can identify
# them. 0xFEED is obviously not a real USB vendor, which is the point.
POINTER_VENDOR = 0xFEED
POINTER_PRODUCT = 0x7001
KEYBOARD_VENDOR = 0xFEED
KEYBOARD_PRODUCT = 0x7002

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

# Characters that require shift. Maps the shifted character to the base keycode name.
SHIFT_CHARS = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
    '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': 'minus', '+': 'equal',
    '{': 'leftbrace', '}': 'rightbrace',
    ':': 'semicolon', '"': 'apostrophe',
    '~': 'grave', '|': 'backslash',
    '<': 'comma', '>': 'dot', '?': 'slash',
}

# Map printable characters to (keycode, needs_shift).
# Built from KEYCODES + SHIFT_CHARS so the mapping stays consistent.
CHAR_MAP = {}

# Lowercase letters: a-z → keycode, no shift
for _c in 'abcdefghijklmnopqrstuvwxyz':
    CHAR_MAP[_c] = (KEYCODES[_c], False)

# Uppercase letters: A-Z → same keycode as lowercase, with shift
for _c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    CHAR_MAP[_c] = (KEYCODES[_c.lower()], True)

# Digits: 0-9 → keycode, no shift
for _c in '0123456789':
    CHAR_MAP[_c] = (KEYCODES[_c], False)

# Simple punctuation (unshifted keys)
CHAR_MAP['-'] = (KEYCODES['minus'], False)
CHAR_MAP['='] = (KEYCODES['equal'], False)
CHAR_MAP['['] = (KEYCODES['leftbrace'], False)
CHAR_MAP[']'] = (KEYCODES['rightbrace'], False)
CHAR_MAP[';'] = (KEYCODES['semicolon'], False)
CHAR_MAP["'"] = (KEYCODES['apostrophe'], False)
CHAR_MAP['`'] = (KEYCODES['grave'], False)
CHAR_MAP['\\'] = (KEYCODES['backslash'], False)
CHAR_MAP[','] = (KEYCODES['comma'], False)
CHAR_MAP['.'] = (KEYCODES['dot'], False)
CHAR_MAP['/'] = (KEYCODES['slash'], False)
CHAR_MAP[' '] = (KEYCODES['space'], False)
CHAR_MAP['\t'] = (KEYCODES['tab'], False)
CHAR_MAP['\n'] = (KEYCODES['enter'], False)

# Shifted punctuation/symbols
for _shifted_char, _base_name in SHIFT_CHARS.items():
    CHAR_MAP[_shifted_char] = (KEYCODES[_base_name], True)

# Keys that are modifiers (pressed first, released last)
MODIFIER_NAMES = {'ctrl', 'lctrl', 'rctrl', 'shift', 'lshift', 'rshift',
                   'alt', 'lalt', 'ralt', 'super', 'meta'}

# Aliases for X11/XKB-style keysym names that don't match canonical
# KEYCODES keys after simple normalization. Every agent has Page_Down,
# BackSpace, Control_L memorized from XKB; a wrong guess should not blow
# up a chained command. Keys are post-normalization (lowercase,
# underscores/spaces/dashes stripped). Values are canonical KEYCODES keys.
KEY_ALIASES = {
    # L/R modifiers — XKB uses Shift_L/R, we use lshift/rshift.
    # Both orderings (prefix and suffix) accepted so Left_Shift and
    # Shift_L normalize the same way.
    'leftshift': 'lshift', 'shiftleft': 'lshift', 'shiftl': 'lshift',
    'rightshift': 'rshift', 'shiftright': 'rshift', 'shiftr': 'rshift',
    'leftcontrol': 'lctrl', 'controlleft': 'lctrl',
    'leftctrl': 'lctrl', 'ctrlleft': 'lctrl',
    'controll': 'lctrl', 'ctrll': 'lctrl',
    'rightcontrol': 'rctrl', 'controlright': 'rctrl',
    'rightctrl': 'rctrl', 'ctrlright': 'rctrl',
    'controlr': 'rctrl', 'ctrlr': 'rctrl',
    'leftalt': 'lalt', 'altleft': 'lalt', 'altl': 'lalt',
    'rightalt': 'ralt', 'altright': 'ralt', 'altr': 'ralt',
    # Bare XKB keysym names that don't match our short forms
    'control': 'ctrl',
    # Common abbreviations
    'del': 'delete',
    'ins': 'insert',
}


def _normalize_key_name(name):
    """Normalize a key name for lookup in KEYCODES.

    Accepts X11/XKB keysym forms (Page_Down, BackSpace, Control_L, Caps_Lock)
    and our canonical short forms (pagedown, backspace, lctrl, capslock).
    Lowercases, strips underscores/spaces/dashes, then consults KEY_ALIASES
    for semantic renames where the normalized form still doesn't match a
    KEYCODES key. The caller does the final KEYCODES lookup and emits a
    clear error on genuinely unknown keys.

    Args:
        name: Raw key name as provided by the user.

    Returns:
        Normalized name suitable for direct KEYCODES lookup.
    """
    stripped = name.strip().lower()
    for char in '_- ':
        stripped = stripped.replace(char, '')
    return KEY_ALIASES.get(stripped, stripped)

# Cached UInput devices — created lazily on first use.
# Split into pointer (ABS + mouse buttons) and keyboard (keys) devices so
# libinput classifies each cleanly. See module docstring.
_pointer_device = None
_keyboard_device = None

# Whether to try the daemon for input commands. Set to False to skip daemon.
_use_daemon = True


def _try_daemon(cmd):
    """Try sending a command to the input daemon.

    Returns the response dict if successful, None if daemon unavailable.
    Attempts auto-start on first failure.
    """
    global _use_daemon
    if not _use_daemon:
        return None
    from tine.inputd import send_command, start_daemon
    try:
        resp = send_command(cmd)
        if resp.get('ok'):
            return resp
        # Daemon responded but command failed — return error, don't fall back
        return resp
    except (ConnectionError, OSError):
        # Try auto-starting the daemon
        if start_daemon():
            try:
                return send_command(cmd)
            except (ConnectionError, OSError):
                pass
        # Daemon failed to start — stop retrying for this process
        _use_daemon = False
        return None


def _pointer_capabilities():
    """Capability dict for the pointer UInput device.

    Pure absolute pointer: ABS_X/ABS_Y + BTN_LEFT/RIGHT/MIDDLE. No keyboard
    keys — combining them on one device trips libinput's cap:kp classification
    and EV_ABS motion is silently dropped.
    """
    return {
        ecodes.EV_ABS: [
            (ecodes.ABS_X, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
            (ecodes.ABS_Y, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
        ],
        ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
    }


def _keyboard_capabilities():
    """Capability dict for the keyboard UInput device.

    Pure keyboard: only EV_KEY with the keycodes tine types. No BTN_* keys,
    no axes, no INPUT_PROP_DIRECT.
    """
    return {
        ecodes.EV_KEY: [code for code in KEYCODES.values()],
    }


def _ensure_devices():
    """Create both uinput devices if not yet cached.

    Both devices are created before the warm-up sleep so the 2s wait
    covers both in parallel instead of being paid twice.
    """
    global _pointer_device, _keyboard_device
    if _pointer_device is not None and _keyboard_device is not None:
        return

    if _pointer_device is None:
        _pointer_device = UInput(
            _pointer_capabilities(), name='tine-pointer',
            vendor=POINTER_VENDOR, product=POINTER_PRODUCT, version=1,
            input_props=[ecodes.INPUT_PROP_DIRECT],
        )
    if _keyboard_device is None:
        _keyboard_device = UInput(
            _keyboard_capabilities(), name='tine-keyboard',
            vendor=KEYBOARD_VENDOR, product=KEYBOARD_PRODUCT, version=1,
        )

    # Give the compositor time to recognize the new devices.
    # Mutter on GNOME 49 Wayland needs ~2s to register a new uinput device
    # via libinput. Shorter delays are unreliable — events silently go nowhere.
    time.sleep(2.0)


def _get_pointer_uinput():
    """Return the cached pointer UInput device, creating it on first use."""
    _ensure_devices()
    return _pointer_device


def _get_keyboard_uinput():
    """Return the cached keyboard UInput device, creating it on first use."""
    _ensure_devices()
    return _keyboard_device


def _physical_to_abs(phys_x, phys_y):
    """Convert physical screen coordinates to absolute uinput values.

    The uinput absolute axis maps [0, ABS_MAX] to the full physical display.
    """
    config = get_display_config()
    abs_x = int(phys_x * ABS_MAX / config['physical_width'])
    abs_y = int(phys_y * ABS_MAX / config['physical_height'])
    return abs_x, abs_y



def move(x, y):
    """Move mouse to absolute screen position (physical pixel coordinates).

    Tries the persistent input daemon first for zero-warmup operation.
    Falls back to direct UInput if daemon is unavailable.

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

    resp = _try_daemon({'action': 'move', 'x': x, 'y': y})
    if resp is not None:
        if not resp.get('ok'):
            raise RuntimeError(resp.get('error', 'daemon move failed'))
        return

    abs_x, abs_y = _physical_to_abs(x, y)
    ui = _get_pointer_uinput()
    ui.write(ecodes.EV_ABS, ecodes.ABS_X, abs_x)
    ui.write(ecodes.EV_ABS, ecodes.ABS_Y, abs_y)
    ui.syn()


def click(x, y, button='left', double=False):
    """Click at absolute screen position (physical pixel coordinates).

    Tries the persistent input daemon first for zero-warmup operation.
    Falls back to direct UInput if daemon is unavailable.

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

    resp = _try_daemon({'action': 'click', 'x': int(x), 'y': int(y),
                        'button': button, 'double': double})
    if resp is not None:
        if not resp.get('ok'):
            raise RuntimeError(resp.get('error', 'daemon click failed'))
        return

    move(x, y)
    # The compositor needs time to process the ABS position update before
    # the button click, otherwise the click registers at the old cursor
    # position. 100ms is enough for Mutter on GNOME 49 Wayland.
    time.sleep(0.1)

    ui = _get_pointer_uinput()
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

    Coordinate pipeline depends on bbox['coord']:
      'screen': bbox is already in screen-logical coords (e.g. OCR refs ref_tN)
        physical = center * scale_factor
      'window': bbox is relative to window frame_rect (AT-SPI refs for every
        toolkit on Wayland — GTK4 natively, Firefox via the walker's
        frame-origin subtraction; see finding-20260418-3ff1)
        physical = (window_offset + center) * scale_factor

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

    bbox = ref['bbox']
    coord_space = bbox.get('coord', 'window')

    if coord_space == 'screen':
        # Already in screen-logical coords — no offset needed
        screen_logical_x = center[0]
        screen_logical_y = center[1]
    else:
        # Window-relative — add frame_rect position
        screen_logical_x = window_offset[0] + center[0]
        screen_logical_y = window_offset[1] + center[1]

    # Physical screen coordinates (after scaling)
    scale = get_display_config()['scale']
    screen_physical_x = screen_logical_x * scale
    screen_physical_y = screen_logical_y * scale

    click(int(screen_physical_x), int(screen_physical_y), button=button, double=double)


def type_text(text, delay_ms=None):
    """Type text via evdev key events through the tine-keyboard device.

    Tries the persistent input daemon first for zero-warmup operation.
    Falls back to direct UInput if daemon is unavailable.

    Args:
        text: String to type.
        delay_ms: Optional delay between keystrokes in milliseconds.
            Defaults to 2ms if not specified.

    Raises:
        ValueError: If text is not a string, delay is negative, or a
            character has no keycode mapping.
    """
    if not isinstance(text, str):
        raise ValueError(f'text must be a string, got {type(text).__name__}')
    if delay_ms is not None:
        delay_ms = int(delay_ms)
        if delay_ms < 0:
            raise ValueError(f'delay_ms must be non-negative, got {delay_ms}')
    else:
        delay_ms = 2

    resp = _try_daemon({'action': 'type', 'text': text, 'delay_ms': delay_ms})
    if resp is not None:
        if not resp.get('ok'):
            error = resp.get('error', 'daemon type failed')
            if 'No keycode mapping' in error:
                raise ValueError(error)
            raise RuntimeError(error)
        return

    delay_s = delay_ms / 1000.0
    ui = _get_keyboard_uinput()
    shift_code = KEYCODES['shift']

    for char in text:
        if char not in CHAR_MAP:
            raise ValueError(f'No keycode mapping for character: {char!r}')

        keycode, needs_shift = CHAR_MAP[char]

        if needs_shift:
            ui.write(ecodes.EV_KEY, shift_code, 1)
            ui.syn()

        ui.write(ecodes.EV_KEY, keycode, 1)
        ui.syn()
        ui.write(ecodes.EV_KEY, keycode, 0)
        ui.syn()

        if needs_shift:
            ui.write(ecodes.EV_KEY, shift_code, 0)
            ui.syn()

        if delay_s > 0:
            time.sleep(delay_s)


def key_combo(combo_str):
    """Press a key combination via evdev.

    Tries the persistent input daemon first for zero-warmup operation.
    Falls back to direct UInput if daemon is unavailable.

    Parses human-readable combo strings like 'ctrl+c', 'alt+f4', 'ctrl+shift+t'.
    Modifiers are pressed first in order, then the key, then all released in reverse.

    Args:
        combo_str: Key combination string (case insensitive, '+' separated).

    Raises:
        ValueError: If any key name is unrecognized.
    """
    if not isinstance(combo_str, str):
        raise ValueError(f'combo_str must be a string, got {type(combo_str).__name__}')

    parts = [_normalize_key_name(p) for p in combo_str.split('+')]
    parts = [p for p in parts if p]  # filter empty segments from 'ctrl+' or '+'

    if not parts:
        raise ValueError('Empty key combo')

    # Validate keys before trying daemon — fail fast on unknown keys
    for part in parts:
        if part not in KEYCODES:
            raise ValueError(f'Unknown key: {part}. Known keys: {", ".join(sorted(KEYCODES))}')

    # Send the already-normalized combo so the daemon receives canonical
    # keycode names regardless of which X11/XKB alias the caller used.
    resp = _try_daemon({'action': 'key', 'combo': '+'.join(parts)})
    if resp is not None:
        if not resp.get('ok'):
            error = resp.get('error', 'daemon key failed')
            if 'Unknown key' in error:
                raise ValueError(error)
            raise RuntimeError(error)
        return

    # Separate modifiers and regular keys
    modifiers = []
    keys = []
    for part in parts:
        if part in MODIFIER_NAMES:
            modifiers.append(KEYCODES[part])
        else:
            keys.append(KEYCODES[part])

    ui = _get_keyboard_uinput()

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
