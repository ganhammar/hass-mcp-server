"""Constants for the MCP Server integration."""

import json
from pathlib import Path
from typing import Final

DOMAIN = "mcp_server_http_transport"

# The version a client sees in serverInfo is the one the manifest declares, so a
# release bump in one place is a bump everywhere.
VERSION: Final[str] = json.loads(
    Path(__file__).with_name("manifest.json").read_text(encoding="utf-8")
)["version"]

# MCP Server configuration
DEFAULT_PORT = 8080
DEFAULT_HOST = "0.0.0.0"

# Authentication configuration
CONF_NATIVE_AUTH = "native_auth_enabled"

# Whether only Home Assistant administrators may use the endpoint. Home Assistant
# itself restricts most of what the tools do (config changes, restart, templates,
# the error log) to administrators, so an entry without the key requires one.
CONF_REQUIRE_ADMIN = "require_admin"

# Feature flags
CONF_CONFIG_FILE_ACCESS = "config_file_access_enabled"
CONF_CAMERA_IMAGE_ACCESS = "camera_image_access_enabled"
CONF_IMAGE_FILE_ACCESS = "image_file_access_enabled"
CONF_TEMPLATE_RENDERING = "template_rendering_enabled"
CONF_APPDAEMON_FILE_ACCESS = "appdaemon_file_access_enabled"
CONF_INTEGRATION_MANAGEMENT = "integration_management_enabled"

# Limits on which tools a client is offered at all, on top of the feature flags.
# Read-only mode keeps the tools annotated readOnlyHint; disabled tools are
# listed by name.
CONF_READ_ONLY = "read_only_enabled"
CONF_DISABLED_TOOLS = "disabled_tools"
CONF_APPDAEMON_APPS_ROOT = "appdaemon_apps_root"

DEFAULT_APPDAEMON_APPS_ROOT = "/addon_configs/a0d7b954_appdaemon/apps"
APPDAEMON_SHARED_ROOTS = ("/share/", "/media/")

# The name a client sees in serverInfo. Clients that key the namespace they show
# for a server's tools off it end up with one reachable server when two instances
# report the same name, so the name is configurable per instance.
CONF_SERVER_NAME = "server_name"
DEFAULT_SERVER_NAME = "home-assistant-mcp-server"

# What the initialize result tells a client's model about using the server, so it
# starts with the right tool rather than exploring. Every tool it names is
# read-only and not behind an option; tests/test_http.py holds it to that.
SERVER_INSTRUCTIONS = (
    "Tools for the owner's Home Assistant. Answer in as few calls as you can: go "
    "straight to the tool for the kind of question.\n"
    "- The state of something by name or room ('are the office lights on?', 'how warm "
    "is the bedroom?'): search_entities with the thing and the room as plain words, "
    "such as 'office lights' or 'bedroom temperature'. It matches names, aliases, rooms, "
    "floors and devices in any order and returns current states, so there is no need to "
    "look up areas or entity IDs first. Names are in the owner's language; when a word "
    "matches nothing, the reply lists the rooms.\n"
    "- Several entities whose IDs you already know: batch_get_state.\n"
    "- Why something did or did not happen: get_logbook and get_history for what "
    "changed and when, list_traces and get_trace for what an automation or script did.\n"
    "- Where an entity, service or name is used in automations, scripts, scenes or "
    "dashboards: search_config.\n"
    "- Energy use and other long-term figures: get_statistics.\n"
    "Which tools are offered depends on what the owner allows, so one named here may "
    "be missing."
)


def validate_server_name(value: str) -> str:
    """Validate the client-facing server name."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Server name must be a non-empty string")
    return value.strip()


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
        # /share and /media are the only writable folders Home Assistant Core
        # shares with add-ons on Home Assistant OS; an add-on's private config
        # folder (/addon_configs, /app_configs) is not mounted into Core at all.
        raise ValueError(
            "AppDaemon apps root must be under /share or /media, the folders Home "
            "Assistant Core shares with add-ons; add-on private folders such as "
            "/addon_configs and /app_configs are not visible to Core"
        )
    return normalized


# HTTP paths. Home Assistant's built-in mcp_server integration serves its
# streamable transport on /api/mcp from 2025.11 onwards, and aiohttp resolves a
# duplicated path to whichever integration registered it first, silently. This
# integration therefore also answers on a path nothing else claims.
MCP_PATH = "/api/mcp"
MCP_HTTP_PATH = "/api/mcp_http"
RESOURCE_METADATA_PREFIX = "/.well-known/oauth-protected-resource"

# Repairs issue raised while another integration also serves MCP_PATH.
ISSUE_ENDPOINT_CONFLICT = "endpoint_conflict"
# Raised while more than one config entry exists. They share one set of options, so
# only the entry set up last is in effect.
ISSUE_MULTIPLE_ENTRIES = "multiple_entries"

# Repairs issue raised while the paired OIDC provider cannot bind a token's audience
# to this resource, so tokens are validated without an audience check.
ISSUE_OIDC_AUDIENCE_UNSUPPORTED = "oidc_audience_unsupported"

# First hass-oidc-server release whose validate_access_token accepts expected_audience.
OIDC_PROVIDER_MIN_AUDIENCE_VERSION = "1.5.0"
OIDC_PROVIDER_RELEASES_URL = "https://github.com/ganhammar/hass-oidc-server/releases"

# Bus event fired after every tool call that is not read-only, as an audit trail.
EVENT_TOOL_CALLED = f"{DOMAIN}_tool_called"
