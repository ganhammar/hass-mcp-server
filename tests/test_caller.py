"""Tests for the current caller a tool call runs as."""

import asyncio
from unittest.mock import Mock

import pytest
from homeassistant.core import Context

from custom_components.mcp_server_http_transport.caller import (
    Caller,
    acting_as,
    caller_context,
    current_caller,
)
from custom_components.mcp_server_http_transport.tools import (
    caller_context as tools_caller_context,
)
from custom_components.mcp_server_http_transport.tools import (
    current_caller as tools_current_caller,
)


def _caller(user_id: str = "anna") -> Caller:
    return Caller(user_id=user_id, auth_method="oidc", user=Mock(id=user_id))


def test_tools_import_the_same_accessors():
    assert tools_current_caller is current_caller
    assert tools_caller_context is caller_context


def test_a_caller_from_a_long_lived_token_has_no_client_and_no_scopes():
    caller = Caller(user_id="anna", auth_method="native", user=Mock())

    assert caller.client_id is None
    assert caller.scopes == frozenset()


def test_there_is_no_caller_outside_a_call():
    assert current_caller() is None
    assert caller_context() is None


def test_acting_as_sets_the_caller_and_resets_it():
    caller = _caller()

    with acting_as(caller) as context:
        assert current_caller() is caller
        assert caller_context() is context

    assert current_caller() is None
    assert caller_context() is None


def test_the_context_is_one_per_call_and_carries_the_user():
    caller = _caller()

    with acting_as(caller) as first:
        assert isinstance(first, Context)
        assert first.user_id == "anna"
        assert caller_context() is caller_context()
    with acting_as(caller) as second:
        pass

    assert first.id != second.id


def test_the_caller_is_reset_when_the_block_raises():
    with pytest.raises(RuntimeError), acting_as(_caller()):
        raise RuntimeError("boom")

    assert current_caller() is None


def test_a_nested_call_restores_the_outer_caller():
    outer, inner = _caller("anna"), _caller("bob")

    with acting_as(outer):
        with acting_as(inner):
            assert current_caller() is inner
        assert current_caller() is outer


async def test_the_caller_holds_across_awaits_and_executor_jobs():
    caller = _caller()
    loop = asyncio.get_running_loop()

    with acting_as(caller):
        await asyncio.sleep(0)
        assert current_caller() is caller
        # The function itself runs in a thread without the context.
        assert await loop.run_in_executor(None, current_caller) is None
        assert current_caller() is caller


async def test_concurrent_calls_each_see_their_own_caller():
    both_inside = asyncio.Barrier(2)

    async def call(caller: Caller) -> tuple[Caller | None, str | None]:
        with acting_as(caller):
            await both_inside.wait()
            context = caller_context()
            return current_caller(), context.user_id if context else None

    anna, bob = _caller("anna"), _caller("bob")
    results = await asyncio.gather(call(anna), call(bob))

    assert results == [(anna, "anna"), (bob, "bob")]
    assert current_caller() is None


async def test_a_caller_set_in_one_task_is_not_seen_by_another():
    started = asyncio.Event()
    release = asyncio.Event()

    async def request() -> None:
        with acting_as(_caller("anna")):
            started.set()
            await release.wait()

    async def peek() -> Caller | None:
        return current_caller()

    task = asyncio.create_task(request())
    await started.wait()
    seen_meanwhile = await asyncio.create_task(peek())
    release.set()
    await task

    assert seen_meanwhile is None
    assert await asyncio.create_task(peek()) is None
