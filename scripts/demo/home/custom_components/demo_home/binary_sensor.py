"""Door sensors of the demo home."""

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import Platform

from . import DEVICES


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None) -> None:
    async_add_entities(
        DemoDoor(object_id, name, spec)
        for object_id, name, _room, spec in DEVICES[Platform.BINARY_SENSOR]
    )


class DemoDoor(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, object_id: str, name: str, spec: dict[str, Any]) -> None:
        self._attr_unique_id = object_id
        self._attr_name = name
        self._attr_is_on = spec["on"]
