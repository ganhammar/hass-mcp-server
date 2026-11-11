"""Who a request acts as, and how a tool finds out while it runs.

The transport authenticates each request into a ``Caller`` and, for the length of
a tool call, makes it the current caller with ``acting_as``. Tool code reads it
with ``current_caller`` or ``caller_context`` instead of taking it as an argument.

The current caller lives in a ``ContextVar``. aiohttp serves every request in a
task of its own, and a task works on its own copy of the context, so a caller set
for one request is never seen by another, including one served at the same time.
``acting_as`` also resets the variable when the call ends, so code that runs later
in the same task sees no caller. The value holds across every ``await`` in the
call. A function handed to ``hass.async_add_executor_job`` runs in a thread
without the context and sees no caller, so a handler reads it before handing work
off. A task or timer created during the call starts with a copy of the context
and can outlive the call by far, an integration set up by a tool keeps its timers
until it is reloaded, so the call is marked as ended when it finishes and reads
no caller from then on, in every copy.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from homeassistant.auth.models import User
from homeassistant.core import Context


@dataclass(frozen=True)
class Caller:
    """The Home Assistant user an authorized request acts as.

    ``client_id`` is the OAuth client the token was issued to, and ``scopes`` what
    the token grants. A Long-Lived Access Token carries neither, so it has no
    client and no scopes. The client chooses its own ID, so anything logging it
    treats it as the caller's text and escapes it first.
    """

    user_id: str
    auth_method: str
    user: User
    client_id: str | None = None
    scopes: frozenset[str] = frozenset()


@dataclass
class _ActiveCall:
    """A tool call in progress. ``ended`` is set when it finishes."""

    caller: Caller
    context: Context
    ended: bool = False


_ACTIVE_CALL: ContextVar[_ActiveCall | None] = ContextVar(
    "mcp_server_http_transport_caller", default=None
)


@contextmanager
def acting_as(caller: Caller) -> Iterator[Context]:
    """Make ``caller`` the current caller until the block ends.

    Yields the ``Context`` that everything done in the block is recorded under:
    one per call, carrying the caller's user ID, so the changes a single tool call
    makes and the audit event for it share one context ID.
    """
    context = Context(user_id=caller.user_id)
    call = _ActiveCall(caller, context)
    token = _ACTIVE_CALL.set(call)
    try:
        yield context
    finally:
        call.ended = True
        _ACTIVE_CALL.reset(token)


def _running_call() -> _ActiveCall | None:
    active = _ACTIVE_CALL.get()
    return active if active is not None and not active.ended else None


def current_caller() -> Caller | None:
    """Return the caller the running tool call acts for, or None outside a call."""
    active = _running_call()
    return active.caller if active is not None else None


def caller_context() -> Context | None:
    """Return the Context to record the running call's changes under, or None.

    Pass it as ``context=`` to ``hass.services.async_call`` and
    ``hass.bus.async_fire``, so Home Assistant attributes what follows to the
    caller. None outside a call is what both take by default.
    """
    active = _running_call()
    return active.context if active is not None else None
