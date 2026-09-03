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

Read the handler in core to the end. Three things have to hold, and each has bitten
this repo:

**The fields.** Services routinely return less than the method they wrap.
`calendar.get_events` filters events through `LIST_EVENT_FIELDS` and drops `uid`,
`rrule` and `recurrence_id`, which the calendar tools need, so it is not a
substitute for `async_get_events`.

**The response shape.** A plain service returns whatever its handler returns, which
is often an envelope rather than the payload. `recorder.get_statistics` returns
`{"statistics": {statistic_id: [rows]}}`, not rows keyed by ID. Entity services are
the ones keyed by entity ID, and that keying is added by the helper, not the
handler. Getting this wrong fails silently: the lookup misses and the tool reports
no data rather than an error.

**The version it landed in.** A service is only supported API on versions that have
it. `recorder.get_statistics` arrived in 2025.6 and raises `ServiceNotFound` on
anything older. Check the oldest HA in the CI matrix in `.github/workflows/main.yml`
before assuming a service is reachable.

## Anything left in category 2 or 3

Give it a contract test in `tests/test_ha_api_contracts.py` pinning the signature or
fields relied on. An HA release that changes it then fails CI here instead of
silently on a user's upgrade.

Pin service responses the same way, by driving the real handler. Mocking a service
in a tool test only asserts the shape the tool already assumes, so it stays green
when core changes underneath it.

## Conventions

Black and Ruff at line length 100, both enforced in CI. Tools register through
`@register_tool` and return `{"content": [{"type": "text", ...}]}`; a failure a
handler anticipates comes back as text in that same shape rather than as a raised
exception.

`call_tool` owns the two things a handler does not have to. It rejects a call the
caller got wrong — an unknown name, a non-object `arguments`, a declared-required
property that is absent or null — by raising `InvalidToolRequest`, which the
transport reports as JSON-RPC `-32602`; a handler therefore never has to defend
against a missing required key. Anything a handler raises that it did not expect
is caught and returned as a result with `isError: true`, so a single failing tool
cannot take down the JSON-RPC response.

Two consequences worth knowing before you write a tool. Listing a property in
`required` means the handler is guaranteed it, so any guidance the handler wants
to give about that property being absent is unreachable — put the reason in the
property's schema `description`, which the `-32602` message quotes back. And
`isError` is currently set only by that wrapper, so a handler-caught failure still
returns a success-shaped result whose text happens to describe an error.
