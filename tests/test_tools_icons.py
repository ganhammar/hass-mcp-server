"""Tests for set_icon and the icon argument of the create and update tools.

These run against the real entity registry. Writing YAML, reloading and the
helper storage collection are patched out; the item's registry entry is created
directly, which is what loading it would have done. That the load really does
register it first is covered in test_e2e.py.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.mcp_server_http_transport.tools.config import (
    create_automation,
    create_scene,
    create_script,
    update_automation,
    update_scene,
    update_script,
)
from custom_components.mcp_server_http_transport.tools.helpers import (
    create_helper,
    update_helper,
)
from custom_components.mcp_server_http_transport.tools.icons import set_icon

_CONFIG_MANAGER = "custom_components.mcp_server_http_transport.config_manager"
_HELPERS = "custom_components.mcp_server_http_transport.tools.helpers"


def _register(hass: HomeAssistant, domain: str, platform: str, unique_id: str) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create(domain, platform, unique_id, suggested_object_id="test")
        .entity_id
    )


def _icon(hass: HomeAssistant, entity_id: str) -> str | None:
    return er.async_get(hass).async_get(entity_id).icon


@pytest.fixture
def automation(hass: HomeAssistant) -> str:
    """Automation 'abc', registered the way loading automations.yaml would."""
    return _register(hass, "automation", "automation", "abc")


class TestCreateWithIcon:
    async def test_icon_is_set_as_the_registry_override(self, hass: HomeAssistant, automation: str):
        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="abc")):
            result = await create_automation(hass, {"config": {}, "icon": "mdi:lightbulb"})

        assert _icon(hass, automation) == "mdi:lightbulb"
        assert result["content"][0]["text"] == (
            "Successfully created automation with id: abc, with icon mdi:lightbulb"
        )

    async def test_without_icon_nothing_is_set(self, hass: HomeAssistant, automation: str):
        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="abc")):
            result = await create_automation(hass, {"config": {}})

        assert _icon(hass, automation) is None
        assert result["content"][0]["text"] == "Successfully created automation with id: abc"

    async def test_icon_and_category_are_both_applied(self, hass: HomeAssistant, automation: str):
        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="abc")):
            result = await create_automation(
                hass, {"config": {}, "category": "Lighting", "icon": "mdi:lamp"}
            )

        entry = er.async_get(hass).async_get(automation)
        assert entry.icon == "mdi:lamp"
        assert "automation" in entry.categories
        text = result["content"][0]["text"]
        assert "Lighting" in text and "with icon mdi:lamp" in text

    async def test_scene_is_looked_up_on_the_homeassistant_platform(self, hass: HomeAssistant):
        scene = _register(hass, "scene", "homeassistant", "s1")

        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="s1")):
            await create_scene(hass, {"config": {}, "icon": "mdi:weather-night"})

        assert _icon(hass, scene) == "mdi:weather-night"

    async def test_script_is_looked_up_by_its_key(self, hass: HomeAssistant):
        script = _register(hass, "script", "script", "morning")

        with patch(f"{_CONFIG_MANAGER}.create_dict_entry", AsyncMock(return_value="morning")):
            await create_script(hass, {"key": "morning", "config": {}, "icon": "mdi:coffee"})

        assert _icon(hass, script) == "mdi:coffee"

    async def test_helper_is_looked_up_by_its_storage_id(self, hass: HomeAssistant):
        helper = _register(hass, "input_boolean", "input_boolean", "guests")
        collection = Mock(async_create_item=AsyncMock(return_value={"id": "guests"}))

        with patch(f"{_HELPERS}._get_collection", return_value=collection):
            await create_helper(
                hass,
                {"domain": "input_boolean", "config": {"name": "Guests"}, "icon": "mdi:account"},
            )

        assert _icon(hass, helper) == "mdi:account"

    async def test_malformed_icon_is_reported_without_failing_the_create(
        self, hass: HomeAssistant, automation: str
    ):
        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="abc")):
            result = await create_automation(hass, {"config": {}, "icon": "lightbulb"})

        assert _icon(hass, automation) is None
        text = result["content"][0]["text"]
        assert text.startswith("Successfully created automation with id: abc")
        assert "icon could not be set" in text

    async def test_unregistered_item_reports_the_icon_failure(self, hass: HomeAssistant):
        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="missing")):
            result = await create_automation(hass, {"config": {}, "icon": "mdi:lamp"})

        assert "icon could not be set" in result["content"][0]["text"]


class TestUpdateWithIcon:
    async def test_automation_icon_is_replaced(self, hass: HomeAssistant, automation: str):
        er.async_get(hass).async_update_entity(automation, icon="mdi:lamp")

        with patch(f"{_CONFIG_MANAGER}.update_list_entry", AsyncMock()):
            result = await update_automation(
                hass, {"automation_id": "abc", "config": {}, "icon": "mdi:lightbulb"}
            )

        assert _icon(hass, automation) == "mdi:lightbulb"
        assert result["content"][0]["text"] == (
            "Successfully updated automation, with icon mdi:lightbulb"
        )

    async def test_empty_icon_removes_the_override(self, hass: HomeAssistant, automation: str):
        er.async_get(hass).async_update_entity(automation, icon="mdi:lamp")

        with patch(f"{_CONFIG_MANAGER}.update_list_entry", AsyncMock()):
            result = await update_automation(
                hass, {"automation_id": "abc", "config": {}, "icon": ""}
            )

        assert _icon(hass, automation) is None
        assert "with no icon override" in result["content"][0]["text"]

    async def test_without_icon_the_override_is_kept(self, hass: HomeAssistant, automation: str):
        er.async_get(hass).async_update_entity(automation, icon="mdi:lamp")

        with patch(f"{_CONFIG_MANAGER}.update_list_entry", AsyncMock()):
            result = await update_automation(hass, {"automation_id": "abc", "config": {}})

        assert _icon(hass, automation) == "mdi:lamp"
        assert result["content"][0]["text"] == "Successfully updated automation"

    async def test_scene(self, hass: HomeAssistant):
        scene = _register(hass, "scene", "homeassistant", "s1")

        with patch(f"{_CONFIG_MANAGER}.update_list_entry", AsyncMock()):
            await update_scene(hass, {"scene_id": "s1", "config": {}, "icon": "mdi:sofa"})

        assert _icon(hass, scene) == "mdi:sofa"

    async def test_script(self, hass: HomeAssistant):
        script = _register(hass, "script", "script", "morning")

        with patch(f"{_CONFIG_MANAGER}.update_dict_entry", AsyncMock()):
            await update_script(hass, {"key": "morning", "config": {}, "icon": "mdi:coffee"})

        assert _icon(hass, script) == "mdi:coffee"

    async def test_helper(self, hass: HomeAssistant):
        helper = _register(hass, "input_boolean", "input_boolean", "guests")
        collection = Mock(async_update_item=AsyncMock())

        with patch(f"{_HELPERS}._get_collection", return_value=collection):
            result = await update_helper(
                hass, {"entity_id": helper, "config": {"name": "Guests"}, "icon": "mdi:account"}
            )

        assert _icon(hass, helper) == "mdi:account"
        assert result["content"][0]["text"] == "Successfully updated helper, with icon mdi:account"


class TestSetIcon:
    async def test_sets_the_override(self, hass: HomeAssistant, automation: str):
        result = await set_icon(hass, {"entity_id": automation, "icon": "mdi:lamp"})

        assert _icon(hass, automation) == "mdi:lamp"
        assert result["content"][0]["text"] == f"{automation} now has icon mdi:lamp"

    async def test_empty_icon_removes_the_override(self, hass: HomeAssistant, automation: str):
        er.async_get(hass).async_update_entity(automation, icon="mdi:lamp")

        result = await set_icon(hass, {"entity_id": automation, "icon": ""})

        assert _icon(hass, automation) is None
        assert result["content"][0]["text"] == f"{automation} now has no icon override"

    async def test_any_registered_entity_can_be_given_an_icon(self, hass: HomeAssistant):
        light = _register(hass, "light", "hue", "l1")

        await set_icon(hass, {"entity_id": light, "icon": "mdi:ceiling-light"})

        assert _icon(hass, light) == "mdi:ceiling-light"

    async def test_malformed_icon_is_refused(self, hass: HomeAssistant, automation: str):
        result = await set_icon(hass, {"entity_id": automation, "icon": "lamp"})

        assert result["content"][0]["text"].startswith("Error setting icon:")
        assert _icon(hass, automation) is None

    async def test_an_unknown_entity_is_reported(self, hass: HomeAssistant):
        result = await set_icon(hass, {"entity_id": "automation.nope", "icon": "mdi:lamp"})

        assert "not in the entity registry" in result["content"][0]["text"]
