"""Combined tree + screenshot + optional OCR for agent orientation.

describe() gives an agent a complete picture of the current desktop state.
format_for_agent() formats the result as text for LLM consumption.

With ``ocr=True``, detected text regions get ``ref_tN`` IDs merged into the
same ref cache as the AT-SPI2 refs. Text refs that duplicate a named AT-SPI2
ref (same normalized text, OCR center inside the AT-SPI2 bbox) are dropped
so the agent sees each thing once.
"""

import os
import tempfile

import tine.tree
from tine.walker import walk_atspi


def _get_tree(app=None):
    """Run the AT-SPI2 pipeline and return formatted tree, ref cache, and app name.

    Returns:
        Tuple of (formatted_tree_text, ref_cache, app_name).

    Raises:
        RuntimeError: if the AT-SPI2 walker subprocess fails.
    """
    raw_tree = walk_atspi(app=app)
    collapsed = tine.tree.collapse_containers(raw_tree)
    annotated, ref_cache = tine.tree.assign_refs(collapsed)
    app_name = (raw_tree.get('name', '')
                or raw_tree.get('application', {}).get('name', ''))
    return tine.tree.format_tree(annotated), ref_cache, app_name


def _get_windows():
    """Get window list via D-Bus."""
    from tine.screenshot import list_windows
    return list_windows()


def _capture_screenshot(path, annotate=False, ref_cache=None, scale=1.0):
    """Capture and optionally annotate a screenshot."""
    from tine.screenshot import capture, annotate as annotate_fn

    capture(path)
    if annotate and ref_cache is not None:
        annotate_fn(path, ref_cache, scale=scale)
    return path


def _next_text_ref_index(ref_cache):
    """Highest existing ref_tN index + 1, or 1 if none."""
    max_idx = 0
    for key in ref_cache:
        if key.startswith('ref_t'):
            try:
                idx = int(key[len('ref_t'):])
            except ValueError:
                continue
            if idx > max_idx:
                max_idx = idx
    return max_idx + 1


def _run_ocr(screenshot_path, ref_cache, scale, window_offset):
    """Run OCR on the screenshot, dedup against AT-SPI2 refs, merge ref_tN.

    Mutates ref_cache in place. Returns the list of assigned text refs
    (ordered) so format_for_agent can emit them in detection order.
    """
    from tine.ocr import (
        assign_text_refs, dedup_against_atspi, detect_text,
    )

    detections = detect_text(screenshot_path, scale=scale)
    detections = dedup_against_atspi(
        detections, ref_cache, window_offset=window_offset, scale=scale,
    )
    start_index = _next_text_ref_index(ref_cache)
    text_refs = assign_text_refs(detections, start_index=start_index)
    ref_cache.update(text_refs)
    return list(text_refs.keys())


def describe(app=None, annotate=False, scale=1.0, ocr=False,
             window_offset=(0, 0)):
    """Get combined desktop state for agent orientation.

    Args:
        app: Optional app name to filter tree to.
        annotate: If True, screenshot includes Set-of-Mark overlays.
        scale: Display scale factor.
        ocr: If True, run OCR on the captured screenshot and merge
            ``ref_tN`` entries into the ref cache.
        window_offset: (x, y) screen-logical origin of the focused window,
            used for OCR dedup against window-relative AT-SPI2 bboxes.

    Returns:
        Dict with keys: tree, screenshot, windows, ref_cache, app_name,
        text_ref_order (list of ref_tN keys in detection order).

    Raises:
        RuntimeError: if the AT-SPI2 walker fails.
    """
    tree, ref_cache, app_name = _get_tree(app=app)
    windows = _get_windows()

    fd, screenshot_path = tempfile.mkstemp(suffix='.png', prefix='tine-describe-')
    os.close(fd)
    _capture_screenshot(screenshot_path, annotate=annotate,
                        ref_cache=ref_cache, scale=scale)

    text_ref_order = []
    if ocr:
        text_ref_order = _run_ocr(
            screenshot_path, ref_cache, scale, window_offset,
        )

    return {
        'tree': tree,
        'screenshot': screenshot_path,
        'windows': windows,
        'ref_cache': ref_cache,
        'app_name': app_name,
        'text_ref_order': text_ref_order,
    }


def format_for_agent(result):
    """Format describe() result as text for LLM consumption.

    Args:
        result: Dict from describe().

    Returns:
        Formatted string with windows, tree, detected text, and screenshot path.
    """
    sections = []

    windows = result.get('windows', [])
    if windows:
        lines = ['## Windows']
        for w in windows:
            focused = ' (focused)' if w.get('focused') else ''
            title = w.get('title', '(untitled)')
            app = w.get('app_id', w.get('wm_class', ''))
            width = w.get('width', '?')
            height = w.get('height', '?')
            x = w.get('x', '?')
            y = w.get('y', '?')
            lines.append(
                f"- {title} [{app}] "
                f"{width}x{height} at ({x},{y}){focused}"
            )
        sections.append('\n'.join(lines))
    else:
        sections.append('## Windows\nNo windows open.')

    tree = result.get('tree', '')
    if tree:
        sections.append(f'## Accessibility Tree\n{tree}')
    else:
        sections.append('## Accessibility Tree\nNo tree available.')

    text_ref_order = result.get('text_ref_order') or []
    if text_ref_order:
        ref_cache = result.get('ref_cache') or {}
        lines = ['## Detected Text']
        for ref_id in text_ref_order:
            entry = ref_cache.get(ref_id)
            if not isinstance(entry, dict):
                continue
            text = entry.get('text', '')
            bbox = entry.get('bbox') or {}
            x = bbox.get('x', '?')
            y = bbox.get('y', '?')
            w = bbox.get('w', '?')
            h = bbox.get('h', '?')
            lines.append(f'{ref_id}: {text!r} at ({x},{y}) {w}x{h}')
        sections.append('\n'.join(lines))

    screenshot = result.get('screenshot', '')
    if screenshot:
        sections.append(f'## Screenshot\n{screenshot}')

    return '\n\n'.join(sections)
