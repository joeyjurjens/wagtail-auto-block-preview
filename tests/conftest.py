from wagtail_auto_block_preview.core import muted_signals, registry

import pytest


@pytest.fixture(autouse=True)
def _reset_faker_registry():
    """Isolate tests from each other's registrations — reset both before
    (so a previous test's leftovers can't leak in) and after (so this
    test's own registrations don't leak out)."""
    registry.reset()
    muted_signals.reset()
    yield
    registry.reset()
    muted_signals.reset()
