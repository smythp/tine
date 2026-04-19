"""Display configuration detection via env vars or Mutter D-Bus API.

get_display_config() returns the display's scale factor, physical width, and
physical height. It checks env vars first (TINE_SCALE, TINE_PHYSICAL_WIDTH,
TINE_PHYSICAL_HEIGHT), then queries org.gnome.Mutter.DisplayConfig via gdbus.

If both sources fail, the function raises RuntimeError rather than returning
a silent default. Anything that computes click coordinates from the config
(grid cells, ref bbox scaling, uinput ABS mapping) would otherwise produce
real-but-subtly-wrong pixel values when the default doesn't match the actual
display — a failure mode too easy for an automated agent to miss.

Environments that deliberately want a hardcoded fallback (headless tests,
exotic configs where neither env vars nor Mutter are available) can opt in
via TINE_DISPLAY_FALLBACK=WxH.

Results are cached — display config doesn't change mid-session.
"""

import os
import re
import subprocess


_cached_config = None


def get_display_config():
    """Return display configuration as a dict.

    Keys: 'scale' (float), 'physical_width' (int), 'physical_height' (int).

    Resolution order:
    1. TINE_SCALE / TINE_PHYSICAL_WIDTH / TINE_PHYSICAL_HEIGHT env vars
    2. Mutter DisplayConfig D-Bus API (GetCurrentState)
    3. TINE_DISPLAY_FALLBACK=WxH env var (opt-in hardcoded fallback)

    Raises:
        RuntimeError: If none of the three sources produces a valid config.
            The message names every source tried so misconfigured systems
            are easy to diagnose from a single stderr line.
        ValueError: If TINE_DISPLAY_FALLBACK is set but malformed.
    """
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    config = _from_env()
    if config is not None:
        _cached_config = config
        return _cached_config

    config = _from_mutter_dbus()
    if config is not None:
        _cached_config = config
        return _cached_config

    config = _from_fallback_env()
    if config is not None:
        _cached_config = config
        return _cached_config

    raise RuntimeError(
        'Display config unavailable. '
        'Set TINE_SCALE + TINE_PHYSICAL_WIDTH + TINE_PHYSICAL_HEIGHT, '
        'ensure Mutter DisplayConfig D-Bus is reachable (gdbus in PATH, '
        'GNOME Shell session), or set TINE_DISPLAY_FALLBACK=WxH to opt '
        'into a hardcoded fallback.'
    )


def _from_env():
    """Read display config from env vars. Returns dict or None if all are missing."""
    scale = os.environ.get('TINE_SCALE')
    width = os.environ.get('TINE_PHYSICAL_WIDTH')
    height = os.environ.get('TINE_PHYSICAL_HEIGHT')

    if scale is None and width is None and height is None:
        return None

    # If any are set, use them all (with defaults for missing ones)
    return {
        'scale': float(scale) if scale is not None else 1.0,
        'physical_width': int(width) if width is not None else 1920,
        'physical_height': int(height) if height is not None else 1080,
    }


def _from_fallback_env():
    """Read opt-in hardcoded fallback from TINE_DISPLAY_FALLBACK.

    Format: WxH (e.g. '1920x1080'). Scale is always 1.0 in fallback mode;
    environments that need a non-1.0 scale should use the full env-var trio
    (TINE_SCALE + TINE_PHYSICAL_WIDTH + TINE_PHYSICAL_HEIGHT) instead.

    Returns:
        Config dict if the env var is set and well-formed, None if unset.

    Raises:
        ValueError: If the env var is set but malformed. Preferred over
            silently falling through so the user sees exactly what's wrong.
    """
    raw = os.environ.get('TINE_DISPLAY_FALLBACK')
    if raw is None:
        return None

    m = re.fullmatch(r'(\d+)x(\d+)', raw.strip())
    if m is None:
        raise ValueError(
            f'TINE_DISPLAY_FALLBACK={raw!r} is malformed. '
            f"Expected 'WxH', e.g. '1920x1080'."
        )

    return {
        'scale': 1.0,
        'physical_width': int(m.group(1)),
        'physical_height': int(m.group(2)),
    }


def _from_mutter_dbus():
    """Query Mutter DisplayConfig via gdbus. Returns dict or None on failure."""
    try:
        result = subprocess.run(
            [
                'gdbus', 'call', '--session',
                '--dest', 'org.gnome.Mutter.DisplayConfig',
                '--object-path', '/org/gnome/Mutter/DisplayConfig',
                '--method', 'org.gnome.Mutter.DisplayConfig.GetCurrentState',
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    return _parse_mutter_output(result.stdout)


def _parse_mutter_output(stdout):
    """Parse GetCurrentState output to extract primary monitor config.

    GetCurrentState returns a complex GVariant tuple. The monitors array
    (index 1) contains entries like:
        ((connector, vendor, product, serial),
         [((width, height, refresh, scale, ...), [...props])],
         {...properties})

    The logical monitors array (index 2) contains entries like:
        (x, y, scale, transform, primary, [connector_specs], {...properties})

    We find the primary logical monitor to get the scale, then find
    its connector in the monitors array to get the physical resolution.
    """
    if not stdout or not stdout.strip():
        return None

    try:
        return _extract_from_gvariant(stdout)
    except (ValueError, IndexError):
        return None


def _extract_from_gvariant(text):
    """Extract scale and resolution from gdbus GVariant text output.

    The output is a nested tuple. We look for the logical monitors section
    to find the primary monitor's scale and associated connector, then look
    up that connector's mode in the monitors section for resolution.

    This parser uses string scanning rather than full GVariant parsing —
    it looks for the structural patterns in the text output.
    """
    # Find logical monitors — they contain (x, y, scale, transform, primary, ...)
    # Primary monitor has 'true' for the primary field.
    # Strategy: find all scale values from logical monitors, prefer primary.

    # Look for patterns like: (0, 0, 1.25, 0, true, ...  or  (0, 0, 2.0, 0, true, ...
    # The logical monitors section has tuples starting with (int, int, double, uint, bool, ...)
    # gdbus may include type annotations like 'uint32 0' for the transform field
    logical_monitor_pattern = re.compile(
        r'\((\d+),\s*(\d+),\s*([0-9.]+),\s*(?:uint32\s+)?\d+,\s*(true|false),'
    )

    primary_scale = None
    primary_match_end = None
    any_scale = None

    for m in logical_monitor_pattern.finditer(text):
        scale = float(m.group(3))
        is_primary = m.group(4) == 'true'
        if any_scale is None:
            any_scale = scale
        if is_primary:
            primary_scale = scale
            primary_match_end = m.end()
            break

    scale = primary_scale if primary_scale is not None else any_scale
    if scale is None:
        return None

    # Extract resolution from the primary logical monitor's connector spec.
    # The logical monitor entry contains [('connector', 'WxH@refresh')], e.g.
    #   [('eDP-1', '1920x1080@60.052')]
    # Parse width and height from that mode string rather than scanning all
    # monitor modes (which would grab whichever monitor appears first).
    width = None
    height = None
    mode_string_pattern = re.compile(r"'(\d{3,5})x(\d{3,5})@[0-9.]+'")

    if primary_match_end is not None:
        # Search the text after the primary logical monitor match
        remaining = text[primary_match_end:]
        mode_match = mode_string_pattern.search(remaining)
        if mode_match:
            w, h = int(mode_match.group(1)), int(mode_match.group(2))
            if 640 <= w <= 7680 and 480 <= h <= 4320:
                width, height = w, h

    if width is None or height is None:
        # Fallback: scan all mode strings in the full text
        for m in mode_string_pattern.finditer(text):
            w, h = int(m.group(1)), int(m.group(2))
            if 640 <= w <= 7680 and 480 <= h <= 4320:
                width, height = w, h
                break

    if width is None or height is None:
        return {'scale': scale, 'physical_width': 1920, 'physical_height': 1080}

    return {'scale': scale, 'physical_width': width, 'physical_height': height}
