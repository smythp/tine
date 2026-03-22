"""Tests for tine tree walker: collapsing, ref assignment, formatting.

These tests define the contract for RSP A. They use real AT-SPI2 tree
dumps from archgnome as fixtures, so they test against actual GTK4/libadwaita,
VTE/GTK3, and gnome-shell tree structures.

The processing pipeline is:
    raw tree dict → collapse_containers → assign_refs → format_tree → text output

Role name convention: AT-SPI2 uses space-separated names like 'push button',
'toggle button', 'check box'. The fixtures use these names. Synthetic test
nodes must use the same convention.
"""

import json
import os
import pytest

# Fixture loading
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'research', 'fixtures')


def load_fixture(name):
    path = os.path.join(FIXTURE_DIR, name)
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def settings_tree():
    return load_fixture('tree_settings.json')


@pytest.fixture
def terminal_tree():
    return load_fixture('tree_terminal.json')


@pytest.fixture
def nautilus_tree():
    return load_fixture('tree_nautilus.json')


@pytest.fixture
def gnome_shell_tree():
    return load_fixture('tree_gnome_shell.json')


# ---------------------------------------------------------------------------
# Helpers for building small synthetic trees (for targeted unit tests)
# ---------------------------------------------------------------------------

def make_node(role, name='', children=None, bbox=None, states=None, actions=None):
    """Build a synthetic tree node matching the fixture JSON schema.

    Role names must use AT-SPI2 convention: space-separated like 'push button',
    'toggle button', 'check box', 'menu item', etc.

    bbox defaults to {x:0, y:0, w:0, h:0} meaning "no bbox data available."
    The implementation must NOT filter nodes with zero-area bboxes — these are
    common for elements where AT-SPI2 didn't report geometry. Only filter on
    INT_MIN coords or absurdly large dimensions (rendering artifacts).
    """
    return {
        'role': role,
        'name': name,
        'bbox': bbox or {'x': 0, 'y': 0, 'w': 0, 'h': 0},
        'states': states or [],
        'actions': actions or [],
        'children': children or [],
        'child_index': 0,
    }


# ===========================================================================
# 1. Container Collapsing
# ===========================================================================

class TestContainerCollapsing:
    """Unnamed panel/grouping/filler nodes that are pure layout wrappers
    should be collapsed, promoting their children."""

    def test_single_unnamed_panel_collapsed(self):
        """panel '' with one child -> child promoted."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'Settings', children=[
            make_node('panel', '', children=[
                make_node('button', 'Wi-Fi'),
            ]),
        ])
        result = collapse_containers(tree)
        assert result['role'] == 'frame'
        assert len(result['children']) == 1
        assert result['children'][0]['role'] == 'button'

    def test_chain_of_unnamed_panels_collapsed(self):
        """Multiple nested unnamed panels -> all collapsed."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'Settings', children=[
            make_node('panel', '', children=[
                make_node('panel', '', children=[
                    make_node('panel', '', children=[
                        make_node('button', 'Wi-Fi'),
                    ]),
                ]),
            ]),
        ])
        result = collapse_containers(tree)
        assert result['role'] == 'frame'
        assert len(result['children']) == 1
        assert result['children'][0]['role'] == 'button'

    def test_unnamed_grouping_collapsed(self):
        """grouping '' -> collapsed like unnamed panel."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'Settings', children=[
            make_node('grouping', '', children=[
                make_node('list', 'Settings categories'),
            ]),
        ])
        result = collapse_containers(tree)
        assert result['children'][0]['role'] == 'list'
        assert result['children'][0]['name'] == 'Settings categories'

    def test_unnamed_filler_collapsed(self):
        """filler '' -> collapsed."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'Window', children=[
            make_node('filler', '', children=[
                make_node('button', 'Close'),
            ]),
        ])
        result = collapse_containers(tree)
        assert result['children'][0]['role'] == 'button'

    def test_named_panel_preserved(self):
        """panel 'Sidebar' -> kept (carries semantic meaning)."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'Settings', children=[
            make_node('panel', 'Sidebar', children=[
                make_node('button', 'Wi-Fi'),
            ]),
        ])
        result = collapse_containers(tree)
        assert result['children'][0]['role'] == 'panel'
        assert result['children'][0]['name'] == 'Sidebar'

    def test_named_grouping_preserved(self):
        """grouping 'Settings' -> kept."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'App', children=[
            make_node('grouping', 'Settings', children=[
                make_node('list', 'Items'),
            ]),
        ])
        result = collapse_containers(tree)
        assert result['children'][0]['role'] == 'grouping'
        assert result['children'][0]['name'] == 'Settings'

    def test_unnamed_panel_multiple_children_promoted(self):
        """Unnamed panel with multiple children -> children promoted."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'App', children=[
            make_node('panel', '', children=[
                make_node('button', 'OK'),
                make_node('button', 'Cancel'),
            ]),
        ])
        result = collapse_containers(tree)
        assert len(result['children']) == 2
        assert result['children'][0]['role'] == 'button'
        assert result['children'][1]['role'] == 'button'

    def test_scroll_pane_preserved(self):
        """scroll pane -> always kept (viewport context matters)."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'App', children=[
            make_node('scroll pane', '', children=[
                make_node('list', 'Items'),
            ]),
        ])
        result = collapse_containers(tree)
        assert result['children'][0]['role'] == 'scroll pane'

    def test_settings_fixture_deep_collapse(self, settings_tree):
        """Real Settings tree: 9+ wrapper layers should collapse down
        so that 'list Settings categories' is reachable within a few levels."""
        from tine.tree import collapse_containers
        result = collapse_containers(settings_tree)

        def find_node(node, target_role, target_name):
            if node['role'] == target_role and node['name'] == target_name:
                return node, 0
            for child in node.get('children', []):
                found = find_node(child, target_role, target_name)
                if found:
                    return found[0], found[1] + 1
            return None

        found = find_node(result, 'list', 'Settings categories')
        assert found is not None, "Settings categories list not found after collapsing"
        node, depth = found
        # In the raw tree it's at depth ~14. After collapsing should be <= 5.
        assert depth <= 5, f"Settings categories at depth {depth}, expected <= 5"

    def test_terminal_fixture_filler_collapse(self, terminal_tree):
        """Real Terminal tree: filler chains should collapse so buttons
        like Close, Find, Menu are accessible near the top."""
        from tine.tree import collapse_containers
        result = collapse_containers(terminal_tree)

        def collect_buttons(node):
            buttons = []
            if node['role'] in ('button', 'toggle button'):
                buttons.append(node['name'])
            for child in node.get('children', []):
                buttons.extend(collect_buttons(child))
            return buttons

        buttons = collect_buttons(result)
        assert 'Close' in buttons
        assert 'Find' in buttons

    def test_gnome_shell_int_min_subtrees_pruned(self, gnome_shell_tree):
        """gnome-shell has deep chains of unnamed panels at INT_MIN with
        negative dimensions. No element with INT_MIN coords and negative/zero
        dimensions should get a ref — the filter is geometry-based."""
        from tine.tree import collapse_containers, assign_refs
        collapsed = collapse_containers(gnome_shell_tree)
        _, ref_cache = assign_refs(collapsed)
        INT_MIN = -2147483648
        for info in ref_cache.values():
            bbox = info['bbox']
            if bbox['x'] == INT_MIN:
                assert bbox['w'] > 0, (
                    f"Ref assigned to INT_MIN element with non-positive width: "
                    f"{info['role']} '{info['name']}' bbox={bbox}"
                )

    def test_gnome_shell_named_panels_preserved(self, gnome_shell_tree):
        """Named panels like 'Window group', 'Overview' carry meaning."""
        from tine.tree import collapse_containers
        result = collapse_containers(gnome_shell_tree)

        def has_named_panel(node, name):
            if node['role'] == 'panel' and node['name'] == name:
                return True
            return any(has_named_panel(c, name) for c in node.get('children', []))

        assert has_named_panel(result, 'Overview')

    def test_gnome_shell_fixture_smoke(self, gnome_shell_tree):
        """gnome-shell tree is the largest/most complex.
        Has deep INT_MIN chains, negative-dimension bboxes, and named panels.
        A regression that over-filters or under-filters would show in the bounds."""
        from tine.tree import collapse_containers, assign_refs, format_tree
        collapsed = collapse_containers(gnome_shell_tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)
        output = format_tree(tree_with_refs)
        # gnome-shell has app grid buttons, overview elements, etc
        assert len(ref_cache) >= 5, f"Too few refs from gnome-shell: {len(ref_cache)}"
        assert len(ref_cache) <= 200, f"Too many refs (under-filtering): {len(ref_cache)}"
        lines = [l for l in output.strip().split('\n') if l.strip()]
        assert len(lines) >= 5, f"Output too sparse: {len(lines)} lines"


# ===========================================================================
# 2. Label Echo Deduplication
# ===========================================================================

class TestLabelEchoDedup:
    """When a button contains a label with the same name, the label
    should be removed (it's redundant)."""

    def test_button_with_echo_label(self):
        """button 'Wi-Fi' > panel > label 'Wi-Fi' -> label removed."""
        from tine.tree import collapse_containers
        tree = make_node('button', 'Wi-Fi', children=[
            make_node('panel', '', children=[
                make_node('label', 'Wi-Fi'),
            ]),
        ])
        result = collapse_containers(tree)
        assert result['role'] == 'button'
        assert result['name'] == 'Wi-Fi'
        # No children (label was echo, panel was unnamed wrapper)
        assert len(result.get('children', [])) == 0

    def test_button_with_different_label_kept(self):
        """button 'Submit' > label 'Send Form' -> label kept (different text)."""
        from tine.tree import collapse_containers
        tree = make_node('button', 'Submit', children=[
            make_node('label', 'Send Form'),
        ])
        result = collapse_containers(tree)
        assert len(result['children']) == 1
        assert result['children'][0]['name'] == 'Send Form'

    def test_echo_label_behind_named_wrapper(self):
        """button 'Wi-Fi' > panel 'content' > label 'Wi-Fi'
        Named panel survives collapsing, but the echo label inside
        should still be removed since it echoes the ancestor button.

        This means echo dedup must walk up through the ancestor chain,
        not just check the immediate parent. A label echoes if ANY
        ancestor with a matching name exists."""
        from tine.tree import collapse_containers
        tree = make_node('button', 'Wi-Fi', children=[
            make_node('panel', 'content', children=[
                make_node('label', 'Wi-Fi'),
            ]),
        ])
        result = collapse_containers(tree)
        # The named panel may or may not survive, but the echo label
        # should not appear in the final output

        def has_echo_label(node, parent_name):
            if node['role'] == 'label' and node['name'] == parent_name:
                return True
            return any(has_echo_label(c, parent_name) for c in node.get('children', []))

        assert not has_echo_label(result, 'Wi-Fi')

    def test_settings_fixture_no_echo_labels(self, settings_tree):
        """Real Settings tree: after collapsing, buttons should not have
        echo label children."""
        from tine.tree import collapse_containers
        result = collapse_containers(settings_tree)

        def check_no_echo(node):
            if node['role'] in ('button', 'toggle button') and node['name']:
                for child in node.get('children', []):
                    if child['role'] == 'label' and child['name'] == node['name']:
                        return False, f"Echo label '{child['name']}' in {node['role']} '{node['name']}'"
            for child in node.get('children', []):
                ok, msg = check_no_echo(child)
                if not ok:
                    return ok, msg
            return True, ''

        ok, msg = check_no_echo(result)
        assert ok, msg


# ===========================================================================
# 3. Ref Assignment
# ===========================================================================

class TestRefAssignment:
    """Refs should be assigned to interactive and semantically meaningful
    elements. Unnamed wrappers and separators get no refs."""

    def test_button_gets_ref(self):
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'OK', bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)

        assert len(ref_cache) >= 1
        button_ref = None
        for ref_id, info in ref_cache.items():
            if info['name'] == 'OK':
                button_ref = ref_id
                break
        assert button_ref is not None, "Button 'OK' should have a ref"

    def test_entry_gets_ref(self):
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('text', 'Search', bbox={'x': 10, 'y': 20, 'w': 200, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)
        names = [info['name'] for info in ref_cache.values()]
        assert 'Search' in names

    def test_separator_no_ref(self):
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('separator', ''),
                make_node('button', 'OK'),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)
        roles = [info['role'] for info in ref_cache.values()]
        assert 'separator' not in roles

    def test_unnamed_wrapper_no_ref(self):
        """After collapsing, any remaining unnamed structural nodes
        should not get refs."""
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('scroll pane', '', children=[
                    make_node('button', 'Click'),
                ]),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)
        for info in ref_cache.values():
            assert info['name'] != '' or info['role'] not in ('panel', 'filler', 'grouping')

    def test_named_list_gets_ref(self):
        """Named containers like 'Settings categories' list get refs."""
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('list', 'Settings categories',
                          bbox={'x': 0, 'y': 0, 'w': 245, 'h': 600},
                          children=[
                    make_node('button', 'Wi-Fi', bbox={'x': 0, 'y': 0, 'w': 233, 'h': 43}),
                ]),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)
        names = [info['name'] for info in ref_cache.values()]
        assert 'Settings categories' in names

    def test_refs_are_sequential(self):
        """Refs should be ref_1, ref_2, ref_3 etc."""
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'A', bbox={'x': 10, 'y': 10, 'w': 80, 'h': 30}),
                make_node('button', 'B', bbox={'x': 10, 'y': 50, 'w': 80, 'h': 30}),
                make_node('button', 'C', bbox={'x': 10, 'y': 90, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)
        ref_ids = sorted(ref_cache.keys())
        assert ref_ids[0] == 'ref_1'
        assert ref_ids[-1] == f'ref_{len(ref_ids)}'

    def test_duplicate_named_elements_get_distinct_refs(self):
        """Multiple elements with the same name (e.g. two 'Close' buttons in
        different windows) must get separate refs. Keying the cache on name
        alone would silently drop one."""
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'Close', bbox={'x': 10, 'y': 10, 'w': 30, 'h': 30}),
                make_node('button', 'Close', bbox={'x': 200, 'y': 10, 'w': 30, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)
        close_refs = [rid for rid, info in ref_cache.items() if info['name'] == 'Close']
        assert len(close_refs) == 2, f"Expected 2 refs for 'Close', got {len(close_refs)}"
        assert close_refs[0] != close_refs[1]

    def test_unnamed_interactive_element_gets_ref(self):
        """button '' (icon button without accessible name) should still get
        a ref — the agent can click it by position. Common in toolbars."""
        from tine.tree import collapse_containers, assign_refs, format_tree
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', '', bbox={'x': 10, 'y': 10, 'w': 30, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)
        assert len(ref_cache) >= 1, "Unnamed button should still get a ref"
        output = format_tree(tree_with_refs)
        assert '[ref_' in output

    def test_garbage_bbox_no_ref(self):
        """Elements with absurdly large bboxes (GTK4 rendering artifacts)
        should not get refs — clicking them would target nonsense coords."""
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('panel', 'Garbage', bbox={'x': 0, 'y': 0, 'w': 1401416896, 'h': 32509}),
                make_node('button', 'OK', bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)
        for info in ref_cache.values():
            assert info['bbox']['w'] < 10000, f"Garbage bbox assigned ref: {info}"

    def test_assign_refs_annotates_tree(self):
        """assign_refs should add a 'ref' field to nodes that get refs."""
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'OK', bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)

        def find_ref_on_node(node, target_name):
            if node.get('name') == target_name:
                return node.get('ref')
            for c in node.get('children', []):
                r = find_ref_on_node(c, target_name)
                if r:
                    return r
            return None

        assert find_ref_on_node(tree_with_refs, 'OK') is not None

    def test_all_roles_that_get_refs(self):
        """Every interactive role should get a ref.
        The implementation must use a broad inclusive list — not just
        roles observed in our test fixtures."""
        from tine.tree import collapse_containers, assign_refs
        # AT-SPI2 space-separated role names.
        # All of these MUST get refs — the list is intentionally broad.
        interactive_roles = [
            # Confirmed in archgnome fixtures
            'button', 'toggle button', 'check box', 'check menu item',
            'menu item', 'text', 'terminal', 'table cell', 'list item',
            'menu',
            # Standard AT-SPI2 roles expected in other apps (not in current fixtures)
            'radio button', 'slider', 'combo box', 'link', 'entry',
            'spin button',
        ]
        children = [
            make_node(role, f'Element_{i}', bbox={'x': 0, 'y': i * 30, 'w': 100, 'h': 25})
            for i, role in enumerate(interactive_roles)
        ]
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=children),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)
        ref_roles = {info['role'] for info in ref_cache.values()}
        for role in interactive_roles:
            assert role in ref_roles, f"Interactive role '{role}' should get a ref"

    def test_settings_fixture_ref_count(self, settings_tree):
        """Real Settings tree should produce a reasonable number of refs.
        Raw tree has 162+ elements. After collapsing, interactive elements
        (buttons, lists, etc) should get refs — roughly 20-50."""
        from tine.tree import collapse_containers, assign_refs
        collapsed = collapse_containers(settings_tree)
        _, ref_cache = assign_refs(collapsed)
        assert len(ref_cache) >= 15, f"Too few refs: {len(ref_cache)}"
        assert len(ref_cache) <= 80, f"Too many refs: {len(ref_cache)}"


# ===========================================================================
# 4. Ref Lookup
# ===========================================================================

class TestRefLookup:
    """Given a ref ID, should be able to look up role, name, bbox."""

    def test_lookup_returns_correct_info(self):
        from tine.tree import collapse_containers, assign_refs
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'OK', bbox={'x': 100, 'y': 200, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)

        ok_ref = None
        for ref_id, info in ref_cache.items():
            if info['name'] == 'OK':
                ok_ref = ref_id
                break

        info = ref_cache[ok_ref]
        assert info['role'] == 'button'
        assert info['name'] == 'OK'
        assert info['bbox'] == {'x': 100, 'y': 200, 'w': 80, 'h': 30}

    def test_lookup_bbox_center(self):
        """Ref cache should support getting the center point of an element's bbox."""
        from tine.refs import bbox_center
        bbox = {'x': 100, 'y': 200, 'w': 80, 'h': 30}
        cx, cy = bbox_center(bbox)
        assert cx == 140
        assert cy == 215

    def test_bbox_center_zero_size(self):
        """Zero-size bbox returns the origin point."""
        from tine.refs import bbox_center
        bbox = {'x': 50, 'y': 60, 'w': 0, 'h': 0}
        cx, cy = bbox_center(bbox)
        assert cx == 50
        assert cy == 60

    def test_bbox_center_zero_height(self):
        """Zero-height element at valid position — center still computable."""
        from tine.refs import bbox_center
        bbox = {'x': 100, 'y': 200, 'w': 245, 'h': 0}
        cx, cy = bbox_center(bbox)
        assert cx == 222  # 100 + 245//2
        assert cy == 200

    def test_bbox_center_negative_dimensions(self):
        """INT_MIN bboxes with negative w/h should not produce valid centers.
        Should either raise or return a sentinel value."""
        from tine.refs import bbox_center
        bbox = {'x': -2147483648, 'y': -2147483648, 'w': -2147483648, 'h': -2147483648}
        # Implementation decides: raise ValueError or return None
        # But it must NOT silently return a garbage coordinate
        try:
            result = bbox_center(bbox)
            # If it returns something, it should be None or a sentinel
            assert result is None, f"Garbage bbox returned {result}, expected None or raise"
        except (ValueError, TypeError):
            pass  # raising is acceptable


# ===========================================================================
# 5. Output Formatting
# ===========================================================================

class TestOutputFormatting:
    """The formatted output should be indented text with bracketed refs
    and states in parens when non-default."""

    def test_basic_format(self):
        from tine.tree import collapse_containers, assign_refs, format_tree
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'OK', bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)
        output = format_tree(tree_with_refs)

        assert 'button' in output
        assert "'OK'" in output or '"OK"' in output
        assert '[ref_' in output

    def test_states_shown_when_non_default(self):
        from tine.tree import collapse_containers, assign_refs, format_tree
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'Submit',
                          states=['focused', 'sensitive', 'showing', 'visible'],
                          bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, _ = assign_refs(collapsed)
        output = format_tree(tree_with_refs)
        # 'focused' is non-default and should appear
        assert 'focused' in output
        # These are default states and should be omitted
        assert 'showing' not in output
        assert 'visible' not in output
        assert 'sensitive' not in output

    def test_enabled_is_default_state(self):
        """'enabled' is default like 'sensitive' — should be omitted."""
        from tine.tree import collapse_containers, assign_refs, format_tree
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'Go',
                          states=['enabled', 'sensitive', 'showing', 'visible'],
                          bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, _ = assign_refs(collapsed)
        output = format_tree(tree_with_refs)
        assert 'enabled' not in output
        assert 'sensitive' not in output

    def test_disabled_state_shown(self):
        """A button missing 'sensitive' state is disabled — show that."""
        from tine.tree import collapse_containers, assign_refs, format_tree
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'Back',
                          states=['showing', 'visible'],
                          bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, _ = assign_refs(collapsed)
        output = format_tree(tree_with_refs)
        assert 'disabled' in output

    def test_indentation_reflects_nesting(self):
        from tine.tree import collapse_containers, assign_refs, format_tree
        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('list', 'Items', children=[
                    make_node('button', 'A'),
                    make_node('button', 'B'),
                ]),
            ]),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, _ = assign_refs(collapsed)
        output = format_tree(tree_with_refs)
        lines = output.strip().split('\n')

        list_line = [l for l in lines if 'Items' in l]
        button_lines = [l for l in lines if "'A'" in l or '"A"' in l]
        assert len(list_line) == 1
        assert len(button_lines) == 1
        list_indent = len(list_line[0]) - len(list_line[0].lstrip())
        button_indent = len(button_lines[0]) - len(button_lines[0].lstrip())
        assert button_indent > list_indent

    def test_settings_fixture_output_readable(self, settings_tree):
        """Full pipeline on real Settings tree should produce readable output."""
        from tine.tree import collapse_containers, assign_refs, format_tree
        collapsed = collapse_containers(settings_tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)
        output = format_tree(tree_with_refs)

        assert 'Wi-Fi' in output
        assert 'Bluetooth' in output
        assert 'Keyboard' in output
        assert '[ref_' in output
        lines = [l for l in output.strip().split('\n') if l.strip()]
        assert len(lines) < 100, f"Output too verbose: {len(lines)} lines"
        assert len(lines) >= 15, f"Output too sparse: {len(lines)} lines"


# ===========================================================================
# 6. Hidden Element Handling
# ===========================================================================

class TestHiddenElements:
    """Elements with INT_MIN coordinates or 1x1 size at INT_MIN position
    are off-screen/hidden. They should be handled appropriately."""

    INT_MIN = -2147483648

    def test_int_min_leaf_filtered(self):
        """A leaf element at INT_MIN with no visible children -> filtered out."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'Window', children=[
            make_node('button', 'Visible', bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            make_node('label', 'Ghost', bbox={'x': self.INT_MIN, 'y': self.INT_MIN, 'w': 1, 'h': 1}),
        ])
        result = collapse_containers(tree)
        names = [c['name'] for c in result['children']]
        assert 'Visible' in names
        assert 'Ghost' not in names

    def test_int_min_parent_with_semantic_children_kept(self):
        """Menu bar at INT_MIN but with semantic children -> kept.
        (Terminal menus are off-screen but contain useful structure.)"""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'Window', children=[
            make_node('menu bar', '', bbox={'x': self.INT_MIN, 'y': self.INT_MIN, 'w': 1, 'h': 1}, children=[
                make_node('menu', 'File', bbox={'x': self.INT_MIN, 'y': self.INT_MIN, 'w': 1, 'h': 1}, children=[
                    make_node('menu item', 'New', bbox={'x': self.INT_MIN, 'y': self.INT_MIN, 'w': 1, 'h': 1}),
                ]),
            ]),
        ])
        result = collapse_containers(tree)

        def has_role(node, role):
            if node['role'] == role:
                return True
            return any(has_role(c, role) for c in node.get('children', []))

        assert has_role(result, 'menu bar') or has_role(result, 'menu')

    def test_negative_dimension_bbox(self):
        """gnome-shell has bboxes with negative width AND height
        (not just INT_MIN position + 1x1). These should be treated as hidden."""
        from tine.tree import collapse_containers
        tree = make_node('frame', 'Window', children=[
            make_node('button', 'Good', bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
            make_node('panel', 'Bad', bbox={
                'x': self.INT_MIN, 'y': self.INT_MIN,
                'w': self.INT_MIN, 'h': self.INT_MIN,
            }),
        ])
        result = collapse_containers(tree)
        names = [c['name'] for c in result['children']]
        assert 'Good' in names
        assert 'Bad' not in names

    def test_terminal_fixture_menus_preserved(self, terminal_tree):
        """Real terminal tree: menus at INT_MIN should survive collapsing
        since they contain actionable menu items."""
        from tine.tree import collapse_containers
        result = collapse_containers(terminal_tree)

        def collect_menu_items(node):
            items = []
            if node['role'] == 'menu item':
                items.append(node['name'])
            for child in node.get('children', []):
                items.extend(collect_menu_items(child))
            return items

        items = collect_menu_items(result)
        assert len(items) > 5, f"Too few menu items: {items}"


# ===========================================================================
# 7. Edge Cases
# ===========================================================================

class TestEdgeCases:

    def test_empty_tree(self):
        """Application with no children."""
        from tine.tree import collapse_containers, assign_refs, format_tree
        tree = make_node('application', 'Empty')
        collapsed = collapse_containers(tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)
        output = format_tree(tree_with_refs)
        assert len(ref_cache) == 0
        assert 'Empty' in output or output.strip() == ''

    def test_single_button(self):
        """Minimal tree: just one button."""
        from tine.tree import collapse_containers, assign_refs, format_tree
        tree = make_node('application', 'App', children=[
            make_node('button', 'Go', bbox={'x': 0, 'y': 0, 'w': 50, 'h': 30}),
        ])
        collapsed = collapse_containers(tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)
        output = format_tree(tree_with_refs)
        assert len(ref_cache) == 1
        assert 'Go' in output

    def test_deeply_nested_single_path(self):
        """20 levels of unnamed panels wrapping one button -> collapses clean."""
        from tine.tree import collapse_containers
        node = make_node('button', 'Deep')
        for _ in range(20):
            node = make_node('panel', '', children=[node])
        tree = make_node('frame', 'App', children=[node])
        result = collapse_containers(tree)
        assert result['children'][0]['role'] == 'button'
        assert result['children'][0]['name'] == 'Deep'

    def test_collapsible_root_node(self):
        """When the root itself is an unnamed collapsible node (no parent to
        promote into), the root should be replaced by its children if it has
        one child, or kept as-is if it has multiple children."""
        from tine.tree import collapse_containers
        # Single child: root is replaced
        tree = make_node('panel', '', children=[
            make_node('button', 'OK', bbox={'x': 10, 'y': 10, 'w': 80, 'h': 30}),
        ])
        result = collapse_containers(tree)
        assert result['role'] == 'button'
        assert result['name'] == 'OK'

    def test_nautilus_fixture_loads_and_processes(self, nautilus_tree):
        """Nautilus fixture should process without errors and produce refs."""
        from tine.tree import collapse_containers, assign_refs, format_tree
        collapsed = collapse_containers(nautilus_tree)
        tree_with_refs, ref_cache = assign_refs(collapsed)
        output = format_tree(tree_with_refs)
        assert len(ref_cache) > 0
        assert len(output) > 0


# ===========================================================================
# 8. Ref Cache Persistence
# ===========================================================================

class TestRefCachePersistence:
    """Ref cache should be saveable and loadable."""

    def test_save_and_load(self, tmp_path):
        from tine.tree import collapse_containers, assign_refs
        from tine.refs import save_ref_cache, load_ref_cache

        tree = make_node('application', 'App', children=[
            make_node('frame', 'Window', children=[
                make_node('button', 'OK', bbox={'x': 10, 'y': 20, 'w': 80, 'h': 30}),
                make_node('button', 'Cancel', bbox={'x': 100, 'y': 20, 'w': 80, 'h': 30}),
            ]),
        ])
        collapsed = collapse_containers(tree)
        _, ref_cache = assign_refs(collapsed)

        cache_path = tmp_path / 'refs.json'
        save_ref_cache(ref_cache, str(cache_path))
        loaded = load_ref_cache(str(cache_path))

        assert loaded == ref_cache

    def test_load_missing_file_returns_empty(self, tmp_path):
        from tine.refs import load_ref_cache
        loaded = load_ref_cache(str(tmp_path / 'nonexistent.json'))
        assert loaded == {}
