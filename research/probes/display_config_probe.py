"""Probe: compare tine's display-config view against Mutter + actual screenshot.

Prints:

  1. Raw Mutter GetCurrentState output (truncated to first few KB)
  2. What tine.display.get_display_config() reports: scale, physical_width,
     physical_height
  3. A screenshot captured via the extension, and its actual PNG dimensions

On a single-monitor unit-scale setup all four should agree. Under fractional
scaling or multi-monitor layouts they drift apart, which is the shape of the
latent bug described in finding-20260419-<audit>:

  * On fractional scaling (e.g. 1.25x 1920x1080), Mutter reports scale=1.25
    and the primary mode at 1920x1080, but the compositor's logical stage
    is 1536x864. tine's ABS-axis mapping divides by physical_width=1920,
    which assumes the ABS range spans the physical mode rather than the
    logical stage.
  * On dual monitors, Mutter reports the primary only via tine's parser.
    The screenshot spans both monitors (e.g. 3840x1080). Clicks using a
    grid or ABS device over the full stage overflow ABS_MAX = 65535 once
    the requested pixel exceeds physical_width.

Run on archgnome after any layout or scale change:

    source /tmp/tine-env.sh && \
        /home/patrick/projects/tine/venv/bin/python3 \
        /home/patrick/projects/tine/research/probes/display_config_probe.py
"""

import os
import subprocess
import sys

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from tine.display import get_display_config
    from tine.screenshot import capture, list_windows
except ImportError as e:
    print(f'tine not importable: {e}. Run inside the venv.', file=sys.stderr)
    sys.exit(1)


def dump_mutter_raw():
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
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return f'<gdbus unavailable: {e}>'
    if result.returncode != 0:
        return f'<gdbus failed: {result.stderr.strip()}>'
    out = result.stdout
    if len(out) > 4000:
        out = out[:4000] + f'\n... (truncated, total {len(out)} bytes)'
    return out


def dump_tine_config():
    return get_display_config()


def dump_screenshot_dims():
    if Image is None:
        return None, '<PIL not available>'
    path = capture()
    try:
        img = Image.open(path)
        w, h = img.size
        return (w, h), path
    except Exception as e:
        return None, f'<open failed: {e}>'


def main():
    print('# Mutter DisplayConfig raw output\n')
    print(dump_mutter_raw())
    print()
    print('# tine.display.get_display_config()\n')
    cfg = dump_tine_config()
    print(f'scale={cfg["scale"]!r}')
    print(f'physical_width={cfg["physical_width"]!r}')
    print(f'physical_height={cfg["physical_height"]!r}')
    print()
    print('# Actual screenshot dimensions (via extension Full)\n')
    dims, path_or_err = dump_screenshot_dims()
    if dims is None:
        print(path_or_err)
    else:
        w, h = dims
        print(f'path={path_or_err}')
        print(f'pixel_width={w}  pixel_height={h}')
        print()
        print('# Consistency check')
        if w == cfg['physical_width'] and h == cfg['physical_height']:
            print(f'OK: screenshot matches tine.display '
                  f'({w}x{h})')
        else:
            print(f'MISMATCH: screenshot {w}x{h} vs '
                  f"display_config {cfg['physical_width']}x"
                  f"{cfg['physical_height']}")
            print('The ABS-axis mapping in tine.input._physical_to_abs '
                  'divides by physical_width/height. If the screenshot '
                  'spans a larger region than those (multi-monitor) or a '
                  'smaller logical stage than those (fractional scale), '
                  'clicks will mis-target.')
    print()
    print('# Windows via extension (x, y are compositor logical coords)\n')
    try:
        for w in list_windows():
            focused = ' (focused)' if w.get('focused') else ''
            print(
                f"  {w.get('title','(untitled)')[:40]:40}  "
                f"{w.get('width'):>5}x{w.get('height'):<5} at "
                f"({w.get('x'):>5},{w.get('y'):>5}){focused}"
            )
    except Exception as e:
        print(f'<list_windows failed: {e}>')


if __name__ == '__main__':
    main()
