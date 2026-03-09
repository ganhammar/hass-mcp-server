"""MCP Server implementation."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_LOGGER = logging.getLogger(__name__)


class HomeAssistantMCPServer:
    """Home Assistant MCP Server."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the MCP server."""
        self.hass = hass
        self.server = Server("home-assistant-mcp-server")

        # Register handlers
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register MCP handlers."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools."""
            return [
                Tool(
                    name="get_state",
                    description="Get the state of a Home Assistant entity",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "entity_id": {
                                "type": "string",
                                "description": "The entity ID (e.g., light.living_room)",
                            }
                        },
                        "required": ["entity_id"],
                    },
                ),
                Tool(
                    name="call_service",
                    description="Call a Home Assistant service",
                    inputSchema={
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
                ),
                Tool(
                    name="list_entities",
                    description="List all entities in Home Assistant",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "description": "Filter by domain (optional)",
                            }
                        },
                    },
                ),
                Tool(
                    name="list_automations",
                    description="List all automations in Home Assistant",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                Tool(
                    name="get_automation_config",
                    description="Get the configuration of a specific automation",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "automation_id": {
                                "type": "string",
                                "description": "The automation ID (e.g., my_automation_id)",
                            }
                        },
                        "required": ["automation_id"],
                    },
                ),
                Tool(
                    name="update_automation_config",
                    description="Update the configuration of a specific automation",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "automation_id": {
                                "type": "string",
                                "description": "The automation ID to update",
                            },
                            "config": {
                                "type": "object",
                                "description": "The new configuration for the automation",
                            },
                        },
                        "required": ["automation_id", "config"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent]:
            """Handle tool calls."""
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

        return [TextContent(type="text", text=str(result))]

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

        return [TextContent(type="text", text=str(entities))]

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

        return [TextContent(type="text", text=str(automations))]

    async def _get_automation_config(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Get automation configuration."""
        automation_id = arguments["automation_id"]

        try:
            # Try to get automation config from Home Assistant config
            from homeassistant.components.automation import DOMAIN

            automation_configs = self.hass.data.get(DOMAIN, {})
            config = automation_configs.get(automation_id)

            if config is None:
                return [TextContent(type="text", text=f"Automation {automation_id} not found")]

            return [TextContent(type="text", text=str(config))]
        except Exception as e:
            _LOGGER.error("Error getting automation config: %s", e)
            return [TextContent(type="text", text=f"Error getting automation config: {str(e)}")]

    async def _update_automation_config(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Update automation configuration."""
        automation_id = arguments["automation_id"]
        new_config = arguments["config"]

        try:
            # Call the automation.reload service to reload automations from config
            await self.hass.services.async_call(
                "automation",
                "reload",
                {},
                blocking=True
            )

            # Then update the specific automation with the new config
            from homeassistant.components.automation import DOMAIN

            if DOMAIN not in self.hass.data:
                self.hass.data[DOMAIN] = {}

            self.hass.data[DOMAIN][automation_id] = new_config

            return [TextContent(type="text", text=f"Successfully updated automation {automation_id}")]
        except Exception as e:
            _LOGGER.error("Error updating automation config: %s", e)
            return [TextContent(type="text", text=f"Error updating automation config: {str(e)}")]

    async def run(self, host: str, port: int) -> None:
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            init_options = InitializationOptions(
                server_name="home-assistant",
                server_version="0.1.0",
                capabilities=self.server.get_capabilities(
                    notification_options=None, experimental_capabilities=None
                ),
            )

            await self.server.run(
                read_stream,
                write_stream,
                init_options,
            )
