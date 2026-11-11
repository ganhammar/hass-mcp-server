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

## Protocol revisions

`protocol.py` is the one place that knows the MCP revisions the server speaks
and what differs between them: which methods each has, whether it accepts
batches, what it declares as capabilities, its resource-not-found code, and how a
result is finished for it (`resultType`, the server named in `_meta`, the cache
hints). `classify` places each message in its revision. One that carries
`io.modelcontextprotocol/protocolVersion` in `params._meta` is in the stateless
era (2026-07-28 on); any other is in the handshake era, in the revision its
`MCP-Protocol-Version` header names, or 2025-03-26 without one. Handlers return
the same payload whatever the revision, and the transport never branches on a
version string.

The endpoint does not validate `Origin`, although the Streamable HTTP transport
says a server MUST, against DNS rebinding. It accepts only a bearer token in the
`Authorization` header, which a browser never attaches by itself, so a page that
reaches it by rebinding has nothing to authenticate with; Home Assistant's own
API takes the same position. A check cannot tell a rebinding page from a page on
Home Assistant's own address without the instance's URLs, which most installs
never set, and so refuses real clients behind a reverse proxy. Add one, before
authentication, only once the endpoint accepts a credential a browser sends on
its own, such as a cookie.

To add a revision, read its changelog and the pages on the base protocol,
versioning, the Streamable HTTP transport and each server feature, then add a
`Revision` to `REVISIONS`, newest first, describing every difference in its
fields. A difference no field covers gets a new field with the old behaviour as
its default, so the revisions already listed keep what they have. Then extend
`tests/test_protocol.py`, which pins what each revision gets, and the end-to-end
tests in `tests/test_e2e.py`.

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

A handler learns who called it from `current_caller()` in `tools/__init__.py`: the
`Caller` with the user, how they authenticated, the OAuth client and the token's
scopes, or None outside a request. `caller_context()` is the Home Assistant
`Context` for the request, one per request and carrying the user's ID. The
transport sets both around each JSON-RPC request with a context variable, so no
handler signature carries them; read them on the event loop, since a function passed to
`async_add_executor_job` does not see them. Pass `context=caller_context()` to a
service call or event that is the change the caller asked for, as `call_service`,
`fire_event` and `restart_ha` do: Home Assistant then records the user against the
change, and checks a service call against that user's permissions. An event is
not checked. A refusal only reaches the handler when the call is awaited, so a
tool that calls with `blocking=False`, as `restart_ha` does, checks the caller
itself first. Leave the context off a service call that is the tool's own
plumbing, such as the reload after a YAML write or the `recorder.get_statistics`
read. Most of those are admin-only, so for a non-administrator the reload would be
refused after the file has already changed.

`call_tool` and `get_tool_schemas` also apply the two options that limit the tool
set, read-only mode and the list of disabled tools, and then the access the
caller's token grants. Read-only mode goes by the `readOnlyHint` annotation and
nothing else, which makes the annotation a security boundary, and so does the
access: a read-only tool needs only Read in its area. Give every tool an annotation, and use `ANNOTATION_READ_ONLY` only for a
tool that cannot change anything. A tool that previews by default but can apply
the change is not read-only. A withheld tool raises `ToolNotAvailable`, an
`InvalidToolRequest`, so it never reaches the handler. The list of disabled
tools is stored by name, so renaming a tool silently switches it back on for
anyone who had disabled it.

A new tool, resource or prompt also needs a place in `access.py`, the table both
the consent screen description (`oidc_provider.py`) and the enforcement work from.
A tool goes in `_TOOLS_BY_AREA` under the area whose things it reads or changes, and
its `readOnlyHint` decides whether it needs Read or Full there. A resource goes in
`RESOURCE_AREAS`, and a prompt in `PROMPT_AREAS` with every area whose data it puts
into its text. `tests/test_access.py` fails when one is missing, and a caller limited
by scopes is never offered one that is. A handler that reads across areas, as
`search_config` does, checks `tool_unavailable_reason` or `may_read` for each part
and leaves out what the caller may not read. A service a tool calls with names the
caller chose goes through `service_refusal` first, as `call_service` does.

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
