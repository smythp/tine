"""Probe: compare tine's display-config view against Mutter + actual screenshot,
then demonstrate the ABS-axis saturation that mis-routes clicks on a secondary
monitor.

Prints:

  1. Raw Mutter GetCurrentState output (truncated to first few KB)
  2. What tine.display.get_display_config() reports: scale, physical_width,
     physical_height
  3. A screenshot captured via the extension, and its actual PNG dimensions
  4. Consistency check between (2) and (3)
  5. ABS-saturation demonstration: picks a pixel on the secondary monitor,
     runs it through the same `_physical_to_abs` formula tine/input.py uses,
     and shows that abs_x exceeds ABS_MAX (65535). libinput saturates the
     value at ABS_MAX, which maps back to pixel `physical_width - 1` on the
     primary monitor — the right edge of primary, not the intended pixel.
  6. Grid-cell drift: computes where `B3` draws on the full screenshot vs
     where `tine click B3` actually fires (uses only primary dims), so the
     agent's "pick B3" and tine's "click B3" disagree by cell widths.

On a single-monitor unit-scale setup (5) and (6) are skipped and the probe
just confirms the config view matches the screenshot. Under fractional
scaling or multi-monitor layouts the drift is produced concretely — not
described.

Context:

  * On fractional scaling (e.g. 1.25x 1920x1080), Mutter reports scale=1.25
    and the primary mode at 1920x1080, but the compositor's logical stage
    is 1536x864. tine's ABS-axis mapping divides by physical_width=1920,
    which assumes the ABS range spans the physical mode rather than the
    logical stage.
  * On dual monitors, Mutter reports the primary only via tine's parser.
    The screenshot spans both monitors (e.g. 3840x1080). Clicks using a
    grid or ABS device over the full stage overflow ABS_MAX = 65535 once
    the requested pixel exceeds physical_width — see finding-20260419-wru6.

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
    from tine.input import ABS_MAX
    from tine.screenshot import capture, grid_cell_center, list_windows
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
    demonstrate_abs_saturation(cfg, dims)
    print()
    demonstrate_grid_drift(cfg, dims)
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


def _apply_abs_formula(phys_x, phys_y, cfg):
    """Mirror tine.input._physical_to_abs verbatim, but return pre-clamp
    values so the probe can show overflow.

    tine.input._physical_to_abs clamps to an int but does not cap at ABS_MAX;
    libinput / the kernel does the saturation downstream.
    """
    abs_x = int(phys_x * ABS_MAX / cfg['physical_width'])
    abs_y = int(phys_y * ABS_MAX / cfg['physical_height'])
    return abs_x, abs_y


def _saturate(abs_val):
    """Apply the ABS_MAX saturation that libinput/kernel does on the axis."""
    return max(0, min(abs_val, ABS_MAX))


def _abs_to_primary_pixel(abs_val, phys_dim):
    """Inverse of the formula: what pixel on the primary monitor does this
    ABS value resolve to? libinput maps [0, ABS_MAX] to [0, primary_dim).
    """
    return int(abs_val * phys_dim / ABS_MAX)


def demonstrate_abs_saturation(cfg, screenshot_dims):
    """Show concretely what happens to a click on the secondary monitor.

    Picks a test pixel 100px into the secondary monitor region (horizontally
    and/or vertically, depending on how the screenshot extends past primary),
    runs it through the same formula tine/input.py uses, and shows the
    saturated result.
    """
    print('# ABS-axis saturation demonstration\n')
    if screenshot_dims is None:
        print('skipped: no screenshot dims (PIL missing or capture failed)')
        return
    shot_w, shot_h = screenshot_dims
    phys_w = cfg['physical_width']
    phys_h = cfg['physical_height']
    overflow_x = shot_w > phys_w
    overflow_y = shot_h > phys_h
    if not overflow_x and not overflow_y:
        print('skipped: screenshot fits inside primary physical dims; no '
              'secondary-monitor region to address.')
        print(f'(screenshot {shot_w}x{shot_h}, physical '
              f'{phys_w}x{phys_h} — single-monitor or this parser already '
              'reports the full stage.)')
        return

    test_x = phys_w + 100 if overflow_x else phys_w // 2
    test_y = phys_h + 100 if overflow_y else phys_h // 2
    print(f'Target pixel: ({test_x}, {test_y})  '
          f'-- on secondary region (screenshot is {shot_w}x{shot_h}, '
          f'primary is {phys_w}x{phys_h})')
    abs_x, abs_y = _apply_abs_formula(test_x, test_y, cfg)
    print(f'Formula: int(phys * {ABS_MAX} / physical_dim)')
    print(f'  abs_x = int({test_x} * {ABS_MAX} / {phys_w}) = {abs_x}  '
          f'(ABS_MAX = {ABS_MAX}, overflow = {abs_x > ABS_MAX})')
    print(f'  abs_y = int({test_y} * {ABS_MAX} / {phys_h}) = {abs_y}  '
          f'(overflow = {abs_y > ABS_MAX})')
    sat_x = _saturate(abs_x)
    sat_y = _saturate(abs_y)
    if sat_x == abs_x and sat_y == abs_y:
        print('No saturation — ABS values are within range. (Did the '
              'parser change? This case should not arise when screenshot '
              'exceeds physical dims.)')
        return
    landed_x = _abs_to_primary_pixel(sat_x, phys_w)
    landed_y = _abs_to_primary_pixel(sat_y, phys_h)
    print(f'Saturated by libinput/kernel at ABS_MAX: ({sat_x}, {sat_y})')
    print(f'Resulting cursor pixel on primary: ({landed_x}, {landed_y})')
    print(f'Drift from intended target: '
          f'({landed_x - test_x}, {landed_y - test_y}) px '
          '-- the click lands at the primary edge, not on the '
          'secondary monitor.')


def demonstrate_grid_drift(cfg, screenshot_dims):
    """Show the mismatch between where `B3` is drawn and where `tine click
    B3` actually fires.

    grid_overlay draws using the screenshot's own dimensions; the CLI grid
    branch in `_cmd_click` / `_cmd_target` resolves cells using
    cfg['physical_width']/['physical_height'] — primary only. On a
    multi-monitor layout these disagree by a fraction proportional to the
    ratio of screenshot dims to physical dims.
    """
    print('# Grid-cell drift demonstration (cell B3 on a 5x3 grid)\n')
    if screenshot_dims is None:
        print('skipped: no screenshot dims (PIL missing or capture failed)')
        return
    shot_w, shot_h = screenshot_dims
    phys_w = cfg['physical_width']
    phys_h = cfg['physical_height']
    drawn = grid_cell_center('B3', shot_w, shot_h, cols=5, rows=3)
    clicked = grid_cell_center('B3', phys_w, phys_h, cols=5, rows=3)
    print(f'Drawn center of B3 on the screenshot '
          f'({shot_w}x{shot_h}): {drawn}')
    print(f'tine click B3 fires at (primary dims {phys_w}x{phys_h}): '
          f'{clicked}')
    if drawn == clicked:
        print('agree -- primary dims match screenshot dims; no grid drift.')
    else:
        print(f'DRIFT: agent sees B3 at {drawn}, tine clicks at {clicked} '
              f'-- delta ({drawn[0] - clicked[0]}, '
              f'{drawn[1] - clicked[1]}) px.')


if __name__ == '__main__':
    main()
