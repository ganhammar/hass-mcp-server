"""Test constants."""

import pytest


def test_constants():
    """Test constants are defined correctly."""
    from custom_components.mcp_server_http_transport.const import (
        DEFAULT_HOST,
        DEFAULT_PORT,
        DOMAIN,
    )

    assert DOMAIN == "mcp_server_http_transport"
    assert DEFAULT_PORT == 8080
    assert DEFAULT_HOST == "0.0.0.0"


def test_validate_server_name_strips_surrounding_whitespace():
    """A padded name is stored trimmed, so a client sees the same string."""
    from custom_components.mcp_server_http_transport.const import validate_server_name

    assert validate_server_name("  ha-mcp-secundair  ") == "ha-mcp-secundair"


def test_validate_server_name_rejects_blank_name():
    """A blank name leaves a client with nothing to key a tool namespace on."""
    from custom_components.mcp_server_http_transport.const import validate_server_name

    with pytest.raises(ValueError):
        validate_server_name("   ")
