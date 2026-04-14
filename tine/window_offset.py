"""Resolve an app name to its top-left window origin in screen coords.

AT-SPI2 reports element bboxes relative to the window's frame_rect. To turn
one into an absolute screen position (for clicks, for annotation, for OCR
dedup) we need to add the window's on-screen (x, y). This module encapsulates
that lookup — normalising the various app name forms (wm_class, app_id,
reverse-domain, hyphenated) against the window list returned by the GNOME
Shell extension.
"""

import sys

import tine.screenshot


def _safe_coord(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def normalize_app_name(name):
    """Normalize an app name / wm_class / app_id for cross-format matching.

    AT-SPI2 reports app names with hyphens (``gnome-calculator``) while GNOME's
    wm_class and app_id use reverse-domain notation (``org.gnome.Calculator``).
    Removing separators and common prefixes lets substring matching work
    across both formats.
    """
    if not name:
        return ''
    s = name.lower()
    for prefix in ('org.', 'com.', 'net.', 'io.'):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.replace('-', '').replace('.', '')


def _pick_best_match(matches):
    """From a list of matching windows, prefer the focused one."""
    for w in matches:
        if w.get('focused'):
            return (_safe_coord(w.get('x')), _safe_coord(w.get('y')))
    w = matches[0]
    return (_safe_coord(w.get('x')), _safe_coord(w.get('y')))


def resolve_window_offset(app_name, windows=None):
    """Match ``app_name`` against the window list, returning its (x, y) origin.

    Matching order:
      1. Normalized wm_class substring match
      2. Normalized app_id substring match
      3. Raw title substring match (case-insensitive, last resort)

    When multiple windows match, the focused one is preferred.

    Args:
        app_name: App name reported by AT-SPI2 (or stored in the ref cache).
        windows: Optional pre-fetched window list. If omitted, list_windows()
            is called. Pass this if you already have the list to avoid a
            second D-Bus round-trip.

    Returns:
        Tuple ``(x, y)``. Falls back to ``(0, 0)`` with a stderr warning when
        nothing matches or the window list can't be fetched.
    """
    if not app_name:
        print('Warning: empty _app, cannot resolve window offset — using (0, 0)',
              file=sys.stderr)
        return (0, 0)

    if windows is None:
        try:
            windows = tine.screenshot.list_windows()
        except Exception:
            print('Warning: could not list windows — using offset (0, 0)',
                  file=sys.stderr)
            return (0, 0)

    app_norm = normalize_app_name(app_name)

    if app_norm:
        matches = [w for w in windows
                   if w.get('wm_class')
                   and app_norm in normalize_app_name(w['wm_class'])]
        if matches:
            return _pick_best_match(matches)

    if app_norm:
        matches = [w for w in windows
                   if w.get('app_id')
                   and app_norm in normalize_app_name(w['app_id'])]
        if matches:
            return _pick_best_match(matches)

    app_lower = app_name.lower()
    matches = [w for w in windows if app_lower in w.get('title', '').lower()]
    if matches:
        return _pick_best_match(matches)

    print(f'Warning: no window match for "{app_name}" — using offset (0, 0)',
          file=sys.stderr)
    return (0, 0)
