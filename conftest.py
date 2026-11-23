"""Repository-wide pytest compatibility and Home Assistant fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import Generator

import pytest


def pytest_runtest_setup() -> None:
    """Provide the loop expected by the Home Assistant cleanup fixture.

    Home Assistant installs a strict event-loop policy.  Under Python 3.13 and
    pytest 9, the third-party ``verify_cleanup`` fixture runs before
    pytest-asyncio has installed a current loop.  Create one at the start of
    each test only when the policy reports that none is current; the fixture
    remains responsible for its normal leak checks and cleanup.
    """
    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None
    if previous_loop is not None and not previous_loop.is_running():
        previous_loop.close()
    asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def enable_event_loop_debug() -> Generator[None]:
    """Override a test-harness fixture incompatible with pytest 9/Python 3.13.

    pytest-homeassistant-custom-component 0.13.316 calls get_event_loop before
    pytest-asyncio creates a loop. Event-loop debug is diagnostic only; the HA
    fixtures and leak checks remain enabled.
    """
    yield


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    """Allow loading integrations from custom_components."""
    yield
