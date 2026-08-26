# Working on this integration

## Calling into Home Assistant

Tools live in `custom_components/mcp_server_http_transport/tools/`. Most reach into
Home Assistant, and not every reach carries the same risk. Three categories, and
they are easy to confuse because they all look like `homeassistant.components.*`:

1. **Entity platform contracts.** Methods declared on an entity base class that
   every integration on that platform implements: `CalendarEntity.async_get_events`,
   `async_turn_on`, and so on. Safe to call. Home Assistant's own services and REST
   views are thin wrappers over them.
2. **Cross-integration internals.** Module-level functions imported out of another
   integration, such as `recorder.statistics.statistics_during_period`. No
   compatibility promise, no deprecation cycle. Prefer a service call.
3. **`hass.data[DATA_*]` lookups.** Unsupported, and usually the only way to resolve
   an entity object. Guard for the component being absent.

## Before replacing a private call with a service

Confirm the service response carries every field the code reads. Home Assistant
services routinely return less than the method they wrap: `calendar.get_events`
filters events through `LIST_EVENT_FIELDS` and drops `uid`, `rrule` and
`recurrence_id`, which the calendar tools need, so it is not a substitute for
`async_get_events`. Check the field filter in core before assuming a swap is
possible.

Entity services key their response by entity ID; plain services do not.

## Anything left in category 2 or 3

Give it a contract test in `tests/test_ha_api_contracts.py` pinning the signature or
fields relied on. An HA release that changes it then fails CI here instead of
silently on a user's upgrade.

## Conventions

Black and Ruff at line length 100, both enforced in CI. Tools register through
`@register_tool` and return `{"content": [{"type": "text", ...}]}`; errors come back
as text in that same shape rather than as raised exceptions.
