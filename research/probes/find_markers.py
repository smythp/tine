"""Locate bbox-probe.html markers in a screenshot and verify CSS -> screen linearity.

Each marker is an 8x8 solid-color block at a known CSS position (see
bbox-probe.html). This script searches the screenshot for each marker — first
at the stated color, then at the channel-inverse of that color to tolerate
Firefox dark-mode override. It reports:

  - Where the CSS (0, 0) origin landed on screen (the content-area top-left).
  - Each other marker's delta from the expected position (which should be 0
    if the CSS-to-screen mapping is 1:1 and linear).

Any non-zero delta means ground truth isn't where you think it is and further
measurements built on top of it will compound the error.

Usage: python3 find_markers.py <screenshot.png>
"""
import sys
from PIL import Image

# Markers from bbox-probe.html: (name, css_x, css_y, rgb). Order matters: the
# first entry is treated as the origin and used to anchor the linearity check.
MARKERS = [
    ('c1_magenta',    0,   0, (0xff, 0x00, 0xff)),
    ('c2_violet',   100, 100, (0x7f, 0x00, 0xff)),
    ('c3_sky',      200, 200, (0x00, 0xc8, 0xff)),
    ('c4_orange',   400, 400, (0xff, 0x80, 0x00)),
    ('c5_darkgrn',  700, 500, (0x00, 0xa0, 0x00)),
    ('c6_darkred',  900, 100, (0xc0, 0x00, 0x00)),
    ('c7_olive',     50, 600, (0x80, 0x80, 0x00)),
]
BLOCK = 3   # require an NxN run of same color — filters single-pixel UI false positives
TOL = 10    # per-channel rgb tolerance (anti-aliasing, compression)


def _invert(rgb):
    return (255 - rgb[0], 255 - rgb[1], 255 - rgb[2])


def _match(px, x, y, rgb):
    p = px[x, y]
    return (abs(p[0] - rgb[0]) <= TOL
            and abs(p[1] - rgb[1]) <= TOL
            and abs(p[2] - rgb[2]) <= TOL)


def _find_block(pixels, w, h, rgb):
    """Return (x, y) of the first BLOCKxBLOCK run matching rgb, or None."""
    for y in range(h - BLOCK):
        for x in range(w - BLOCK):
            ok = True
            for dy in range(BLOCK):
                for dx in range(BLOCK):
                    if not _match(pixels, x + dx, y + dy, rgb):
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                return (x, y)
    return None


def find_markers(path):
    img = Image.open(path).convert('RGB')
    w, h = img.size
    px = img.load()
    print(f'screenshot: {path}  size={w}x{h}')

    found = []
    inverted_any = False
    for name, cx, cy, rgb in MARKERS:
        pos = _find_block(px, w, h, rgb)
        color_mode = 'normal'
        if pos is None:
            pos = _find_block(px, w, h, _invert(rgb))
            if pos is not None:
                color_mode = 'inverted'
                inverted_any = True
        if pos is None:
            print(f'  {name:12s} css=({cx:4d},{cy:4d}) rgb={rgb}  NOT FOUND')
            continue
        print(f'  {name:12s} css=({cx:4d},{cy:4d}) rgb={rgb}  screen={pos}  {color_mode}')
        found.append((name, cx, cy, pos[0], pos[1]))

    if inverted_any:
        print('\nNote: one or more markers found via channel-inverse colors '
              '(Firefox dark-mode forced-colors override). Layout unaffected; '
              'positions are still truth.')

    if len(found) < 2:
        print('\nNot enough markers to check linearity. Is the probe page loaded?')
        return

    # Anchor on the origin marker (first entry in MARKERS, expected CSS (0, 0)).
    origin = next((f for f in found if f[1] == 0 and f[2] == 0), None)
    if origin is None:
        print('\nOrigin marker CSS(0,0) not found. Cannot compute content offset.')
        return
    _, _, _, sx0, sy0 = origin
    print(f'\nContent origin: CSS (0, 0) -> screen ({sx0}, {sy0})')
    print(f'Formula: screen(css_x, css_y) = ({sx0} + css_x, {sy0} + css_y)\n')

    all_ok = True
    for name, cx, cy, sx, sy in found:
        if (cx, cy) == (0, 0):
            continue
        exp_x, exp_y = sx0 + cx, sy0 + cy
        dx, dy = sx - exp_x, sy - exp_y
        flag = 'OK' if (dx, dy) == (0, 0) else f'DRIFT ({dx:+d},{dy:+d})'
        print(f'  {name:12s} expected=({exp_x},{exp_y})  actual=({sx},{sy})  {flag}')
        if (dx, dy) != (0, 0):
            all_ok = False

    print()
    if all_ok:
        print('CSS -> screen mapping is linear with unit scale. Ground truth trusted.')
    else:
        print('CSS -> screen mapping is NOT linear. Investigate before relying on '
              'content-origin-based math.')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    find_markers(sys.argv[1])
