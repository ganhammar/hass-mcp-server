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
handler anticipates comes back as `error_result(text)`, the same shape with
`isError: true`, rather than as a raised exception.

Serialize any JSON a tool, resource, or prompt returns with `json_utils.dumps`. It
is compact and keeps non-ASCII text as is, which is a third fewer tokens than
`indent=2` for the model that reads it, and its encoder accepts every value Home
Assistant's own API accepts, falling back to `str()` rather than failing a whole
response on one exotic attribute. Ruff flags `json.dumps` anywhere in the package
(TID251); the helper carries the one `noqa`, and tests are exempt.

`call_tool` owns the two things a handler does not have to. It rejects a call the
caller got wrong — an unknown name, a non-object `arguments`, a declared-required
property that is absent or null — by raising `InvalidToolRequest`, which the
transport reports as JSON-RPC `-32602`; a handler therefore never has to defend
against a missing required key. Anything a handler raises that it did not expect
is caught and returned as a result with `isError: true`, so a single failing tool
cannot take down the JSON-RPC response.

Listing a property in `required` means the handler is guaranteed it, so any
guidance the handler wants to give about that property being absent is
unreachable. Put the reason in the property's schema `description`, which the
`-32602` message quotes back.

Return `error_result` whenever the call did not do what was asked, so a client can
tell failure from success without reading the text: an unknown ID, an argument the
handler rejects, an option that is off, an integration that is not loaded, a
refused operation, a missing `confirm=true`, an exception the handler caught, a
write Home Assistant turned down. Decide by what happened, not by how the message
reads. A result that reports the tool worked carries no `isError`, and that
includes an empty listing, a preview or dry run, a no-op that finds the state
already as asked, partial success that names what it skipped, and a config flow
step the model is meant to read and answer: an abort, a form shown again with
errors, or input the step rejected while the flow stays open. A lookup of one named
thing that does not exist (`get_state` on a missing entity) is an error; a listing
that finds nothing is not. The transport audits an `isError` result with the
outcome `error`, so a refused call to a tool that can change something fires the
audit event as one. `tests/test_tools_is_error.py` holds a failing call for every
tool module and fails when a module is added without one.

`call_tool` and `get_tool_schemas` also apply the two options that limit the tool
set: read-only mode and the list of disabled tools. Read-only mode goes by the
`readOnlyHint` annotation and nothing else, which makes the annotation a security
boundary. Give every tool an annotation, and use `ANNOTATION_READ_ONLY` only for a
tool that cannot change anything. A tool that previews by default but can apply
the change is not read-only. A withheld tool raises `ToolNotAvailable`, an
`InvalidToolRequest`, so it never reaches the handler. The list of disabled
tools is stored by name, so renaming a tool silently switches it back on for
anyone who had disabled it.

A new tool, resource or prompt needs a row in `docs/tools.md`, which is written
by hand so each can be described for a person rather than for the model.
`tests/test_docs.py` fails when one is missing, and when a link between the
README and the pages under `docs/` leads nowhere. The README links to those pages
by full URL because HACS shows it outside the repository.

`get_prompt` does the same for `prompts/get`: an unknown name, non-object
`arguments`, or a `required` argument that is absent or null raises
`InvalidPromptRequest` and never reaches a handler. Some clients call
`prompts/get` for every prompt at connect time with placeholder values, so a
handler that cannot use a value it was given (a timestamp that will not parse)
raises `InvalidPromptArguments` rather than logging it; an ID that merely does not
exist is the client's input too, so log it at debug, not with a traceback.
