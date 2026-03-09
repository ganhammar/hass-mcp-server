"""MCP Server implementation."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent

from .tools import MCPTools

_LOGGER = logging.getLogger(__name__)


class HomeAssistantMCPServer:
    """Home Assistant MCP Server."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the MCP server."""
        self.hass = hass
        self.server = Server("home-assistant-mcp-server")
        self.tools = MCPTools(hass)

        # Register handlers
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register MCP handlers."""

        @self.server.list_tools()
        async def list_tools():
            """List available tools."""
            return self.tools.get_tools_list(as_mcp=True)

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent]:
            """Handle tool calls."""
            return await self.tools.call_tool(name, arguments)

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
