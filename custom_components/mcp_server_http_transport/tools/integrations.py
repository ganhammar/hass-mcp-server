"""Tools that set up, reload and remove integrations through their config flows."""

import logging
from contextlib import suppress
from typing import Any

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import DependencyError
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView

from ..const import DOMAIN
from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_NON_IDEMPOTENT,
    dumps,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)

_DISABLED_NOTE = (
    'Disabled by default: it must be enabled with "Enable integration management" in the '
    "integration options"
)

# What the caller does next for each result type that leaves the flow open.
_NEXT_STEP = {
    FlowResultType.FORM: (
        "Call continue_config_flow with user_input holding a value for each field in "
        "data_schema. When data_schema is empty the step only asks for confirmation: send an "
        "empty object as user_input."
    ),
    FlowResultType.MENU: (
        'Call continue_config_flow with user_input {"next_step_id": <one of menu_options>}.'
    ),
    FlowResultType.EXTERNAL_STEP: (
        "This step needs a browser and cannot be completed over MCP. Ask the user to open the "
        "url and finish there, then call continue_config_flow without user_input to pick the "
        "flow up, or abort_config_flow to drop it."
    ),
    FlowResultType.EXTERNAL_STEP_DONE: (
        "The external step is finished. Call continue_config_flow without user_input."
    ),
    FlowResultType.SHOW_PROGRESS: (
        "Home Assistant is working on this step. Call continue_config_flow without user_input "
        "to check again."
    ),
}


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _enabled(hass: HomeAssistant) -> bool:
    """Return True when the integration management option is on."""
    return hass.data.get(DOMAIN, {}).get("integration_management", False)


def _disabled() -> dict[str, Any]:
    """Build the response telling the caller how to enable integration management."""
    return _text(
        "Integration management is disabled. Enable it in the MCP Server integration "
        "settings: Settings → Devices & Services → MCP Server → Configure → "
        "Enable integration management."
    )


def _unknown_flow(flow_id: str) -> dict[str, Any]:
    return _text(
        f"No config flow in progress with ID '{flow_id}'. It may have finished, been aborted, "
        "or been dropped by a Home Assistant restart. Start again with start_config_flow."
    )


def _unknown_entry(entry_id: str) -> dict[str, Any]:
    return _text(f"No config entry with ID '{entry_id}'. Use list_integrations to find entry IDs.")


def _entry_json(entry: config_entries.ConfigEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": entry.title,
        "state": str(entry.state),
    }


def _flow_result_json(hass: HomeAssistant, result: data_entry_flow.FlowResult) -> dict[str, Any]:
    """Reduce a flow result to what a caller needs to take the next step.

    The data a finished flow collected is left out: it is what the caller just
    sent, and it routinely holds credentials.
    """
    result_type = result["type"]
    data: dict[str, Any] = {
        "type": str(result_type),
        "flow_id": result["flow_id"],
        "domain": result["handler"],
    }

    if result_type == FlowResultType.CREATE_ENTRY:
        data["entry"] = _entry_json(result["result"])
        return data

    for key in ("step_id", "reason", "url", "progress_action"):
        if result.get(key) is not None:
            data[key] = result[key]
    if "menu_options" in result:
        options = result["menu_options"]
        data["menu_options"] = options if isinstance(options, dict) else list(options)
    if "data_schema" in result:
        # Core's own view turns the voluptuous schema into the field list the
        # frontend renders. The library behind it differs between Home Assistant
        # versions, so the view is the one stable way to get the same list.
        view = FlowManagerResourceView(hass.config_entries.flow)
        data["data_schema"] = view._prepare_result_json(result)["data_schema"]
    for key in ("errors", "description_placeholders"):
        if result.get(key):
            data[key] = result[key]
    if result.get("last_step") is not None:
        data["last_step"] = result["last_step"]
    if result_type in _NEXT_STEP:
        data["next"] = _NEXT_STEP[result_type]
    return data


def _menu_refusal(hass: HomeAssistant, flow_id: str, user_input: dict[str, Any]) -> str | None:
    """Return why the flow's current menu step cannot take this input, or None.

    Home Assistant drops the whole flow when a menu is answered with a step the
    flow does not have, so a wrong choice is turned away before it gets there.
    The flow manager only gives out a step's type and options on the flow object
    it keeps in ``_progress``; where that cannot be read the input goes through
    unchecked.
    """
    flow = getattr(hass.config_entries.flow, "_progress", {}).get(flow_id)
    step = getattr(flow, "cur_step", None)
    if not step or step.get("type") != FlowResultType.MENU:
        return None
    options = list(step.get("menu_options", ()))
    if user_input.get("next_step_id") in options:
        return None
    return (
        'This step is a menu: user_input must be {"next_step_id": <option>} with one of '
        f"{dumps(options)}. The flow is still on the same step."
    )


@register_tool(
    name="start_config_flow",
    description=(
        "Start setting up an integration through its config flow, the same flow the "
        '"Add integration" dialog runs. Returns the flow_id and the first step: a form with '
        "its field schema, a menu, an abort reason, or the created entry when the flow needs "
        "no input. Follow the result's `next` hint, and call abort_config_flow on a flow you "
        "will not finish so it does not linger. Flows that need a browser (OAuth) report an "
        f"external step with its URL and cannot be completed here. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Domain of the integration to set up (e.g., remote_calendar)",
            }
        },
        "required": ["domain"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def start_config_flow(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Start a user-initiated config flow for a domain."""
    if not _enabled(hass):
        return _disabled()

    domain = arguments["domain"]
    try:
        result = await hass.config_entries.flow.async_init(
            domain, context={"source": config_entries.SOURCE_USER}
        )
    except data_entry_flow.UnknownHandler:
        return _text(
            f"No integration with a config flow found for domain '{domain}'. It may be "
            "misspelled, not installed, or set up through YAML only."
        )
    except DependencyError as err:
        return _text(
            f"Could not start a config flow for '{domain}': its dependencies failed to set up "
            f"({', '.join(err.failed_dependencies)})."
        )

    try:
        return _text(dumps(_flow_result_json(hass, result)))
    except Exception:
        # The caller never learns this flow's ID, so nothing else could abort it.
        with suppress(data_entry_flow.UnknownFlow):
            hass.config_entries.flow.async_abort(result["flow_id"])
        raise


@register_tool(
    name="continue_config_flow",
    description=(
        "Submit input to the current step of a config flow and return what comes next: "
        "another form or menu, validation errors on the same step, an abort reason, or the "
        "created config entry. For a form, user_input maps each data_schema field name to its "
        'value; for a menu it is {"next_step_id": "<option>"}; omit it to re-read a step that '
        "is waiting on a browser or on Home Assistant. Anything sent in user_input, passwords "
        "and API keys included, passes through this conversation and the client's transcript, "
        f"so leave flows that take long-lived credentials to the user. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flow_id": {
                "type": "string",
                "description": "The flow_id returned by start_config_flow",
            },
            "user_input": {
                "type": "object",
                "description": (
                    "Values for the current step, keyed by field name. Omit to re-read the "
                    "current step without submitting anything"
                ),
            },
        },
        "required": ["flow_id"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def continue_config_flow(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Submit one step of a config flow."""
    if not _enabled(hass):
        return _disabled()

    flow_id = arguments["flow_id"]
    user_input = arguments.get("user_input")
    if user_input is not None and not isinstance(user_input, dict):
        return _text("user_input must be an object keyed by field name.")

    if user_input and (refusal := _menu_refusal(hass, flow_id, user_input)):
        return _text(refusal)

    try:
        result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    except data_entry_flow.UnknownFlow:
        return _unknown_flow(flow_id)
    except data_entry_flow.UnknownStep as err:
        return _text(
            f"The flow tried to move to a step it does not have ({err}) and Home Assistant "
            "dropped it. Start again with start_config_flow."
        )
    except data_entry_flow.InvalidData as err:
        return _text(
            "The input does not match the step's data_schema, so nothing was submitted and "
            f"the flow is still on the same step: {dumps(err.schema_errors)}"
        )

    return _text(dumps(_flow_result_json(hass, result)))


@register_tool(
    name="abort_config_flow",
    description=(
        "Abort a config flow that is still in progress, discarding what was entered. Use it "
        f"for any flow started with start_config_flow that will not be finished. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flow_id": {
                "type": "string",
                "description": "The flow_id returned by start_config_flow",
            }
        },
        "required": ["flow_id"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def abort_config_flow(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Abort a config flow in progress."""
    if not _enabled(hass):
        return _disabled()

    flow_id = arguments["flow_id"]
    try:
        hass.config_entries.flow.async_abort(flow_id)
    except data_entry_flow.UnknownFlow:
        return _unknown_flow(flow_id)

    return _text(f"Aborted config flow '{flow_id}'")


def _own_entry(action: str) -> dict[str, Any]:
    """Refuse an operation on the entry that serves this very request."""
    return _text(
        f"Refusing to {action} the MCP Server's own config entry: it is serving this "
        "request. Do it from Settings → Devices & Services instead."
    )


@register_tool(
    name="reload_integration",
    description=(
        "Reload one config entry of an integration, unloading it and setting it up again. "
        f"Entry IDs come from list_integrations. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entry_id": {
                "type": "string",
                "description": "The config entry ID, as listed by list_integrations",
            }
        },
        "required": ["entry_id"],
    },
    annotations=ANNOTATION_IDEMPOTENT,
)
async def reload_integration(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Reload a config entry."""
    if not _enabled(hass):
        return _disabled()

    entry_id = arguments["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return _unknown_entry(entry_id)
    if entry.domain == DOMAIN:
        return _own_entry("reload")

    try:
        await hass.config_entries.async_reload(entry_id)
    except config_entries.OperationNotAllowed as err:
        return _text(f"Config entry '{entry_id}' cannot be reloaded: {err}")

    return _text(dumps({**_entry_json(entry), "require_restart": not entry.state.recoverable}))


@register_tool(
    name="delete_integration",
    description=(
        "Remove one config entry of an integration, together with the devices and entities "
        "it provides. This cannot be undone: setting the integration up again means running "
        f"its config flow from the start. Requires explicit confirmation. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entry_id": {
                "type": "string",
                "description": "The config entry ID, as listed by list_integrations",
            },
            "confirm": {
                "type": "boolean",
                "description": "Must be true to confirm removing the config entry",
            },
        },
        "required": ["entry_id", "confirm"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def delete_integration(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Remove a config entry."""
    if not _enabled(hass):
        return _disabled()

    entry_id = arguments["entry_id"]
    if arguments["confirm"] is not True:
        return _text(
            "Removing a config entry requires confirm=true. It removes the entry together "
            "with its devices and entities."
        )

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return _unknown_entry(entry_id)
    if entry.domain == DOMAIN:
        return _own_entry("remove")

    result = await hass.config_entries.async_remove(entry_id)

    return _text(
        dumps(
            {
                "removed": {"entry_id": entry_id, "domain": entry.domain, "title": entry.title},
                "require_restart": result["require_restart"],
            }
        )
    )
