"""The device registry is read without the access Home Assistant deprecates.

From 2026.9 `DeviceRegistry.devices` is a view that reports any use as a mapping
(`.values()`, `.get()`, lookup by ID, `in`) through `report_usage`. These drive the
tools and resources that enumerate devices against the real registry and fail when
one of them is reported.
"""

import json
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mcp_server_http_transport.resources import read_resource
from custom_components.mcp_server_http_transport.tools.entities import list_devices
from custom_components.mcp_server_http_transport.tools.labels import delete_label


@pytest.fixture
def reported():
    """Collect what the device registry reports as deprecated use."""
    with patch.object(dr, "report_usage") as report:
        yield report


@pytest.fixture
def devices(hass: HomeAssistant) -> list[dr.DeviceEntry]:
    entry = MockConfigEntry(domain="demo")
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    return [
        registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("demo", name)}, name=name
        )
        for name in ("Lamp", "Fan")
    ]


def _assert_nothing_reported(reported, caplog) -> None:
    reported.assert_not_called()
    assert "deprecated" not in caplog.text


async def test_list_devices_does_not_use_the_registry_as_a_mapping(hass, devices, reported, caplog):
    result = await list_devices(hass, {})

    assert {d["id"] for d in json.loads(result["content"][0]["text"])} == {d.id for d in devices}
    _assert_nothing_reported(reported, caplog)


async def test_list_devices_by_area_does_not_use_the_registry_as_a_mapping(
    hass, devices, reported, caplog
):
    area = ar.async_get(hass).async_create("Hall")
    dr.async_get(hass).async_update_device(devices[0].id, area_id=area.id)

    result = await list_devices(hass, {"area_id": area.id})

    assert [d["id"] for d in json.loads(result["content"][0]["text"])] == [devices[0].id]
    _assert_nothing_reported(reported, caplog)


async def test_the_devices_resource_does_not_use_the_registry_as_a_mapping(
    hass, devices, reported, caplog
):
    (content,) = await read_resource(hass, "hass://devices")

    assert {d["id"] for d in json.loads(content["text"])} == {d.id for d in devices}
    _assert_nothing_reported(reported, caplog)


async def test_deleting_a_label_counts_its_devices_without_using_the_registry_as_a_mapping(
    hass, devices, reported, caplog
):
    label = lr.async_get(hass).async_create("Doomed")
    dr.async_get(hass).async_update_device(devices[0].id, labels={label.label_id})

    result = await delete_label(hass, {"label_id": label.label_id})

    assert "1 device" in result["content"][0]["text"]
    _assert_nothing_reported(reported, caplog)
