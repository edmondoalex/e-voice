"""Repository-wide pytest compatibility and Home Assistant fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest


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
