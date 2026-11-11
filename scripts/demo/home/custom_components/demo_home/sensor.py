"""Temperature and humidity sensors of the demo home."""

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, Platform, UnitOfTemperature

from . import DEVICES

KINDS = {
    "temperature": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
    "humidity": (SensorDeviceClass.HUMIDITY, PERCENTAGE),
}


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None) -> None:
    async_add_entities(
        DemoSensor(object_id, name, spec)
        for object_id, name, _room, spec in DEVICES[Platform.SENSOR]
    )


class DemoSensor(SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, object_id: str, name: str, spec: dict[str, Any]) -> None:
        self._attr_unique_id = object_id
        self._attr_name = name
        self._attr_device_class, self._attr_native_unit_of_measurement = KINDS[spec["kind"]]
        self._attr_native_value = spec["value"]
