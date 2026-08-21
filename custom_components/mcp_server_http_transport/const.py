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
CONF_APPDAEMON_FILE_ACCESS = "appdaemon_file_access_enabled"
CONF_APPDAEMON_APPS_ROOT = "appdaemon_apps_root"

DEFAULT_APPDAEMON_APPS_ROOT = "/addon_configs/a0d7b954_appdaemon/apps"
APPDAEMON_SHARED_ROOTS = ("/share/", "/media/")


def validate_appdaemon_apps_root(value: str) -> str:
    """Validate the bounded AppDaemon apps root option."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("AppDaemon apps root must be an absolute path")
    normalized = value.rstrip("/") or "/"
    if "//" in normalized or any(part in (".", "..") for part in normalized.split("/")[1:]):
        raise ValueError("AppDaemon apps root contains an invalid path component")
    if normalized == DEFAULT_APPDAEMON_APPS_ROOT:
        return normalized
    if not normalized.startswith(APPDAEMON_SHARED_ROOTS):
        raise ValueError("AppDaemon apps root must be under /share or /media")
    return normalized
