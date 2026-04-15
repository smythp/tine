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


def _run_ocr(screenshot_path, ref_cache, scale, app_name):
    """Run OCR on the screenshot, cross-check AT-SPI2 coords, merge ref_tN.

    Pipeline:
      1. detect text on the screenshot
      2. ``verify_atspi_coords`` tags every named AT-SPI2 ref in
         ``ref_cache`` with a ``coord_verified`` field (``true`` /
         ``false`` / ``unknown``) and returns the set of disputed
         detections — OCR hits whose pixel position disagrees with the
         corresponding AT-SPI2 ref center
      3. dedup with those disputed detections preserved, so the agent
         can see both the AT-SPI2 ref and the OCR detection that
         contradicts it
      4. assign ``ref_tN`` ids to surviving detections
      5. link ``disputed_by: ref_tN`` on the refs that have a surviving
         dispute

    Resolves the window offset from app_name internally so callers don't
    need to compute it.

    Mutates ref_cache in place. Returns the list of assigned text refs
    (ordered) so format_for_agent can emit them in detection order.
    """
    from tine.ocr import (
        assign_text_refs, dedup_against_atspi, detect_text,
        next_text_ref_index, verify_atspi_coords,
    )
    from tine.window_offset import resolve_window_offset

    window_offset = resolve_window_offset(app_name)
    detections = detect_text(screenshot_path, scale=scale)

    disputes = verify_atspi_coords(
        ref_cache, detections, window_offset=window_offset,
    )
    preserve_ids = {id(d) for d in disputes.values() if d is not None}

    kept = dedup_against_atspi(
        detections, ref_cache, window_offset=window_offset, scale=scale,
        preserve_ids=preserve_ids,
    )
    start_index = next_text_ref_index(ref_cache)
    text_refs = assign_text_refs(kept, start_index=start_index)
    ref_cache.update(text_refs)

    text_ref_order = list(text_refs.keys())
    for ref_id, disputed_det in disputes.items():
        if disputed_det is None:
            continue
        for idx, det in enumerate(kept):
            if det is disputed_det:
                if idx < len(text_ref_order):
                    entry = ref_cache.get(ref_id)
                    if isinstance(entry, dict):
                        entry['disputed_by'] = text_ref_order[idx]
                break

    return text_ref_order


def describe(app=None, annotate=False, scale=1.0, ocr=False):
    """Get combined desktop state for agent orientation.

    Args:
        app: Optional app name to filter tree to.
        annotate: If True, screenshot includes Set-of-Mark overlays.
        scale: Display scale factor.
        ocr: If True, run OCR on the captured screenshot and merge
            ``ref_tN`` entries into the ref cache.

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
            screenshot_path, ref_cache, scale, app_name,
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

    ref_cache_for_verify = result.get('ref_cache') or {}
    verify_rows = []
    for ref_id, entry in ref_cache_for_verify.items():
        if ref_id == '_app' or not isinstance(entry, dict):
            continue
        if ref_id.startswith('ref_t'):
            continue
        status = entry.get('coord_verified')
        if status in (None, 'true'):
            continue
        name = entry.get('name', '')
        disputed_by = entry.get('disputed_by')
        if disputed_by:
            disputed_entry = ref_cache_for_verify.get(disputed_by) or {}
            dbbox = disputed_entry.get('bbox') or {}
            dx = dbbox.get('x', '?')
            dy = dbbox.get('y', '?')
            verify_rows.append(
                f"{ref_id}: {name!r} coord_verified={status}, "
                f"disputed by {disputed_by} at ({dx},{dy})"
            )
        else:
            verify_rows.append(
                f"{ref_id}: {name!r} coord_verified={status}"
            )
    if verify_rows:
        sections.append(
            '## Coord Verification\n'
            'Refs whose AT-SPI2 coordinates disagree with OCR or have no '
            'OCR cross-check. ``false`` means the click center is likely '
            'wrong — prefer ``disputed by`` ref_tN if shown, or fall back '
            'to ``tine activate`` / grid / target. ``unknown`` means no '
            'definitive OCR match (ambiguous or no coverage).\n'
            + '\n'.join(verify_rows)
        )

    screenshot = result.get('screenshot', '')
    if screenshot:
        sections.append(f'## Screenshot\n{screenshot}')

    return '\n\n'.join(sections)
