"""MCP tool definitions and handlers for Home Assistant."""

import json
import logging
from datetime import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

_LOGGER = logging.getLogger(__name__)

# Tool registry: name -> {"schema": {...}, "handler": callable}
TOOLS: dict[str, dict[str, Any]] = {}


def register_tool(name: str, description: str, input_schema: dict[str, Any]):
    """Decorator to register a tool with its schema and handler."""

    def decorator(func):
        TOOLS[name] = {
            "schema": {
                "name": name,
                "description": description,
                "inputSchema": input_schema,
            },
            "handler": func,
        }
        return func

    return decorator


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return all tool schemas."""
    return [tool["schema"] for tool in TOOLS.values()]


async def call_tool(hass: HomeAssistant, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool by name."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    return await tool["handler"](hass, arguments)


# --- Tool Implementations ---


@register_tool(
    name="get_state",
    description="Get the state of a Home Assistant entity",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity ID (e.g., light.living_room)",
            }
        },
        "required": ["entity_id"],
    },
)
async def get_state(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get entity state."""
    entity_id = arguments["entity_id"]
    state = hass.states.get(entity_id)

    if state is None:
        return {"content": [{"type": "text", "text": f"Entity {entity_id} not found"}]}

    result = {
        "entity_id": state.entity_id,
        "state": state.state,
        "attributes": dict(state.attributes),
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@register_tool(
    name="call_service",
    description="Call a Home Assistant service",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "The service domain (e.g., light, switch)",
            },
            "service": {
                "type": "string",
                "description": "The service name (e.g., turn_on, turn_off)",
            },
            "entity_id": {
                "type": "string",
                "description": "The entity ID to target",
            },
            "data": {
                "type": "object",
                "description": "Additional service data",
            },
        },
        "required": ["domain", "service"],
    },
)
async def call_service(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a Home Assistant service."""
    domain = arguments["domain"]
    service = arguments["service"]
    entity_id = arguments.get("entity_id")
    data = arguments.get("data", {})

    service_data = {**data}
    if entity_id:
        service_data["entity_id"] = entity_id

    try:
        await hass.services.async_call(domain, service, service_data, blocking=True)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully called {domain}.{service}",
                }
            ]
        }
    except Exception as e:
        _LOGGER.error("Error calling service: %s", e)
        return {"content": [{"type": "text", "text": f"Error calling service: {str(e)}"}]}


@register_tool(
    name="list_entities",
    description="List all entities in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Filter by domain (optional)",
            }
        },
    },
)
async def list_entities(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List entities."""
    domain_filter = arguments.get("domain")

    entities = []
    for state in hass.states.async_all():
        if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
            continue
        entities.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
            }
        )

    return {"content": [{"type": "text", "text": json.dumps(entities, indent=2)}]}


@register_tool(
    name="get_config",
    description="Get Home Assistant configuration info (version, location, units, timezone)",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def get_config(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get Home Assistant configuration."""
    config = hass.config
    result = {
        "location_name": config.location_name,
        "latitude": config.latitude,
        "longitude": config.longitude,
        "elevation": config.elevation,
        "unit_system": config.units.as_dict(),
        "time_zone": str(config.time_zone),
        "version": config.version,
        "currency": config.currency,
        "country": config.country,
        "language": config.language,
    }

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@register_tool(
    name="list_areas",
    description="List all areas in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def list_areas(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all areas."""
    registry = ar.async_get(hass)
    areas = [
        {
            "id": area.id,
            "name": area.name,
            "floor_id": area.floor_id,
        }
        for area in registry.async_list_areas()
    ]

    return {"content": [{"type": "text", "text": json.dumps(areas, indent=2)}]}


@register_tool(
    name="list_devices",
    description="List devices in Home Assistant, optionally filtered by area",
    input_schema={
        "type": "object",
        "properties": {
            "area_id": {
                "type": "string",
                "description": "Filter by area ID (optional)",
            }
        },
    },
)
async def list_devices(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List devices."""
    registry = dr.async_get(hass)
    area_filter = arguments.get("area_id")

    devices = []
    for device in registry.devices.values():
        if area_filter and device.area_id != area_filter:
            continue
        devices.append(
            {
                "id": device.id,
                "name": device.name,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "area_id": device.area_id,
                "name_by_user": device.name_by_user,
            }
        )

    return {"content": [{"type": "text", "text": json.dumps(devices, indent=2)}]}


@register_tool(
    name="list_services",
    description="List available services in Home Assistant, optionally filtered by domain",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Filter by domain (optional)",
            }
        },
    },
)
async def list_services(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List available services."""
    domain_filter = arguments.get("domain")
    services = hass.services.async_services()

    if domain_filter:
        services = {k: v for k, v in services.items() if k == domain_filter}

    # Convert service objects to serializable format
    result = {}
    for domain, domain_services in services.items():
        result[domain] = list(domain_services.keys())

    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


@register_tool(
    name="render_template",
    description="Evaluate a Jinja2 template in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "template": {
                "type": "string",
                "description": "The Jinja2 template string to render",
            },
            "variables": {
                "type": "object",
                "description": "Optional variables to pass to the template",
            },
        },
        "required": ["template"],
    },
)
async def render_template(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Render a Jinja2 template."""
    from homeassistant.helpers.template import Template

    template_str = arguments["template"]
    variables = arguments.get("variables", {})

    try:
        tpl = Template(template_str, hass)
        result = tpl.async_render(variables=variables, parse_result=False)
        return {"content": [{"type": "text", "text": str(result)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error rendering template: {str(e)}"}]}


@register_tool(
    name="get_history",
    description="Get state history of an entity over a time range",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity ID",
            },
            "start_time": {
                "type": "string",
                "description": "Start time in ISO format (e.g., 2024-01-01T00:00:00)",
            },
            "end_time": {
                "type": "string",
                "description": "End time in ISO format (optional, defaults to now)",
            },
        },
        "required": ["entity_id", "start_time"],
    },
)
async def get_history(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get state history for an entity."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_significant_states

    entity_id = arguments["entity_id"]
    start_time = dt.fromisoformat(arguments["start_time"])
    end_time_str = arguments.get("end_time")
    end_time = dt.fromisoformat(end_time_str) if end_time_str else dt.now()

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states,
            hass,
            start_time,
            end_time,
            [entity_id],
        )

        history = []
        for state in states.get(entity_id, []):
            history.append(
                {
                    "state": state.state,
                    "last_changed": state.last_changed.isoformat(),
                    "attributes": dict(state.attributes),
                }
            )
        return {"content": [{"type": "text", "text": json.dumps(history, indent=2)}]}
    except Exception as e:
        _LOGGER.error("Error getting history: %s", e)
        return {"content": [{"type": "text", "text": f"Error getting history: {str(e)}"}]}


# --- Config CRUD Tools (automations, scenes, scripts) ---


@register_tool(
    name="create_automation",
    description="Create a new automation in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "description": (
                    "Automation configuration (alias, trigger, action, condition, mode, etc.)"
                ),
            }
        },
        "required": ["config"],
    },
)
async def create_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new automation."""
    from .config_manager import create_list_entry

    try:
        entry_id = await create_list_entry(
            hass, "automations.yaml", arguments["config"], "automation"
        )
        return {
            "content": [
                {"type": "text", "text": f"Successfully created automation with id: {entry_id}"}
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating automation: {str(e)}"}]}


@register_tool(
    name="update_automation",
    description="Update an existing automation in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "automation_id": {
                "type": "string",
                "description": "The automation ID to update",
            },
            "config": {
                "type": "object",
                "description": "Updated automation config"
                " (alias, trigger, action, condition, mode, etc.)",
            },
        },
        "required": ["automation_id", "config"],
    },
)
async def update_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update an existing automation."""
    from .config_manager import update_list_entry

    try:
        await update_list_entry(
            hass,
            "automations.yaml",
            arguments["automation_id"],
            arguments["config"],
            "automation",
        )
        return {"content": [{"type": "text", "text": "Successfully updated automation"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating automation: {str(e)}"}]}


@register_tool(
    name="delete_automation",
    description="Delete an automation from Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "automation_id": {
                "type": "string",
                "description": "The automation ID to delete",
            }
        },
        "required": ["automation_id"],
    },
)
async def delete_automation(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete an automation."""
    from .config_manager import delete_list_entry

    try:
        await delete_list_entry(hass, "automations.yaml", arguments["automation_id"], "automation")
        return {"content": [{"type": "text", "text": "Successfully deleted automation"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting automation: {str(e)}"}]}


@register_tool(
    name="create_scene",
    description="Create a new scene in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "description": "Scene configuration (name, entities, etc.)",
            }
        },
        "required": ["config"],
    },
)
async def create_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new scene."""
    from .config_manager import create_list_entry

    try:
        entry_id = await create_list_entry(hass, "scenes.yaml", arguments["config"], "scene")
        return {
            "content": [{"type": "text", "text": f"Successfully created scene with id: {entry_id}"}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating scene: {str(e)}"}]}


@register_tool(
    name="update_scene",
    description="Update an existing scene in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "scene_id": {
                "type": "string",
                "description": "The scene ID to update",
            },
            "config": {
                "type": "object",
                "description": "Updated scene configuration (name, entities, etc.)",
            },
        },
        "required": ["scene_id", "config"],
    },
)
async def update_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update an existing scene."""
    from .config_manager import update_list_entry

    try:
        await update_list_entry(
            hass, "scenes.yaml", arguments["scene_id"], arguments["config"], "scene"
        )
        return {"content": [{"type": "text", "text": "Successfully updated scene"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating scene: {str(e)}"}]}


@register_tool(
    name="delete_scene",
    description="Delete a scene from Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "scene_id": {
                "type": "string",
                "description": "The scene ID to delete",
            }
        },
        "required": ["scene_id"],
    },
)
async def delete_scene(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a scene."""
    from .config_manager import delete_list_entry

    try:
        await delete_list_entry(hass, "scenes.yaml", arguments["scene_id"], "scene")
        return {"content": [{"type": "text", "text": "Successfully deleted scene"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting scene: {str(e)}"}]}


@register_tool(
    name="create_script",
    description="Create a new script in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Script identifier (becomes script.{key} entity)",
            },
            "config": {
                "type": "object",
                "description": "Script configuration (alias, sequence, mode, etc.)",
            },
        },
        "required": ["key", "config"],
    },
)
async def create_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new script."""
    from .config_manager import create_dict_entry

    try:
        key = await create_dict_entry(
            hass, "scripts.yaml", arguments["key"], arguments["config"], "script"
        )
        return {
            "content": [{"type": "text", "text": f"Successfully created script with key: {key}"}]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating script: {str(e)}"}]}


@register_tool(
    name="update_script",
    description="Update an existing script in Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The script key to update",
            },
            "config": {
                "type": "object",
                "description": "Updated script configuration (alias, sequence, mode, etc.)",
            },
        },
        "required": ["key", "config"],
    },
)
async def update_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update an existing script."""
    from .config_manager import update_dict_entry

    try:
        await update_dict_entry(
            hass, "scripts.yaml", arguments["key"], arguments["config"], "script"
        )
        return {"content": [{"type": "text", "text": "Successfully updated script"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating script: {str(e)}"}]}


@register_tool(
    name="delete_script",
    description="Delete a script from Home Assistant",
    input_schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The script key to delete",
            }
        },
        "required": ["key"],
    },
)
async def delete_script(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a script."""
    from .config_manager import delete_dict_entry

    try:
        await delete_dict_entry(hass, "scripts.yaml", arguments["key"], "script")
        return {"content": [{"type": "text", "text": "Successfully deleted script"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting script: {str(e)}"}]}


# --- Dashboard Tools ---


@register_tool(
    name="list_dashboards",
    description="List all Lovelace dashboards with metadata (url_path, title, icon, mode)",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
async def list_dashboards_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all dashboards."""
    from .dashboard_manager import list_dashboards

    try:
        dashboards = await list_dashboards(hass)
        return {"content": [{"type": "text", "text": json.dumps(dashboards, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error listing dashboards: {str(e)}"}]}


@register_tool(
    name="get_dashboard_config",
    description=(
        "Get the full configuration (views and cards) of a Lovelace dashboard. "
        'Use url_path="default" for the main Overview dashboard.'
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": (
                    'Dashboard URL path (e.g., "energy", "map"). '
                    'Use "default" for the main Overview dashboard.'
                ),
            }
        },
        "required": ["url_path"],
    },
)
async def get_dashboard_config_tool(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Get dashboard configuration."""
    from .dashboard_manager import get_dashboard_config

    try:
        config = await get_dashboard_config(hass, arguments["url_path"])
        return {"content": [{"type": "text", "text": json.dumps(config, indent=2)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error getting dashboard config: {str(e)}"}]}


@register_tool(
    name="save_dashboard_config",
    description=(
        "Save (replace) the full configuration of a Lovelace dashboard. "
        'Use url_path="default" for the main Overview dashboard.'
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": (
                    'Dashboard URL path (e.g., "energy", "map"). '
                    'Use "default" for the main Overview dashboard.'
                ),
            },
            "config": {
                "type": "object",
                "description": "Full dashboard config with views and cards",
            },
        },
        "required": ["url_path", "config"],
    },
)
async def save_dashboard_config_tool(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Save dashboard configuration."""
    from .dashboard_manager import save_dashboard_config

    try:
        await save_dashboard_config(hass, arguments["url_path"], arguments["config"])
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully saved config for dashboard '{arguments['url_path']}'",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error saving dashboard config: {str(e)}"}]}


@register_tool(
    name="delete_dashboard_config",
    description=(
        "Delete (reset) the configuration of a Lovelace dashboard to empty. "
        'Use url_path="default" for the main Overview dashboard.'
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": (
                    'Dashboard URL path (e.g., "energy", "map"). '
                    'Use "default" for the main Overview dashboard.'
                ),
            }
        },
        "required": ["url_path"],
    },
)
async def delete_dashboard_config_tool(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Delete dashboard configuration."""
    from .dashboard_manager import delete_dashboard_config

    try:
        await delete_dashboard_config(hass, arguments["url_path"])
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully deleted config for dashboard '{arguments['url_path']}'",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting dashboard config: {str(e)}"}]}


@register_tool(
    name="create_dashboard",
    description=(
        "Create a new Lovelace dashboard (experimental). "
        "This uses internal HA APIs that may change between versions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": "URL path for the new dashboard (e.g., 'my-dashboard')",
            },
            "title": {
                "type": "string",
                "description": "Dashboard title shown in sidebar",
            },
            "icon": {
                "type": "string",
                "description": "MDI icon for the sidebar (e.g., 'mdi:view-dashboard')",
            },
            "require_admin": {
                "type": "boolean",
                "description": "Whether the dashboard requires admin access (default: false)",
            },
            "show_in_sidebar": {
                "type": "boolean",
                "description": "Whether to show the dashboard in the sidebar (default: true)",
            },
        },
        "required": ["url_path", "title"],
    },
)
async def create_dashboard_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a new dashboard."""
    from .dashboard_manager import create_dashboard

    try:
        item = await create_dashboard(
            hass,
            url_path=arguments["url_path"],
            title=arguments["title"],
            icon=arguments.get("icon"),
            require_admin=arguments.get("require_admin", False),
            show_in_sidebar=arguments.get("show_in_sidebar", True),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Successfully created dashboard '{arguments['url_path']}': "
                        f"{json.dumps(item, indent=2)}"
                    ),
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error creating dashboard: {str(e)}"}]}


@register_tool(
    name="update_dashboard",
    description=(
        "Update a Lovelace dashboard's metadata such as title, icon, or visibility "
        "(experimental). This uses internal HA APIs that may change between versions."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": "URL path of the dashboard to update",
            },
            "title": {
                "type": "string",
                "description": "New dashboard title",
            },
            "icon": {
                "type": "string",
                "description": "New MDI icon (e.g., 'mdi:view-dashboard')",
            },
            "require_admin": {
                "type": "boolean",
                "description": "Whether the dashboard requires admin access",
            },
            "show_in_sidebar": {
                "type": "boolean",
                "description": "Whether to show the dashboard in the sidebar",
            },
        },
        "required": ["url_path"],
    },
)
async def update_dashboard_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update dashboard metadata."""
    from .dashboard_manager import update_dashboard

    url_path = arguments["url_path"]
    fields = {k: v for k, v in arguments.items() if k != "url_path"}

    try:
        item = await update_dashboard(hass, url_path, **fields)
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Successfully updated dashboard '{url_path}': "
                        f"{json.dumps(item, indent=2)}"
                    ),
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error updating dashboard: {str(e)}"}]}


@register_tool(
    name="delete_dashboard",
    description=(
        "Delete a Lovelace dashboard and its stored configuration (experimental). "
        "This uses internal HA APIs that may change between versions. "
        "Cannot delete the default dashboard."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url_path": {
                "type": "string",
                "description": "URL path of the dashboard to delete",
            }
        },
        "required": ["url_path"],
    },
)
async def delete_dashboard_tool(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a dashboard."""
    from .dashboard_manager import delete_dashboard

    try:
        await delete_dashboard(hass, arguments["url_path"])
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully deleted dashboard '{arguments['url_path']}'",
                }
            ]
        }
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error deleting dashboard: {str(e)}"}]}
