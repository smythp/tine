"""Tests for tine.atspi_walker._walk.

These tests mock the gi/Atspi API surface so we can exercise the frame-origin
subtraction logic without a live AT-SPI bus. The interesting contract is that
direct children of an 'application' node define their own frame coordinate
system, and descendant bboxes are stored window-relative (coord='window').

Regression: finding-20260418-3ff1 — Firefox's AT-SPI bridge reports frame
extents with a non-zero origin (e.g. WINDOW=(26, 23)) on Wayland, and every
descendant's WINDOW extents are reported in that same fake frame. Before the
fix, walker trusted SCREEN extents when non-zero and tagged them 'screen',
which downstream then used as-is — off by (real_window - fake_frame).
"""

import sys
import types

import pytest


# tine.atspi_walker imports gi and gi.repository.Atspi at module load. On
# systems without PyGObject (CI / dev boxes without the at-spi stack), the
# import fails before we can get to the unit under test. Install minimal
# stubs before importing the walker so _walk is exercisable in isolation.
def _install_gi_stubs():
    if 'gi' in sys.modules and hasattr(sys.modules['gi'], '_tine_stubbed'):
        return

    class _CoordType:
        SCREEN = 0
        WINDOW = 1

    class _StateType(int):
        def __new__(cls, v):
            return int.__new__(cls, v)
        value_nick = ''

    class _Atspi:
        CoordType = _CoordType
        StateType = _StateType

        @staticmethod
        def init():
            pass

        @staticmethod
        def get_desktop(_):
            return None

    gi = types.ModuleType('gi')
    gi._tine_stubbed = True
    gi.require_version = lambda *a, **kw: None
    repo = types.ModuleType('gi.repository')
    repo.Atspi = _Atspi
    sys.modules['gi'] = gi
    sys.modules['gi.repository'] = repo


_install_gi_stubs()

from tine import atspi_walker as aw  # noqa: E402


class FakeExtents:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class FakeComponent:
    def __init__(self, window_extents):
        self._w = window_extents

    def get_extents(self, coord_type):
        # We only exercise CoordType.WINDOW in the post-fix walker. Return the
        # same shape for any request so tests don't depend on the enum value.
        return self._w


class FakeNode:
    """Minimal AT-SPI2 node stand-in. Only implements what _walk calls."""

    def __init__(self, *, role, name='', window_extents=None, actions=(),
                 states=(), children=()):
        self._role = role
        self._name = name
        self._comp = (FakeComponent(FakeExtents(*window_extents))
                      if window_extents is not None else None)
        self._actions = list(actions)
        self._states = list(states)
        self._children = list(children)

    def get_role_name(self):
        return self._role

    def get_name(self):
        return self._name

    def get_component_iface(self):
        return self._comp

    def get_child_count(self):
        return len(self._children)

    def get_child_at_index(self, i):
        return self._children[i]

    def get_state_set(self):
        class _SS:
            def contains(_self, st):
                return False
        return _SS()

    def get_action_iface(self):
        if not self._actions:
            return None
        owner = self

        class _AI:
            def get_n_actions(_self):
                return len(owner._actions)

            def get_action_name(_self, i):
                return owner._actions[i]
        return _AI()


def _run(node):
    aw._walk._count = 0
    return aw._walk(node)


class TestBboxSubtraction:
    def test_gtk4_style_frame_at_origin_is_passthrough(self):
        """GTK4 reports frame.WINDOW=(0, 0) and elements at their true
        window-relative coords. Subtraction is a no-op.
        """
        leaf = FakeNode(role='button', name='7',
                        window_extents=(24, 428, 64, 44))
        frame = FakeNode(role='filler', name='Calculator',
                         window_extents=(0, 0, 360, 616),
                         children=[leaf])
        app = FakeNode(role='application', name='gnome-calculator',
                       children=[frame])

        tree = _run(app)
        button = tree['children'][0]['children'][0]
        assert button['bbox'] == {'x': 24, 'y': 428, 'w': 64, 'h': 44,
                                  'coord': 'window'}

    def test_firefox_style_fake_frame_origin_is_subtracted(self):
        """Firefox reports frame.WINDOW=(26, 23) and every descendant in that
        fake frame. Subtraction recovers true window-relative coords.
        """
        leaf = FakeNode(role='link', name='tgtA 20,20',
                        window_extents=(46, 128, 140, 40))
        frame = FakeNode(role='frame', name='Firefox',
                         window_extents=(26, 23, 1280, 777),
                         children=[leaf])
        app = FakeNode(role='application', name='Firefox',
                       children=[frame])

        tree = _run(app)
        link = tree['children'][0]['children'][0]
        # (46 - 26, 128 - 23) = (20, 105)
        assert link['bbox'] == {'x': 20, 'y': 105, 'w': 140, 'h': 40,
                                'coord': 'window'}

    def test_frame_node_itself_reports_origin_subtracted_to_zero(self):
        """The frame/filler node is at (0, 0) after subtracting itself."""
        frame = FakeNode(role='frame', name='Firefox',
                         window_extents=(26, 23, 1280, 777))
        app = FakeNode(role='application', name='Firefox', children=[frame])

        tree = _run(app)
        assert tree['children'][0]['bbox'] == {
            'x': 0, 'y': 0, 'w': 1280, 'h': 777, 'coord': 'window',
        }

    def test_bbox_always_tagged_window(self):
        leaf = FakeNode(role='link', name='x',
                        window_extents=(50, 50, 10, 10))
        frame = FakeNode(role='frame', name='win',
                         window_extents=(0, 0, 800, 600),
                         children=[leaf])
        app = FakeNode(role='application', name='app', children=[frame])

        tree = _run(app)
        assert tree['children'][0]['children'][0]['bbox']['coord'] == 'window'

    def test_node_without_component_has_zero_bbox(self):
        """App nodes typically return no component interface. We should fall
        back to a zeroed bbox without raising.
        """
        app = FakeNode(role='application', name='gnome-calculator')
        tree = _run(app)
        assert tree['bbox'] == {'x': 0, 'y': 0, 'w': 0, 'h': 0,
                                'coord': 'window'}


class TestMultipleFrames:
    def test_each_application_child_gets_its_own_frame_origin(self):
        """Two Firefox windows at different positions each define their own
        frame origin. Descendants in each subtree are subtracted by THEIR
        frame's origin.
        """
        leaf_a = FakeNode(role='link', name='A',
                          window_extents=(46, 128, 140, 40))
        frame_a = FakeNode(role='frame', name='win1',
                           window_extents=(26, 23, 1280, 777),
                           children=[leaf_a])
        # Second frame with a different fake-origin offset.
        leaf_b = FakeNode(role='link', name='B',
                          window_extents=(100, 200, 80, 20))
        frame_b = FakeNode(role='frame', name='win2',
                           window_extents=(10, 40, 1280, 777),
                           children=[leaf_b])

        app = FakeNode(role='application', name='Firefox',
                       children=[frame_a, frame_b])

        tree = _run(app)
        link_a = tree['children'][0]['children'][0]
        link_b = tree['children'][1]['children'][0]
        # A: (46-26, 128-23) = (20, 105)
        assert link_a['bbox']['x'] == 20 and link_a['bbox']['y'] == 105
        # B: (100-10, 200-40) = (90, 160)
        assert link_b['bbox']['x'] == 90 and link_b['bbox']['y'] == 160


class TestDeepDescendants:
    def test_grandchild_bbox_uses_frame_not_parent_origin(self):
        """A deeply nested element's bbox is relative to the FRAME, not to
        its direct parent. The frame_origin is threaded unchanged through
        non-application ancestors.
        """
        grandchild = FakeNode(role='push button', name='Go',
                              window_extents=(100, 200, 50, 30))
        middle = FakeNode(role='panel', name='',
                          window_extents=(50, 50, 400, 400),
                          children=[grandchild])
        frame = FakeNode(role='frame', name='win',
                         window_extents=(26, 23, 1280, 777),
                         children=[middle])
        app = FakeNode(role='application', name='Firefox',
                       children=[frame])

        tree = _run(app)
        btn = tree['children'][0]['children'][0]['children'][0]
        # Grandchild's WINDOW (100, 200) minus frame's (26, 23) = (74, 177).
        # Notably NOT relative to middle panel's (50, 50).
        assert btn['bbox']['x'] == 74 and btn['bbox']['y'] == 177
