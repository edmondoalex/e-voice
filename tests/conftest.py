"""Home Assistant test fixtures for Ekonex Voice."""

from __future__ import annotations

from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    """Allow loading integrations from custom_components."""
    yield
