"""The access a person can grant an application, and what each part of the server needs.

A person who connects an application through the paired OIDC provider chooses on its
consent screen what the application may do here: one of four levels, or a setting of
None, Read or Full for each of thirteen areas. The choice reaches this server as scopes
in the access token. This module is the one table both sides of that work from:
``oidc_provider.py`` describes the areas and levels to the provider from it, and the
tools, resources, prompts and completions are limited by it on every request.

A tool needs Read in its area when it is annotated read-only and Full otherwise, so the
``readOnlyHint`` annotation decides the level here as it decides read-only mode. A
resource needs Read in its area. A prompt needs Read in every area whose data it puts
into the text it returns: it changes nothing itself, and any change it suggests goes
through tools that are checked on their own.

Scopes are looked up as whole strings in ``SCOPE_GRANTS``, never matched by prefix, so
a scope this server does not define grants nothing. The ``mcp:`` prefix is used in one
place only, to tell whether a token carries any scope of this server at all, and that
can only limit a token further.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache, partial
from types import MappingProxyType
from typing import Any

from .caller import Caller, current_caller

_LOGGER = logging.getLogger(__name__)

SCOPE_PREFIX = "mcp:"

NONE = "none"
READ = "read"
FULL = "full"
# Lowest first: a level allows everything the levels before it allow.
LEVELS = (NONE, READ, FULL)
LEVEL_TITLES = {NONE: "None", READ: "Read", FULL: "Full"}


def _rank(level: str) -> int:
    return LEVELS.index(level)


@dataclass(frozen=True)
class Area:
    """A part of Home Assistant that access is chosen for, as None, Read or Full."""

    id: str
    title: str
    section: str
    highest: str

    @property
    def options(self) -> tuple[str, ...]:
        """The settings the consent screen offers for this area, lowest first."""
        return LEVELS[: _rank(self.highest) + 1]

    def scope(self, level: str) -> str:
        """The scope that grants this area at a level other than None."""
        return f"{SCOPE_PREFIX}{self.id}:{level}"


# The headings the areas are grouped under on the consent screen, in order.
SECTIONS = {"devices": "Devices", "setup": "Setup", "system": "System"}

ENTITIES = "entities"
CALENDARS = "calendars"
CAMERAS = "cameras"
AUTOMATIONS = "automations"
DASHBOARDS = "dashboards"
HELPERS = "helpers"
REGISTRIES = "registries"
TEMPLATES = "templates"
KNX = "knx"
DIAGNOSTICS = "diagnostics"
INTEGRATIONS = "integrations"
FILES = "files"
APPDAEMON = "appdaemon"

AREAS: Mapping[str, Area] = MappingProxyType(
    {
        area.id: area
        for area in (
            Area(ENTITIES, "Entities and their states", "devices", FULL),
            Area(CALENDARS, "Calendars", "devices", FULL),
            Area(CAMERAS, "Cameras and images", "devices", READ),
            Area(AUTOMATIONS, "Automations, scripts and scenes", "setup", FULL),
            Area(DASHBOARDS, "Dashboards", "setup", FULL),
            Area(HELPERS, "Helpers", "setup", FULL),
            Area(REGISTRIES, "Areas, floors, devices and labels", "setup", FULL),
            Area(TEMPLATES, "Templates", "setup", READ),
            Area(KNX, "KNX", "setup", FULL),
            Area(DIAGNOSTICS, "Diagnostics", "system", FULL),
            Area(INTEGRATIONS, "Integrations", "system", FULL),
            Area(FILES, "Config files", "system", FULL),
            Area(APPDAEMON, "AppDaemon apps", "system", FULL),
        )
    }
)


@dataclass(frozen=True)
class Preset:
    """A level: one setting for every area, granted by a single scope."""

    id: str
    title: str
    levels: Mapping[str, str]

    @property
    def scope(self) -> str:
        return f"{SCOPE_PREFIX}{self.id}"


# What each level sets every area to. Each level turns one more section to Full.
# Config files and AppDaemon apps stay at None below Administer: they are read
# tools, but the files often hold passwords written inline.
_PRESET_IDS = ("read", "control", "configure", "admin")
_PRESET_TITLES = ("Look only", "Control devices", "Edit the setup", "Administer")
_PRESET_TABLE = {
    #              Look only  Control  Edit the setup  Administer
    ENTITIES: (READ, FULL, FULL, FULL),
    CALENDARS: (READ, FULL, FULL, FULL),
    CAMERAS: (READ, READ, READ, READ),
    AUTOMATIONS: (READ, READ, FULL, FULL),
    DASHBOARDS: (READ, READ, FULL, FULL),
    HELPERS: (READ, READ, FULL, FULL),
    REGISTRIES: (READ, READ, FULL, FULL),
    TEMPLATES: (READ, READ, READ, READ),
    KNX: (READ, READ, FULL, FULL),
    DIAGNOSTICS: (READ, READ, READ, FULL),
    INTEGRATIONS: (READ, READ, READ, FULL),
    FILES: (NONE, NONE, NONE, FULL),
    APPDAEMON: (NONE, NONE, NONE, FULL),
}

PRESETS: tuple[Preset, ...] = tuple(
    Preset(
        preset_id,
        title,
        MappingProxyType({area: row[index] for area, row in _PRESET_TABLE.items()}),
    )
    for index, (preset_id, title) in enumerate(zip(_PRESET_IDS, _PRESET_TITLES, strict=True))
)

# Every scope this server understands, and the setting it grants in each area it names.
SCOPE_GRANTS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        **{preset.scope: preset.levels for preset in PRESETS},
        **{
            area.scope(level): MappingProxyType({area.id: level})
            for area in AREAS.values()
            for level in area.options
            if level != NONE
        },
    }
)


def describe_requirement(area: str, level: str) -> str:
    """Name an area and setting as the consent screen does, such as "Dashboards at Full"."""
    return f"{AREAS[area].title} at {LEVEL_TITLES[level]}"


@dataclass(frozen=True)
class Grant:
    """What a token's scopes allow in each area."""

    levels: Mapping[str, str]

    def allows(self, area: str, level: str) -> bool:
        """Return whether the grant reaches ``level`` in ``area``."""
        return _rank(self.levels.get(area, NONE)) >= _rank(level)

    @property
    def grants_anything(self) -> bool:
        return any(level != NONE for level in self.levels.values())


def carries_server_scope(scopes: Iterable[str]) -> bool:
    """Return whether any scope is one of this server's, known or not."""
    return any(scope.startswith(SCOPE_PREFIX) for scope in scopes)


@lru_cache(maxsize=64)
def grant_from_scopes(scopes: frozenset[str]) -> Grant:
    """Return what a set of scopes grants: in each area, the highest any scope gives it.

    A token can carry a level's shorthand, one scope per area, or both. A scope not in
    ``SCOPE_GRANTS`` grants nothing.
    """
    levels = dict.fromkeys(AREAS, NONE)
    for scope in scopes:
        for area, level in SCOPE_GRANTS.get(scope, {}).items():
            if _rank(level) > _rank(levels[area]):
                levels[area] = level
    return Grant(MappingProxyType(levels))


def grant_for(caller: Caller | None) -> Grant | None:
    """Return what a caller's token allows, or None when its scopes do not limit it.

    Only an OAuth token can be limited, and the transport decides when it is: see
    ``Caller.limited_by_scopes``. A Long-Lived Access Token, an OAuth token that is not
    limited, and code running outside a request are limited by the integration's options
    alone.
    """
    if caller is None or not caller.limited_by_scopes:
        return None
    return grant_from_scopes(caller.scopes)


def current_grant() -> Grant | None:
    """Return what the token of the request being served allows, or None when unlimited."""
    return grant_for(current_caller())


def not_granted(what: str, area: str, level: str) -> str:
    """Explain a refusal: the access granted to the application does not reach this."""
    return (
        f"The access granted to this application does not include {what}: it needs "
        f"{describe_requirement(area, level)}. The person who connected the application "
        "can change what it may do under Connected applications in Home Assistant"
    )


# The area each tool belongs to. Whether it needs Read or Full there follows its
# readOnlyHint annotation.
_TOOLS_BY_AREA: dict[str, tuple[str, ...]] = {
    ENTITIES: (
        "get_state",
        "batch_get_state",
        "list_entities",
        "search_entities",
        "get_domain_stats",
        "list_services",
        "describe_service",
        "get_history",
        "get_logbook",
        "get_statistics",
        "list_statistic_ids",
        "validate_statistics",
        "call_service",
    ),
    CALENDARS: (
        "list_calendar_events",
        "create_calendar_event",
        "create_recurring_calendar_event",
        "delete_calendar_events",
    ),
    CAMERAS: ("get_camera_image", "get_image_file"),
    AUTOMATIONS: (
        "search_config",
        "list_automations",
        "get_automation_config",
        "create_automation",
        "update_automation",
        "delete_automation",
        "list_scenes",
        "get_scene_config",
        "create_scene",
        "update_scene",
        "delete_scene",
        "list_scripts",
        "get_script_config",
        "create_script",
        "update_script",
        "delete_script",
        "list_traces",
        "get_trace",
    ),
    DASHBOARDS: (
        "list_dashboards",
        "get_dashboard_config",
        "save_dashboard_config",
        "patch_dashboard_config",
        "delete_dashboard_config",
        "create_dashboard",
        "update_dashboard",
        "delete_dashboard",
    ),
    HELPERS: (
        "list_helpers",
        "get_helper_config",
        "create_helper",
        "update_helper",
        "delete_helper",
    ),
    REGISTRIES: (
        "list_areas",
        "create_area",
        "update_area",
        "delete_area",
        "list_floors",
        "create_floor",
        "update_floor",
        "delete_floor",
        "list_devices",
        "get_device_details",
        "update_device",
        "list_labels",
        "create_label",
        "update_label",
        "delete_label",
        "list_categories",
        "set_category",
        "set_icon",
        "list_entity_registry",
        "get_entity_registry",
        "update_entity_registry",
        "delete_entity",
    ),
    TEMPLATES: ("render_template",),
    KNX: (
        "knx_recent_telegrams",
        "knx_get_base_data",
        "knx_get_entities",
        "knx_create_entity",
        "knx_update_entity",
        "knx_delete_entity",
    ),
    # Correcting and clearing statistics sit here rather than with the statistics they
    # change: they rewrite the recorder's history, as the recorder's own services do,
    # and those need this area at Full. Firing an event sits here too: Home Assistant
    # lets only administrators fire events, since core and integrations act on events
    # such as user_removed as if Home Assistant itself had fired them.
    DIAGNOSTICS: (
        "get_config",
        "get_system_status",
        "get_system_health",
        "check_config",
        "list_repairs",
        "list_backups",
        "get_error_log",
        "adjust_statistics",
        "clear_statistics",
        "fire_event",
        "restart_ha",
    ),
    # Removing a device sits with integrations, next to the option that switches it on.
    INTEGRATIONS: (
        "list_integrations",
        "start_config_flow",
        "continue_config_flow",
        "abort_config_flow",
        "start_options_flow",
        "start_reconfigure_flow",
        "reload_integration",
        "set_integration_enabled",
        "delete_integration",
        "delete_device",
    ),
    FILES: (
        "list_config_files",
        "get_config_file",
        "save_config_file",
        "delete_config_file",
        "batch_edit_config_files",
        "backup_config_files",
        "list_config_backups",
        "cleanup_config_backups",
        "restore_config_backup",
    ),
    APPDAEMON: (
        "list_appdaemon_files",
        "get_appdaemon_file",
        "save_appdaemon_file",
        "delete_appdaemon_file",
        "backup_appdaemon_files",
        "list_appdaemon_backups",
        "cleanup_appdaemon_backups",
        "restore_appdaemon_backup",
    ),
}

TOOL_AREAS: Mapping[str, str] = MappingProxyType(
    {tool: area for area, tools in _TOOLS_BY_AREA.items() for tool in tools}
)

# The area each resource reads, by its URI or URI template.
RESOURCE_AREAS: Mapping[str, str] = MappingProxyType(
    {
        "hass://config": DIAGNOSTICS,
        "hass://areas": REGISTRIES,
        "hass://floors": REGISTRIES,
        "hass://devices": REGISTRIES,
        "hass://labels": REGISTRIES,
        "hass://services": ENTITIES,
        "hass://entities": ENTITIES,
        "hass://entity/{entity_id}": ENTITIES,
        "hass://entities/domain/{domain}": ENTITIES,
        "hass://dashboard/{url_path}": DASHBOARDS,
        "hass://integrations": INTEGRATIONS,
    }
)

# The areas whose data each prompt puts into its text. A prompt needs Read in all of them.
PROMPT_AREAS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "automation_review": (AUTOMATIONS,),
        "automation_builder": (ENTITIES,),
        "automation_debugger": (AUTOMATIONS, ENTITIES),
        "automation_audit": (AUTOMATIONS, ENTITIES),
        "troubleshoot_device": (ENTITIES,),
        "setup_guide": (ENTITIES,),
        "schedule_optimizer": (AUTOMATIONS, ENTITIES),
        "naming_conventions": (ENTITIES,),
        "daily_summary": (ENTITIES,),
        "energy_report": (ENTITIES,),
        "dashboard_builder": (ENTITIES, REGISTRIES),
        "change_validator": (AUTOMATIONS, DIAGNOSTICS),
        "security_review": (ENTITIES, INTEGRATIONS, DIAGNOSTICS),
    }
)

# The area whose data a completion of each argument lists. Completions from a fixed
# list, such as trigger types, read nothing and need no area.
COMPLETION_AREAS: Mapping[str, str] = MappingProxyType(
    {
        "entity_id": ENTITIES,
        "entity_ids": ENTITIES,
        "domain": ENTITIES,
        "service": ENTITIES,
        "area_id": REGISTRIES,
        "url_path": DASHBOARDS,
        "automation_id": AUTOMATIONS,
        "scene_id": AUTOMATIONS,
        "key": AUTOMATIONS,
    }
)


def may_read(area: str) -> bool:
    """Return whether the request being served may read an area."""
    grant = current_grant()
    return grant is None or grant.allows(area, READ)


# Services that reach Home Assistant itself rather than a device: restarting or
# stopping it, running commands on the host, add-ons, backups, updates, the recorder's
# history, remote access and logging, and raw access to a bus, such as publishing any
# MQTT message. A token limited by its scopes calls them only with Diagnostics at Full,
# the area that holds restart_ha. A service Home Assistant registered as one only
# administrators may call needs the same, so the list holds what that does not
# catch on every supported Home Assistant version: services registered without the
# administrator check, such as shell_command's and python_script's, and services only
# later versions register with it, such as mqtt.publish.
SYSTEM_SERVICE_DOMAINS = frozenset(
    {
        "homeassistant",
        "hassio",
        "shell_command",
        "python_script",
        "pyscript",
        "recorder",
        "backup",
        "update",
        "cloud",
        "system_log",
        "logger",
        "profiler",
        "debugpy",
        "mqtt",
        "synology_dsm",
    }
)
# Single services that reach the system in a domain whose other services act on devices.
SYSTEM_SERVICES = frozenset({("homematic", "set_install_mode")})
# Every domain's reload service reads its configuration again, and some integrations
# register it without the administrator check.
RELOAD_SERVICE = "reload"
# The services of the homeassistant domain that act on entities, which Entities and
# their states at Full covers.
HOMEASSISTANT_ENTITY_SERVICES = frozenset({"turn_on", "turn_off", "toggle", "update_entity"})


def is_admin_service(hass: Any, domain: str, service: str) -> bool:
    """Return whether Home Assistant registered a service as one only administrators may call.

    ``async_register_admin_service`` registers a ``functools.partial`` of the private
    ``_async_admin_handler`` as the service's job target, and that is what this looks
    for in ``hass.services.async_services_internal()``, keyed by the lower-cased names
    Home Assistant registers under. Both are internals: should either change, this
    answers False rather than raising, and the fixed lists still hold. A contract test
    in tests/test_ha_api_contracts.py pins them. Must run on the event loop.
    """
    try:
        from homeassistant.helpers import service as service_helper

        admin_handler = getattr(service_helper, "_async_admin_handler", None)
        if admin_handler is None:
            return False
        registered = hass.services.async_services_internal().get(domain, {}).get(service)
        target = getattr(getattr(registered, "job", None), "target", None)
        return isinstance(target, partial) and target.func is admin_handler
    except Exception:  # noqa: BLE001 - a core change leaves the fixed lists in charge
        _LOGGER.debug("Could not tell whether %s.%s is admin-only", domain, service, exc_info=True)
        return False


def is_system_service(hass: Any, domain: Any, service: Any) -> bool:
    """Return whether a service reaches the system, by its names as Home Assistant reads them.

    Home Assistant lower-cases both names when it registers a service and when it looks
    one up, so they are compared lower-cased here too. A name that is not a string is
    not a service Home Assistant can call.
    """
    if not isinstance(domain, str) or not isinstance(service, str):
        return False
    domain, service = domain.lower(), service.lower()
    if domain == "homeassistant":
        if service not in HOMEASSISTANT_ENTITY_SERVICES:
            return True
    elif (
        domain in SYSTEM_SERVICE_DOMAINS
        or service == RELOAD_SERVICE
        or (domain, service) in SYSTEM_SERVICES
    ):
        return True
    return is_admin_service(hass, domain, service)


def service_refusal(hass: Any, domain: Any, service: Any) -> str | None:
    """Return why the request being served may not call a service, or None when it may.

    Only a token limited by its scopes is filtered. Calling any service needs Entities
    and their states at Full, which the call_service tool itself requires; a service
    that reaches the system needs Diagnostics at Full as well.
    """
    grant = current_grant()
    if (
        grant is None
        or grant.allows(DIAGNOSTICS, FULL)
        or not is_system_service(hass, domain, service)
    ):
        return None
    return not_granted(
        f"the system service {domain}.{service}, which reaches past devices",
        DIAGNOSTICS,
        FULL,
    )
