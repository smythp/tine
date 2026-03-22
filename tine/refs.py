"""Ref cache utilities for tine.

Refs are short IDs (ref_1, ref_2, ...) assigned to interactive AT-SPI2
elements. The ref cache maps ref IDs to {role, name, bbox} for lookup.
"""

import json
import os

INT_MIN = -2147483648


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
    """Write ref cache to JSON file."""
    with open(path, 'w') as f:
        json.dump(cache, f)


def load_ref_cache(path):
    """Load ref cache from JSON file. Returns {} if file doesn't exist or is invalid."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}
