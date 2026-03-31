"""MCP prompt definitions and handlers for Home Assistant."""

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant

# Prompt registry: name -> {"definition": {...}, "handler": callable}
PROMPTS: dict[str, dict[str, Any]] = {}


def register_prompt(name: str, description: str, arguments: list[dict[str, Any]] | None = None):
    """Decorator to register a prompt with its definition and handler."""

    def decorator(func):
        PROMPTS[name] = {
            "definition": {
                "name": name,
                "description": description,
                "arguments": arguments or [],
            },
            "handler": func,
        }
        return func

    return decorator


def get_prompts() -> list[dict[str, Any]]:
    """Return all prompt definitions."""
    return [p["definition"] for p in PROMPTS.values()]


async def get_prompt(hass: HomeAssistant, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a prompt by name with arguments."""
    prompt = PROMPTS.get(name)
    if prompt is None:
        raise ValueError(f"Unknown prompt: {name}")
    handler = prompt["handler"]
    if asyncio.iscoroutinefunction(handler):
        return await handler(hass, arguments)
    return handler(hass, arguments)


# Import submodules so prompts auto-register via @register_prompt
from . import automation, diagnostics, reporting  # noqa: F401, E402
