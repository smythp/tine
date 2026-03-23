"""Screenshot capture and Set-of-Mark annotation.

capture() takes a screenshot via the GNOME Shell extension D-Bus API.
annotate() draws numbered ref labels on bounding boxes using PIL.
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
