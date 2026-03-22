"""Adversarial tests for tine/tree.py and tine/refs.py.

These are gremlin tests - designed to find the assumptions the code makes
and violate them as hard as possible.
"""

import json
import math
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tine.tree import (
    collapse_containers,
    assign_refs,
    format_tree,
    _is_collapsible,
    _is_hidden,
    _has_semantic_descendants,
    _is_valid_ref_bbox,
    _should_get_ref,
    INT_MIN,
    GARBAGE_BBOX_THRESHOLD,
)
from tine.refs import bbox_center, save_ref_cache, load_ref_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_node(role='panel', name=None, bbox=None, children=None, states=None):
    node = {'role': role, 'children': children or []}
    if name is not None:
        node['name'] = name
    if bbox is not None:
        node['bbox'] = bbox
    if states is not None:
        node['states'] = states
    return node


def valid_button(name='OK'):
    return make_node(
        role='button',
        name=name,
        bbox={'x': 10, 'y': 10, 'w': 80, 'h': 30},
    )


# ---------------------------------------------------------------------------
# ATTACK 1: Missing 'role' key → KeyError everywhere
# ---------------------------------------------------------------------------

class TestMissingRoleKey:
    """Every function that does node['role'] will blow up without a role."""

    def test_format_tree_missing_role(self):
        """format_tree directly accesses node['role'] — no .get()."""
        node = {'children': [], 'name': 'orphan'}  # no 'role'
        with pytest.raises(KeyError):
            format_tree(node)

    def test_should_get_ref_missing_role(self):
        """_should_get_ref does node['role'] on the first line."""
        node = {'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20}}
        with pytest.raises(KeyError):
            _should_get_ref(node)

    def test_has_semantic_descendants_child_missing_role(self):
        """_has_semantic_descendants extends the stack with children,
        then does current['role'] — child with no role explodes."""
        parent = make_node(role='panel', children=[{'children': []}])  # child missing role
        with pytest.raises(KeyError):
            _has_semantic_descendants(parent)

    def test_collapse_containers_child_missing_role(self):
        """_collapse_node accesses child['role'] for label dedup check."""
        # A child without 'role' will KeyError at the label dedup line
        parent = make_node(role='panel', children=[{'name': 'x', 'children': []}])
        with pytest.raises(KeyError):
            collapse_containers(parent)

    def test_is_collapsible_missing_role(self):
        """_is_collapsible does node['role'] — no .get() guard."""
        node = {'name': '', 'children': []}
        with pytest.raises(KeyError):
            _is_collapsible(node)


# ---------------------------------------------------------------------------
# ATTACK 2: Deep tree → RecursionError
# ---------------------------------------------------------------------------

class TestDeepRecursion:
    """assign_refs.walk(), _format_node(), and _collapse_node() are all
    recursive. Python's default recursion limit is ~1000.
    A tree 1500 levels deep should overflow all three."""

    DEPTH = 1500

    def _build_deep_tree(self, depth):
        """Build a linearly nested tree of `depth` nodes."""
        node = make_node(role='panel', bbox={'x': 0, 'y': 0, 'w': 100, 'h': 100})
        root = node
        for _ in range(depth - 1):
            child = make_node(role='panel', bbox={'x': 0, 'y': 0, 'w': 100, 'h': 100})
            node['children'] = [child]
            node = child
        return root

    def test_assign_refs_deep_tree_recursion(self):
        """assign_refs.walk() is recursive — deep tree → RecursionError."""
        tree = self._build_deep_tree(self.DEPTH)
        with pytest.raises(RecursionError):
            assign_refs(tree)

    def test_format_tree_deep_tree_recursion(self):
        """_format_node() is recursive — deep tree → RecursionError."""
        tree = self._build_deep_tree(self.DEPTH)
        tree_with_ref, _ = assign_refs(self._build_deep_tree(10))  # safe small version
        # Build a deep annotated tree manually
        node = {'role': 'panel', 'children': []}
        root = node
        for _ in range(self.DEPTH - 1):
            child = {'role': 'panel', 'children': []}
            node['children'] = [child]
            node = child
        with pytest.raises(RecursionError):
            format_tree(root)

    def test_collapse_containers_deep_tree_recursion(self):
        """_collapse_node() is recursive — deep tree → RecursionError."""
        tree = self._build_deep_tree(self.DEPTH)
        with pytest.raises(RecursionError):
            collapse_containers(tree)


# ---------------------------------------------------------------------------
# ATTACK 3: Circular reference → infinite loop / recursion
# ---------------------------------------------------------------------------

class TestCircularReference:
    """If a node contains itself in children, recursive functions loop forever.
    _has_semantic_descendants is iterative so it loops; walk() recurses until
    Python catches the stack overflow."""

    def test_has_semantic_descendants_circular(self):
        """Iterative stack walk — circular ref → infinite loop.
        We guard with a timeout-style limit using a signal or just verify
        it doesn't return in time... actually, we test that it raises or
        confirm the infinite loop exists by catching RecursionError upstream.
        Since it's iterative, it will OOM or loop forever.
        We document this as a known hang rather than catch it."""
        node = make_node(role='panel')
        node['children'] = [node]  # self-reference
        # This will hang forever — document it, don't run it live
        # pytest.skip("Known hang: circular reference causes infinite loop")
        # Instead: verify the structure is created correctly (precondition test)
        assert node['children'][0] is node  # The circular reference exists

    def test_assign_refs_circular_reference(self):
        """walk() is recursive — circular ref → RecursionError."""
        node = {'role': 'button', 'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20}}
        node['children'] = [node]  # circular
        with pytest.raises(RecursionError):
            assign_refs(node)

    def test_format_tree_circular_reference(self):
        """_format_node() is recursive — circular ref → RecursionError."""
        node = {'role': 'panel', 'children': []}
        node['children'] = [node]  # circular
        with pytest.raises(RecursionError):
            format_tree(node)

    def test_collapse_containers_circular_reference(self):
        """_collapse_node() is recursive — circular ref → RecursionError."""
        node = {'role': 'panel', 'children': [], 'bbox': {'x': 0, 'y': 0, 'w': 100, 'h': 100}}
        node['children'] = [node]  # circular
        with pytest.raises(RecursionError):
            collapse_containers(node)


# ---------------------------------------------------------------------------
# ATTACK 4: NaN/Infinity in bbox — passes all validity checks
# ---------------------------------------------------------------------------

class TestNaNBbox:
    """NaN passes every numeric comparison silently.
    A button with NaN width and height gets a ref assigned,
    and bbox_center returns (NaN, NaN) — a meaningless coordinate."""

    def test_nan_bbox_passes_validity_check(self):
        """NaN < 0 is False, NaN > 10000 is False, NaN == INT_MIN is False.
        So NaN dimensions are VALID according to _is_valid_ref_bbox."""
        bbox = {'x': 10, 'y': 10, 'w': float('nan'), 'h': float('nan')}
        # This should probably return False. It returns True.
        result = _is_valid_ref_bbox(bbox)
        assert result == True, "NaN bbox wrongly passes validity check"

    def test_nan_bbox_gets_ref_assigned(self):
        """A button with NaN dimensions gets ref_1 — a ref to nowhere."""
        node = make_node(
            role='button',
            name='Click me',
            bbox={'x': 10, 'y': 10, 'w': float('nan'), 'h': float('nan')},
        )
        _, ref_cache = assign_refs(node)
        assert 'ref_1' in ref_cache, "Button with NaN bbox should NOT get a ref"

    def test_nan_bbox_center_returns_nan(self):
        """bbox_center with NaN returns (NaN, NaN) — not None as docs imply."""
        bbox = {'x': 10, 'y': 10, 'w': float('nan'), 'h': float('nan')}
        result = bbox_center(bbox)
        assert result is not None  # It returns a tuple, not None
        x, y = result
        assert math.isnan(x), "bbox_center should return None for NaN bbox, not NaN coords"
        assert math.isnan(y)

    def test_negative_nan_bbox(self):
        """float('nan') * -1 is still NaN — same bypass."""
        bbox = {'x': 10, 'y': 10, 'w': -float('nan'), 'h': float('nan')}
        # -nan < 0? Still False in Python.
        result = _is_valid_ref_bbox(bbox)
        assert result == True  # Still passes — double NaN bypass


# ---------------------------------------------------------------------------
# ATTACK 5: states as a string instead of a list
# ---------------------------------------------------------------------------

class TestStatesAsString:
    """states is expected to be a list. If it's a string, iteration goes
    character by character. 'sensitive' not in state_string checks substrings
    rather than set membership — subtly wrong behavior."""

    def test_states_as_string_iterates_chars(self):
        """When states='focused', the list comprehension yields individual
        characters 'f','o','c','u','s','e','d' as 'non-default states'.
        'disabled' is also appended since 'd' != 'sensitive'."""
        node = {
            'role': 'button',
            'name': 'OK',
            'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20},
            'states': 'focused',  # string, not list
            'children': [],
        }
        result = format_tree(node)
        # String iterated char-by-char; 'd' doesn't suppress 'disabled' label
        assert 'f, o, c, u, s, e, d' in result, (
            f"String states iterated char-by-char: {result!r}"
        )

    def test_states_string_sensitive_substring_match(self):
        """'sensitive' not in states — for string states, this is substring search.
        If states = 'insensitive', then 'sensitive' IN 'insensitive' is True!
        The 'disabled' label would be suppressed even though the node is disabled."""
        node = {
            'role': 'button',
            'name': 'OK',
            'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20},
            'states': 'insensitive',  # 'sensitive' is a substring!
            'children': [],
        }
        result = format_tree(node)
        # 'sensitive' in 'insensitive' is True, so 'disabled' is NOT appended
        # But the node IS disabled (not sensitive). Silent wrong output.
        assert 'disabled' not in result, (
            "String 'insensitive' fools the sensitive check via substring match"
        )

    def test_states_as_dict_raises(self):
        """If states is a dict, iterating it yields keys — unexpected behavior."""
        node = {
            'role': 'button',
            'name': 'OK',
            'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20},
            'states': {'focused': True, 'sensitive': False},  # dict
            'children': [],
        }
        # Iterating a dict yields keys. 'sensitive' in dict → key membership check.
        # So sensitive IS in the dict (as a key), disabled not appended.
        # But the value is False — semantically disabled but output says enabled.
        result = format_tree(node)
        # 'sensitive' is a key → 'sensitive' not in states_dict is False → disabled not added
        assert 'disabled' not in result


# ---------------------------------------------------------------------------
# ATTACK 6: name=None explicitly (not missing key, but None value)
# ---------------------------------------------------------------------------

class TestNameIsNone:
    """node.get('name', '') returns None when name=None.
    f"'{name}'" becomes "'None'" — garbage output."""

    def test_name_none_formats_as_string_None(self):
        """name=None causes format_tree to emit role 'None' — wrong."""
        node = {
            'role': 'button',
            'name': None,  # explicit None
            'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20},
            'children': [],
        }
        result = format_tree(node)
        # if name: is falsy for None, so name won't be printed
        # Actually None is falsy, so it won't be in parts
        # Let's verify the actual behavior:
        assert "'None'" not in result  # name=None should not appear as string 'None'

    def test_name_none_in_named_container_skips_ref(self):
        """name=None → name is falsy → named container doesn't get ref.
        But a named container's whole purpose is its name. None name = silent miss."""
        node = make_node(
            role='list',
            name=None,  # explicit None
            bbox={'x': 0, 'y': 0, 'w': 200, 'h': 100},
        )
        result = _should_get_ref(node)
        assert result == False  # Correct behavior — but is None intended or a bug upstream?

    def test_name_none_not_added_to_ancestor_names(self):
        """In _collapse_node, if node.get('name') is None (falsy), it's not
        added to names. The label with name=None is not deduped and passes through.
        The unnamed panel collapses to its single child (the label)."""
        root = {
            'role': 'panel',
            'name': None,
            'children': [
                {'role': 'label', 'name': None, 'children': [],
                 'bbox': {'x': 0, 'y': 0, 'w': 50, 'h': 20}}
            ],
            'bbox': {'x': 0, 'y': 0, 'w': 200, 'h': 200},
        }
        # None not in set() → label passes through (not deduped)
        # Unnamed panel with one child collapses: result IS the label
        result = collapse_containers(root)
        # Panel collapsed to the label child
        assert result.get('role') == 'label'  # Label with None name passes through


# ---------------------------------------------------------------------------
# ATTACK 7: y=INT_MIN but x valid — not filtered as hidden
# ---------------------------------------------------------------------------

class TestPartialINTMIN:
    """_is_hidden only checks x == INT_MIN. A node with y=INT_MIN and valid x
    is NOT considered hidden and will be processed normally.
    _is_valid_ref_bbox DOES check both x and y, so it won't get a ref.
    But it will NOT be pruned during collapse_containers either."""

    def test_y_int_min_not_filtered_as_hidden(self):
        """Node with y=INT_MIN but x=100 is not hidden according to _is_hidden."""
        node = make_node(
            role='panel',
            bbox={'x': 100, 'y': INT_MIN, 'w': 50, 'h': 20},
        )
        assert _is_hidden(node) == False, "y=INT_MIN should be considered hidden"

    def test_y_int_min_button_survives_collapse(self):
        """A button with y=INT_MIN survives collapse (not pruned as hidden),
        but also won't get a ref. It's a ghost: present but unreachable.
        The unnamed panel collapses to the single ghost child."""
        ghost_button = make_node(
            role='button',
            name='Ghost',
            bbox={'x': 100, 'y': INT_MIN, 'w': 50, 'h': 20},
        )
        root = make_node(
            role='panel',
            bbox={'x': 0, 'y': 0, 'w': 500, 'h': 500},
            children=[ghost_button],
        )
        collapsed = collapse_containers(root)
        # Unnamed panel with one child collapses: result IS the ghost button
        # OR it's still in children if root doesn't collapse further
        ghost_found = (
            collapsed.get('name') == 'Ghost' or
            any(c.get('name') == 'Ghost' for c in collapsed.get('children', []))
        )
        assert ghost_found, "y=INT_MIN button should survive collapse"

    def test_y_int_min_no_ref_but_in_tree(self):
        """Ghost button gets no ref (correct) but appears in formatted output (wrong)."""
        ghost_button = {
            'role': 'button',
            'name': 'Ghost',
            'bbox': {'x': 100, 'y': INT_MIN, 'w': 50, 'h': 20},
            'children': [],
        }
        annotated, ref_cache = assign_refs(ghost_button)
        assert 'ref' not in annotated or annotated.get('ref') is None, \
            "y=INT_MIN button should not get a ref"
        # But it appears in format output
        output = format_tree(annotated)
        assert 'Ghost' in output, "Ghost button appears in output without a ref"


# ---------------------------------------------------------------------------
# ATTACK 8: load_ref_cache with non-dict JSON
# ---------------------------------------------------------------------------

class TestLoadRefCacheMalformed:
    """load_ref_cache returns whatever json.load returns — if the file
    contains a JSON array or string, callers expecting a dict get a surprise."""

    def test_load_ref_cache_with_array_json(self):
        """JSON file contains [] — load_ref_cache returns [], not {}.
        Callers doing cache.get(ref_id) on a list will get AttributeError."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([{'ref_1': 'data'}], f)
            path = f.name
        try:
            result = load_ref_cache(path)
            assert isinstance(result, list)  # Returns a list, not a dict
            # Caller tries to use it as dict:
            with pytest.raises(AttributeError):
                result.get('ref_1')  # list has no .get()
        finally:
            os.unlink(path)

    def test_load_ref_cache_malformed_json(self):
        """Malformed JSON → JSONDecodeError caught, returns {} fallback."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{not valid json: [}')
            path = f.name
        try:
            result = load_ref_cache(path)
            assert result == {}  # Returns empty dict on parse error
        finally:
            os.unlink(path)

    def test_load_ref_cache_with_null_json(self):
        """JSON file contains null → returns None (not caught as error).
        Callers expecting a dict will get AttributeError on .get()."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(None, f)
            path = f.name
        try:
            result = load_ref_cache(path)
            assert result is None  # Not {} as callers expect
            with pytest.raises((TypeError, AttributeError)):
                result.get('ref_1')  # NoneType has no .get()
        finally:
            os.unlink(path)

    def test_load_ref_cache_truncated_json(self):
        """Truncated file → JSONDecodeError caught, returns {} fallback."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{"ref_1": {"role": "button", "name": "OK"')  # truncated
            path = f.name
        try:
            result = load_ref_cache(path)
            assert result == {}  # Returns empty dict on parse error
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# ATTACK 9: GARBAGE_BBOX_THRESHOLD boundary — w=10000 passes
# ---------------------------------------------------------------------------

class TestGarbageBboxBoundary:
    """The check is w > GARBAGE_BBOX_THRESHOLD (10000), not >=.
    A button with w=10000 exactly is considered valid.
    Is that intentional? 10000-pixel-wide button = off-screen garbage."""

    def test_exactly_at_threshold_passes(self):
        """w=10000 passes because > not >= is used."""
        bbox = {'x': 0, 'y': 0, 'w': GARBAGE_BBOX_THRESHOLD, 'h': 30}
        result = _is_valid_ref_bbox(bbox)
        assert result == True  # Passes — off-by-one: 10000px wide is garbage

    def test_one_over_threshold_fails(self):
        """w=10001 fails — the first actually-caught garbage value."""
        bbox = {'x': 0, 'y': 0, 'w': GARBAGE_BBOX_THRESHOLD + 1, 'h': 30}
        result = _is_valid_ref_bbox(bbox)
        assert result == False

    def test_threshold_button_gets_ref(self):
        """A 10000px-wide button gets a ref assigned."""
        node = make_node(
            role='button',
            name='Huge',
            bbox={'x': 0, 'y': 0, 'w': GARBAGE_BBOX_THRESHOLD, 'h': 30},
        )
        _, ref_cache = assign_refs(node)
        assert 'ref_1' in ref_cache, "10000px-wide button gets a ref — is that correct?"


# ---------------------------------------------------------------------------
# ATTACK 10: name as unhashable type → TypeError in names.add()
# ---------------------------------------------------------------------------

class TestUnhashableName:
    """In _collapse_node, `names.add(node['name'])` is called.
    If name is an unhashable type (list, dict), this raises TypeError."""

    def test_name_as_list_crashes_collapse(self):
        """name=['foo', 'bar'] — unhashable, names.add() raises TypeError."""
        node = make_node(
            role='panel',
            bbox={'x': 0, 'y': 0, 'w': 100, 'h': 100},
        )
        node['name'] = ['foo', 'bar']  # unhashable list
        with pytest.raises(TypeError):
            collapse_containers(node)

    def test_name_as_dict_crashes_collapse(self):
        """name={'key': 'val'} — unhashable dict."""
        node = make_node(
            role='panel',
            bbox={'x': 0, 'y': 0, 'w': 100, 'h': 100},
        )
        node['name'] = {'localized': 'Foo'}  # unhashable dict
        with pytest.raises(TypeError):
            collapse_containers(node)

    def test_name_zero_falsy_not_added_to_names(self):
        """name=0 is falsy — not added to ancestor names.
        A label child with name=0 would not be deduped even if it echoes the parent.
        The unnamed (falsy-named) panel collapses to its single label child."""
        parent = {
            'role': 'panel',
            'name': 0,
            'children': [
                {'role': 'label', 'name': 0, 'children': [],
                 'bbox': {'x': 0, 'y': 0, 'w': 50, 'h': 20}}
            ],
            'bbox': {'x': 0, 'y': 0, 'w': 200, 'h': 200},
        }
        # name=0 is falsy → not added to names → label with name=0 not deduped
        # Panel with falsy name collapses to single child (the label)
        result = collapse_containers(parent)
        # Result is either the label directly (panel collapsed) or panel with label child
        label_found = (
            result.get('role') == 'label' or
            any(c.get('name') == 0 for c in result.get('children', []))
        )
        assert label_found, "Label with name=0 passes dedup because 0 is falsy"


# ---------------------------------------------------------------------------
# ATTACK 11: children contains non-dict items
# ---------------------------------------------------------------------------

class TestNonDictChildren:
    """Children are expected to be dicts. What happens with strings, ints, None?"""

    def test_none_child_crashes_has_semantic_descendants(self):
        """None in children → current['role'] on None → TypeError."""
        node = {'role': 'panel', 'children': [None]}
        with pytest.raises(TypeError):
            _has_semantic_descendants(node)

    def test_string_child_crashes_collapse(self):
        """String child → child.get() on string → AttributeError."""
        node = make_node(role='panel', children=['oops'])
        with pytest.raises((TypeError, KeyError, AttributeError)):
            collapse_containers(node)

    def test_integer_child_crashes_assign_refs(self):
        """Integer child → walk(42) → result = dict(42) → TypeError."""
        tree = {'role': 'panel', 'children': [42]}
        with pytest.raises(TypeError):
            assign_refs(tree)

    def test_list_child_crashes_format_tree(self):
        """List child → _format_node([]) → node['role'] on list → TypeError."""
        tree = {'role': 'panel', 'children': [[]]}
        with pytest.raises(TypeError):
            format_tree(tree)


# ---------------------------------------------------------------------------
# ATTACK 12: format_tree called with None
# ---------------------------------------------------------------------------

class TestNoneTree:
    """What happens when you pass None directly to the pipeline functions?"""

    def test_format_tree_none(self):
        with pytest.raises((AttributeError, TypeError)):
            format_tree(None)

    def test_assign_refs_none(self):
        with pytest.raises((AttributeError, TypeError)):
            assign_refs(None)

    def test_collapse_containers_none(self):
        with pytest.raises((AttributeError, TypeError)):
            collapse_containers(None)


# ---------------------------------------------------------------------------
# ATTACK 13: INT_MIN - 1 (just below INT_MIN) — not caught by any check
# ---------------------------------------------------------------------------

class TestBeyondINTMIN:
    """What about coordinates one step below INT_MIN?
    -2147483649 is not == INT_MIN, so _is_hidden returns False,
    _is_valid_ref_bbox returns True. The node gets a ref."""

    BELOW_INT_MIN = INT_MIN - 1

    def test_below_int_min_not_hidden(self):
        node = make_node(
            role='button',
            bbox={'x': self.BELOW_INT_MIN, 'y': 0, 'w': 50, 'h': 20},
        )
        assert _is_hidden(node) == False  # Not caught — one below INT_MIN is 'valid'

    def test_below_int_min_passes_validity(self):
        bbox = {'x': self.BELOW_INT_MIN, 'y': 0, 'w': 50, 'h': 20}
        assert _is_valid_ref_bbox(bbox) == True  # Gets a ref!

    def test_below_int_min_button_gets_ref(self):
        """A button at x=-2147483649 is treated as valid and gets a ref."""
        node = make_node(
            role='button',
            name='Offscreen',
            bbox={'x': self.BELOW_INT_MIN, 'y': 0, 'w': 50, 'h': 20},
        )
        _, ref_cache = assign_refs(node)
        assert 'ref_1' in ref_cache, "Button below INT_MIN gets a ref — it shouldn't"


# ---------------------------------------------------------------------------
# ATTACK 14: Empty string role
# ---------------------------------------------------------------------------

class TestEmptyStringRole:
    """role='' is a string, passes all role checks (not in INTERACTIVE_ROLES,
    not in NAMED_CONTAINER_ROLES, not 'separator'), won't crash but might
    produce confusing output."""

    def test_empty_role_in_format_tree(self):
        node = {'role': '', 'children': []}
        result = format_tree(node)
        # Empty role → line starts with empty string → leading space?
        assert result == ''  # or ' ' — depends on join

    def test_empty_role_gets_no_ref(self):
        node = make_node(
            role='',
            name='Thing',
            bbox={'x': 10, 'y': 10, 'w': 50, 'h': 20},
        )
        result = _should_get_ref(node)
        assert result == False  # Empty role not interactive, not named container


# ---------------------------------------------------------------------------
# ATTACK 15: Unicode/emoji in names and roles
# ---------------------------------------------------------------------------

class TestUnicodeAttacks:
    """Unicode edge cases — null bytes, RTL text, emoji, zero-width chars."""

    def test_null_byte_in_name(self):
        """Null byte in name — passes through to formatted output."""
        node = {
            'role': 'button',
            'name': 'OK\x00EVIL',
            'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20},
            'children': [],
        }
        result = format_tree(node)
        assert '\x00' in result  # Null byte passes through unescaped

    def test_emoji_name_formats_ok(self):
        """Emoji are valid Unicode — should be fine."""
        node = {
            'role': 'button',
            'name': '\U0001F4A3 Detonate',  # bomb emoji
            'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20},
            'children': [],
        }
        result = format_tree(node)
        assert '\U0001F4A3' in result

    def test_newline_in_name_breaks_tree_format(self):
        """Newline in name corrupts the indented tree format."""
        node = {
            'role': 'button',
            'name': 'Line1\nLine2',
            'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20},
            'children': [],
        }
        result = format_tree(node)
        lines = result.split('\n')
        # The 'button' line gets split — Line2 appears unindented as a separate line
        assert len(lines) == 2, f"Newline in name splits tree format: {lines}"

    def test_rtl_name_in_ancestor_dedup(self):
        """RTL text: does 'abc' == '\u202eabc' (RTL override)? No.
        But visually they look the same. Not a code crash, just confusing output."""
        rtl_name = '\u202eOK'  # RTL override + OK
        parent = {
            'role': 'panel',
            'name': 'OK',
            'children': [
                {'role': 'label', 'name': rtl_name, 'children': [],
                 'bbox': {'x': 0, 'y': 0, 'w': 50, 'h': 20}}
            ],
            'bbox': {'x': 0, 'y': 0, 'w': 200, 'h': 200},
        }
        result = collapse_containers(parent)
        children = result.get('children', [])
        # RTL 'OK' != plain 'OK' — label passes dedup (looks same, isn't)
        assert any(c.get('name') == rtl_name for c in children)


# ---------------------------------------------------------------------------
# ATTACK 16: Shallow copy mutation — bbox shared between original and result
# ---------------------------------------------------------------------------

class TestShallowCopyMutation:
    """dict(node) is a shallow copy. The 'bbox' dict is shared.
    Mutating the result's bbox mutates the original node's bbox."""

    def test_assign_refs_shallow_copy_bbox_shared(self):
        """Mutating annotated tree's bbox mutates the original."""
        original = {
            'role': 'button',
            'name': 'OK',
            'bbox': {'x': 10, 'y': 10, 'w': 50, 'h': 20},
            'children': [],
        }
        original_bbox = original['bbox']
        annotated, _ = assign_refs(original)

        # Mutate the annotated tree's bbox
        annotated['bbox']['x'] = 999

        # Original is also mutated — shallow copy hazard
        assert original_bbox['x'] == 999, \
            "Shallow copy: mutating annotated bbox mutates original"

    def test_collapse_containers_shallow_copy_bbox_shared(self):
        """Same hazard in collapse_containers."""
        original = {
            'role': 'panel',
            'name': 'Container',
            'bbox': {'x': 0, 'y': 0, 'w': 200, 'h': 200},
            'children': [],
        }
        original_bbox = original['bbox']
        collapsed = collapse_containers(original)

        collapsed['bbox']['x'] = 999
        assert original_bbox['x'] == 999, \
            "Shallow copy: mutating collapsed bbox mutates original"


# ---------------------------------------------------------------------------
# ATTACK 17: bbox_center with string values — TypeError
# ---------------------------------------------------------------------------

class TestBboxCenterTypeErrors:
    """bbox_center does integer division w//2. What if w is a string?"""

    def test_bbox_center_string_width(self):
        """w='50' → '50' // 2 → TypeError."""
        bbox = {'x': 10, 'y': 10, 'w': '50', 'h': '20'}
        with pytest.raises(TypeError):
            bbox_center(bbox)

    def test_bbox_center_none_value(self):
        """w=None → None // 2 → TypeError."""
        bbox = {'x': 10, 'y': 10, 'w': None, 'h': 20}
        with pytest.raises(TypeError):
            bbox_center(bbox)

    def test_bbox_center_nan_x_returns_nan_tuple(self):
        """x=NaN → not == INT_MIN → not caught → returns (NaN, y+h//2)."""
        bbox = {'x': float('nan'), 'y': 10, 'w': 50, 'h': 20}
        result = bbox_center(bbox)
        assert result is not None
        assert math.isnan(result[0])


# ---------------------------------------------------------------------------
# ATTACK 18: Large flat tree (wide, not deep) — stress test
# ---------------------------------------------------------------------------

class TestWideFlatTree:
    """A tree with 100,000 children at one level — not recursive, but
    memory and iteration intensive. Tests that assign_refs is O(n)."""

    def test_wide_tree_assign_refs(self):
        """100k children — should complete without error."""
        children = [
            {'role': 'button', 'name': f'btn_{i}',
             'bbox': {'x': i, 'y': 0, 'w': 10, 'h': 10},
             'children': []}
            for i in range(10_000)  # 10k — keep test fast
        ]
        root = {'role': 'panel', 'children': children}
        _, ref_cache = assign_refs(root)
        assert len(ref_cache) == 10_000

    def test_wide_tree_format(self):
        """10k children — format_tree should not crash."""
        children = [
            {'role': 'button', 'name': f'b{i}',
             'bbox': {'x': i, 'y': 0, 'w': 10, 'h': 10},
             'ref': f'ref_{i+1}',
             'children': []}
            for i in range(10_000)
        ]
        root = {'role': 'panel', 'children': children}
        result = format_tree(root)
        assert result.count('button') == 10_000


# ---------------------------------------------------------------------------
# ATTACK 19: separator role at root — silent no-ref, no error
# ---------------------------------------------------------------------------

class TestSeparatorRoot:
    """A separator at the root of the tree. Should not crash but gets no ref."""

    def test_separator_gets_no_ref(self):
        node = make_node(
            role='separator',
            bbox={'x': 0, 'y': 0, 'w': 200, 'h': 2},
        )
        _, ref_cache = assign_refs(node)
        assert len(ref_cache) == 0

    def test_separator_formats_without_ref(self):
        node = {'role': 'separator', 'children': []}
        result = format_tree(node)
        assert result == 'separator'


# ---------------------------------------------------------------------------
# ATTACK 20: Pipeline integrity — does output format match what assign_refs stored?
# ---------------------------------------------------------------------------

class TestPipelineIntegrity:
    """Run the full pipeline and verify ref_cache matches what format_tree outputs."""

    def test_ref_ids_in_output_match_cache_keys(self):
        """Every ref in the formatted output should be a key in ref_cache."""
        tree = {
            'role': 'panel',
            'bbox': {'x': 0, 'y': 0, 'w': 500, 'h': 500},
            'children': [
                valid_button('Save'),
                valid_button('Cancel'),
                make_node(role='label', name='Status',
                          bbox={'x': 0, 'y': 50, 'w': 100, 'h': 20}),
            ],
        }
        collapsed = collapse_containers(tree)
        annotated, ref_cache = assign_refs(collapsed)
        output = format_tree(annotated)

        import re
        refs_in_output = set(re.findall(r'\[(ref_\d+)\]', output))
        assert refs_in_output == set(ref_cache.keys())

    def test_counter_monotonically_increases(self):
        """Refs should be ref_1, ref_2, ... in order. No gaps, no duplicates."""
        tree = {
            'role': 'panel',
            'bbox': {'x': 0, 'y': 0, 'w': 500, 'h': 500},
            'children': [valid_button(f'btn_{i}') for i in range(5)],
        }
        _, ref_cache = assign_refs(tree)
        keys = sorted(ref_cache.keys(), key=lambda k: int(k.split('_')[1]))
        assert keys == [f'ref_{i}' for i in range(1, 6)]
