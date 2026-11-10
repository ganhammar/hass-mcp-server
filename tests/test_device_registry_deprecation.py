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
from custom_components.mcp_server_http_transport.tools.entities import (
    get_device_details,
    list_devices,
)
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


@pytest.fixture
def child_device(hass: HomeAssistant, devices) -> dr.DeviceEntry:
    """A child of the first device, on a core that has child devices."""
    registry = dr.async_get(hass)
    create_child = getattr(registry, "async_get_or_create_child", None)
    if create_child is None:
        pytest.skip("this core has no child devices")
    return create_child(
        config_entry_id=devices[0].config_entry_id,
        identifiers={("demo", "socket-1")},
        name="Socket 1",
        parent_device_id=devices[0].id,
    )


async def test_a_child_device_is_described_without_the_details_it_does_not_have(
    hass, devices, child_device, reported, caplog
):
    """A child device has no manufacturer or model, and reading them is reported."""
    result = await get_device_details(hass, {"device_id": child_device.id})

    described = json.loads(result["content"][0]["text"])["device"]
    assert described == {
        "id": child_device.id,
        "name": "Socket 1",
        "manufacturer": None,
        "model": None,
        "area_id": None,
        "name_by_user": None,
        "parent_device_id": devices[0].id,
    }
    _assert_nothing_reported(reported, caplog)


async def test_deleting_a_label_counts_the_child_devices_that_carry_it(
    hass, devices, child_device, reported, caplog
):
    """Home Assistant clears the label from child devices too."""
    registry = dr.async_get(hass)
    label = lr.async_get(hass).async_create("Doomed")
    registry.async_update_device(devices[0].id, labels={label.label_id})
    registry.async_update_child_device(child_device.id, labels={label.label_id})

    result = await delete_label(hass, {"label_id": label.label_id})

    assert "2 devices" in result["content"][0]["text"]
    assert registry.async_get(child_device.id).labels == set()
    _assert_nothing_reported(reported, caplog)
