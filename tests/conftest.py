"""Pytest configuration and fixtures."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = Mock()
    hass.data = {}
    hass.config = Mock()
    hass.config.entries = Mock()
    hass.config.entries.async_domains = Mock(return_value=["oidc_provider"])
    hass.http = Mock()
    hass.http.register_view = Mock()
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = Mock()
    entry.data = {}
    entry.options = {}
    return entry
