"""Tests for the label tools.

These run against the real label, entity, device and area registries, so the
cascade on delete is Home Assistant's own rather than one the test assumes.
"""

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.resources import _read_labels
from custom_components.mcp_server_http_transport.tools import (
    TOOLS,
    InvalidToolArguments,
    call_tool,
)
from custom_components.mcp_server_http_transport.tools.entities import list_labels
from custom_components.mcp_server_http_transport.tools.labels import (
    create_label,
    delete_label,
    label_json,
    update_label,
)


def _text(result: dict) -> str:
    assert "isError" not in result
    return result["content"][0]["text"]


def _error_text(result: dict) -> str:
    assert result["isError"] is True
    return result["content"][0]["text"]


def _json_after(prefix: str, result: dict) -> dict:
    text = _text(result)
    assert text.startswith(prefix)
    return json.loads(text[len(prefix) :])


class TestCreateLabel:
    async def test_creates_a_label_with_every_field(self, hass: HomeAssistant):
        result = await create_label(
            hass,
            {
                "name": "Outdoor",
                "color": "green",
                "icon": "mdi:tree",
                "description": "Garden devices",
            },
        )

        (label,) = lr.async_get(hass).async_list_labels()
        assert (label.name, label.color, label.icon, label.description) == (
            "Outdoor",
            "green",
            "mdi:tree",
            "Garden devices",
        )
        assert _json_after("Created label ", result) == {
            "label_id": label.label_id,
            "name": "Outdoor",
            "color": "green",
            "icon": "mdi:tree",
            "description": "Garden devices",
        }

    async def test_only_a_name_is_needed(self, hass: HomeAssistant):
        await create_label(hass, {"name": "Bare"})

        (label,) = lr.async_get(hass).async_list_labels()
        assert (label.color, label.icon, label.description) == (None, None, None)

    async def test_name_is_trimmed(self, hass: HomeAssistant):
        await create_label(hass, {"name": "  Padded  "})

        (label,) = lr.async_get(hass).async_list_labels()
        assert label.name == "Padded"

    @pytest.mark.parametrize("name", ["", "   "])
    async def test_blank_name_is_refused(self, hass: HomeAssistant, name: str):
        result = await create_label(hass, {"name": name})

        assert _error_text(result) == "Error creating label: A label needs a name"
        assert not list(lr.async_get(hass).async_list_labels())

    async def test_duplicate_name_is_refused_case_insensitively(self, hass: HomeAssistant):
        lr.async_get(hass).async_create("Outdoor")

        result = await create_label(hass, {"name": "outdoor"})

        assert _error_text(result).startswith("Error creating label:")
        assert "already in use" in _error_text(result)
        assert len(list(lr.async_get(hass).async_list_labels())) == 1

    async def test_invalid_icon_is_refused_and_nothing_is_created(self, hass: HomeAssistant):
        result = await create_label(hass, {"name": "Bad", "icon": "tree"})

        assert _error_text(result).startswith("Error creating label:")
        assert not list(lr.async_get(hass).async_list_labels())

    @pytest.mark.parametrize("color", ["red", "light-green", "blue-grey", "#ff9800", "#FF9800"])
    async def test_accepts_what_the_frontend_accepts(self, hass: HomeAssistant, color: str):
        await create_label(hass, {"name": "L", "color": color})

        (label,) = lr.async_get(hass).async_list_labels()
        assert label.color == color

    @pytest.mark.parametrize(
        "color",
        ["Red", "light green", "chartreuse", "#fff", "ff9800", "#ff98", "#gggggg", "rgb(1,2,3)"],
    )
    async def test_invalid_color_is_refused_and_nothing_is_created(
        self, hass: HomeAssistant, color: str
    ):
        result = await create_label(hass, {"name": "L", "color": color})

        assert _error_text(result).startswith(
            f"Error creating label: '{color}' is not a valid color"
        )
        assert "#RRGGBB" in _error_text(result)
        assert "light-green" in _error_text(result)
        assert not list(lr.async_get(hass).async_list_labels())

    @pytest.mark.parametrize(
        ("field", "value"),
        [("name", 5), ("color", 5), ("icon", True), ("description", 5), ("description", ["a"])],
    )
    async def test_non_string_arguments_are_refused(self, hass: HomeAssistant, field: str, value):
        arguments = {"name": "L", field: value}

        result = await create_label(hass, arguments)

        type_name = type(value).__name__
        assert (
            _error_text(result)
            == f"Error creating label: '{field}' must be a string, not {type_name}"
        )
        assert not list(lr.async_get(hass).async_list_labels())

    async def test_created_label_is_listed(self, hass: HomeAssistant):
        await create_label(hass, {"name": "Outdoor", "color": "green"})

        listed = json.loads(_text(await list_labels(hass, {})))

        assert [(label["name"], label["color"]) for label in listed] == [("Outdoor", "green")]


class TestUpdateLabel:
    @pytest.fixture
    def label(self, hass: HomeAssistant) -> lr.LabelEntry:
        return lr.async_get(hass).async_create(
            "Outdoor", color="green", icon="mdi:tree", description="Garden"
        )

    async def test_only_the_given_fields_change(self, hass: HomeAssistant, label):
        result = await update_label(hass, {"label_id": label.label_id, "color": "red"})

        updated = lr.async_get(hass).async_get_label(label.label_id)
        assert (updated.name, updated.color, updated.icon, updated.description) == (
            "Outdoor",
            "red",
            "mdi:tree",
            "Garden",
        )
        assert _json_after("Updated label ", result)["color"] == "red"

    async def test_rename_keeps_the_label_id(self, hass: HomeAssistant, label):
        await update_label(hass, {"label_id": label.label_id, "name": "Garden"})

        assert lr.async_get(hass).async_get_label(label.label_id).name == "Garden"

    async def test_rename_to_its_own_name_in_another_case_is_allowed(self, hass, label):
        await update_label(hass, {"label_id": label.label_id, "name": "OUTDOOR"})

        assert lr.async_get(hass).async_get_label(label.label_id).name == "OUTDOOR"

    async def test_rename_to_another_labels_name_is_refused(self, hass: HomeAssistant, label):
        other = lr.async_get(hass).async_create("Indoor")

        result = await update_label(hass, {"label_id": label.label_id, "name": "indoor"})

        assert "already used by label" in _error_text(result)
        assert other.label_id in _error_text(result)
        assert lr.async_get(hass).async_get_label(label.label_id).name == "Outdoor"

    async def test_empty_string_clears_an_optional_field(self, hass: HomeAssistant, label):
        await update_label(
            hass, {"label_id": label.label_id, "color": "", "icon": "", "description": ""}
        )

        updated = lr.async_get(hass).async_get_label(label.label_id)
        assert (updated.color, updated.icon, updated.description) == (None, None, None)
        assert updated.name == "Outdoor"

    async def test_null_is_treated_as_not_given(self, hass: HomeAssistant, label):
        await update_label(hass, {"label_id": label.label_id, "color": None, "icon": "mdi:leaf"})

        updated = lr.async_get(hass).async_get_label(label.label_id)
        assert (updated.color, updated.icon) == ("green", "mdi:leaf")

    async def test_blank_name_is_refused(self, hass: HomeAssistant, label):
        result = await update_label(hass, {"label_id": label.label_id, "name": " "})

        assert "needs a name" in _error_text(result)
        assert lr.async_get(hass).async_get_label(label.label_id).name == "Outdoor"

    async def test_invalid_icon_is_refused_and_nothing_changes(self, hass: HomeAssistant, label):
        result = await update_label(
            hass, {"label_id": label.label_id, "name": "Renamed", "icon": "leaf"}
        )

        assert _error_text(result).startswith("Error updating label:")
        unchanged = lr.async_get(hass).async_get_label(label.label_id)
        assert (unchanged.name, unchanged.icon) == ("Outdoor", "mdi:tree")

    async def test_invalid_color_is_refused_and_nothing_changes(self, hass: HomeAssistant, label):
        result = await update_label(
            hass, {"label_id": label.label_id, "name": "Renamed", "color": "Red"}
        )

        assert _error_text(result).startswith("Error updating label: 'Red' is not a valid color")
        unchanged = lr.async_get(hass).async_get_label(label.label_id)
        assert (unchanged.name, unchanged.color) == ("Outdoor", "green")

    async def test_hex_color_is_accepted(self, hass: HomeAssistant, label):
        await update_label(hass, {"label_id": label.label_id, "color": "#ff9800"})

        assert lr.async_get(hass).async_get_label(label.label_id).color == "#ff9800"

    @pytest.mark.parametrize(
        ("field", "value"), [("name", 5), ("color", 5), ("icon", 5), ("description", {"a": 1})]
    )
    async def test_non_string_arguments_are_refused(
        self, hass: HomeAssistant, label, field: str, value
    ):
        result = await update_label(hass, {"label_id": label.label_id, field: value})

        type_name = type(value).__name__
        assert (
            _error_text(result)
            == f"Error updating label: '{field}' must be a string, not {type_name}"
        )
        unchanged = lr.async_get(hass).async_get_label(label.label_id)
        assert (unchanged.name, unchanged.color, unchanged.icon, unchanged.description) == (
            "Outdoor",
            "green",
            "mdi:tree",
            "Garden",
        )

    async def test_no_fields_is_reported(self, hass: HomeAssistant, label):
        result = await update_label(hass, {"label_id": label.label_id})

        assert "Nothing to update" in _error_text(result)

    async def test_unknown_label_is_reported(self, hass: HomeAssistant):
        result = await update_label(hass, {"label_id": "nope", "name": "X"})

        assert (
            _error_text(result)
            == "Error updating label: No label with label_id 'nope' (see list_labels)"
        )


class TestDeleteLabel:
    async def test_deletes_the_label(self, hass: HomeAssistant):
        label = lr.async_get(hass).async_create("Outdoor")
        keep = lr.async_get(hass).async_create("Keep")

        result = await delete_label(hass, {"label_id": label.label_id})

        assert [lb.label_id for lb in lr.async_get(hass).async_list_labels()] == [keep.label_id]
        assert _text(result) == f"Deleted label 'Outdoor' ({label.label_id})"

    async def test_removes_the_label_from_what_carried_it(self, hass: HomeAssistant):
        """The cascade is Home Assistant's own, and is what the response counts."""
        labels = lr.async_get(hass)
        doomed = labels.async_create("Doomed")
        keep = labels.async_create("Keep")

        entities = er.async_get(hass)
        entity_id = entities.async_get_or_create("light", "hue", "l1").entity_id
        entities.async_update_entity(entity_id, labels={doomed.label_id, keep.label_id})

        config_entry = MockConfigEntry(domain="hue")
        config_entry.add_to_hass(hass)
        devices = dr.async_get(hass)
        device = devices.async_get_or_create(
            config_entry_id=config_entry.entry_id, identifiers={("hue", "d1")}
        )
        devices.async_update_device(device.id, labels={doomed.label_id})

        areas = ar.async_get(hass)
        area = areas.async_create("Garden", labels={doomed.label_id})

        result = await delete_label(hass, {"label_id": doomed.label_id})
        await hass.async_block_till_done()

        assert entities.async_get(entity_id).labels == {keep.label_id}
        assert devices.async_get(device.id).labels == set()
        assert areas.async_get_area(area.id).labels == set()
        assert _text(result).endswith(", removing it from 1 entity, 1 device, 1 area")

    async def test_counts_are_plural_above_one(self, hass: HomeAssistant):
        doomed = lr.async_get(hass).async_create("Doomed")
        entities = er.async_get(hass)
        for unique_id in ("l1", "l2"):
            entity_id = entities.async_get_or_create("light", "hue", unique_id).entity_id
            entities.async_update_entity(entity_id, labels={doomed.label_id})
        ar.async_get(hass).async_create("Garden", labels={doomed.label_id})
        ar.async_get(hass).async_create("Porch", labels={doomed.label_id})

        result = await delete_label(hass, {"label_id": doomed.label_id})

        assert _text(result).endswith(", removing it from 2 entities, 2 areas")

    async def test_unknown_label_is_reported_and_nothing_is_deleted(self, hass: HomeAssistant):
        lr.async_get(hass).async_create("Outdoor")

        result = await delete_label(hass, {"label_id": "nope"})

        assert (
            _error_text(result)
            == "Error deleting label: No label with label_id 'nope' (see list_labels)"
        )
        assert len(list(lr.async_get(hass).async_list_labels())) == 1

    async def test_is_marked_destructive(self):
        assert TOOLS["delete_label"]["schema"]["annotations"]["destructiveHint"] is True


class TestLabelShape:
    """list_labels, hass://labels and the label tools' own replies report one shape."""

    async def test_the_tool_resource_and_replies_agree(self, hass: HomeAssistant):
        created = await create_label(
            hass, {"name": "Outdoor", "color": "green", "icon": "mdi:tree", "description": "Garden"}
        )
        (label,) = lr.async_get(hass).async_list_labels()

        (listed,) = json.loads(_text(await list_labels(hass, {})))
        (resource,) = json.loads(_read_labels(hass, "hass://labels")[0]["text"])

        assert listed == resource == label_json(label) == _json_after("Created label ", created)
        assert set(listed) == {"label_id", "name", "color", "icon", "description"}


class TestDispatch:
    """The tools reach callers through call_tool, which owns required-argument checks."""

    @pytest.fixture(autouse=True)
    def loaded(self, hass: HomeAssistant) -> None:
        """Stand in for the set-up entry, without which no tool is dispatched."""
        hass.data[DOMAIN] = {"read_only": False}

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("create_label", {}),
            ("create_label", {"name": None}),
            ("update_label", {"name": "X"}),
            ("delete_label", {}),
        ],
    )
    async def test_missing_required_argument_is_rejected(self, hass, tool, arguments):
        with pytest.raises(InvalidToolArguments):
            await call_tool(hass, tool, arguments)

    async def test_full_lifecycle(self, hass: HomeAssistant):
        created = await call_tool(hass, "create_label", {"name": "Outdoor"})
        label_id = _json_after("Created label ", created)["label_id"]

        await call_tool(hass, "update_label", {"label_id": label_id, "color": "green"})
        listed = json.loads(_text(await call_tool(hass, "list_labels", {})))
        assert [(lb["label_id"], lb["color"]) for lb in listed] == [(label_id, "green")]

        await call_tool(hass, "delete_label", {"label_id": label_id})
        assert json.loads(_text(await call_tool(hass, "list_labels", {}))) == []
