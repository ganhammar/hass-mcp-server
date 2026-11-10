"""Tests for the area, floor and device registry tools, and the entity fields beside them.

These run against the real registries, so a rejected name or the cascade on delete
is Home Assistant's own rather than one the test assumes.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.resources import _read_areas, _read_floors
from custom_components.mcp_server_http_transport.tools import (
    TOOLS,
    call_tool,
    registries,
    tool_is_read_only,
)
from custom_components.mcp_server_http_transport.tools.entities import list_areas
from custom_components.mcp_server_http_transport.tools.entity_registry import (
    update_entity_registry,
)
from custom_components.mcp_server_http_transport.tools.registries import (
    create_area,
    create_floor,
    delete_area,
    delete_floor,
    list_floors,
    update_area,
    update_device,
    update_floor,
)


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _json(result: dict):
    return json.loads(_text(result))


def _json_after(prefix: str, result: dict) -> dict:
    text = _text(result)
    assert text.startswith(prefix), text
    return json.loads(text[len(prefix) :])


@pytest.fixture
def label(hass: HomeAssistant) -> str:
    return lr.async_get(hass).async_create("Outdoor").label_id


@pytest.fixture
def floor(hass: HomeAssistant) -> str:
    return fr.async_get(hass).async_create("Ground", level=0).floor_id


@pytest.fixture
def area(hass: HomeAssistant) -> str:
    return ar.async_get(hass).async_create("Kitchen").id


@pytest.fixture
def device(hass: HomeAssistant) -> dr.DeviceEntry:
    entry = MockConfigEntry(domain="demo")
    entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("demo", "lamp")}, name="Lamp"
    )


@pytest.fixture
def entity(hass: HomeAssistant, device: dr.DeviceEntry) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create(
            "light",
            "demo",
            "lamp-1",
            device_id=device.id,
            config_entry=hass.config_entries.async_get_entry(device.primary_config_entry),
        )
        .entity_id
    )


def _set_supports_unload(hass: HomeAssistant, entity: str, value: bool) -> None:
    entry_id = er.async_get(hass).async_get(entity).config_entry_id
    hass.config_entries.async_get_entry(entry_id).supports_unload = value


class TestRegistration:
    def test_only_the_listing_tool_is_read_only(self):
        tools = {
            "create_area",
            "update_area",
            "delete_area",
            "list_floors",
            "create_floor",
            "update_floor",
            "delete_floor",
            "update_device",
        }

        assert tools <= set(TOOLS)
        assert {name for name in tools if tool_is_read_only(name)} == {"list_floors"}

    @pytest.mark.parametrize("name", ["delete_area", "delete_floor", "update_device"])
    def test_tools_that_can_take_something_away_are_destructive(self, name):
        assert TOOLS[name]["schema"]["annotations"]["destructiveHint"] is True


class TestCreateArea:
    async def test_creates_an_area_with_every_field(self, hass, floor, label):
        created = _json_after(
            "Created area ",
            await create_area(
                hass,
                {
                    "name": " Kitchen ",
                    "floor_id": floor,
                    "icon": "mdi:stove",
                    "labels": [label],
                    "aliases": ["Galley", " ", "Cookhouse"],
                },
            ),
        )

        (stored,) = ar.async_get(hass).async_list_areas()
        assert created == {
            "id": stored.id,
            "name": "Kitchen",
            "floor_id": floor,
            "icon": "mdi:stove",
            "labels": [label],
            "aliases": ["Cookhouse", "Galley"],
        }
        assert stored.aliases == {"Galley", "Cookhouse"}

    async def test_the_tool_resource_and_reply_agree(self, hass):
        created = _json_after("Created area ", await create_area(hass, {"name": "Hall"}))

        assert _json(await list_areas(hass, {})) == [created]
        assert json.loads(_read_areas(hass, "hass://areas")[0]["text"]) == [created]

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"name": "  "}, "needs a name"),
            ({"name": 5}, "'name' must be a string"),
            ({"name": "X", "floor_id": "attic"}, "No floor with floor_id 'attic'"),
            ({"name": "X", "labels": ["nope"]}, "No label with label_id nope"),
            ({"name": "X", "labels": "outdoor"}, "'labels' must be a list of strings"),
            ({"name": "X", "aliases": [1]}, "'aliases' must be a list of strings"),
            ({"name": "X", "icon": "stove"}, "is not a valid icon"),
        ],
    )
    async def test_invalid_input_is_refused_and_nothing_is_created(self, hass, arguments, message):
        text = _text(await create_area(hass, arguments))

        assert text.startswith("Error creating area: ")
        assert message in text
        assert list(ar.async_get(hass).async_list_areas()) == []

    async def test_duplicate_name_is_refused(self, hass, area):
        text = _text(await create_area(hass, {"name": "kitchen"}))

        assert text.startswith("Error creating area: ")
        assert len(list(ar.async_get(hass).async_list_areas())) == 1


class TestUpdateArea:
    async def test_only_the_given_fields_change(self, hass, area, floor):
        registry = ar.async_get(hass)
        registry.async_update(area, icon="mdi:stove", aliases={"Galley"})

        updated = _json_after(
            "Updated area ", await update_area(hass, {"area_id": area, "floor_id": floor})
        )

        assert updated["floor_id"] == floor
        assert (updated["name"], updated["icon"], updated["aliases"]) == (
            "Kitchen",
            "mdi:stove",
            ["Galley"],
        )

    async def test_rename_keeps_the_area_id(self, hass, area):
        updated = _json_after(
            "Updated area ", await update_area(hass, {"area_id": area, "name": "Scullery"})
        )

        assert (updated["id"], updated["name"]) == (area, "Scullery")

    async def test_empty_values_clear(self, hass, area, floor, label):
        ar.async_get(hass).async_update(
            area, floor_id=floor, icon="mdi:stove", labels={label}, aliases={"Galley"}
        )

        updated = _json_after(
            "Updated area ",
            await update_area(
                hass, {"area_id": area, "floor_id": "", "icon": "", "labels": [], "aliases": []}
            ),
        )

        assert updated == {
            "id": area,
            "name": "Kitchen",
            "floor_id": None,
            "icon": None,
            "labels": [],
            "aliases": [],
        }

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"area_id": "nowhere", "name": "X"}, "No area with area_id 'nowhere'"),
            ({}, "Nothing to update"),
            ({"name": None, "icon": None}, "Nothing to update"),
            ({"name": " "}, "needs a name"),
            ({"floor_id": "attic"}, "No floor with floor_id 'attic'"),
        ],
    )
    async def test_invalid_input_is_refused_and_nothing_changes(
        self, hass, area, arguments, message
    ):
        before = ar.async_get(hass).async_get_area(area)

        text = _text(await update_area(hass, {"area_id": area, **arguments}))

        assert text.startswith("Error updating area: ")
        assert message in text
        assert ar.async_get(hass).async_get_area(area) == before

    async def test_rename_to_another_areas_name_is_refused(self, hass, area):
        ar.async_get(hass).async_create("Hall")

        text = _text(await update_area(hass, {"area_id": area, "name": "Hall"}))

        assert text.startswith("Error updating area: ")
        assert ar.async_get(hass).async_get_area(area).name == "Kitchen"


class TestDeleteArea:
    async def test_reports_what_is_left_without_an_area(self, hass, area, device, entity):
        dr.async_get(hass).async_update_device(device.id, area_id=area)
        er.async_get(hass).async_update_entity(entity, area_id=area)

        text = _text(await delete_area(hass, {"area_id": area}))
        await hass.async_block_till_done()

        assert text == (
            f"Deleted area 'Kitchen' ({area}), leaving 1 device and 1 entity without an area"
        )
        assert ar.async_get(hass).async_get_area(area) is None
        assert dr.async_get(hass).async_get(device.id).area_id is None
        assert er.async_get(hass).async_get(entity).area_id is None

    async def test_an_empty_area_is_just_deleted(self, hass, area):
        assert (
            _text(await delete_area(hass, {"area_id": area})) == f"Deleted area 'Kitchen' ({area})"
        )

    async def test_unknown_area_is_reported(self, hass, area):
        text = _text(await delete_area(hass, {"area_id": "nowhere"}))

        assert text == "Error deleting area: No area with area_id 'nowhere' (see list_areas)"
        assert ar.async_get(hass).async_get_area(area) is not None


class TestFloors:
    async def test_full_lifecycle(self, hass):
        created = _json_after(
            "Created floor ",
            await create_floor(
                hass, {"name": "Basement", "level": -1, "icon": "mdi:stairs", "aliases": ["Cellar"]}
            ),
        )
        assert created == {
            "floor_id": created["floor_id"],
            "name": "Basement",
            "icon": "mdi:stairs",
            "level": -1,
            "aliases": ["Cellar"],
        }
        assert _json(await list_floors(hass, {})) == [created]
        assert json.loads(_read_floors(hass, "hass://floors")[0]["text"]) == [created]

        updated = _json_after(
            "Updated floor ",
            await update_floor(
                hass,
                {"floor_id": created["floor_id"], "name": "Cellar", "icon": "", "aliases": []},
            ),
        )
        assert updated == {**created, "name": "Cellar", "icon": None, "aliases": []}

        text = _text(await delete_floor(hass, {"floor_id": created["floor_id"]}))
        assert text == f"Deleted floor 'Cellar' ({created['floor_id']})"
        assert _json(await list_floors(hass, {})) == []

    async def test_delete_reports_the_areas_left_without_a_floor(self, hass, floor, area):
        registry = ar.async_get(hass)
        registry.async_update(area, floor_id=floor)
        registry.async_create("Hall", floor_id=floor)

        text = _text(await delete_floor(hass, {"floor_id": floor}))
        await hass.async_block_till_done()

        assert text == f"Deleted floor 'Ground' ({floor}), leaving 2 areas without a floor"
        assert [a.floor_id for a in registry.async_list_areas()] == [None, None]

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"name": ""}, "needs a name"),
            ({"name": "X", "level": "1"}, "'level' must be an integer, not str"),
            ({"name": "X", "level": True}, "'level' must be an integer, not bool"),
            ({"name": "X", "icon": "stairs"}, "is not a valid icon"),
            ({"name": "ground"}, "already in use"),
        ],
    )
    async def test_invalid_input_is_refused_and_nothing_is_created(
        self, hass, floor, arguments, message
    ):
        text = _text(await create_floor(hass, arguments))

        assert text.startswith("Error creating floor: ")
        assert message in text
        assert len(list(fr.async_get(hass).async_list_floors())) == 1

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"floor_id": "attic", "name": "X"}, "No floor with floor_id 'attic'"),
            ({}, "Nothing to update"),
            ({"level": 1.5}, "'level' must be an integer"),
        ],
    )
    async def test_invalid_update_is_refused(self, hass, floor, arguments, message):
        text = _text(await update_floor(hass, {"floor_id": floor, **arguments}))

        assert text.startswith("Error updating floor: ")
        assert message in text
        assert fr.async_get(hass).async_get_floor(floor).level == 0

    async def test_unknown_floor_is_not_deleted(self, hass):
        text = _text(await delete_floor(hass, {"floor_id": "attic"}))

        assert text == "Error deleting floor: No floor with floor_id 'attic' (see list_floors)"


class TestUpdateDevice:
    async def test_renames_moves_and_labels_a_device(self, hass, device, area, label):
        updated = _json_after(
            "Updated device ",
            await update_device(
                hass,
                {
                    "device_id": device.id,
                    "name": " Reading lamp ",
                    "area_id": area,
                    "labels": [label],
                },
            ),
        )

        assert updated == {
            "id": device.id,
            "name": "Lamp",
            "name_by_user": "Reading lamp",
            "area_id": area,
            "labels": [label],
            "disabled_by": None,
        }

    async def test_empty_values_clear(self, hass, device, area, label):
        dr.async_get(hass).async_update_device(
            device.id, name_by_user="Reading lamp", area_id=area, labels={label}
        )

        updated = _json_after(
            "Updated device ",
            await update_device(
                hass, {"device_id": device.id, "name": "", "area_id": "", "labels": []}
            ),
        )

        assert (updated["name_by_user"], updated["area_id"], updated["labels"]) == (None, None, [])

    async def test_disabling_and_enabling_is_recorded_as_the_users(self, hass, device):
        disabled = _json_after(
            "Updated device ", await update_device(hass, {"device_id": device.id, "disabled": True})
        )
        assert disabled["disabled_by"] == "user"

        enabled = _json_after(
            "Updated device ",
            await update_device(hass, {"device_id": device.id, "disabled": False}),
        )
        assert enabled["disabled_by"] is None

    async def test_a_device_something_else_disabled_keeps_that_reason(self, hass, device):
        registry = dr.async_get(hass)
        registry.async_update_device(device.id, disabled_by=dr.DeviceEntryDisabler.INTEGRATION)

        text = _text(await update_device(hass, {"device_id": device.id, "disabled": True}))

        assert text.startswith("Device is already disabled: ")
        assert registry.async_get(device.id).disabled_by is dr.DeviceEntryDisabler.INTEGRATION

    async def test_an_unchanged_disabled_does_not_block_the_other_fields(self, hass, device, area):
        updated = _json_after(
            "Updated device ",
            await update_device(hass, {"device_id": device.id, "disabled": False, "area_id": area}),
        )

        assert (updated["area_id"], updated["disabled_by"]) == (area, None)

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"device_id": "nope", "name": "X"}, "No device with device_id 'nope'"),
            ({}, "Nothing to update"),
            ({"area_id": "nowhere"}, "No area with area_id 'nowhere'"),
            ({"labels": ["nope"]}, "No label with label_id nope"),
            ({"disabled": "yes"}, "'disabled' must be true or false, not str"),
            ({"name": 3}, "'name' must be a string"),
        ],
    )
    async def test_invalid_input_is_refused_and_nothing_changes(
        self, hass, device, arguments, message
    ):
        text = _text(await update_device(hass, {"device_id": device.id, **arguments}))

        assert text.startswith("Error updating device: ")
        assert message in text
        assert dr.async_get(hass).async_get(device.id) == device


class _SplitAwareRegistry:
    """Stand-in for a device registry that knows child devices and split devices.

    Home Assistant 2026.9 has it; the lookups and both update methods are pinned
    against the real one in test_ha_api_contracts.py.
    """

    def __init__(self, *, main=None, child=None, composite=None):
        self._kinds = {"main": main, "child": child, "composite": composite}
        self.updated: list[tuple[str, str, dict]] = []

    def async_get(
        self,
        device_id,
        *,
        include_child_devices=True,
        include_main_devices=True,
        include_composite_devices=True,
    ):
        wanted = {
            "main": include_main_devices,
            "child": include_child_devices,
            "composite": include_composite_devices,
        }
        for kind, device in self._kinds.items():
            if wanted[kind] and device is not None and device.id == device_id:
                return device
        return None

    def async_update_device(self, device_id, **changes):
        self.updated.append(("main", device_id, changes))

    def async_update_child_device(self, device_id, **changes):
        self.updated.append(("child", device_id, changes))


class _Child(SimpleNamespace):
    pass


def _stand_in_device(cls=SimpleNamespace, **fields):
    return cls(
        **{
            "id": "dev",
            "name": "Lamp",
            "name_by_user": None,
            "area_id": None,
            "labels": set(),
            "disabled_by": None,
            "disabled": False,
            **fields,
        }
    )


class TestUpdateDeviceOnASplitAwareRegistry:
    async def _update(self, hass, registry, arguments):
        with (
            patch.object(registries.dr, "async_get", return_value=registry),
            patch.object(registries.dr, "ChildDeviceEntry", _Child, create=True),
        ):
            return _text(await update_device(hass, arguments))

    async def test_a_main_device_is_updated_as_one(self, hass):
        registry = _SplitAwareRegistry(main=_stand_in_device())

        text = await self._update(hass, registry, {"device_id": "dev", "name": "Reading lamp"})

        assert text.startswith("Updated device ")
        assert registry.updated == [("main", "dev", {"name_by_user": "Reading lamp"})]

    async def test_a_child_device_goes_through_its_own_update(self, hass):
        registry = _SplitAwareRegistry(child=_stand_in_device(_Child))

        text = await self._update(hass, registry, {"device_id": "dev", "disabled": True})

        assert text.startswith("Updated device ")
        assert registry.updated == [("child", "dev", {"disabled_by": dr.DeviceEntryDisabler.USER})]

    async def test_the_old_id_of_a_split_device_is_refused(self, hass):
        """An update to it would land on every device it was split into."""
        registry = _SplitAwareRegistry(composite=_stand_in_device())

        text = await self._update(hass, registry, {"device_id": "dev", "disabled": True})

        assert text.startswith("Error updating device: 'dev' is the old ID of a device")
        assert registry.updated == []

    async def test_unknown_device_is_reported(self, hass):
        text = await self._update(hass, _SplitAwareRegistry(), {"device_id": "dev", "name": "X"})

        assert text == "Error updating device: No device with device_id 'dev' (see list_devices)"


class TestEntityRegistryFields:
    """update_entity_registry's area, labels, disabled and hidden, on the real registry."""

    async def test_preview_shows_the_change_without_making_it(self, hass, entity, area, label):
        result = _json(
            await update_entity_registry(
                hass,
                {
                    "entity_id": entity,
                    "area_id": area,
                    "labels": [label],
                    "disabled": True,
                    "hidden": True,
                },
            )
        )

        assert (result["applied"], result["changed"]) == (False, True)
        assert result["before"]["labels"] == []
        assert {
            k: result["after"][k] for k in ("area_id", "labels", "disabled_by", "hidden_by")
        } == {
            "area_id": area,
            "labels": [label],
            "disabled_by": "user",
            "hidden_by": "user",
        }
        stored = er.async_get(hass).async_get(entity)
        assert (stored.area_id, stored.labels, stored.disabled_by, stored.hidden_by) == (
            None,
            set(),
            None,
            None,
        )

    async def test_apply_writes_every_field(self, hass, entity, area, label):
        result = _json(
            await update_entity_registry(
                hass,
                {
                    "entity_id": entity,
                    "area_id": area,
                    "labels": [label],
                    "disabled": True,
                    "hidden": True,
                    "apply": True,
                },
            )
        )

        stored = er.async_get(hass).async_get(entity)
        assert result["applied"] is True
        assert (stored.area_id, stored.labels) == (area, {label})
        assert stored.disabled_by is er.RegistryEntryDisabler.USER
        assert stored.hidden_by is er.RegistryEntryHider.USER
        assert result["after"]["labels"] == [label]

    async def test_apply_clears_and_restores(self, hass, entity, area, label):
        registry = er.async_get(hass)
        registry.async_update_entity(
            entity,
            area_id=area,
            labels={label},
            disabled_by=er.RegistryEntryDisabler.USER,
            hidden_by=er.RegistryEntryHider.USER,
        )

        await update_entity_registry(
            hass,
            {
                "entity_id": entity,
                "area_id": "",
                "labels": [],
                "disabled": False,
                "hidden": False,
                "apply": True,
            },
        )

        stored = registry.async_get(entity)
        assert (stored.area_id, stored.labels, stored.disabled_by, stored.hidden_by) == (
            None,
            set(),
            None,
            None,
        )

    async def _enable(self, hass, entity) -> dict:
        er.async_get(hass).async_update_entity(entity, disabled_by=er.RegistryEntryDisabler.USER)
        return _json(
            await update_entity_registry(
                hass, {"entity_id": entity, "disabled": False, "apply": True}
            )
        )

    async def test_enabling_says_a_reload_brings_the_entity_back(self, hass, entity):
        _set_supports_unload(hass, entity, True)

        result = await self._enable(hass, entity)

        assert result["takes_effect"] == (
            "The entity is enabled once Home Assistant reloads its integration, about "
            "30 seconds from now."
        )

    async def test_enabling_says_when_only_a_restart_brings_the_entity_back(self, hass, entity):
        """An integration that cannot be unloaded is not reloaded for it."""
        _set_supports_unload(hass, entity, False)

        result = await self._enable(hass, entity)

        assert result["takes_effect"] == "The entity is enabled once Home Assistant restarts."

    async def test_enabling_an_entity_without_a_config_entry_needs_a_restart(self, hass):
        entity = er.async_get(hass).async_get_or_create("light", "yaml", "lamp-2").entity_id

        result = await self._enable(hass, entity)

        assert result["takes_effect"] == "The entity is enabled once Home Assistant restarts."

    async def test_only_enabling_says_when_it_takes_effect(self, hass, entity):
        result = _json(
            await update_entity_registry(
                hass, {"entity_id": entity, "disabled": True, "apply": True}
            )
        )

        assert "takes_effect" not in result

    async def test_what_something_else_disabled_keeps_that_reason(self, hass, entity):
        registry = er.async_get(hass)
        registry.async_update_entity(entity, disabled_by=er.RegistryEntryDisabler.INTEGRATION)

        result = _json(
            await update_entity_registry(
                hass, {"entity_id": entity, "disabled": True, "apply": True}
            )
        )

        assert result["changed"] is False
        assert registry.async_get(entity).disabled_by is er.RegistryEntryDisabler.INTEGRATION

    async def test_an_entity_of_a_disabled_device_cannot_be_enabled(self, hass, entity, device):
        dr.async_get(hass).async_update_device(device.id, disabled_by=dr.DeviceEntryDisabler.USER)
        await hass.async_block_till_done()
        assert er.async_get(hass).async_get(entity).disabled_by is not None

        text = _text(
            await update_entity_registry(
                hass, {"entity_id": entity, "disabled": False, "apply": True}
            )
        )

        assert text == "Error: The entity's device is disabled. Enable it with update_device."
        assert er.async_get(hass).async_get(entity).disabled_by is not None

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"area_id": "nowhere"}, "No area with area_id 'nowhere'"),
            ({"labels": ["nope"]}, "No label with label_id nope"),
            ({"disabled": 1}, "'disabled' must be true or false"),
            ({"hidden": "no"}, "'hidden' must be true or false"),
        ],
    )
    async def test_invalid_input_is_refused(self, hass, entity, arguments, message):
        text = _text(
            await update_entity_registry(hass, {"entity_id": entity, **arguments, "apply": True})
        )

        assert text.startswith("Error: ")
        assert message in text


class TestDispatch:
    """The tools reach callers through call_tool, as a client drives them."""

    @pytest.fixture(autouse=True)
    def loaded(self, hass: HomeAssistant) -> None:
        """Stand in for the set-up entry, without which no tool is dispatched."""
        hass.data[DOMAIN] = {"read_only": False}

    async def test_a_room_is_set_up_end_to_end(self, hass, device, entity):
        floor = _json_after(
            "Created floor ",
            await call_tool(hass, "create_floor", {"name": "Upstairs", "level": 1}),
        )
        area = _json_after(
            "Created area ",
            await call_tool(hass, "create_area", {"name": "Study", "floor_id": floor["floor_id"]}),
        )
        await call_tool(hass, "update_device", {"device_id": device.id, "area_id": area["id"]})
        await call_tool(
            hass,
            "update_entity_registry",
            {"entity_id": entity, "hidden": True, "apply": True},
        )

        assert dr.async_get(hass).async_get(device.id).area_id == area["id"]
        assert er.async_get(hass).async_get(entity).hidden_by is er.RegistryEntryHider.USER
        assert _json(await call_tool(hass, "list_areas", {}))[0]["floor_id"] == floor["floor_id"]
