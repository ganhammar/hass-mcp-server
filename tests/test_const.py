"""Test constants."""


def test_constants():
    """Test constants are defined correctly."""
    from custom_components.mcp_server.const import (
        DEFAULT_HOST,
        DEFAULT_PORT,
        DOMAIN,
    )

    assert DOMAIN == "mcp_server_http_transport"
    assert DEFAULT_PORT == 8080
    assert DEFAULT_HOST == "0.0.0.0"
