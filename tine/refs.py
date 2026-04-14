"""Ref cache utilities for tine.

Refs are short IDs assigned to targetable things on screen, cached so agents
can call back to them by id instead of bbox coordinates.

Two namespaces share one cache file:

    ref_1, ref_2, ...   AT-SPI2 interactive elements  (entry has 'role')
    ref_t1, ref_t2, ... OCR-detected text regions     (entry has 'text')

Both entry shapes have a 'bbox'. OCR entries always carry ``coord: 'screen'``
so click_ref skips the window-offset step. The two namespaces are kept
distinct so agents don't conflate a button ref with a text ref; each has
its own counter.
"""

import json
import os
import re
import tempfile

INT_MIN = -2147483648

# Combined pattern matches both ref_N (AT-SPI2) and ref_tN (OCR text).
# Kept here so cli.py and any other caller share one definition.
REF_ID_PATTERN = re.compile(r'^ref_t?\d+$')
TEXT_REF_PATTERN = re.compile(r'^ref_t\d+$')


def is_ref_id(s):
    """Return True if ``s`` matches either ref namespace."""
    return isinstance(s, str) and bool(REF_ID_PATTERN.match(s))


def is_text_ref(s):
    """Return True if ``s`` is an OCR text ref (``ref_tN``)."""
    return isinstance(s, str) and bool(TEXT_REF_PATTERN.match(s))


def bbox_center(bbox):
    """Return (x, y) center of a bounding box, or None for invalid bboxes.

    Center = (x + w//2, y + h//2).
    Returns None for INT_MIN bboxes with negative dimensions.
    """
    if not isinstance(bbox, dict):
        return None

    x = bbox.get('x', 0)
    y = bbox.get('y', 0)
    w = bbox.get('w', 0)
    h = bbox.get('h', 0)

    if x == INT_MIN or y == INT_MIN:
        return None
    if w == INT_MIN or h == INT_MIN:
        return None
    if w <= 0 or h <= 0:
        return None

    return (x + w // 2, y + h // 2)


def save_ref_cache(cache, path):
    """Write ref cache to JSON file atomically (write-then-rename)."""
    dir_path = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(cache, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_ref_cache(path):
    """Load ref cache from JSON file. Returns {} if file doesn't exist or is invalid."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}
