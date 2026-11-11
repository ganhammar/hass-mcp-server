"""A made-up home for the README demo: two floors, five rooms and a few devices.

Every entity keeps the state it is created with, so the demo answer is the same on
every run. The floors and rooms are created on setup, and each entity is placed in
its room through the entity registry before its platform adds it.
"""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

DOMAIN = "demo_home"

FLOORS = {
    "Ground floor": (0, ["Hallway", "Kitchen", "Living room"]),
    "Upstairs": (1, ["Bedroom", "Office"]),
}

# platform: [(object ID, name, room, what the platform needs to build it)]
DEVICES = {
    Platform.LIGHT: [
        ("living_room_lamp", "Living room lamp", "Living room", {"on": True, "brightness": 150}),
        ("kitchen_ceiling", "Kitchen ceiling light", "Kitchen", {"on": True, "brightness": 255}),
        ("hallway_light", "Hallway light", "Hallway", {"on": False}),
        ("bedroom_lamp", "Bedroom lamp", "Bedroom", {"on": False}),
        ("office_desk_lamp", "Office desk lamp", "Office", {"on": True, "brightness": 200}),
    ],
    Platform.SENSOR: [
        ("living_room_temperature", "Living room temperature", "Living room",
         {"kind": "temperature", "value": 21.4}),
        ("living_room_humidity", "Living room humidity", "Living room",
         {"kind": "humidity", "value": 44}),
        ("bedroom_temperature", "Bedroom temperature", "Bedroom",
         {"kind": "temperature", "value": 19.2}),
        ("bedroom_humidity", "Bedroom humidity", "Bedroom", {"kind": "humidity", "value": 51}),
        ("office_temperature", "Office temperature", "Office",
         {"kind": "temperature", "value": 22.1}),
    ],
    Platform.BINARY_SENSOR: [
        ("front_door", "Front door", "Hallway", {"on": False}),
        ("balcony_door", "Balcony door", "Bedroom", {"on": True}),
    ],
    Platform.MEDIA_PLAYER: [
        ("living_room_speaker", "Living room speaker", "Living room",
         {"title": "Sunday Morning Mix", "volume": 0.3}),
    ],
}  # fmt: skip


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Create the floors and rooms, place the entities and load the platforms."""
    floors = fr.async_get(hass)
    areas = ar.async_get(hass)
    entities = er.async_get(hass)

    room_ids = {}
    for floor_name, (level, rooms) in FLOORS.items():
        floor = floors.async_get_floor_by_name(floor_name) or floors.async_create(
            floor_name, level=level
        )
        for room in rooms:
            area = areas.async_get_area_by_name(room) or areas.async_create(
                room, floor_id=floor.floor_id
            )
            room_ids[room] = area.id

    for platform, devices in DEVICES.items():
        for object_id, _name, room, _spec in devices:
            entry = entities.async_get_or_create(
                platform, DOMAIN, object_id, suggested_object_id=object_id
            )
            entities.async_update_entity(entry.entity_id, area_id=room_ids[room])
        hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config))

    return True
