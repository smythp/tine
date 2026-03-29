"""Combined tree + screenshot for agent orientation.

describe() gives an agent a complete picture of the current desktop state.
format_for_agent() formats the result as text for LLM consumption.
"""

import os
import tempfile

import tine.tree
from tine.walker import walk_atspi


def _get_tree(app=None):
    """Run the AT-SPI2 pipeline and return formatted tree and ref cache.

    Returns:
        Tuple of (formatted_tree_text, ref_cache).

    Raises:
        RuntimeError: if the AT-SPI2 walker subprocess fails.
    """
    raw_tree = walk_atspi(app=app)
    collapsed = tine.tree.collapse_containers(raw_tree)
    annotated, ref_cache = tine.tree.assign_refs(collapsed)
    return tine.tree.format_tree(annotated), ref_cache


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


def describe(app=None, annotate=False, scale=1.0):
    """Get combined desktop state for agent orientation.

    Args:
        app: Optional app name to filter tree to.
        annotate: If True, screenshot includes Set-of-Mark overlays.
        scale: Display scale factor for annotation coordinates.

    Returns:
        Dict with keys: tree, screenshot, windows, ref_cache.

    Raises:
        RuntimeError: if the AT-SPI2 walker fails.
    """
    tree, ref_cache = _get_tree(app=app)
    windows = _get_windows()

    fd, screenshot_path = tempfile.mkstemp(suffix='.png', prefix='tine-describe-')
    os.close(fd)
    _capture_screenshot(screenshot_path, annotate=annotate,
                        ref_cache=ref_cache, scale=scale)

    return {
        'tree': tree,
        'screenshot': screenshot_path,
        'windows': windows,
        'ref_cache': ref_cache,
    }


def format_for_agent(result):
    """Format describe() result as text for LLM consumption.

    Args:
        result: Dict from describe().

    Returns:
        Formatted string with tree, windows, and screenshot path.
    """
    sections = []

    # Windows section
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

    # Tree section
    tree = result.get('tree', '')
    if tree:
        sections.append(f'## Accessibility Tree\n{tree}')
    else:
        sections.append('## Accessibility Tree\nNo tree available.')

    # Screenshot
    screenshot = result.get('screenshot', '')
    if screenshot:
        sections.append(f'## Screenshot\n{screenshot}')

    return '\n\n'.join(sections)
