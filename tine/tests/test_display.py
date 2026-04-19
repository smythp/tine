"""Tests for tine.display — display config detection and caching."""

import subprocess

import pytest

import tine.display


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    """Clear cached config and all TINE_* display env vars before each test.

    Individual tests set the env vars they need; unsetting everything here
    keeps one test's environment from leaking into another's expectations.
    """
    monkeypatch.setattr(tine.display, '_cached_config', None)
    for var in ('TINE_SCALE', 'TINE_PHYSICAL_WIDTH', 'TINE_PHYSICAL_HEIGHT',
                'TINE_DISPLAY_FALLBACK'):
        monkeypatch.delenv(var, raising=False)


class TestEnvVars:
    """Env vars take precedence over D-Bus and fallback."""

    def test_all_env_vars_set(self, monkeypatch):
        monkeypatch.setenv('TINE_SCALE', '1.25')
        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '2560')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1440')

        config = tine.display.get_display_config()

        assert config['scale'] == 1.25
        assert config['physical_width'] == 2560
        assert config['physical_height'] == 1440

    def test_partial_env_vars_raise_valueerror(self, monkeypatch):
        """Partial TINE_* env setup used to silently fill missing values
        with 1920x1080. That path bypassed the new hard-fail, so require
        all three explicitly."""
        monkeypatch.setenv('TINE_SCALE', '2.0')

        with pytest.raises(ValueError, match='Partial TINE display env'):
            tine.display.get_display_config()

    def test_partial_env_error_names_missing_vars(self, monkeypatch):
        """The error should say exactly which vars are missing so the
        user doesn't have to re-read the docs."""
        monkeypatch.setenv('TINE_SCALE', '2.0')
        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '2560')

        with pytest.raises(ValueError) as exc_info:
            tine.display.get_display_config()

        msg = str(exc_info.value)
        assert 'TINE_PHYSICAL_HEIGHT' in msg
        assert 'TINE_SCALE' not in msg  # not missing
        assert 'TINE_PHYSICAL_WIDTH' not in msg  # not missing

    def test_env_vars_skip_dbus(self, monkeypatch):
        """When env vars are set, D-Bus is never queried."""
        monkeypatch.setenv('TINE_SCALE', '1.0')
        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1080')

        calls = []
        original_run = subprocess.run

        def tracking_run(*args, **kwargs):
            calls.append(args)
            return original_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, 'run', tracking_run)

        tine.display.get_display_config()

        assert len(calls) == 0


class TestDbusQuery:
    """D-Bus query when env vars are not set."""

    SAMPLE_GVARIANT = (
        "((uint32 2,),"
        " [((('eDP-1', 'BOE', '0x0931', '0x00000000'),"
        " [((1920, 1080, 60.052, 1.0, [1.0, 1.25, 1.5], {'is-current': <true>}),"
        " [])]),"
        " {'is-builtin': <true>})],"
        " [(0, 0, 1.25, uint32 0, true,"
        " [('eDP-1', '1920x1080@60.052')],"
        " {'is-primary': <true>})],"
        " {'layout-mode': <uint32 2>})"
    )

    def test_parses_mutter_output(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=self.SAMPLE_GVARIANT, stderr='')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        config = tine.display.get_display_config()

        assert config['scale'] == 1.25
        assert config['physical_width'] == 1920
        assert config['physical_height'] == 1080


class TestHardFailWhenAllSourcesFail:
    """Both env vars and Mutter unavailable -> raise, do not silently default."""

    def test_dbus_failure_raises_runtimeerror(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout='', stderr='error')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(RuntimeError, match='Display config unavailable'):
            tine.display.get_display_config()

    def test_gdbus_not_found_raises_runtimeerror(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(RuntimeError, match='Display config unavailable'):
            tine.display.get_display_config()

    def test_gdbus_timeout_raises_runtimeerror(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(RuntimeError, match='Display config unavailable'):
            tine.display.get_display_config()

    def test_error_message_names_every_source(self, monkeypatch):
        """Diagnostic value: a single stderr line should tell the user
        every knob they can turn."""
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(RuntimeError) as exc_info:
            tine.display.get_display_config()

        msg = str(exc_info.value)
        assert 'TINE_SCALE' in msg
        assert 'TINE_PHYSICAL_WIDTH' in msg
        assert 'TINE_PHYSICAL_HEIGHT' in msg
        assert 'Mutter' in msg
        assert 'TINE_DISPLAY_FALLBACK' in msg

    def test_failure_does_not_cache(self, monkeypatch):
        """A failed lookup must not poison subsequent attempts after the
        user fixes their env. If we cached None-or-raise, setting env vars
        after the first call would have no effect."""
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(RuntimeError):
            tine.display.get_display_config()

        # Now fix the env and retry — should succeed.
        monkeypatch.setenv('TINE_SCALE', '1.0')
        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '1080')

        config = tine.display.get_display_config()
        assert config['physical_width'] == 1920


class TestDisplayFallbackEnv:
    """TINE_DISPLAY_FALLBACK is the opt-in escape hatch for exotic configs."""

    def test_fallback_used_when_both_env_and_dbus_fail(self, monkeypatch):
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', '1920x1080')

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        config = tine.display.get_display_config()

        assert config == {'scale': 1.0, 'physical_width': 1920, 'physical_height': 1080}

    def test_fallback_accepts_non_default_resolution(self, monkeypatch):
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', '2560x1440')

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        config = tine.display.get_display_config()

        assert config == {'scale': 1.0, 'physical_width': 2560, 'physical_height': 1440}

    def test_fallback_ignored_when_mutter_succeeds(self, monkeypatch):
        """Fallback is a last resort — Mutter still wins over it."""
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', '800x600')

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=TestDbusQuery.SAMPLE_GVARIANT, stderr=''
            )

        monkeypatch.setattr(subprocess, 'run', fake_run)

        config = tine.display.get_display_config()

        assert config['physical_width'] == 1920  # from Mutter, not fallback
        assert config['scale'] == 1.25

    def test_fallback_ignored_when_explicit_env_vars_set(self, monkeypatch):
        """Explicit TINE_SCALE+WIDTH+HEIGHT still wins over fallback."""
        monkeypatch.setenv('TINE_SCALE', '2.0')
        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '3840')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '2160')
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', '800x600')

        config = tine.display.get_display_config()

        assert config['scale'] == 2.0
        assert config['physical_width'] == 3840

    def test_fallback_malformed_raises_valueerror(self, monkeypatch):
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', '1920')

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(ValueError, match='TINE_DISPLAY_FALLBACK'):
            tine.display.get_display_config()

    def test_fallback_malformed_non_numeric_raises(self, monkeypatch):
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', 'widexheight')

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(ValueError, match='TINE_DISPLAY_FALLBACK'):
            tine.display.get_display_config()

    def test_fallback_rejects_zero_dimensions(self, monkeypatch):
        """0x0 parses as an int pair but makes every downstream click
        collapse to the origin — reject it at the door."""
        for bad in ('0x0', '1920x0', '0x1080', '639x1080', '1920x479'):
            monkeypatch.setenv('TINE_DISPLAY_FALLBACK', bad)
            tine.display._cached_config = None

            def fake_run(cmd, **kwargs):
                raise FileNotFoundError('gdbus not found')

            monkeypatch.setattr(subprocess, 'run', fake_run)

            with pytest.raises(ValueError, match='implausible dimensions'):
                tine.display.get_display_config()

    def test_fallback_rejects_unicode_digits(self, monkeypatch):
        """Fullwidth digits like 'ï¼‘ï¼™ï¼’ï¼ ' match \\d in default re mode — use
        re.ASCII to reject them."""
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', 'ï¼‘ï¼™ï¼’ï¼ xï¼‘ï¼ ï¼˜ï¼ ')

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(ValueError, match='TINE_DISPLAY_FALLBACK'):
            tine.display.get_display_config()

    def test_fallback_accepts_edge_of_range(self, monkeypatch):
        """Boundary resolutions on the plausible range should parse."""
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', '7680x4320')

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        config = tine.display.get_display_config()
        assert config['physical_width'] == 7680
        assert config['physical_height'] == 4320

    def test_fallback_unset_is_not_consulted(self, monkeypatch):
        """Unset fallback doesn't prevent the raise — we never silently
        default without the opt-in."""
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(RuntimeError):
            tine.display.get_display_config()

    def test_fallback_trims_whitespace(self, monkeypatch):
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', '  1920x1080  ')

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError('gdbus not found')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        config = tine.display.get_display_config()
        assert config['physical_width'] == 1920


class TestCaching:
    """Second call returns cached result without re-querying."""

    def test_cached_after_first_call(self, monkeypatch):
        monkeypatch.setenv('TINE_SCALE', '1.5')
        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '3840')
        monkeypatch.setenv('TINE_PHYSICAL_HEIGHT', '2160')

        first = tine.display.get_display_config()

        # Change env vars — should not affect second call
        monkeypatch.setenv('TINE_SCALE', '2.0')
        monkeypatch.setenv('TINE_PHYSICAL_WIDTH', '1920')

        second = tine.display.get_display_config()

        assert first is second
        assert second['scale'] == 1.5
        assert second['physical_width'] == 3840


class TestParseGvariant:
    """Test GVariant output parsing edge cases."""

    def test_dual_monitor_picks_primary(self):
        text = (
            "((uint32 2,),"
            " [((('DP-1', 'Dell', 'U2720Q', 'ABC123'),"
            " [((3840, 2160, 60.0, 2.0, [1.0, 2.0], {'is-current': <true>}), [])]),"
            " {'is-builtin': <false>}),"
            " ((('eDP-1', 'BOE', '0x0931', '0x00000000'),"
            " [((1920, 1080, 60.0, 1.0, [1.0, 1.25], {'is-current': <true>}), [])]),"
            " {'is-builtin': <true>})],"
            " [(0, 0, 2.0, 0, false,"
            " [('DP-1', '3840x2160@60.0')], {}),"
            " (1920, 0, 1.25, 0, true,"
            " [('eDP-1', '1920x1080@60.0')], {'is-primary': <true>})],"
            " {'layout-mode': <uint32 2>})"
        )
        config = tine.display._parse_mutter_output(text)

        assert config['scale'] == 1.25  # primary monitor's scale
        assert config['physical_width'] == 1920  # primary monitor's resolution, not DP-1's 3840
        assert config['physical_height'] == 1080

    def test_empty_output_returns_none(self):
        assert tine.display._parse_mutter_output('') is None
        assert tine.display._parse_mutter_output('  ') is None

    def test_garbage_output_returns_none(self):
        assert tine.display._parse_mutter_output('not a gvariant') is None

    def test_scale_without_parseable_mode_returns_none(self):
        """Previously this returned {'scale': X, 'physical_width': 1920,
        'physical_height': 1080} — a silent default that bypassed the
        outer hard-fail. Now it returns None so the caller can fall
        through to TINE_DISPLAY_FALLBACK or raise cleanly."""
        text_with_scale_but_no_mode = (
            "((uint32 2,),"
            " [((('eDP-1', 'BOE', '0x0931', '0x00000000'),"
            " [], {'is-builtin': <true>})],"
            " [(0, 0, 1.25, uint32 0, true, [], {'is-primary': <true>})],"
            " {'layout-mode': <uint32 2>})"
        )
        assert tine.display._parse_mutter_output(text_with_scale_but_no_mode) is None


class TestMutterParseFailureFallsThrough:
    """When Mutter returns output but the parser can't extract a mode
    string, we fall through to TINE_DISPLAY_FALLBACK / raise rather than
    returning a silent 1920x1080 default."""

    MODELESS_OUTPUT = (
        "((uint32 2,),"
        " [((('eDP-1', 'BOE', '0x0931', '0x00000000'),"
        " [], {'is-builtin': <true>})],"
        " [(0, 0, 1.25, uint32 0, true, [], {'is-primary': <true>})],"
        " {'layout-mode': <uint32 2>})"
    )

    def test_raises_when_no_fallback_set(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=self.MODELESS_OUTPUT, stderr='')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        with pytest.raises(RuntimeError, match='Display config unavailable'):
            tine.display.get_display_config()

    def test_uses_fallback_when_set(self, monkeypatch):
        monkeypatch.setenv('TINE_DISPLAY_FALLBACK', '2560x1440')

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=self.MODELESS_OUTPUT, stderr='')

        monkeypatch.setattr(subprocess, 'run', fake_run)

        config = tine.display.get_display_config()
        assert config['physical_width'] == 2560
        assert config['physical_height'] == 1440
