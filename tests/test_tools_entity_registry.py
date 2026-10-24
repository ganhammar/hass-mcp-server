"""Tests for read, preview and update operations on Home Assistant's entity registry."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from custom_components.mcp_server_http_transport.tools import TOOLS, call_tool
from custom_components.mcp_server_http_transport.tools import entity_registry as registry_tools


def _make_entry(entity_id, name=None, disabled_by=None):
    return SimpleNamespace(
        entity_id=entity_id,
        name=name,
        original_name="Original",
        unique_id=f"unique_{entity_id}",
        platform="test",
        config_entry_id="config-1",
        device_id="device-1",
        area_id="living_room",
        disabled_by=disabled_by,
        hidden_by=None,
    )


def _data(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def registry_hass():
    """Mock a populated registry without using live Home Assistant states."""
    hass = Mock()
    entries = {
        "sensor.temperature": _make_entry("sensor.temperature", disabled_by="user"),
        "light.bedroom": _make_entry("light.bedroom"),
        "light.living_room": _make_entry("light.living_room", name="Living room"),
    }
    registry = Mock()
    registry.entities = entries
    registry.async_get.side_effect = lambda entity_id: registry.entities.get(entity_id)
    with patch.object(registry_tools.er, "async_get", return_value=registry):
        yield hass, registry


def test_registry_tools_registered_with_annotations():
    assert set(TOOLS) >= {
        "list_entity_registry",
        "get_entity_registry",
        "update_entity_registry",
    }
    assert TOOLS["list_entity_registry"]["schema"]["annotations"]["readOnlyHint"] is True
    assert TOOLS["get_entity_registry"]["schema"]["annotations"]["readOnlyHint"] is True
    assert TOOLS["update_entity_registry"]["schema"]["annotations"]["destructiveHint"] is True


async def test_list_registry_sorted_and_includes_disabled_entities(registry_hass):
    hass, _ = registry_hass
    result = _data(await registry_tools.list_entity_registry(hass, {}))
    assert [entry["entity_id"] for entry in result["entities"]] == [
        "light.bedroom",
        "light.living_room",
        "sensor.temperature",
    ]
    assert result["entities"][2]["disabled_by"] == "user"
    assert result["total"] == 3
    assert result["has_more"] is False
    hass.states.async_all.assert_not_called()


async def test_list_registry_domain_filter(registry_hass):
    hass, _ = registry_hass
    result = _data(await registry_tools.list_entity_registry(hass, {"domain": "light"}))
    assert [entry["entity_id"] for entry in result["entities"]] == [
        "light.bedroom",
        "light.living_room",
    ]
    assert result["total"] == 2


async def test_list_registry_paginates_by_default(registry_hass):
    hass, registry = registry_hass
    registry.entities = {
        f"sensor.test_{index:03}": _make_entry(f"sensor.test_{index:03}")
        for index in range(105)
    }
    first = _data(await registry_tools.list_entity_registry(hass, {"domain": "sensor"}))
    second = _data(
        await registry_tools.list_entity_registry(hass, {"domain": "sensor", "offset": 100})
    )
    assert first["total"] == second["total"] == 105
    assert len(first["entities"]) == 100
    assert len(second["entities"]) == 5
    assert first["has_more"] is True
    assert second["has_more"] is False
    assert first["entities"][-1]["entity_id"] == "sensor.test_099"
    assert second["entities"][0]["entity_id"] == "sensor.test_100"


@pytest.mark.parametrize(
    "arguments",
    [
        {"domain": 1},
        {"domain": "light.other"},
        {"limit": 0},
        {"limit": 501},
        {"limit": True},
        {"offset": -1},
        {"offset": False},
        {"unexpected": True},
    ],
)
async def test_list_registry_rejects_invalid_arguments(registry_hass, arguments):
    hass, _ = registry_hass
    result = await registry_tools.list_entity_registry(hass, arguments)
    assert result["content"][0]["text"].startswith("Error:")
    assert "isError" not in result


async def test_get_registry_entity_even_when_disabled(registry_hass):
    hass, registry = registry_hass
    data = _data(
        await registry_tools.get_entity_registry(hass, {"entity_id": "sensor.temperature"})
    )
    assert data["disabled_by"] == "user"
    assert data["platform"] == "test"
    registry.async_get.assert_called_once_with("sensor.temperature")
    hass.states.get.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        {"entity_id": "sensor.missing"},
        {"entity_id": "invalid"},
        {"entity_id": 123},
        {"entity_id": "sensor.temperature", "extra": True},
    ],
)
async def test_get_registry_error_is_plain_text(registry_hass, arguments):
    hass, _ = registry_hass
    result = await registry_tools.get_entity_registry(hass, arguments)
    assert result["content"][0]["text"].startswith("Error:")
    assert "isError" not in result


async def test_update_registry_preview_only(registry_hass):
    hass, registry = registry_hass
    data = _data(
        await registry_tools.update_entity_registry(
            hass,
            {
                "entity_id": "light.living_room",
                "new_entity_id": "light.lounge",
                "name": "Lounge",
            },
        )
    )
    assert data["applied"] is False
    assert data["changed"] is True
    assert data["mode"] == "preview"
    assert data["before"]["entity_id"] == "light.living_room"
    assert data["after"]["entity_id"] == "light.lounge"
    assert data["after"]["name"] == "Lounge"
    assert "References are not migrated" in data["warning"]
    registry.async_update_entity.assert_not_called()


async def test_update_registry_applies_using_ha_api(registry_hass):
    hass, registry = registry_hass
    registry.async_update_entity.return_value = _make_entry("light.lounge", name="Lounge")
    result = _data(
        await registry_tools.update_entity_registry(
            hass,
            {
                "entity_id": "light.living_room",
                "new_entity_id": "light.lounge",
                "name": "Lounge",
                "apply": True,
            },
        )
    )
    registry.async_update_entity.assert_called_once_with(
        "light.living_room", new_entity_id="light.lounge", name="Lounge"
    )
    assert result["applied"] is True
    assert result["mode"] == "applied"
    assert result["before"]["name"] == "Living room"
    assert result["after"]["name"] == "Lounge"
    assert result["after"]["entity_id"] == "light.lounge"
    hass.states.async_available.assert_not_called()


async def test_update_registry_name_only(registry_hass):
    hass, registry = registry_hass
    registry.async_update_entity.return_value = _make_entry("light.living_room", name="New Name")
    result = _data(
        await registry_tools.update_entity_registry(
            hass, {"entity_id": "light.living_room", "name": "New Name", "apply": True}
        )
    )
    registry.async_update_entity.assert_called_once_with("light.living_room", name="New Name")
    assert result["after"]["entity_id"] == "light.living_room"
    assert result["after"]["name"] == "New Name"


@pytest.mark.parametrize(
    "arguments",
    [
        {"entity_id": "unknown"},
        {"entity_id": "light.living_room"},
        {"entity_id": "light.living_room", "apply": "true", "name": "Test"},
        {"entity_id": "light.living_room", "name": ""},
        {"entity_id": "light.living_room", "name": "New\nLine"},
        {"entity_id": "light.living_room", "new_entity_id": 42},
        {"entity_id": "light.living_room", "name": "Test", "extra": True},
        {"entity_id": "light.missing", "name": "Test"},
    ],
)
async def test_update_registry_invalid_request_does_not_mutate(registry_hass, arguments):
    hass, registry = registry_hass
    result = await registry_tools.update_entity_registry(hass, arguments)
    assert result["content"][0]["text"].startswith("Error:")
    assert "isError" not in result
    registry.async_update_entity.assert_not_called()


@pytest.mark.parametrize(
    "new_id, message",
    [
        ("light.existing", "already exists"),
        ("sensor.moved", "domain change"),
        ("invalid", "invalid entity ID"),
    ],
)
async def test_update_registry_delegates_id_validation_to_ha(registry_hass, new_id, message):
    hass, registry = registry_hass
    registry.async_update_entity.side_effect = ValueError(message)
    result = await registry_tools.update_entity_registry(
        hass,
        {
            "entity_id": "light.living_room",
            "new_entity_id": new_id,
            "apply": True,
        },
    )
    registry.async_update_entity.assert_called_once_with(
        "light.living_room", new_entity_id=new_id
    )
    assert message in result["content"][0]["text"]
    assert "isError" not in result


async def test_unexpected_registry_error_is_handled_by_dispatcher(registry_hass):
    hass, registry = registry_hass
    registry.async_get.side_effect = RuntimeError("registry unavailable")
    result = await call_tool(hass, "get_entity_registry", {"entity_id": "light.living_room"})
    assert result["isError"] is True
    assert "registry unavailable" in result["content"][0]["text"]
