"""Constants for the MCP Server integration."""

DOMAIN = "mcp_server_http_transport"

# MCP Server configuration
DEFAULT_PORT = 8080
DEFAULT_HOST = "0.0.0.0"

# Authentication configuration
CONF_NATIVE_AUTH = "native_auth_enabled"

# Feature flags
CONF_CONFIG_FILE_ACCESS = "config_file_access_enabled"
CONF_CAMERA_IMAGE_ACCESS = "camera_image_access_enabled"
CONF_IMAGE_FILE_ACCESS = "image_file_access_enabled"

# HTTP paths. Home Assistant's built-in mcp_server integration serves its
# streamable transport on /api/mcp from 2025.11 onwards, and aiohttp resolves a
# duplicated path to whichever integration registered it first, silently. This
# integration therefore also answers on a path nothing else claims.
MCP_PATH = "/api/mcp"
MCP_HTTP_PATH = "/api/mcp_http"
RESOURCE_METADATA_PREFIX = "/.well-known/oauth-protected-resource"

# Repairs issue raised while another integration also serves MCP_PATH.
ISSUE_ENDPOINT_CONFLICT = "endpoint_conflict"
