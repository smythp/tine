"""Tests for the Level 3 click sanity check (brief-20260415-7o14).

Two layers are covered:

  * ``tine.tree.hash_subtree`` — stable under bbox changes, reactive to
    role/name/state changes
  * ``tine cli click ref_N`` — emits a stderr warning when the AT-SPI2
    walker reports an unchanged tree after the click
"""

import json
import os

import tine.cli as cli_mod
import tine.input
import tine.refs
import tine.screenshot
import tine.tree
import tine.walker


def _cache_with_ref(path, app='Calculator'):
    cache = {
        '_app': app,
        'ref_1': {
            'role': 'button',
            'name': 'Equals',
            'bbox': {'x': 100, 'y': 100, 'w': 80, 'h': 40, 'coord': 'window'},
            'actions': ['click'],
        },
    }
    tine.refs.save_ref_cache(cache, path)
    return cache


class TestHashSubtree:
    def test_identical_trees_hash_equal(self):
        tree = {'role': 'frame', 'name': 'Calculator', 'states': ['focused'],
                'children': [
                    {'role': 'button', 'name': '=', 'states': ['sensitive'],
                     'children': []},
                ]}
        assert tine.tree.hash_subtree(tree) == tine.tree.hash_subtree(tree)

    def test_bbox_change_does_not_affect_hash(self):
        """Window moves / scroll must not trigger false-positive warnings."""
        a = {'role': 'frame', 'name': 'Calc', 'states': [],
             'bbox': {'x': 0, 'y': 0, 'w': 400, 'h': 300}, 'children': []}
        b = dict(a)
        b['bbox'] = {'x': 500, 'y': 200, 'w': 400, 'h': 300}
        assert tine.tree.hash_subtree(a) == tine.tree.hash_subtree(b)

    def test_role_change_changes_hash(self):
        a = {'role': 'button', 'name': 'X', 'states': [], 'children': []}
        b = {'role': 'link', 'name': 'X', 'states': [], 'children': []}
        assert tine.tree.hash_subtree(a) != tine.tree.hash_subtree(b)

    def test_name_change_changes_hash(self):
        a = {'role': 'button', 'name': 'Submit', 'states': [], 'children': []}
        b = {'role': 'button', 'name': 'Cancel', 'states': [], 'children': []}
        assert tine.tree.hash_subtree(a) != tine.tree.hash_subtree(b)

    def test_state_change_changes_hash(self):
        a = {'role': 'check box', 'name': 'Remember me',
             'states': ['sensitive'], 'children': []}
        b = {'role': 'check box', 'name': 'Remember me',
             'states': ['sensitive', 'checked'], 'children': []}
        assert tine.tree.hash_subtree(a) != tine.tree.hash_subtree(b)

    def test_state_order_does_not_matter(self):
        a = {'role': 'entry', 'name': 'query',
             'states': ['focusable', 'sensitive'], 'children': []}
        b = {'role': 'entry', 'name': 'query',
             'states': ['sensitive', 'focusable'], 'children': []}
        assert tine.tree.hash_subtree(a) == tine.tree.hash_subtree(b)

    def test_child_change_changes_hash(self):
        a = {'role': 'frame', 'name': '', 'states': [], 'children': [
            {'role': 'button', 'name': 'A', 'states': [], 'children': []},
        ]}
        b = {'role': 'frame', 'name': '', 'states': [], 'children': [
            {'role': 'button', 'name': 'A', 'states': [], 'children': []},
            {'role': 'button', 'name': 'B', 'states': [], 'children': []},
        ]}
        assert tine.tree.hash_subtree(a) != tine.tree.hash_subtree(b)


class TestClickVerifyWarning:
    def _install(self, monkeypatch, tmp_path, walk_returns):
        """Wire up the minimum stubs to run ``tine click ref_1`` while
        controlling what the walker returns on each call.

        ``walk_returns`` can contain either tree dicts or Exception
        instances; exceptions are raised from the fake walker so the
        pre/post-click error paths can be exercised.
        """
        path = str(tmp_path / 'refs.json')
        _cache_with_ref(path)
        monkeypatch.setenv('TINE_REF_CACHE', path)
        monkeypatch.delenv('TINE_NO_VERIFY_CLICK', raising=False)

        walk_calls = iter(walk_returns)

        def fake_walker(app=None):
            val = next(walk_calls)
            if isinstance(val, BaseException):
                raise val
            return val

        monkeypatch.setattr(cli_mod, '_walk_atspi', fake_walker)
        monkeypatch.setattr(cli_mod.time, 'sleep', lambda _s: None)
        monkeypatch.setattr(tine.input, 'click_ref',
                            lambda *a, **kw: None)
        monkeypatch.setattr(
            tine.screenshot, 'list_windows',
            lambda: [{'title': 'Calculator', 'wm_class': 'gnome-calculator',
                      'x': 72, 'y': 32, 'width': 400, 'height': 300,
                      'focused': True}],
        )

    def test_unchanged_tree_emits_warning(self, monkeypatch, tmp_path, capsys):
        tree = {'role': 'frame', 'name': 'Calc', 'states': [],
                'children': [
                    {'role': 'button', 'name': '=', 'states': [],
                     'children': []},
                ]}
        self._install(monkeypatch, tmp_path, walk_returns=[tree, tree])
        cli_mod.main(['click', 'ref_1'])
        err = capsys.readouterr().err
        assert 'click may not have landed' in err

    def test_changed_tree_does_not_warn(self, monkeypatch, tmp_path, capsys):
        before = {'role': 'frame', 'name': 'Calc', 'states': [],
                  'children': [
                      {'role': 'button', 'name': '=', 'states': [],
                       'children': []},
                  ]}
        after = {'role': 'frame', 'name': 'Calc', 'states': [],
                 'children': [
                     {'role': 'button', 'name': '=',
                      'states': ['focused'], 'children': []},
                 ]}
        self._install(monkeypatch, tmp_path, walk_returns=[before, after])
        cli_mod.main(['click', 'ref_1'])
        err = capsys.readouterr().err
        assert 'click may not have landed' not in err

    def test_pre_click_walker_failure_reports_and_continues(
            self, monkeypatch, tmp_path, capsys):
        """Broken pre-click walker: we must report the failure (so the
        user knows the safety layer isn't running) but still complete
        the click. The "click may not have landed" warning must not
        fire, because we have no before-hash to compare against.
        """
        self._install(
            monkeypatch, tmp_path,
            walk_returns=[RuntimeError('pre-click walker crashed')],
        )
        cli_mod.main(['click', 'ref_1'])
        err = capsys.readouterr().err
        assert 'click verification skipped' in err
        assert 'pre-click walker crashed' in err
        assert 'click may not have landed' not in err

    def test_post_click_walker_failure_reports_and_does_not_warn(
            self, monkeypatch, tmp_path, capsys):
        """Broken post-click walker: pre-walk succeeded so we had a
        before-hash, but after-walk throws. We must report the
        incomplete verification and not emit a false "tree unchanged"
        warning.
        """
        tree = {'role': 'frame', 'name': 'Calc', 'states': [],
                'children': []}
        self._install(
            monkeypatch, tmp_path,
            walk_returns=[tree, RuntimeError('post-click walker crashed')],
        )
        cli_mod.main(['click', 'ref_1'])
        err = capsys.readouterr().err
        assert 'click verification incomplete' in err
        assert 'post-click walker crashed' in err
        assert 'click may not have landed' not in err

    def test_env_var_disables_verification(self, monkeypatch, tmp_path, capsys):
        """Setting TINE_NO_VERIFY_CLICK=1 must skip both walks entirely."""
        walk_count = []

        def counting_walker(app=None):
            walk_count.append(app)
            return {'role': 'frame', 'name': '', 'states': [], 'children': []}

        path = str(tmp_path / 'refs.json')
        _cache_with_ref(path)
        monkeypatch.setenv('TINE_REF_CACHE', path)
        monkeypatch.setenv('TINE_NO_VERIFY_CLICK', '1')
        monkeypatch.setattr(cli_mod, '_walk_atspi', counting_walker)
        monkeypatch.setattr(tine.input, 'click_ref',
                            lambda *a, **kw: None)
        monkeypatch.setattr(
            tine.screenshot, 'list_windows',
            lambda: [{'title': 'Calculator', 'wm_class': 'gnome-calculator',
                      'x': 72, 'y': 32, 'width': 400, 'height': 300,
                      'focused': True}],
        )
        cli_mod.main(['click', 'ref_1'])
        err = capsys.readouterr().err
        assert walk_count == []
        assert 'click may not have landed' not in err

    def test_missing_app_in_cache_skips_verification(
            self, monkeypatch, tmp_path, capsys):
        """If the ref cache has no ``_app`` entry (or it's empty),
        verification must skip entirely rather than falling through to
        ``_walk_atspi(app='')`` and hashing unrelated windows.
        """
        walk_count = []

        def counting_walker(app=None):
            walk_count.append(app)
            return {'role': 'frame', 'name': '', 'states': [], 'children': []}

        path = str(tmp_path / 'refs.json')
        cache = {
            '_app': '',
            'ref_1': {
                'role': 'button',
                'name': 'Equals',
                'bbox': {'x': 100, 'y': 100, 'w': 80, 'h': 40,
                         'coord': 'window'},
                'actions': ['click'],
            },
        }
        tine.refs.save_ref_cache(cache, path)
        monkeypatch.setenv('TINE_REF_CACHE', path)
        monkeypatch.delenv('TINE_NO_VERIFY_CLICK', raising=False)
        monkeypatch.setattr(cli_mod, '_walk_atspi', counting_walker)
        monkeypatch.setattr(tine.input, 'click_ref',
                            lambda *a, **kw: None)
        monkeypatch.setattr(
            tine.screenshot, 'list_windows',
            lambda: [{'title': 'Calculator', 'wm_class': 'gnome-calculator',
                      'x': 72, 'y': 32, 'width': 400, 'height': 300,
                      'focused': True}],
        )
        cli_mod.main(['click', 'ref_1'])
        err = capsys.readouterr().err
        assert walk_count == []
        assert 'click may not have landed' not in err
        assert 'click verification' not in err

    def test_text_ref_click_skips_verification(self, monkeypatch, tmp_path,
                                               capsys):
        """ref_tN clicks don't trigger the verify path — OCR refs are a
        separate source of truth with no AT-SPI2 hash to compare to.
        """
        walk_count = []

        def counting_walker(app=None):
            walk_count.append(app)
            return {'role': 'frame', 'name': '', 'states': [], 'children': []}

        path = str(tmp_path / 'refs.json')
        cache = {
            '_app': 'Firefox',
            'ref_t1': {
                'text': 'Learn more',
                'bbox': {'x': 579, 'y': 459, 'w': 80, 'h': 20,
                         'coord': 'screen'},
                'confidence': 0.99,
            },
        }
        tine.refs.save_ref_cache(cache, path)
        monkeypatch.setenv('TINE_REF_CACHE', path)
        monkeypatch.delenv('TINE_NO_VERIFY_CLICK', raising=False)
        monkeypatch.setattr(cli_mod, '_walk_atspi', counting_walker)
        monkeypatch.setattr(tine.input, 'click_ref',
                            lambda *a, **kw: None)
        cli_mod.main(['click', 'ref_t1'])
        err = capsys.readouterr().err
        assert walk_count == []
        assert 'click may not have landed' not in err
