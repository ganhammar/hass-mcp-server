"""Tests for the category tools and the create tools' category argument.

These run against the real category and entity registries. Writing YAML and
reloading are patched out where a create tool is involved; the item's registry
entry is created directly, which is what the reload would have done. That the
reload really does register it first is covered in test_e2e.py.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import entity_registry as er

from custom_components.mcp_server_http_transport.tools.categories import (
    list_categories,
    set_category,
)
from custom_components.mcp_server_http_transport.tools.config import (
    create_automation,
    create_scene,
    create_script,
)

_CONFIG_MANAGER = "custom_components.mcp_server_http_transport.config_manager"


def _register(hass: HomeAssistant, domain: str, platform: str, unique_id: str) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create(domain, platform, unique_id, suggested_object_id="test")
        .entity_id
    )


@pytest.fixture
def automation(hass: HomeAssistant) -> str:
    """Automation 'abc', registered the way loading automations.yaml would."""
    return _register(hass, "automation", "automation", "abc")


class TestCreateWithCategory:
    async def test_new_category_name_creates_and_assigns_it(
        self, hass: HomeAssistant, automation: str
    ):
        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="abc")):
            result = await create_automation(hass, {"config": {}, "category": "Lighting"})

        (category,) = cr.async_get(hass).async_list_categories(scope="automation")
        assert category.name == "Lighting"
        assert er.async_get(hass).async_get(automation).categories == {
            "automation": category.category_id
        }
        text = result["content"][0]["text"]
        assert text.startswith("Successfully created automation with id: abc")
        assert "Lighting" in text

    async def test_without_category_nothing_is_assigned(self, hass: HomeAssistant, automation: str):
        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="abc")):
            result = await create_automation(hass, {"config": {}})

        assert er.async_get(hass).async_get(automation).categories == {}
        assert result["content"][0]["text"] == "Successfully created automation with id: abc"

    async def test_scene_is_looked_up_on_the_homeassistant_platform(self, hass: HomeAssistant):
        scene = _register(hass, "scene", "homeassistant", "s1")

        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="s1")):
            await create_scene(hass, {"config": {}, "category": "Evening"})

        (category,) = cr.async_get(hass).async_list_categories(scope="scene")
        assert er.async_get(hass).async_get(scene).categories == {"scene": category.category_id}

    async def test_script_is_looked_up_by_its_key(self, hass: HomeAssistant):
        script = _register(hass, "script", "script", "morning")

        with patch(f"{_CONFIG_MANAGER}.create_dict_entry", AsyncMock(return_value="morning")):
            await create_script(hass, {"key": "morning", "config": {}, "category": "Routines"})

        (category,) = cr.async_get(hass).async_list_categories(scope="script")
        assert er.async_get(hass).async_get(script).categories == {"script": category.category_id}

    async def test_unregistered_item_reports_the_category_failure(self, hass: HomeAssistant):
        with patch(f"{_CONFIG_MANAGER}.create_list_entry", AsyncMock(return_value="missing")):
            result = await create_automation(hass, {"config": {}, "category": "Lighting"})

        text = result["content"][0]["text"]
        assert text.startswith("Successfully created automation with id: missing")
        assert "category could not be set" in text


class TestSetCategory:
    async def test_existing_category_is_matched_by_name_case_insensitively(
        self, hass: HomeAssistant, automation: str
    ):
        existing = cr.async_get(hass).async_create(scope="automation", name="Lighting")

        await set_category(hass, {"entity_id": automation, "category": "lighting"})

        assert len(list(cr.async_get(hass).async_list_categories(scope="automation"))) == 1
        assert er.async_get(hass).async_get(automation).categories == {
            "automation": existing.category_id
        }

    async def test_existing_category_is_matched_by_id(self, hass: HomeAssistant, automation: str):
        existing = cr.async_get(hass).async_create(scope="automation", name="Lighting")

        result = await set_category(
            hass, {"entity_id": automation, "category": existing.category_id}
        )

        assert er.async_get(hass).async_get(automation).categories == {
            "automation": existing.category_id
        }
        assert "Lighting" in result["content"][0]["text"]

    async def test_category_of_another_scope_is_not_matched(
        self, hass: HomeAssistant, automation: str
    ):
        cr.async_get(hass).async_create(scope="script", name="Lighting")

        await set_category(hass, {"entity_id": automation, "category": "Lighting"})

        assert len(list(cr.async_get(hass).async_list_categories(scope="automation"))) == 1

    async def test_empty_category_removes_only_the_entitys_own_scope(
        self, hass: HomeAssistant, automation: str
    ):
        existing = cr.async_get(hass).async_create(scope="automation", name="Lighting")
        er.async_get(hass).async_update_entity(
            automation, categories={"automation": existing.category_id, "other": "x"}
        )

        result = await set_category(hass, {"entity_id": automation, "category": ""})

        assert er.async_get(hass).async_get(automation).categories == {"other": "x"}
        assert "no category" in result["content"][0]["text"]

    @pytest.mark.parametrize("domain", ["input_boolean", "counter", "timer", "schedule"])
    async def test_storage_helpers_share_the_helpers_scope(self, hass: HomeAssistant, domain: str):
        helper = _register(hass, domain, domain, "h1")

        await set_category(hass, {"entity_id": helper, "category": "Presence"})

        (category,) = cr.async_get(hass).async_list_categories(scope="helpers")
        assert er.async_get(hass).async_get(helper).categories == {"helpers": category.category_id}

    async def test_config_entry_helpers_share_the_helpers_scope(self, hass: HomeAssistant):
        """A template sensor is a helper because the template integration is one."""
        helper = _register(hass, "sensor", "template", "t1")

        await set_category(hass, {"entity_id": helper, "category": "Energy"})

        assert "helpers" in er.async_get(hass).async_get(helper).categories

    async def test_an_entity_that_is_not_categorizable_is_refused(self, hass: HomeAssistant):
        light = _register(hass, "light", "hue", "l1")

        result = await set_category(hass, {"entity_id": light, "category": "Lighting"})

        assert "cannot be categorized" in result["content"][0]["text"]
        assert er.async_get(hass).async_get(light).categories == {}
        assert not list(cr.async_get(hass).async_list_categories(scope="helpers"))

    async def test_an_unknown_entity_is_reported(self, hass: HomeAssistant):
        result = await set_category(hass, {"entity_id": "automation.nope", "category": "X"})

        assert "not in the entity registry" in result["content"][0]["text"]


class TestListCategories:
    async def test_includes_assigned_entities(self, hass: HomeAssistant, automation: str):
        registry = cr.async_get(hass)
        lighting = registry.async_create(scope="automation", name="Lighting", icon="mdi:lamp")
        registry.async_create(scope="automation", name="Empty")
        registry.async_create(scope="script", name="Script only")
        er.async_get(hass).async_update_entity(
            automation, categories={"automation": lighting.category_id}
        )

        result = await list_categories(hass, {})

        categories = {c["name"]: c for c in json.loads(result["content"][0]["text"])}
        assert set(categories) == {"Lighting", "Empty"}
        assert categories["Lighting"] == {
            "category_id": lighting.category_id,
            "name": "Lighting",
            "icon": "mdi:lamp",
            "entity_ids": [automation],
        }
        assert categories["Empty"]["entity_ids"] == []

    async def test_takes_a_scope(self, hass: HomeAssistant):
        cr.async_get(hass).async_create(scope="helpers", name="Presence")

        result = await list_categories(hass, {"scope": "helpers"})

        assert [c["name"] for c in json.loads(result["content"][0]["text"])] == ["Presence"]
