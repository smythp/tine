"""Display configuration detection via env vars or Mutter D-Bus API.

get_display_config() returns the display's scale factor, physical width, and
physical height. It checks env vars first (TINE_SCALE, TINE_PHYSICAL_WIDTH,
TINE_PHYSICAL_HEIGHT), then queries org.gnome.Mutter.DisplayConfig via gdbus,
and falls back to defaults (1920x1080, scale 1.0) if neither source works.

Results are cached — display config doesn't change mid-session.
"""

import os
import subprocess
import sys


_cached_config = None


def get_display_config():
    """Return display configuration as a dict.

    Keys: 'scale' (float), 'physical_width' (int), 'physical_height' (int).

    Resolution order:
    1. TINE_SCALE / TINE_PHYSICAL_WIDTH / TINE_PHYSICAL_HEIGHT env vars
    2. Mutter DisplayConfig D-Bus API (GetCurrentState)
    3. Defaults: 1920x1080, scale 1.0 (with stderr warning)
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

    print('Warning: display config not available from env vars or Mutter D-Bus — '
          'using defaults (1920x1080, scale 1.0)', file=sys.stderr)
    _cached_config = {'scale': 1.0, 'physical_width': 1920, 'physical_height': 1080}
    return _cached_config


def _from_env():
    """Read display config from env vars. Returns dict or None if any are missing."""
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
    import re

    # Find logical monitor entries: (x, y, scale, transform, primary, ...)
    # primary is 'true' or 'false' in gdbus text output
    # gdbus may include type annotations like 'uint32 0' for the transform field
    logical_monitor_pattern = re.compile(
        r'\((\d+),\s*(\d+),\s*([0-9.]+),\s*(?:uint32\s+)?\d+,\s*(true|false),'
    )

    primary_scale = None
    any_scale = None

    for m in logical_monitor_pattern.finditer(text):
        scale = float(m.group(3))
        is_primary = m.group(4) == 'true'
        if any_scale is None:
            any_scale = scale
        if is_primary:
            primary_scale = scale
            break

    scale = primary_scale if primary_scale is not None else any_scale
    if scale is None:
        return None

    # Find monitor modes — look for resolution patterns like (1920, 1080, ...)
    # Modes appear as ((width, height, refresh_rate, preferred_scale, ...), [...flags])
    # We want the first/current mode of the first monitor.
    mode_pattern = re.compile(
        r'\((\d{3,5}),\s*(\d{3,5}),\s*[0-9.]+,'
    )

    width = None
    height = None
    for m in mode_pattern.finditer(text):
        w, h = int(m.group(1)), int(m.group(2))
        # Sanity check — actual display resolutions
        if 640 <= w <= 7680 and 480 <= h <= 4320:
            width, height = w, h
            break

    if width is None or height is None:
        # Got scale but not resolution — use scale with default resolution
        return {'scale': scale, 'physical_width': 1920, 'physical_height': 1080}

    return {'scale': scale, 'physical_width': width, 'physical_height': height}
