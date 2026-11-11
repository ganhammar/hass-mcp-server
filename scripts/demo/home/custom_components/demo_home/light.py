"""Lights of the demo home."""

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import Platform

from . import DEVICES


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None) -> None:
    async_add_entities(
        DemoLight(object_id, name, spec) for object_id, name, _room, spec in DEVICES[Platform.LIGHT]
    )


class DemoLight(LightEntity):
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, object_id: str, name: str, spec: dict[str, Any]) -> None:
        self._attr_unique_id = object_id
        self._attr_name = name
        self._attr_is_on = spec["on"]
        self._attr_brightness = spec.get("brightness", 255)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self._attr_brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
