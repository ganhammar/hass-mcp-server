"""Pytest configuration and fixtures."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# Mock the oidc_provider module before any imports
def mock_get_issuer_from_request(request):
    """Mock implementation of get_issuer_from_request."""
    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    forwarded_host = request.headers.get("X-Forwarded-Host")

    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}"
    else:
        return str(request.url.origin())


mock_token_validator = Mock()
mock_token_validator.get_issuer_from_request = mock_get_issuer_from_request
mock_token_validator.validate_access_token = Mock(return_value=None)
sys.modules["custom_components.oidc_provider"] = Mock()
sys.modules["custom_components.oidc_provider.token_validator"] = mock_token_validator


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
