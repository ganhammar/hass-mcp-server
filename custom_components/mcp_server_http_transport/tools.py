"""MCP Tools definitions and handlers for Home Assistant."""

import json
import logging
import re
from typing import Any

import yaml

from homeassistant.core import HomeAssistant
from mcp.types import TextContent, Tool

_LOGGER = logging.getLogger(__name__)


class MCPTools:
    """MCP Tools manager for Home Assistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the tools manager."""
        self.hass = hass

    def get_tools_list(self, as_mcp: bool = True) -> list[Tool] | list[dict[str, Any]]:
        """Get list of available tools.

        Args:
            as_mcp: If True, return MCP Tool objects. If False, return dicts for HTTP.
        """
        tools_data = [
            {
                "name": "get_state",
                "description": "Get the state of a Home Assistant entity",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "The entity ID (e.g., light.living_room)",
                        }
                    },
                    "required": ["entity_id"],
                },
            },
            {
                "name": "call_service",
                "description": "Call a Home Assistant service",
                "inputSchema": {
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
            },
            {
                "name": "list_entities",
                "description": "List all entities in Home Assistant",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "Filter by domain (optional)",
                        }
                    },
                },
            },
            {
                "name": "list_automations",
                "description": "List all automations in Home Assistant",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "get_automation_config",
                "description": "Get the configuration of a specific automation",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "automation_id": {
                            "type": "string",
                            "description": "The automation ID (e.g., my_automation_id)",
                        }
                    },
                    "required": ["automation_id"],
                },
            },
            {
                "name": "update_automation_config",
                "description": "Update the configuration of a specific automation",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "automation_id": {
                            "type": "string",
                            "description": "The automation ID to update (e.g., automation.morning_lights)",
                        },
                        "config": {
                            "type": "string",
                            "description": "The new configuration as a YAML string",
                        },
                    },
                    "required": ["automation_id", "config"],
                },
            },
            {
                "name": "create_automation",
                "description": "Create a new automation in Home Assistant",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "config": {
                            "type": "string",
                            "description": "The automation configuration as a YAML string. The 'alias' field is required. An 'id' will be generated automatically if not provided.",
                        },
                    },
                    "required": ["config"],
                },
            },
        ]

        if as_mcp:
            return [Tool(**tool) for tool in tools_data]
        return tools_data

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> list[TextContent] | dict[str, Any]:
        """Call a tool and return result.

        Args:
            name: Tool name
            arguments: Tool arguments
            return_format: If "mcp", return list[TextContent]. If "http", return dict.

        Returns:
            Either MCP TextContent list or HTTP dict format.
        """
        if name == "get_state":
            return await self._get_state(arguments)
        elif name == "call_service":
            return await self._call_service(arguments)
        elif name == "list_entities":
            return await self._list_entities(arguments)
        elif name == "list_automations":
            return await self._list_automations(arguments)
        elif name == "get_automation_config":
            return await self._get_automation_config(arguments)
        elif name == "update_automation_config":
            return await self._update_automation_config(arguments)
        elif name == "create_automation":
            return await self._create_automation(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")

    async def _get_state(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Get entity state."""
        entity_id = arguments["entity_id"]
        state = self.hass.states.get(entity_id)

        if state is None:
            return [TextContent(type="text", text=f"Entity {entity_id} not found")]

        result = {
            "entity_id": state.entity_id,
            "state": state.state,
            "attributes": dict(state.attributes),
            "last_changed": state.last_changed.isoformat(),
            "last_updated": state.last_updated.isoformat(),
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _call_service(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Call a Home Assistant service."""
        domain = arguments["domain"]
        service = arguments["service"]
        entity_id = arguments.get("entity_id")
        data = arguments.get("data", {})

        service_data = {**data}
        if entity_id:
            service_data["entity_id"] = entity_id

        try:
            await self.hass.services.async_call(domain, service, service_data, blocking=True)
            return [TextContent(type="text", text=f"Successfully called {domain}.{service}")]
        except Exception as e:
            _LOGGER.error("Error calling service: %s", e)
            return [TextContent(type="text", text=f"Error calling service: {str(e)}")]

    async def _list_entities(self, arguments: dict[str, Any]) -> list[TextContent]:
        """List entities."""
        domain_filter = arguments.get("domain")

        entities = []
        for state in self.hass.states.async_all():
            if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
                continue
            entities.append(
                {
                    "entity_id": state.entity_id,
                    "state": state.state,
                    "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                }
            )

        return [TextContent(type="text", text=json.dumps(entities, indent=2))]

    async def _list_automations(self, arguments: dict[str, Any]) -> list[TextContent]:
        """List automations."""
        automations = []
        for state in self.hass.states.async_all():
            if state.entity_id.startswith("automation."):
                automations.append(
                    {
                        "entity_id": state.entity_id,
                        "state": state.state,
                        "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                    }
                )

        return [TextContent(type="text", text=json.dumps(automations, indent=2))]

    async def _get_automation_config(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Get automation configuration."""
        automation_id = arguments["automation_id"]

        try:
            from homeassistant.components.automation import DATA_COMPONENT

            component = self.hass.data.get(DATA_COMPONENT)
            if component is None:
                return [TextContent(type="text", text="Automation component not available")]

            entity = component.get_entity(automation_id)
            if entity is None:
                return [TextContent(type="text", text=f"Automation {automation_id} not found")]

            plain_config = json.loads(json.dumps(entity.raw_config, default=str))
            return [
                TextContent(
                    type="text",
                    text=yaml.safe_dump(plain_config, allow_unicode=True, default_flow_style=False),
                )
            ]
        except Exception as e:
            _LOGGER.error("Error getting automation config: %s", e)
            return [TextContent(type="text", text=f"Error getting automation config: {str(e)}")]

    async def _update_automation_config(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Update automation configuration."""
        automation_id = arguments["automation_id"]
        yaml_config: str = arguments["config"]

        try:
            new_config = yaml.safe_load(yaml_config)
        except yaml.YAMLError as e:
            return [TextContent(type="text", text=f"Invalid YAML: {str(e)}")]

        if not isinstance(new_config, dict):
            return [TextContent(type="text", text="Config must be a YAML mapping")]

        automations_path = self.hass.config.path("automations.yaml")

        try:
            with open(automations_path) as f:
                automations: list[dict] = yaml.safe_load(f) or []
        except FileNotFoundError:
            automations = []

        entity_id_suffix = automation_id.removeprefix("automation.")
        updated = False
        for i, entry in enumerate(automations):
            entry_entity_id = (
                f"automation.{entry.get('alias', entry.get('id', '')).lower().replace(' ', '_')}"
            )
            if entry.get("id") == entity_id_suffix or entry_entity_id == automation_id:
                automations[i] = new_config
                updated = True
                break

        if not updated:
            return [
                TextContent(
                    type="text", text=f"Automation {automation_id} not found in automations.yaml"
                )
            ]

        try:
            with open(automations_path, "w") as f:
                yaml.dump(automations, f, allow_unicode=True, default_flow_style=False)
        except OSError as e:
            return [TextContent(type="text", text=f"Error writing automations.yaml: {str(e)}")]

        try:
            await self.hass.services.async_call("automation", "reload", {}, blocking=True)
        except Exception as e:
            _LOGGER.warning("Could not reload automations: %s", e)

        return [TextContent(type="text", text=f"Successfully updated automation {automation_id}")]

    async def _create_automation(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Create a new automation."""
        yaml_config: str = arguments["config"]

        try:
            new_config = yaml.safe_load(yaml_config)
        except yaml.YAMLError as e:
            return [TextContent(type="text", text=f"Invalid YAML: {str(e)}")]

        if not isinstance(new_config, dict):
            return [TextContent(type="text", text="Config must be a YAML mapping")]

        if not new_config.get("alias"):
            return [TextContent(type="text", text="Config must include an 'alias' field")]

        if not new_config.get("id"):
            slug = re.sub(r"[^a-z0-9]+", "_", new_config["alias"].lower()).strip("_")
            new_config["id"] = slug

        automations_path = self.hass.config.path("automations.yaml")

        try:
            with open(automations_path) as f:
                automations: list[dict] = yaml.safe_load(f) or []
        except FileNotFoundError:
            automations = []

        existing_ids = {entry.get("id") for entry in automations}
        if new_config["id"] in existing_ids:
            return [
                TextContent(
                    type="text",
                    text=f"Automation with id '{new_config['id']}' already exists. Use update_automation_config to modify it.",
                )
            ]

        automations.append(new_config)

        try:
            with open(automations_path, "w") as f:
                yaml.dump(automations, f, allow_unicode=True, default_flow_style=False)
        except OSError as e:
            return [TextContent(type="text", text=f"Error writing automations.yaml: {str(e)}")]

        try:
            await self.hass.services.async_call("automation", "reload", {}, blocking=True)
        except Exception as e:
            _LOGGER.warning("Could not reload automations: %s", e)

        entity_id = f"automation.{new_config['id']}"
        return [TextContent(type="text", text=f"Successfully created automation {entity_id}")]
