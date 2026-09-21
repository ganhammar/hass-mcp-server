"""MCP prompt definitions and handlers for Home Assistant."""

import inspect
from typing import Any

from homeassistant.core import HomeAssistant

# Prompt registry: name -> {"definition": {...}, "handler": callable}
PROMPTS: dict[str, dict[str, Any]] = {}


class InvalidPromptRequest(Exception):
    """A prompts/get the caller got wrong; the transport reports it as -32602."""


class UnknownPrompt(InvalidPromptRequest):
    """The prompt name is not registered."""


class InvalidPromptArguments(InvalidPromptRequest):
    """The arguments are not an object, omit a required value, or carry an unusable one.

    A handler raises this for a value it cannot parse (a timestamp that is not
    ISO 8601, say), which keeps a client's placeholder input a caller error
    rather than a server-side traceback.
    """


def register_prompt(
    name: str,
    description: str,
    arguments: list[dict[str, Any]] | None = None,
    annotations: dict[str, Any] | None = None,
):
    """Decorator to register a prompt with its definition and handler."""

    def decorator(func):
        definition = {
            "name": name,
            "description": description,
            "arguments": arguments or [],
        }
        if annotations is not None:
            definition["annotations"] = annotations

        PROMPTS[name] = {
            "definition": definition,
            "handler": func,
        }
        return func

    return decorator


def get_prompts() -> list[dict[str, Any]]:
    """Return all prompt definitions."""
    return [p["definition"] for p in PROMPTS.values()]


def _missing_required(definition: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Return the required arguments the call does not supply.

    An explicit null counts as absent, as it does for tools: a client drifting
    from the advertised definition sends one as readily as it drops the key.
    """
    return [
        arg["name"]
        for arg in definition.get("arguments", [])
        if arg.get("required") and arguments.get(arg["name"]) is None
    ]


def _describe_missing(definition: dict[str, Any], missing: list[str]) -> str:
    """Name each missing argument alongside its description from the definition."""
    descriptions = {arg["name"]: arg.get("description") for arg in definition.get("arguments", [])}
    return ", ".join(
        f"{name} ({descriptions[name]})" if descriptions.get(name) else name for name in missing
    )


async def get_prompt(hass: HomeAssistant, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a prompt by name with arguments.

    Anything the caller got wrong — an unknown name, a non-object arguments, a
    missing required argument — raises InvalidPromptRequest before a handler
    runs, so a handler never has to defend against a required key being absent.
    Clients that enumerate prompts at connect time send placeholder values for
    every argument, so a handler that cannot use a value raises
    InvalidPromptArguments itself rather than logging a server error.
    """
    prompt = PROMPTS.get(name)
    if prompt is None:
        raise UnknownPrompt(f"Unknown prompt: {name}")

    if not isinstance(arguments, dict):
        got = type(arguments).__name__
        raise InvalidPromptArguments(
            f"Arguments for prompt '{name}' must be a JSON object, got {got}"
        )

    missing = _missing_required(prompt["definition"], arguments)
    if missing:
        noun = "arguments" if len(missing) > 1 else "argument"
        raise InvalidPromptArguments(
            f"Missing required {noun} for prompt '{name}': "
            f"{_describe_missing(prompt['definition'], missing)}"
        )

    handler = prompt["handler"]
    if inspect.iscoroutinefunction(handler):
        return await handler(hass, arguments)
    return handler(hass, arguments)


# Import submodules so prompts auto-register via @register_prompt
from . import automation as automation  # noqa: E402
from . import automation_workflows as automation_workflows  # noqa: E402
from . import diagnostics as diagnostics  # noqa: E402
from . import optimization as optimization  # noqa: E402
from . import reporting as reporting  # noqa: E402
from . import workflows as workflows  # noqa: E402
