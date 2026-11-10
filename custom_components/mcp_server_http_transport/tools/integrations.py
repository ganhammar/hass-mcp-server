"""Tools that set up, reconfigure, reload, disable and remove integrations.

Config, reconfigure and options flows all run through the same three steps: a
start tool for each, then continue_config_flow and abort_config_flow, which find
the flow in whichever of Home Assistant's two flow managers holds it.
"""

import logging
import re
from contextlib import suppress
from typing import Any

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import DependencyError
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView

from ..const import DOMAIN
from ..secret_keys import SECRET_MASK, is_secret_key
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


# The integrations no flow or entry tool may touch: this one, whose options decide
# which tools a client may use, and the OIDC provider that guards its endpoint.
_PROTECTED_DOMAINS = frozenset({DOMAIN, "oidc_provider"})

# A URL that carries credentials in its user part or its query string.
_CREDENTIAL_URL = re.compile(r"://[^/\s]*@|://\S*\?\S*=")

_HIDDEN_NOTE = (
    f'A value shown as "{SECRET_MASK}" is stored and not revealed. Send "{SECRET_MASK}" '
    "for that field to keep it as it is."
)


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
        f"No flow in progress with ID '{flow_id}'. It may have finished, been aborted, or "
        "been dropped by a Home Assistant restart. Start it again."
    )


def _own_flow() -> dict[str, Any]:
    """Refuse a flow that would change what this connection is allowed to do.

    The MCP Server's options decide which tools a client may use, so a client that
    could run these flows could lift its own limits. The OIDC provider decides who
    may connect at all.
    """
    return _text(
        "Refusing to configure the MCP Server itself or its OIDC provider: their options "
        "decide what this connection may do. Do it from Settings → Devices & Services "
        "instead."
    )


def _blocked(hass: HomeAssistant, domain: str) -> dict[str, Any] | None:
    """Return the refusal for a domain no flow may touch, or None.

    Besides the protected integrations, a template helper renders whatever
    template it is given into its state, which is what switching template
    rendering off says a client should not do. Other integrations' flows and
    scripts also take templates, so this keeps to the option's intent and is not
    a boundary.
    """
    if domain in _PROTECTED_DOMAINS:
        return _own_flow()
    if domain == "template" and not hass.data.get(DOMAIN, {}).get("template_rendering", False):
        return _text(
            "Template helpers render templates, and template rendering is disabled. Enable "
            "it in the MCP Server integration settings: Settings → Devices & Services → "
            "MCP Server → Configure → Enable template rendering."
        )
    return None


def _is_stored_secret(field: dict[str, Any], value: Any) -> bool:
    """Return True when a value a form is filled in with must not be shown."""
    if value in (None, ""):
        return False
    selector = field.get("selector")
    text = selector.get("text") if isinstance(selector, dict) else None
    if isinstance(text, dict) and text.get("type") == "password":
        return True
    if is_secret_key(field.get("name")):
        return True
    return isinstance(value, str) and _CREDENTIAL_URL.search(value) is not None


def _hide_stored_secrets(fields: Any) -> bool:
    """Mask the stored secrets a serialized form is filled in with.

    A reconfigure or options form comes filled in with the entry's current values,
    as a field's suggested value or its default. The serialized field shares its
    description with the schema the flow goes on using, so that one is replaced
    rather than changed. Returns True when anything was masked.
    """
    hidden = False
    for field in fields if isinstance(fields, list) else ():
        if not isinstance(field, dict):
            continue
        hidden |= _hide_stored_secrets(field.get("schema"))
        description = field.get("description")
        if isinstance(description, dict) and _is_stored_secret(
            field, description.get("suggested_value")
        ):
            field["description"] = {**description, "suggested_value": SECRET_MASK}
            hidden = True
        if _is_stored_secret(field, field.get("default")):
            field["default"] = SECRET_MASK
            hidden = True
    return hidden


def _stored_values(schema: Any) -> dict[str, Any]:
    """Return what a step's form is filled in with, keyed by field name.

    Read off the voluptuous schema the flow keeps: a field's suggested value, or
    else its default. A section holds its own fields one level down.
    """
    stored: dict[str, Any] = {}
    for marker, validator in getattr(schema, "schema", {}).items():
        name = getattr(marker, "schema", None)
        if not isinstance(name, str):
            continue
        if nested := _stored_values(getattr(validator, "schema", None)):
            stored[name] = nested
            continue
        description = getattr(marker, "description", None)
        if isinstance(description, dict) and description.get("suggested_value") is not None:
            stored[name] = description["suggested_value"]
        elif callable(default := getattr(marker, "default", None)):
            stored[name] = default()
    return stored


def _restore_hidden(user_input: dict[str, Any], stored: dict[str, Any]) -> list[str]:
    """Put the stored value back wherever the input holds the mask, in place.

    Returns the fields that hold the mask with nothing stored to put there.
    """
    missing: list[str] = []
    for key, value in user_input.items():
        if isinstance(value, dict) and isinstance(stored.get(key), dict):
            missing += _restore_hidden(value, stored[key])
        elif value == SECRET_MASK:
            if stored.get(key) in (None, ""):
                missing.append(key)
            else:
                user_input[key] = stored[key]
    return missing


def _find_flow(hass: HomeAssistant, flow_id: str) -> tuple[Any, str] | None:
    """Return the manager holding a flow and the domain it configures, or None.

    A config or reconfigure flow is keyed by its domain, an options flow by the
    config entry it belongs to.
    """
    entries = hass.config_entries
    for manager in (entries.flow, entries.options):
        try:
            handler = manager.async_get(flow_id)["handler"]
        except data_entry_flow.UnknownFlow:
            continue
        if manager is entries.flow:
            return manager, handler
        entry = entries.async_get_entry(handler)
        return manager, entry.domain if entry else handler
    return None


def _unknown_entry(entry_id: str) -> dict[str, Any]:
    return _text(f"No config entry with ID '{entry_id}'. Use list_integrations to find entry IDs.")


def _entry_json(entry: config_entries.ConfigEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": entry.title,
        "state": str(entry.state),
    }


def _flow_result_json(
    hass: HomeAssistant, manager: Any, result: data_entry_flow.FlowResult
) -> dict[str, Any]:
    """Reduce a flow result to what a caller needs to take the next step.

    The data a finished flow collected is left out: it is what the caller just
    sent, and it routinely holds credentials. An options flow finishes with the
    same result type as a config flow, and reports the entry whose options it saved.
    """
    result_type = result["type"]
    data: dict[str, Any] = {"type": str(result_type), "flow_id": result["flow_id"]}
    entry = None
    if manager is hass.config_entries.options:
        entry = hass.config_entries.async_get_entry(result["handler"])
        data["domain"] = entry.domain if entry else None
        data["entry_id"] = result["handler"]
    else:
        data["domain"] = result["handler"]

    if result_type == FlowResultType.CREATE_ENTRY:
        if manager is hass.config_entries.options:
            data["options_saved"] = True
            if entry is not None:
                data["entry"] = _entry_json(entry)
        else:
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
        view = FlowManagerResourceView(manager)
        data["data_schema"] = view._prepare_result_json(result)["data_schema"]
        if _hide_stored_secrets(data["data_schema"]):
            data["hidden_values"] = _HIDDEN_NOTE
    for key in ("errors", "description_placeholders"):
        if result.get(key):
            data[key] = result[key]
    if result.get("last_step") is not None:
        data["last_step"] = result["last_step"]
    if result_type in _NEXT_STEP:
        data["next"] = _NEXT_STEP[result_type]
    return data


def _menu_refusal(manager: Any, flow_id: str, user_input: dict[str, Any]) -> str | None:
    """Return why the flow's current menu step cannot take this input, or None.

    Home Assistant drops the whole flow when a menu is answered with a step the
    flow does not have, so a wrong choice is turned away before it gets there.
    The flow manager only gives out a step's type and options on the flow object
    it keeps in ``_progress``; where that cannot be read the input goes through
    unchecked.
    """
    flow = getattr(manager, "_progress", {}).get(flow_id)
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


async def _start(
    hass: HomeAssistant, manager: Any, handler: str, domain: str, context: dict[str, Any]
) -> dict[str, Any]:
    """Start a flow and report its first step, aborting it if that cannot be reported."""
    try:
        result = await manager.async_init(handler, context=context)
    except data_entry_flow.UnknownHandler:
        return _text(
            f"No integration with a config flow found for domain '{domain}'. It may be "
            "misspelled, not installed, or set up through YAML only."
        )
    except DependencyError as err:
        return _text(
            f"Could not start a flow for '{domain}': its dependencies failed to set up "
            f"({', '.join(err.failed_dependencies)})."
        )

    try:
        return _text(dumps(_flow_result_json(hass, manager, result)))
    except Exception:
        # The caller never learns this flow's ID, so nothing else could abort it.
        with suppress(data_entry_flow.UnknownFlow):
            manager.async_abort(result["flow_id"])
        raise


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
    if refusal := _blocked(hass, domain):
        return refusal
    return await _start(
        hass, hass.config_entries.flow, domain, domain, {"source": config_entries.SOURCE_USER}
    )


@register_tool(
    name="continue_config_flow",
    description=(
        "Submit input to the current step of a config, reconfigure or options flow and "
        "return what comes next: another form or menu, validation errors on the same step, "
        "an abort reason, or the created or updated config entry. For a form, user_input "
        "maps each data_schema field name to its value; for a menu it is "
        '{"next_step_id": "<option>"}; omit it to re-read a step that is waiting on a '
        "browser or on Home Assistant. Anything sent in user_input, passwords "
        "and API keys included, passes through this conversation and the client's transcript, "
        f"so leave flows that take long-lived credentials to the user. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flow_id": {
                "type": "string",
                "description": (
                    "The flow_id returned by start_config_flow, start_reconfigure_flow or "
                    "start_options_flow"
                ),
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

    found = _find_flow(hass, flow_id)
    if found is None:
        return _unknown_flow(flow_id)
    manager, domain = found
    if blocked := _blocked(hass, domain):
        return blocked

    if user_input and (refusal := _menu_refusal(manager, flow_id, user_input)):
        return _text(refusal)
    if user_input:
        step = getattr(getattr(manager, "_progress", {}).get(flow_id), "cur_step", None) or {}
        if missing := _restore_hidden(user_input, _stored_values(step.get("data_schema"))):
            return _text(
                f'"{SECRET_MASK}" only stands for a stored value, and there is none for '
                f"{', '.join(missing)}. Send the real value. The flow is still on the same step."
            )

    try:
        result = await manager.async_configure(flow_id, user_input)
    except data_entry_flow.UnknownStep as err:
        return _text(
            f"The flow tried to move to a step it does not have ({err}) and Home Assistant "
            "dropped it. Start it again."
        )
    except data_entry_flow.InvalidData as err:
        return _text(
            "The input does not match the step's data_schema, so nothing was submitted and "
            f"the flow is still on the same step: {dumps(err.schema_errors)}"
        )

    return _text(dumps(_flow_result_json(hass, manager, result)))


@register_tool(
    name="abort_config_flow",
    description=(
        "Abort a config, reconfigure or options flow that is still in progress, discarding "
        f"what was entered. Use it for any flow you started and will not finish. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flow_id": {
                "type": "string",
                "description": "The flow_id the start tool returned",
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
    found = _find_flow(hass, flow_id)
    if found is None:
        return _unknown_flow(flow_id)
    if found[1] in _PROTECTED_DOMAINS:
        return _own_flow()
    found[0].async_abort(flow_id)

    return _text(f"Aborted flow '{flow_id}'")


def _own_entry(action: str) -> dict[str, Any]:
    """Refuse an operation on an entry this very request depends on."""
    return _text(
        f"Refusing to {action} the config entry of the MCP Server or its OIDC provider: "
        "they are serving this request. Do it from Settings → Devices & Services instead."
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
    if entry.domain in _PROTECTED_DOMAINS:
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
    if entry.domain in _PROTECTED_DOMAINS:
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


_ENTRY_ID = {
    "type": "string",
    "description": "The config entry ID, as listed by list_integrations",
}


def _editable_entry(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> tuple[config_entries.ConfigEntry | None, dict[str, Any] | None]:
    """Return the entry a tool may reconfigure, or the response saying why not."""
    if not _enabled(hass):
        return None, _disabled()
    entry_id = arguments["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return None, _unknown_entry(entry_id)
    if blocked := _blocked(hass, entry.domain):
        return None, blocked
    return entry, None


@register_tool(
    name="start_options_flow",
    description=(
        "Start the options flow of a config entry, the flow behind its Configure button, "
        "to change settings of an integration that is already set up. Returns the flow_id "
        "and the first step, whose form is filled in with the current values, stored secrets "
        "masked. Take the steps with continue_config_flow and drop the flow with "
        "abort_config_flow. Helpers made from a config entry, such as template, group and "
        "utility meter helpers, are edited this way. Settings an integration marks as "
        f"advanced are not offered. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {"entry_id": _ENTRY_ID},
        "required": ["entry_id"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def start_options_flow(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Start the options flow of a config entry."""
    entry, refusal = _editable_entry(hass, arguments)
    if entry is None:
        return refusal
    if not entry.supports_options:
        return _text(
            f"The '{entry.domain}' entry '{entry.title}' has no options to configure. Try "
            "start_reconfigure_flow for its connection settings."
        )

    return await _start(
        hass,
        hass.config_entries.options,
        entry.entry_id,
        entry.domain,
        {"show_advanced_options": False},
    )


@register_tool(
    name="start_reconfigure_flow",
    description=(
        "Start the reconfigure flow of a config entry, to change what was entered when the "
        "integration was set up, such as a host, without removing it. Returns the flow_id "
        "and the first step, filled in with the current values, stored secrets masked. Take "
        "the steps with continue_config_flow, which keeps a masked value when it is sent "
        "back as shown, and drop the flow with abort_config_flow. Leave entering a new "
        "password or API key to the user. A flow that succeeds ends with an abort whose "
        f"reason is reconfigure_successful. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {"entry_id": _ENTRY_ID},
        "required": ["entry_id"],
    },
    annotations=ANNOTATION_NON_IDEMPOTENT,
)
async def start_reconfigure_flow(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Start a reconfigure flow for a config entry."""
    entry, refusal = _editable_entry(hass, arguments)
    if entry is None:
        return refusal
    if not entry.supports_reconfigure:
        return _text(
            f"The '{entry.domain}' integration cannot be reconfigured. Try start_options_flow "
            "for its settings, or remove the entry and set it up again."
        )

    return await _start(
        hass,
        hass.config_entries.flow,
        entry.domain,
        entry.domain,
        {"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )


@register_tool(
    name="set_integration_enabled",
    description=(
        "Disable or enable one config entry of an integration. Disabling unloads it and "
        "disables its devices and entities while keeping its configuration; enabling loads "
        f"it again. {_DISABLED_NOTE}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entry_id": _ENTRY_ID,
            "enabled": {
                "type": "boolean",
                "description": "False disables the config entry, true enables it",
            },
        },
        "required": ["entry_id", "enabled"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def set_integration_enabled(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Disable or enable a config entry as the user."""
    if not _enabled(hass):
        return _disabled()

    entry_id = arguments["entry_id"]
    enabled = arguments["enabled"]
    if not isinstance(enabled, bool):
        return _text("enabled must be true or false.")
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return _unknown_entry(entry_id)
    if entry.domain in _PROTECTED_DOMAINS:
        return _own_entry("enable" if enabled else "disable")

    disabled_by = None if enabled else config_entries.ConfigEntryDisabler.USER
    try:
        reloaded = await hass.config_entries.async_set_disabled_by(entry_id, disabled_by)
    except config_entries.OperationNotAllowed:
        # The change is stored, but the entry could not be unloaded or loaded now.
        reloaded = False

    return _text(
        dumps(
            {
                **_entry_json(entry),
                "disabled_by": entry.disabled_by,
                "require_restart": not reloaded,
            }
        )
    )
