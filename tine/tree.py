"""AT-SPI2 tree walker: collapsing, ref assignment, formatting.

Processing pipeline:
    raw tree dict → collapse_containers → assign_refs → format_tree → text output
"""

import hashlib

INT_MIN = -2147483648

# Roles that are pure layout wrappers when unnamed — collapse them.
COLLAPSIBLE_ROLES = {'panel', 'grouping', 'filler'}

# Roles that always get refs (interactive elements the agent can act on).
INTERACTIVE_ROLES = {
    'button', 'toggle button', 'check box', 'check menu item',
    'menu item', 'text', 'terminal', 'table cell', 'list item',
    'menu', 'radio button', 'slider', 'combo box', 'link', 'entry',
    'spin button',
}

# Container roles that get refs when they have a non-empty name.
NAMED_CONTAINER_ROLES = {
    'list', 'tree', 'table', 'page tab list', 'tool bar', 'tree table',
}

# States that are "default" and should be omitted from output.
DEFAULT_STATES = {
    'sensitive', 'showing', 'visible', 'enabled',
    'focusable', 'horizontal', 'vertical',
}

GARBAGE_BBOX_THRESHOLD = 10000


def _is_collapsible(node):
    """An unnamed panel/grouping/filler is a pure layout wrapper."""
    return node['role'] in COLLAPSIBLE_ROLES and not node.get('name')


def _is_hidden(node):
    """Node at INT_MIN coordinates."""
    bbox = node.get('bbox', {})
    return bbox.get('x') == INT_MIN


def _has_negative_dims(node):
    """Node has negative width or height (garbage geometry)."""
    bbox = node.get('bbox', {})
    return bbox.get('w', 0) < 0 or bbox.get('h', 0) < 0


def _has_semantic_descendants(node):
    """Check if node or any descendant has an interactive role.
    Iterative to avoid stack overflow on deep trees."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current['role'] in INTERACTIVE_ROLES:
            return True
        stack.extend(current.get('children', []))
    return False


def _is_valid_ref_bbox(bbox):
    """Check if a bbox is valid for ref assignment."""
    w = bbox.get('w', 0)
    h = bbox.get('h', 0)
    x = bbox.get('x', 0)
    y = bbox.get('y', 0)

    # Zero or negative dimensions — bbox_center returns None for these
    if w <= 0 or h <= 0:
        return False

    # Garbage: absurdly large dimensions
    if w > GARBAGE_BBOX_THRESHOLD or h > GARBAGE_BBOX_THRESHOLD:
        return False

    # Any INT_MIN coordinate — bbox_center returns None for these
    if x == INT_MIN or y == INT_MIN:
        return False

    if w == INT_MIN or h == INT_MIN:
        return False

    return True


def _should_get_ref(node):
    """Determine if a node should be assigned a ref."""
    role = node['role']
    name = node.get('name', '')
    bbox = node.get('bbox', {})

    # Separators never get refs
    if role == 'separator':
        return False

    # Must have valid bbox
    if not _is_valid_ref_bbox(bbox):
        return False

    # Interactive roles always get refs
    if role in INTERACTIVE_ROLES:
        return True

    # Named containers get refs
    if name and role in NAMED_CONTAINER_ROLES:
        return True

    return False


# ---------------------------------------------------------------------------
# collapse_containers
# ---------------------------------------------------------------------------

def collapse_containers(tree):
    """Collapse unnamed layout wrappers, deduplicate echo labels, filter hidden nodes.

    Args:
        tree: Tree dict with keys: role, name, bbox, states, actions, children, child_index.

    Returns:
        Collapsed tree dict.
    """
    result = _collapse_node(tree, set())

    # If root itself is collapsible with single child, replace with that child
    if _is_collapsible(result) and len(result.get('children', [])) == 1:
        result = result['children'][0]

    return result


def _collapse_node(node, ancestor_names):
    """Recursively process a node: filter hidden, collapse wrappers, dedup echo labels."""
    # Build ancestor name set for echo dedup
    names = set(ancestor_names)
    if node.get('name'):
        names.add(node['name'])

    new_children = []
    for child in node.get('children', []):
        # Filter hidden elements (INT_MIN coords)
        if _is_hidden(child):
            if _has_negative_dims(child):
                continue  # Negative dimensions → garbage, prune unconditionally
            if not _has_semantic_descendants(child):
                continue  # No interactive descendants → prune

        # Label echo dedup: skip labels whose name matches any ancestor
        if child['role'] == 'label' and child.get('name') in names:
            continue

        processed = _collapse_node(child, names)

        # Collapse: if processed child is an unnamed wrapper, promote its children
        if _is_collapsible(processed):
            new_children.extend(processed.get('children', []))
        else:
            new_children.append(processed)

    result = dict(node)
    result['children'] = new_children
    return result


# ---------------------------------------------------------------------------
# assign_refs
# ---------------------------------------------------------------------------

def assign_refs(tree):
    """Assign ref_1, ref_2, ... to interactive/semantic elements in pre-order DFS.

    Args:
        tree: Collapsed tree dict from collapse_containers.

    Returns:
        (annotated_tree, ref_cache) where annotated_tree has 'ref' fields
        and ref_cache maps ref_id → {role, name, bbox}.
    """
    ref_cache = {}
    counter = [0]  # mutable counter for closure

    def walk(node):
        result = dict(node)

        # Check if this node should get a ref
        if _should_get_ref(node):
            counter[0] += 1
            ref_id = f'ref_{counter[0]}'
            result['ref'] = ref_id
            entry = {
                'role': node['role'],
                'name': node.get('name', ''),
                'bbox': node.get('bbox', {}),
            }
            actions = node.get('actions', [])
            if actions:
                entry['actions'] = actions
            ref_cache[ref_id] = entry

        # Recurse into children
        result['children'] = [walk(c) for c in node.get('children', [])]
        return result

    annotated = walk(tree)
    return annotated, ref_cache


# ---------------------------------------------------------------------------
# format_tree
# ---------------------------------------------------------------------------

def format_tree(tree):
    """Format annotated tree as indented text.

    Format: role 'name' [ref_N] (non-default-states)
    2 spaces per indentation level.
    """
    lines = []
    _format_node(tree, 0, lines)
    return '\n'.join(lines)


def hash_subtree(tree):
    """Hash the semantic structure of an AT-SPI2 tree for click verification.

    Walks the tree in DFS order and hashes ``(role, name, sorted(states))``
    for every node. Intentionally excludes bounding boxes so a window move
    or scroll doesn't register as a state change — we care about whether
    the click changed role/name/state, not pixel positions. Used by the
    click sanity-check layer to detect silent no-op clicks: if the
    before-click and after-click hashes match, the click probably didn't
    land where the agent expected.

    Args:
        tree: Raw tree dict (walk_atspi output shape) or a collapsed tree.

    Returns:
        Hex SHA-256 digest of the serialized structure.
    """
    parts = []

    def walk(node):
        if not isinstance(node, dict):
            return
        role = node.get('role', '')
        name = node.get('name', '')
        states = node.get('states', []) or []
        state_str = ','.join(sorted(str(s) for s in states))
        parts.append(f'{role}|{name}|{state_str}')
        for child in node.get('children', []) or []:
            walk(child)

    walk(tree)
    serialized = '\n'.join(parts).encode('utf-8', errors='replace')
    return hashlib.sha256(serialized).hexdigest()


def _format_node(node, depth, lines):
    indent = '  ' * depth
    role = node['role']
    name = node.get('name', '')
    ref = node.get('ref', '')
    states = node.get('states', [])

    parts = [role]

    if name:
        parts.append(f"'{name}'")

    if ref:
        parts.append(f'[{ref}]')

    # Compute non-default states
    non_default = [s for s in states if s not in DEFAULT_STATES]

    # Show 'disabled' if 'sensitive' absent from interactive element
    if role in INTERACTIVE_ROLES and 'sensitive' not in states:
        non_default.append('disabled')

    if non_default:
        parts.append(f'({", ".join(non_default)})')

    lines.append(indent + ' '.join(parts))

    for child in node.get('children', []):
        _format_node(child, depth + 1, lines)
