"""Describe the access this server offers to the paired OIDC provider.

The provider (hass-oidc-server) collects this module through Home Assistant's
integration platforms and builds its consent screen from what it returns: four
levels, and thirteen areas a person sets to None, Read or Full. The person's choice
comes back in the access token as scopes, which ``access`` maps to tools. The areas,
levels and scopes come from ``access``, so the screen and the enforcement cannot
drift apart; this module adds the words.

The provider stays generic and runs nothing here except the three functions below.
Without the provider this module is never imported.
"""

from typing import Any

from homeassistant.auth.models import User
from homeassistant.core import HomeAssistant

from .access import (
    APPDAEMON,
    AREAS,
    AUTOMATIONS,
    CALENDARS,
    CAMERAS,
    DASHBOARDS,
    DIAGNOSTICS,
    ENTITIES,
    FILES,
    FULL,
    HELPERS,
    INTEGRATIONS,
    KNX,
    LEVEL_TITLES,
    NONE,
    PRESETS,
    READ,
    REGISTRIES,
    SECTIONS,
    TEMPLATES,
)
from .const import DOMAIN, MCP_HTTP_PATH, MCP_PATH

# What each setting of an area gives, shown under the area's row while it is chosen.
_OPTION_TEXT: dict[str, dict[str, str]] = {
    ENTITIES: {
        NONE: "{app} cannot see states or perform actions.",
        READ: "{app} can see states, history, the logbook and statistics.",
        FULL: "{app} can also perform actions, such as turning lights on and off.",
    },
    CALENDARS: {
        NONE: "{app} cannot see or change calendar events.",
        READ: "{app} can see calendar events.",
        FULL: "{app} can also add and delete events.",
    },
    CAMERAS: {
        NONE: "{app} cannot fetch camera images or image files.",
        READ: (
            "{app} can see what your cameras show right now, and saved images such as " "snapshots."
        ),
    },
    AUTOMATIONS: {
        NONE: "{app} cannot see or change automations, scripts and scenes.",
        READ: "{app} can see how they are set up and how they last ran.",
        FULL: "{app} can also create, change and delete them.",
    },
    DASHBOARDS: {
        NONE: "{app} cannot see or change dashboards.",
        READ: "{app} can see how your dashboards are set up.",
        FULL: "{app} can also create, change and delete dashboards.",
    },
    HELPERS: {
        NONE: "{app} cannot see or change helper settings.",
        READ: "{app} can see your helpers and their settings.",
        FULL: "{app} can also create, change and delete helpers.",
    },
    REGISTRIES: {
        NONE: "{app} cannot see or change how your home is organized.",
        READ: "{app} can see your areas, floors, devices and labels.",
        FULL: "{app} can also change them, rename entities, and remove entities.",
    },
    TEMPLATES: {
        NONE: "{app} cannot use templates.",
        READ: (
            "{app} can work out values with templates. A template can read any state in "
            "your home."
        ),
    },
    KNX: {
        NONE: "{app} cannot see KNX telegrams or change KNX entities.",
        READ: "{app} can see recent KNX telegrams and group addresses.",
        FULL: "{app} can also create and change KNX entities.",
    },
    DIAGNOSTICS: {
        NONE: "{app} cannot see system health, repairs or logs.",
        READ: ("{app} can see system health, repairs, the list of backups and the error log."),
        FULL: (
            "{app} can also restart Home Assistant, fire events, correct or clear "
            "statistics, and perform system actions such as making a backup."
        ),
    },
    INTEGRATIONS: {
        NONE: "{app} cannot see or manage integrations.",
        READ: "{app} can see which integrations are set up.",
        FULL: (
            "{app} can also add, set up, turn off and remove integrations, and remove "
            "devices. Removing an integration also removes its devices."
        ),
    },
    FILES: {
        NONE: "{app} cannot read or change configuration files.",
        READ: (
            "{app} can read your configuration files, such as configuration.yaml. "
            "Never secrets.yaml."
        ),
        FULL: (
            "{app} can also change, delete and restore them. A backup is made before "
            "every change."
        ),
    },
    APPDAEMON: {
        NONE: "{app} cannot read or change AppDaemon apps.",
        READ: "{app} can read the code and settings of your AppDaemon apps.",
        FULL: (
            "{app} can also change, delete and restore them. A backup is made before "
            "every change."
        ),
    },
}


def _when(group: str, *options: str) -> dict[str, Any]:
    return {"group": group, "in": list(options)}


_ENTITIES_SHOWN = _when(ENTITIES, READ)
_ACTIONS_ALLOWED = _when(ENTITIES, FULL)
_BELOW_FULL = (NONE, READ)

# Notes under an area's row, shown while that setting is chosen and every condition
# holds. They say where what a setting hides can still be reached.
_OPTION_NOTES: dict[tuple[str, str], list[dict[str, Any]]] = {
    (ENTITIES, FULL): [
        {
            "text": (
                "Actions reach further than devices. An action can also run a script or "
                "turn an automation off, whatever the other areas say."
            ),
            "when": [{"some_group": "below_highest"}],
        },
        {
            "text": "System actions, such as restarting Home Assistant, need Diagnostics at Full.",
            "when": [_when(DIAGNOSTICS, *_BELOW_FULL)],
        },
    ],
    (CALENDARS, NONE): [
        {
            "text": "Calendars still show up as entities, with their next event.",
            "when": [_when(ENTITIES, READ, FULL)],
        },
    ],
    (CAMERAS, NONE): [
        {"text": "Your cameras still show up as entities.", "when": [_ENTITIES_SHOWN]},
        {
            "text": (
                "Your cameras still show up as entities, and an action can still take a "
                "snapshot."
            ),
            "when": [_ACTIONS_ALLOWED],
        },
    ],
    (AUTOMATIONS, NONE): [
        {"text": "They still show up as entities.", "when": [_ENTITIES_SHOWN]},
        {
            "text": (
                "They still show up as entities, and an action can still run them or turn "
                "them off."
            ),
            "when": [_ACTIONS_ALLOWED],
        },
    ],
    (AUTOMATIONS, FULL): [
        {
            "text": (
                "An automation or script {app} writes can perform any action, restarting "
                "Home Assistant included, whatever the other areas say."
            ),
            "when": [{"some_group": "below_highest"}],
        },
    ],
    (DASHBOARDS, NONE): [
        {
            "text": "Dashboards kept in YAML files can still be changed through Config files.",
            "when": [_when(FILES, FULL)],
        },
        {
            "text": "Dashboards kept in YAML files can still be read through Config files.",
            "when": [_when(FILES, READ)],
        },
    ],
    (DASHBOARDS, READ): [
        {
            "text": "Dashboards kept in YAML files can still be changed through Config files.",
            "when": [_when(FILES, FULL)],
        },
    ],
    (HELPERS, NONE): [
        {"text": "Helpers still show up as entities.", "when": [_ENTITIES_SHOWN]},
        {
            "text": (
                "Helpers still show up as entities, and an action can still change their " "values."
            ),
            "when": [_ACTIONS_ALLOWED],
        },
    ],
    (TEMPLATES, READ): [
        {
            "text": "Templates can still read the states that Entities and their states hides.",
            "when": [_when(ENTITIES, NONE)],
        },
    ],
    (DIAGNOSTICS, FULL): [
        {
            "text": "System actions also need Entities and their states at Full.",
            "when": [_when(ENTITIES, NONE, READ)],
        },
    ],
}

# Notes under the System section, about rows that reach past the others.
_SYSTEM_NOTES: list[dict[str, Any]] = [
    {
        "text": (
            "Config files reach past the other areas. {app} can change any YAML file, such "
            "as configuration.yaml or a dashboard kept in YAML, whatever the other areas say."
        ),
        "when": [_when(FILES, FULL), {"some_group": "below_highest", "except": [FILES]}],
    },
    {
        "text": (
            "Config files reach past the other areas. {app} can read what is written in your "
            "YAML files, such as a dashboard kept in YAML, whatever the other areas say."
        ),
        "when": [_when(FILES, READ), {"some_group": "at_lowest", "except": [FILES]}],
    },
    {
        "text": (
            "AppDaemon apps are code. A changed app can do anything AppDaemon can do in Home "
            "Assistant, whatever the other areas say."
        ),
        "when": [_when(APPDAEMON, FULL), {"some_group": "below_highest", "except": [APPDAEMON]}],
    },
    {
        "text": (
            "Reading AppDaemon apps can show passwords and settings for other parts of your "
            "home, whatever the other areas say."
        ),
        "when": [_when(APPDAEMON, READ), {"some_group": "at_lowest", "except": [APPDAEMON]}],
    },
]

_ACTIONS_REACH = "Actions reach further than devices."

# What each level says on the consent screen, and the note shown while it is selected.
_PRESET_TEXT: dict[str, dict[str, Any]] = {
    "read": {
        "description": "See devices, history and settings, without changing anything.",
    },
    "control": {
        "description": ("Also turn things on and off, perform actions and manage calendar events."),
        "notes": [
            {
                "text": (
                    f"{_ACTIONS_REACH} An action can also run one of your scripts or turn an "
                    "automation off. System actions, such as restarting Home Assistant, need "
                    "Administer."
                )
            }
        ],
    },
    "configure": {
        "description": (
            "Also change automations, scripts, scenes, dashboards and helpers, and organize "
            "devices and entities."
        ),
        "notes": [
            {
                "text": (
                    f"{_ACTIONS_REACH} {{app}} can also write an automation or script that "
                    "performs any action, restarting Home Assistant included."
                )
            }
        ],
    },
    "admin": {
        "description": (
            "Also add and remove integrations and devices, change configuration files and "
            "AppDaemon apps, fire events, and restart Home Assistant."
        ),
    },
}

# What the provider's discovery documents say each scope grants.
_SCOPE_TEXT = {
    READ: "Read access to {title}.",
    FULL: "Full access to {title}, including changes.",
}

_OPTION_OFF = "Turned off in the MCP Server integration's options."
_READ_ONLY = "Not available: the MCP Server integration is in read-only mode."


def _option(area_id: str, level: str) -> dict[str, Any]:
    area = AREAS[area_id]
    option: dict[str, Any] = {
        "id": level,
        "label": LEVEL_TITLES[level],
        "description": _OPTION_TEXT[area_id][level],
    }
    if level != NONE:
        option["scopes"] = [area.scope(level)]
    if notes := _OPTION_NOTES.get((area_id, level)):
        option["notes"] = notes
    return option


def _scopes() -> dict[str, dict[str, str]]:
    scopes = {
        preset.scope: {"title": preset.title, "description": _PRESET_TEXT[preset.id]["description"]}
        for preset in PRESETS
    }
    for area in AREAS.values():
        for level in area.options:
            if level != NONE:
                scopes[area.scope(level)] = {
                    "title": f"{area.title}: {LEVEL_TITLES[level]}",
                    "description": _SCOPE_TEXT[level].format(title=area.title),
                }
    return scopes


def build_description() -> dict[str, Any]:
    """Return the description of this resource, in English."""
    sections: list[dict[str, Any]] = []
    for section_id, section_title in SECTIONS.items():
        section: dict[str, Any] = {
            "id": section_id,
            "title": section_title,
            "groups": [
                {
                    "id": area.id,
                    "title": area.title,
                    "options": [_option(area.id, level) for level in area.options],
                }
                for area in AREAS.values()
                if area.section == section_id
            ],
        }
        if section_id == "system":
            section["notes"] = _SYSTEM_NOTES
        sections.append(section)

    return {
        "resource": [MCP_PATH, MCP_HTTP_PATH],
        "name": "MCP Server",
        "prompt": "Choose what {app} can do in Home Assistant.",
        "detail_intro": (
            "For each area, choose None, Read or Full. Read lets {app} look, and Full also "
            "lets it make changes. None hides the area's tools, but the things themselves "
            "can still show up elsewhere, for example as entities."
        ),
        "scopes": _scopes(),
        "presets": [
            {
                "id": preset.id,
                "title": preset.title,
                "options": dict(preset.levels),
                "scopes": [preset.scope],
                **_PRESET_TEXT[preset.id],
            }
            for preset in PRESETS
        ],
        "default_preset": PRESETS[0].id,
        # A session from before this description could use every tool the options
        # allowed, which is what Administer gives.
        "existing_sessions_preset": PRESETS[-1].id,
        "sections": sections,
    }


def _options(hass: HomeAssistant) -> dict[str, Any]:
    options = hass.data.get(DOMAIN)
    return options if isinstance(options, dict) else {}


def unavailable_areas(hass: HomeAssistant) -> dict[str, str]:
    """Return the areas whose tools are switched off right now, with the reason.

    An area stays choosable while it is unavailable: what an application can do is
    the grant limited by what is switched on, so turning the option on later gives it
    what the person chose.
    """
    options = _options(hass)
    if not options:
        return {}
    unavailable: dict[str, str] = {}
    if not options.get("config_file_access"):
        unavailable[FILES] = _OPTION_OFF
    if not options.get("appdaemon_file_access"):
        unavailable[APPDAEMON] = _OPTION_OFF
    if not options.get("template_rendering"):
        unavailable[TEMPLATES] = _OPTION_OFF
    if not options.get("integration_management"):
        unavailable[INTEGRATIONS] = (
            "Adding, setting up and removing integrations and devices is turned off in the "
            "MCP Server integration's options. The list of integrations can still be seen."
        )
    cameras, images = options.get("camera_image_access"), options.get("image_file_access")
    if not cameras and not images:
        unavailable[CAMERAS] = _OPTION_OFF
    elif not cameras:
        unavailable[CAMERAS] = (
            "Camera images are turned off in the MCP Server integration's options. Saved "
            "images can still be seen."
        )
    elif not images:
        unavailable[CAMERAS] = (
            "Image files are turned off in the MCP Server integration's options. Camera "
            "images can still be seen."
        )

    from .tools import knx

    if knx._get_knx_module(hass) is None:
        unavailable[KNX] = "KNX is not set up in this Home Assistant."
    return unavailable


def read_only_ceiling(hass: HomeAssistant) -> dict[str, Any] | None:
    """Return the most anyone may grant while read-only mode is on, or None when it is off.

    Every area is held at Read, and every level above Look only is locked.
    """
    if not _options(hass).get("read_only"):
        return None
    return {
        "groups": {
            area.id: {"max": READ, "reason": _READ_ONLY}
            for area in AREAS.values()
            if area.highest == FULL
        },
        "locked_presets": [preset.id for preset in PRESETS[1:]],
        "reason": _READ_ONLY,
    }


async def async_describe_resource(hass: HomeAssistant, language: str) -> dict[str, Any]:
    """Describe the access this resource offers. The text is English in every language."""
    return build_description()


async def async_get_availability(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Say which areas are switched off right now, and why."""
    return unavailable_areas(hass)


async def async_get_ceiling(
    hass: HomeAssistant, user: User, language: str
) -> dict[str, Any] | None:
    """Say the most this user may grant: everyone is held at Read in read-only mode."""
    return read_only_ceiling(hass)
