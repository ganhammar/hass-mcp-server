"""The speaker of the demo home, playing a made-up playlist."""

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.const import Platform

from . import DEVICES


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None) -> None:
    async_add_entities(
        DemoSpeaker(object_id, name, spec)
        for object_id, name, _room, spec in DEVICES[Platform.MEDIA_PLAYER]
    )


class DemoSpeaker(MediaPlayerEntity):
    _attr_media_content_type = MediaType.PLAYLIST
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.VOLUME_SET
    )

    def __init__(self, object_id: str, name: str, spec: dict[str, Any]) -> None:
        self._attr_unique_id = object_id
        self._attr_name = name
        self._attr_state = MediaPlayerState.PLAYING
        self._attr_media_title = spec["title"]
        self._attr_volume_level = spec["volume"]

    async def async_media_play(self) -> None:
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        self._attr_state = MediaPlayerState.PAUSED
        self.async_write_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        self._attr_volume_level = volume
        self.async_write_ha_state()
