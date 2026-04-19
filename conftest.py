"""Repo-root pytest conftest.

Pins a default display config for every test so the hard-fail in
tine.display.get_display_config() (raised when neither env vars nor
Mutter D-Bus are available) doesn't cascade through the suite.

Tests that exercise display detection itself clear the cache in their
own fixtures (see tine/tests/test_display.py::reset_cache) and override
this default for the scope of the test.
"""

import pytest


@pytest.fixture(autouse=True)
def _default_display_config(monkeypatch):
    import tine.display
    monkeypatch.setattr(tine.display, '_cached_config', {
        'scale': 1.0, 'physical_width': 1920, 'physical_height': 1080,
    })
