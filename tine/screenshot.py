"""Screenshot capture, Set-of-Mark annotation, and grid overlay.

capture() takes a screenshot via the GNOME Shell extension D-Bus API.
annotate() draws numbered ref labels on bounding boxes using PIL.
grid_overlay() draws a labeled coordinate grid on a screenshot.
list_windows() returns window geometry via the ListWindows D-Bus method.
"""

import json
import os
import re
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

from tine.refs import bbox_center

# D-Bus constants
DBUS_DEST = 'org.gnome.Shell'
DBUS_PATH = '/org/gnome/OCRScreenshot'
DBUS_IFACE = 'org.gnome.OCRScreenshot'


def _gdbus_call(method, *args):
    """Call a D-Bus method on the OCRScreenshot interface."""
    cmd = [
        'gdbus', 'call', '--session',
        '--dest', DBUS_DEST,
        '--object-path', DBUS_PATH,
        '--method', f'{DBUS_IFACE}.{method}',
    ] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f'D-Bus call {method} failed: {result.stderr.strip()}')
    return result.stdout.strip()


def capture(output_path=None):
    """Capture a full screenshot via GNOME Shell extension.

    Args:
        output_path: Path to save PNG. If None, uses a temp file.

    Returns:
        Path to the saved screenshot.

    Raises:
        RuntimeError: If the D-Bus call fails or returns false.
    """
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix='.png', prefix='tine-screenshot-')
        os.close(fd)

    stdout = _gdbus_call('Full', output_path)

    if '(false' in stdout:
        raise RuntimeError(f'Screenshot capture failed: {stdout}')

    # GNOME Shell extension returns before the file is fully flushed.
    # Wait for the file to be readable as a valid PNG.
    import time
    for _ in range(20):
        try:
            with open(output_path, 'rb') as f:
                header = f.read(8)
                if header[:4] == b'\x89PNG':
                    # Check that the file has an IEND chunk (fully written)
                    f.seek(-12, 2)
                    tail = f.read(12)
                    if b'IEND' in tail:
                        break
        except (OSError, ValueError):
            pass
        time.sleep(0.05)

    return output_path


def list_windows():
    """List open windows with geometry and app identification.

    Returns:
        List of dicts with: title, wm_class, app_id, x, y, width, height, focused.

    Raises:
        RuntimeError: If the D-Bus call fails.
    """
    stdout = _gdbus_call('ListWindows')

    # gdbus returns: ('json_string',)
    # Extract the JSON string from between the quotes
    match = re.search(r"\('(.*)'\s*,\s*\)", stdout, re.DOTALL)
    if match:
        json_str = match.group(1)
        # Unescape GVariant single-quoted string escaping:
        # \' → '  and  \\ → \
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


def annotate(image_path, ref_cache, output_path=None, scale=1.0):
    """Draw Set-of-Mark overlays on a screenshot.

    Draws colored bounding boxes with ref number labels at each ref's position.

    Args:
        image_path: Path to input screenshot PNG.
        ref_cache: Dict mapping ref_id -> {role, name, bbox: {x, y, w, h}}.
                   Bboxes are in logical coordinates (AT-SPI2).
        output_path: Path to save annotated image. If None, overwrites input.
        scale: Display scale factor. Logical coords * scale = physical pixels.

    Returns:
        Path to the annotated image.
    """
    if scale <= 0:
        raise ValueError(f'Scale must be positive, got {scale}')

    if output_path is None:
        output_path = image_path

    img = Image.open(image_path)
    draw = ImageDraw.Draw(img)

    # Try to load a readable font, fall back to default
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 14)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype('/usr/share/fonts/TTF/DejaVuSans-Bold.ttf', 14)
        except (OSError, IOError):
            font = ImageFont.load_default()

    for ref_id, ref_data in ref_cache.items():
        # Skip non-dict entries (e.g. _app metadata key)
        if not isinstance(ref_data, dict):
            continue
        bbox = ref_data.get('bbox', {})
        center = bbox_center(bbox)
        if center is None:
            continue

        x = bbox.get('x', 0)
        y = bbox.get('y', 0)
        w = bbox.get('w', 0)
        h = bbox.get('h', 0)

        if w <= 0 or h <= 0:
            continue

        # Scale logical coords to physical pixels
        try:
            px = int(x * scale)
            py = int(y * scale)
            pw = int(w * scale)
            ph = int(h * scale)
        except (OverflowError, ValueError):
            continue

        # Draw bounding box rectangle
        outline_color = (255, 0, 0)
        try:
            draw.rectangle([px, py, px + pw, py + ph], outline=outline_color, width=2)
        except (OverflowError, SystemError, ValueError):
            continue

        # Draw ref number label
        ref_num = ref_id.replace('ref_', '')
        label = ref_num

        # Background for label readability
        label_bbox = draw.textbbox((0, 0), label, font=font)
        label_w = label_bbox[2] - label_bbox[0] + 4
        label_h = label_bbox[3] - label_bbox[1] + 4
        label_x = px
        label_y = py - label_h if py - label_h >= 0 else py

        try:
            draw.rectangle([label_x, label_y, label_x + label_w, label_y + label_h],
                           fill=(255, 0, 0))
            draw.text((label_x + 2, label_y + 2), label, fill=(255, 255, 255), font=font)
        except (OverflowError, SystemError, ValueError):
            pass

    img.save(output_path)
    return output_path


def grid_overlay(image_path, grid_size=100, output_path=None):
    """Draw a labeled coordinate grid on a screenshot.

    Draws semi-transparent grid lines at regular intervals with column labels
    (A, B, C, ...) along the top and row labels (1, 2, 3, ...) along the left.

    Args:
        image_path: Path to input screenshot PNG.
        grid_size: Grid spacing in physical pixels (default 100).
        output_path: Path to save gridded image. If None, overwrites input.

    Returns:
        Path to the gridded image.
    """
    if grid_size <= 0:
        raise ValueError(f'grid_size must be positive, got {grid_size}')

    if output_path is None:
        output_path = image_path

    img = Image.open(image_path).convert('RGBA')
    width, height = img.size

    # Draw grid lines on a transparent overlay
    overlay = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Semi-transparent gray lines
    line_color = (0, 0, 0, 80)

    # Vertical lines
    x = grid_size
    while x < width:
        draw.line([(x, 0), (x, height)], fill=line_color, width=1)
        x += grid_size

    # Horizontal lines
    y = grid_size
    while y < height:
        draw.line([(0, y), (width, y)], fill=line_color, width=1)
        y += grid_size

    # Composite overlay onto image
    img = Image.alpha_composite(img, overlay)

    # Try to load a readable font, fall back to default
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype('/usr/share/fonts/TTF/DejaVuSans-Bold.ttf', 12)
        except (OSError, IOError):
            font = ImageFont.load_default()

    # Convert to RGB for label drawing (no more alpha needed)
    img = img.convert('RGB')
    draw = ImageDraw.Draw(img)

    num_cols = (width + grid_size - 1) // grid_size
    num_rows = (height + grid_size - 1) // grid_size

    # Column labels (A, B, C, ... Z, AA, AB, ...) along top edge
    for col in range(num_cols):
        label = _col_label(col)
        cx = col * grid_size + grid_size // 2
        label_bbox = draw.textbbox((0, 0), label, font=font)
        lw = label_bbox[2] - label_bbox[0] + 4
        lh = label_bbox[3] - label_bbox[1] + 4
        lx = cx - lw // 2
        ly = 2
        draw.rectangle([lx, ly, lx + lw, ly + lh], fill=(0, 0, 0))
        draw.text((lx + 2, ly + 2), label, fill=(255, 255, 255), font=font)

    # Row labels (1, 2, 3, ...) along left edge
    for row in range(num_rows):
        label = str(row + 1)
        cy = row * grid_size + grid_size // 2
        label_bbox = draw.textbbox((0, 0), label, font=font)
        lw = label_bbox[2] - label_bbox[0] + 4
        lh = label_bbox[3] - label_bbox[1] + 4
        lx = 2
        ly = cy - lh // 2
        draw.rectangle([lx, ly, lx + lw, ly + lh], fill=(0, 0, 0))
        draw.text((lx + 2, ly + 2), label, fill=(255, 255, 255), font=font)

    img.save(output_path)
    return output_path


def target_subcell_center(x, y, pick, region_size=100):
    """Return the (new_x, new_y) center of a sub-cell in a 3x3 grid around (x, y).

    Sub-cells use numpad layout:
        7 | 8 | 9
        4 | 5 | 6
        1 | 2 | 3

    Args:
        x: Center x coordinate of the target region.
        y: Center y coordinate of the target region.
        pick: Sub-cell number 1-9 (numpad layout).
        region_size: Size of the 3x3 region in pixels.

    Returns:
        (new_x, new_y) center of the chosen sub-cell.

    Raises:
        ValueError: If pick is not 1-9.
    """
    if not (1 <= pick <= 9):
        raise ValueError(f'pick must be 1-9, got {pick}')

    cell_size = region_size / 3
    half = region_size / 2

    # Numpad layout: column (0=left, 1=center, 2=right)
    # and row (0=bottom, 1=middle, 2=top)
    col = (pick - 1) % 3       # 1,4,7->0  2,5,8->1  3,6,9->2
    row = (pick - 1) // 3      # 1,2,3->0  4,5,6->1  7,8,9->2

    # Convert to pixel offsets from top-left of region
    # row 0 = bottom = largest y, row 2 = top = smallest y
    new_x = int(x - half + col * cell_size + cell_size / 2)
    new_y = int(y - half + (2 - row) * cell_size + cell_size / 2)

    return (new_x, new_y)


def target_overlay(image_path, x, y, region_size=100, output_path=None):
    """Draw a crosshair and 3x3 sub-grid at (x, y) on a screenshot.

    The sub-grid cells are labeled 1-9 in numpad layout:
        7 | 8 | 9
        4 | 5 | 6
        1 | 2 | 3

    Args:
        image_path: Path to input screenshot PNG.
        x: Target x coordinate in physical pixels.
        y: Target y coordinate in physical pixels.
        region_size: Size of the 3x3 region in pixels (default 100).
        output_path: Path to save annotated image. If None, overwrites input.

    Returns:
        Path to the annotated image.
    """
    if region_size <= 0:
        raise ValueError(f'region_size must be positive, got {region_size}')

    if output_path is None:
        output_path = image_path

    img = Image.open(image_path).convert('RGB')
    draw = ImageDraw.Draw(img)

    color = (0, 255, 255)  # cyan
    half = region_size // 2
    cell_size = region_size / 3
    img_w, img_h = img.size

    # Region bounds
    rx0 = x - half
    ry0 = y - half
    rx1 = x + half
    ry1 = y + half

    # Draw crosshair lines (full image width/height)
    draw.line([(x, 0), (x, img_h)], fill=color, width=1)
    draw.line([(0, y), (img_w, y)], fill=color, width=1)

    # Draw 3x3 grid box outline
    draw.rectangle([rx0, ry0, rx1, ry1], outline=color, width=2)

    # Draw internal grid lines (2 vertical, 2 horizontal)
    for i in range(1, 3):
        gx = int(rx0 + i * cell_size)
        draw.line([(gx, ry0), (gx, ry1)], fill=color, width=1)
        gy = int(ry0 + i * cell_size)
        draw.line([(rx0, gy), (rx1, gy)], fill=color, width=1)

    # Load font for labels
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 12)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype('/usr/share/fonts/TTF/DejaVuSans-Bold.ttf', 12)
        except (OSError, IOError):
            font = ImageFont.load_default()

    # Label cells 1-9 (numpad layout)
    for pick in range(1, 10):
        cx, cy = target_subcell_center(x, y, pick, region_size)
        label = str(pick)
        label_bbox = draw.textbbox((0, 0), label, font=font)
        lw = label_bbox[2] - label_bbox[0]
        lh = label_bbox[3] - label_bbox[1]
        lx = cx - lw // 2
        ly = cy - lh // 2
        # Background for readability
        draw.rectangle([lx - 1, ly - 1, lx + lw + 1, ly + lh + 1], fill=(0, 0, 0))
        draw.text((lx, ly), label, fill=color, font=font)

    img.save(output_path)
    return output_path


def _col_label(index):
    """Convert a zero-based column index to a letter label (A, B, ..., Z, AA, AB, ...)."""
    result = ''
    i = index
    while True:
        result = chr(ord('A') + i % 26) + result
        i = i // 26 - 1
        if i < 0:
            break
    return result


def parse_grid_cell(cell_str):
    """Parse a grid cell label like 'B3' into (col_index, row_index).

    Column is letters (A=0, B=1, ..., Z=25, AA=26, ...).
    Row is 1-based number (1=0, 2=1, ...).

    Returns:
        (col_index, row_index) tuple, both zero-based.

    Raises:
        ValueError: If the cell string is not valid.
    """
    m = re.match(r'^([A-Z]+)(\d+)$', cell_str.upper())
    if not m:
        raise ValueError(f'Invalid grid cell: {cell_str!r} (expected format like A1, B3, AA12)')
    col_str = m.group(1)
    row_str = m.group(2)

    # Convert column letters to zero-based index
    col = 0
    for ch in col_str:
        col = col * 26 + (ord(ch) - ord('A') + 1)
    col -= 1  # zero-based

    row = int(row_str) - 1  # zero-based
    if row < 0:
        raise ValueError(f'Row must be >= 1, got {row_str}')

    return (col, row)


def grid_cell_center(cell_str, grid_size=100):
    """Return the (x, y) pixel center of a grid cell.

    Args:
        cell_str: Grid cell label like 'B3'.
        grid_size: Grid spacing in physical pixels.

    Returns:
        (x, y) center of the cell in physical pixels.
    """
    col, row = parse_grid_cell(cell_str)
    x = col * grid_size + grid_size // 2
    y = row * grid_size + grid_size // 2
    return (x, y)
