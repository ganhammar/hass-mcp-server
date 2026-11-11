"""Entity, area, device, and service tools."""

import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.service import async_get_all_descriptions

from . import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_READ_ONLY,
    caller_context,
    dumps,
    error_result,
    register_tool,
)
from .labels import label_json
from .registries import area_json, device_entries, is_child_device

_LOGGER = logging.getLogger(__name__)

_HAS_GET_ENTITY_ALIASES = hasattr(er, "async_get_entity_aliases")


def _get_aliases(hass: HomeAssistant, entry: er.RegistryEntry) -> list[str]:
    """Get entity aliases, handling ComputedNameType on HA 2026.4+."""
    if _HAS_GET_ENTITY_ALIASES:
        return er.async_get_entity_aliases(hass, entry)
    return sorted(str(a) for a in entry.aliases) if entry.aliases else []


@register_tool(
    name="get_state",
    description=(
        "Get the state and attributes of one entity by its ID. To find entities by name "
        "or room, use search_entities, which returns their states too"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity ID (e.g., light.living_room)",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Limit which attribute keys are included in the response "
                    "(e.g., ['brightness', 'color_temp']). Omit for all attributes"
                ),
            },
        },
        "required": ["entity_id"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_state(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get entity state."""
    entity_id = arguments["entity_id"]
    fields = arguments.get("fields")
    state = hass.states.get(entity_id)

    if state is None:
        return error_result(f"Entity {entity_id} not found")

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    aliases = _get_aliases(hass, entry) if entry else []

    attributes = dict(state.attributes)
    if fields is not None:
        attributes = {k: v for k, v in attributes.items() if k in fields}

    result = {
        "entity_id": state.entity_id,
        "state": state.state,
        "attributes": attributes,
        "aliases": aliases,
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }

    return {"content": [{"type": "text", "text": dumps(result)}]}


@register_tool(
    name="call_service",
    description="Call a Home Assistant service",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "The service domain (e.g., light, switch)",
            },
            "service": {
                "type": "string",
                "description": "The service name (e.g., turn_on, turn_off)",
            },
            "entity_id": {
                "type": "string",
                "description": "The entity ID to target",
            },
            "data": {
                "type": "object",
                "description": "Additional service data",
            },
        },
        "required": ["domain", "service"],
    },
    annotations=ANNOTATION_DESTRUCTIVE,
)
async def call_service(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a Home Assistant service, returning its response data when it has any."""
    domain = arguments["domain"]
    service = arguments["service"]
    entity_id = arguments.get("entity_id")
    data = arguments.get("data", {})

    service_data = {**data}
    if entity_id:
        service_data["entity_id"] = entity_id

    try:
        # A service declared SupportsResponse.ONLY cannot be called at all
        # without asking for its response: Home Assistant refuses the call
        # rather than dropping the data. That covers whole categories of reads
        # that only exist as services, among them calendar.get_events,
        # todo.get_items, weather.get_forecasts and recorder.get_statistics.
        # OPTIONAL services do run, but withhold what they were asked for.
        #
        # supports_response() subscripts the service registry directly and
        # raises KeyError for anything the registry does not hold, despite a
        # docstring promising NONE, so has_service() guards it. An unknown
        # service then reaches async_call and is reported as the
        # ServiceNotFound it is.
        supports = (
            hass.services.supports_response(domain, service)
            if hass.services.has_service(domain, service)
            else SupportsResponse.NONE
        )
        wants_response = supports is not SupportsResponse.NONE

        response = await hass.services.async_call(
            domain,
            service,
            service_data,
            blocking=True,
            context=caller_context(),
            return_response=wants_response,
        )

        # An OPTIONAL service with nothing to say answers with an empty dict
        # rather than None, as every script without a response variable does.
        # That carries no more than the success message.
        if wants_response and response:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": dumps(response),
                    }
                ]
            }

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully called {domain}.{service}",
                }
            ]
        }
    except Exception as e:
        _LOGGER.error("Error calling service: %s", e)
        return error_result(f"Error calling service: {str(e)}")


@register_tool(
    name="list_entities",
    description=(
        "List every entity with its state, or every entity of one domain, which is long "
        "on most homes. For a question about particular things or a room, use "
        "search_entities instead"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Filter by domain (optional)",
            },
            "detailed": {
                "type": "boolean",
                "description": (
                    "Include full attributes, last_changed, and last_updated "
                    "(default false for a lean response)"
                ),
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Whitelist specific keys to include per entity "
                    "(e.g., ['entity_id', 'state', 'attributes']). "
                    "Takes precedence over the detailed flag"
                ),
            },
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_entities(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List entities."""
    domain_filter = arguments.get("domain")
    detailed = arguments.get("detailed", False)
    fields = arguments.get("fields")
    registry = er.async_get(hass)

    entities = []
    for state in hass.states.async_all():
        if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
            continue
        entry = registry.async_get(state.entity_id)
        aliases = _get_aliases(hass, entry) if entry else []

        if fields is not None:
            full = {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                "aliases": aliases,
                "attributes": dict(state.attributes),
                "last_changed": state.last_changed.isoformat(),
                "last_updated": state.last_updated.isoformat(),
            }
            entity = {k: v for k, v in full.items() if k in fields}
        elif detailed:
            entity = {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                "aliases": aliases,
                "attributes": dict(state.attributes),
                "last_changed": state.last_changed.isoformat(),
                "last_updated": state.last_updated.isoformat(),
            }
        else:
            entity = {
                "entity_id": state.entity_id,
                "state": state.state,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                "aliases": aliases,
            }
        entities.append(entity)

    return {"content": [{"type": "text", "text": dumps(entities)}]}


@register_tool(
    name="list_areas",
    description=(
        "List all areas with their floor and aliases. search_entities takes room names "
        "directly, so this is for seeing what the rooms are called"
    ),
    input_schema={
        "type": "object",
        "properties": {},
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_areas(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all areas."""
    registry = ar.async_get(hass)
    areas = [area_json(area) for area in registry.async_list_areas()]

    return {"content": [{"type": "text", "text": dumps(areas)}]}


@register_tool(
    name="list_devices",
    description=(
        "List devices in Home Assistant with their area, labels and whether they are "
        "disabled, optionally filtered by area"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "area_id": {
                "type": "string",
                "description": "Filter by area ID (optional)",
            }
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_devices(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List devices."""
    registry = dr.async_get(hass)
    area_filter = arguments.get("area_id")

    devices = []
    for device in device_entries(registry):
        if area_filter and device.area_id != area_filter:
            continue
        devices.append(
            {
                "id": device.id,
                "name": device.name,
                "manufacturer": device.manufacturer,
                "model": device.model,
                "area_id": device.area_id,
                "name_by_user": device.name_by_user,
                "labels": sorted(device.labels),
                "disabled_by": device.disabled_by,
            }
        )

    return {"content": [{"type": "text", "text": dumps(devices)}]}


@register_tool(
    name="get_device_details",
    description=(
        "Get a device together with every entity registered to it, across all domains. "
        "Use this after list_devices when a request is about a physical device "
        "(e.g. 'where is the vacuum', 'what can my washing machine do'): it returns all "
        "related entities so you do not have to guess entity IDs or naming conventions. "
        "Includes each entity's current state and attributes by default"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "device_id": {
                "type": "string",
                "description": "The device ID, as returned by list_devices",
            },
            "include_entities": {
                "type": "boolean",
                "description": "Include entities registered to the device (default true)",
            },
            "include_states": {
                "type": "boolean",
                "description": (
                    "Include each entity's current state and attributes (default true). "
                    "Ignored when include_entities is false"
                ),
            },
            "include_disabled": {
                "type": "boolean",
                "description": "Include disabled entities (default false)",
            },
        },
        "required": ["device_id"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_device_details(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get a device and the entities registered to it."""
    device_id = arguments["device_id"]
    include_entities = arguments.get("include_entities", True)
    include_states = arguments.get("include_states", True)
    include_disabled = arguments.get("include_disabled", False)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if device is None:
        return error_result(f"Device {device_id} not found")

    child = is_child_device(device)
    result: dict[str, Any] = {
        "device": {
            "id": device.id,
            "name": device.name,
            "manufacturer": None if child else device.manufacturer,
            "model": None if child else device.model,
            "area_id": device.area_id,
            "name_by_user": device.name_by_user,
        }
    }
    if child:
        result["device"]["parent_device_id"] = device.parent_device_id

    if include_entities:
        entity_registry = er.async_get(hass)
        entries = er.async_entries_for_device(
            entity_registry, device_id, include_disabled_entities=include_disabled
        )
        entities = []
        for entry in entries:
            entity: dict[str, Any] = {
                "entity_id": entry.entity_id,
                "domain": entry.entity_id.split(".")[0],
                "name": entry.name or entry.original_name or entry.entity_id,
                "disabled": entry.disabled_by is not None,
            }
            if include_states:
                state = hass.states.get(entry.entity_id)
                if state is not None:
                    entity["state"] = state.state
                    entity["attributes"] = dict(state.attributes)
                    entity["name"] = state.attributes.get("friendly_name", entity["name"])
                else:
                    entity["state"] = None
            entities.append(entity)
        result["entities"] = entities

    return {"content": [{"type": "text", "text": dumps(result)}]}


@register_tool(
    name="list_services",
    description="List available services in Home Assistant, optionally filtered by domain",
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Filter by domain (optional)",
            }
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_services(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List available services."""
    domain_filter = arguments.get("domain")
    services = hass.services.async_services()

    if domain_filter:
        services = {k: v for k, v in services.items() if k == domain_filter}

    # Convert service objects to serializable format
    result = {}
    for domain, domain_services in services.items():
        result[domain] = list(domain_services.keys())

    return {"content": [{"type": "text", "text": dumps(result)}]}


@register_tool(
    name="describe_service",
    description=(
        "Get the full parameter schema for a service: each field's description, whether it "
        "is required, example values, selectors, and target constraints. Use this after "
        "list_services when you need to know how to call a service, instead of guessing the "
        "payload. Note: selectors describe a parameter's type and constraints; for dynamic "
        "per-entity options (such as a specific vacuum's mapped rooms) read the target "
        "entity's attributes via get_state"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "The service domain (e.g., vacuum, light)",
            },
            "service": {
                "type": "string",
                "description": (
                    "The service name (e.g., clean_area). "
                    "Omit to describe every service in the domain"
                ),
            },
        },
        "required": ["domain"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def describe_service(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Describe a service's parameters, selectors, and targets."""
    domain = arguments["domain"]
    service = arguments.get("service")

    descriptions = await async_get_all_descriptions(hass)
    domain_services = descriptions.get(domain)
    if not domain_services:
        return error_result(f"No services found for domain {domain}")

    if service is not None:
        description = domain_services.get(service)
        if description is None:
            return error_result(f"Service {domain}.{service} not found")
        result = {service: description}
    else:
        result = dict(domain_services)

    return {"content": [{"type": "text", "text": dumps(result)}]}


SEARCH_LIMIT = 100
# How many room names a reply lists when the query named nothing in the home.
SEARCH_ROOMS_LISTED = 40

_WORD_BREAK = re.compile(r"[\W_]+")

# How well a query word matches a word of an entity's text, best first.
_WHOLE, _INFLECTED, _INSIDE, _STEM_INSIDE = range(4)


def _words(*texts: str | None) -> frozenset[str]:
    """Return the words of some text, casefolded and split on anything but letters and digits."""
    return frozenset(
        word for text in texts if text for word in _WORD_BREAK.split(text.casefold()) if word
    )


def _word_match(word: str, token: str) -> int | None:
    """Return how well a query word matches one word of an entity's text, or None.

    A whole word is best. Next comes the same word with another ending, in either
    direction: 'lights' and 'light', 'kontoret' and 'kontor', 'lampor' and 'lampa',
    or 'temp' for 'temperature'. Then a word inside a longer one, as 'lampa' in
    'taklampa', and last a word inside a longer one once up to two letters of its
    ending are dropped. Numbers and words of one or two letters match whole words
    only, and no rule matches on fewer than four shared letters, which keeps
    'ljus' from finding 'ljud'.
    """
    if word == token:
        return _WHOLE
    if word.isdigit() or len(word) < 3:
        return None
    if token.startswith(word):
        return _INFLECTED
    shared = 0
    for ours, theirs in zip(word, token, strict=False):
        if ours != theirs:
            break
        shared += 1
    if shared >= 4 and len(word) - shared <= 3 and len(token) - shared <= 3:
        return _INFLECTED
    if len(word) >= 4 and word in token:
        return _INSIDE
    for cut in (1, 2):
        if len(word) - cut >= 4 and word[:-cut] in token:
            return _STEM_INSIDE
    return None


def _area_ids_for(registry: ar.AreaRegistry, value: str) -> set[str]:
    """Return the IDs of the areas a value refers to: an ID, else a name, else an alias."""
    area = registry.async_get_area(value) or registry.async_get_area_by_name(value)
    if area is not None:
        return {area.id}
    return {area.id for area in registry.async_get_areas_by_alias(value)}


class _Places:
    """The area, floor and device words an entity can be found by, looked up once each."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._areas = ar.async_get(hass)
        self._floors = fr.async_get(hass)
        self._devices = dr.async_get(hass)
        self._area_words: dict[str, frozenset[str]] = {}
        self._device: dict[str, tuple[str | None, frozenset[str]]] = {}

    def device(self, device_id: str | None) -> tuple[str | None, frozenset[str]]:
        """Return a device's area ID and the words of its names."""
        if not device_id:
            return None, frozenset()
        if device_id not in self._device:
            device = self._devices.async_get(device_id)
            if device is None:
                self._device[device_id] = (None, frozenset())
            else:
                self._device[device_id] = (
                    device.area_id,
                    _words(device.name_by_user, device.name),
                )
        return self._device[device_id]

    def area_name(self, area_id: str | None) -> str | None:
        area = self._areas.async_get_area(area_id) if area_id else None
        return area.name if area else None

    def area_words(self, area_id: str | None) -> frozenset[str]:
        """Return the words of an area's name and aliases, and of its floor's."""
        if not area_id:
            return frozenset()
        if area_id not in self._area_words:
            area = self._areas.async_get_area(area_id)
            names: list[str] = []
            if area is not None:
                names += [area.name, *area.aliases]
                floor = self._floors.async_get_floor(area.floor_id) if area.floor_id else None
                if floor is not None:
                    names += [floor.name, *floor.aliases]
            self._area_words[area_id] = _words(*names)
        return self._area_words[area_id]

    def room_names(self) -> list[str]:
        return sorted(area.name for area in self._areas.async_list_areas())


@register_tool(
    name="search_entities",
    description=(
        "Find entities by plain words and get their current states in one call. Start "
        "here for questions like 'are the office lights on?': pass query 'office lights' "
        "without looking up areas or entity IDs first. Every word must match, in any "
        "order, the entity ID, name, aliases or device class, or the name or an alias of "
        "its area, floor or device; endings may differ ('lights' finds 'light'). A word "
        "nothing in the home matches is left out, and the reply says so and lists the rooms"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Words naming the thing and the room, e.g. 'office light' or 'kitchen "
                    "temperature', in the home's own language where you know its names"
                ),
            },
            "device_class": {
                "type": "string",
                "description": "Filter by device class (e.g., temperature, motion, door)",
            },
            "domain": {
                "type": "string",
                "description": "Filter by domain (e.g., sensor, binary_sensor, light)",
            },
            "area_id": {
                "type": "string",
                "description": "Filter by area: its ID, else its name, else an alias",
            },
            "limit": {
                "type": "integer",
                "description": (
                    f"Maximum number of results (default {SEARCH_LIMIT}); the reply says "
                    "when more matched"
                ),
            },
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def search_entities(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Find entities whose every query word matches their names or where they are."""
    query = arguments.get("query") or ""
    device_class_filter = arguments.get("device_class")
    domain_filter = arguments.get("domain")
    area_argument = arguments.get("area_id")
    limit = arguments.get("limit")
    if limit is None:
        limit = SEARCH_LIMIT

    if not isinstance(query, str):
        return error_result("Error: query must be a string")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return error_result("Error: limit must be a positive integer")
    asked = list(dict.fromkeys(word for word in _WORD_BREAK.split(query.casefold()) if word))
    if not any([asked, device_class_filter, domain_filter, area_argument]):
        return error_result(
            "Error: at least one search parameter "
            "(query, device_class, domain, area_id) must be provided"
        )

    area_filter: set[str] | None = None
    if area_argument:
        area_filter = _area_ids_for(ar.async_get(hass), str(area_argument))
        if not area_filter:
            return error_result(
                f"No area with the ID, name or alias '{area_argument}' (see list_areas)"
            )

    entity_registry = er.async_get(hass)
    places = _Places(hass)
    known: dict[tuple[str, str], int | None] = {}

    def best(word: str, tokens: frozenset[str]) -> int | None:
        """Return the best match of a query word among some words, or None."""
        found: int | None = None
        for token in tokens:
            if (word, token) not in known:
                known[(word, token)] = _word_match(word, token)
            quality = known[(word, token)]
            if quality is not None and (found is None or quality < found):
                found = quality
        return found

    # Every entity the filters leave, with how well each query word matches it.
    candidates: list[tuple[list[int | None], bool, tuple[Any, ...], dict[str, Any]]] = []
    for state in hass.states.async_all():
        domain = state.domain
        if domain_filter and domain != domain_filter:
            continue
        device_class = state.attributes.get("device_class")
        if device_class_filter and device_class != device_class_filter:
            continue

        entry = entity_registry.async_get(state.entity_id)
        aliases = _get_aliases(hass, entry) if entry else []
        device_area, device_words = places.device(entry.device_id if entry else None)
        # An entity's own area wins over its device's.
        area_id = (entry.area_id if entry else None) or device_area

        if area_filter is not None and area_id not in area_filter:
            continue

        friendly_name = str(state.attributes.get("friendly_name", state.entity_id))
        tokens = (
            _words(state.entity_id, friendly_name, *aliases, str(device_class or ""))
            | places.area_words(area_id)
            | device_words
        )
        quality = [best(word, tokens) for word in asked]

        # Entities a person looks at come before configuration, diagnostics and
        # hidden ones.
        secondary = entry is not None and (
            entry.entity_category is not None
            or entry.hidden_by is not None
            or entry.disabled_by is not None
        )
        domain_words = _words(domain) | {domain}
        names_domain = [
            (match := best(word, domain_words)) is not None and match <= _INFLECTED
            for word in asked
        ]
        own_names = {state.entity_id, friendly_name.casefold(), *(a.casefold() for a in aliases)}

        result: dict[str, Any] = {
            "entity_id": state.entity_id,
            "state": state.state,
            "friendly_name": friendly_name,
            "device_class": device_class,
            "area_id": area_id,
            "area": places.area_name(area_id),
            # Newer cores list the entity's own name among its aliases by default.
            "aliases": [a for a in aliases if a and a != friendly_name],
        }
        if (unit := state.attributes.get("unit_of_measurement")) is not None:
            result["unit_of_measurement"] = unit
        candidates.append((quality, secondary, (names_domain, own_names), result))

    # A word that matches nothing at all is one the home does not use, such as
    # 'the' or a room named in another language, so the search goes on without it.
    used = [
        index
        for index in range(len(asked))
        if any(quality[index] is not None for quality, *_ in candidates)
    ]
    unknown = [word for index, word in enumerate(asked) if index not in used]
    phrase = " ".join(asked[index] for index in used)

    # With no word left to search by, the filters alone decide, and without a
    # filter either there is nothing to list.
    filtered = bool(device_class_filter or domain_filter or area_filter is not None)
    matches: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    if used or filtered:
        for quality, secondary, (names_domain, own_names), result in candidates:
            if any(quality[index] is None for index in used):
                continue
            # After the entities a person looks at: a name or alias the query
            # spells out in full, then the closest words, then entities of a
            # domain the query names, so 'office lights' puts the lights before
            # the office light sensor.
            rank = (
                secondary,
                phrase not in own_names,
                sum(quality[index] or 0 for index in used),
                not any(names_domain[index] for index in used),
                result["entity_id"],
            )
            matches.append((rank, result))

    matches.sort(key=lambda match: match[0])
    content = [{"type": "text", "text": dumps([result for _, result in matches[:limit]])}]
    notes: list[str] = []
    if unknown:
        named = ", ".join(f"'{word}'" for word in unknown)
        rooms = places.room_names()
        listed = ", ".join(rooms[:SEARCH_ROOMS_LISTED]) or "none"
        more = (
            f" and {len(rooms) - SEARCH_ROOMS_LISTED} more"
            if len(rooms) > SEARCH_ROOMS_LISTED
            else ""
        )
        notes.append(
            f"Nothing in this home matches {named}, so the search left "
            f"{'it' if len(unknown) == 1 else 'them'} out. Names may be in another "
            f"language. Rooms: {listed}{more}."
        )
    if not matches and used:
        named = ", ".join(f"'{asked[index]}'" for index in used)
        notes.append(
            f"No entity matches {named} together. Try fewer words, or the domain, "
            "device_class or area filter."
        )
    if len(matches) > limit:
        notes.append(
            f"Showing the best {limit} of {len(matches)} matches. Add words or "
            "filters to narrow the search, or raise limit."
        )
    if notes:
        content.append({"type": "text", "text": " ".join(notes)})
    return {"content": content}


@register_tool(
    name="list_labels",
    description=(
        "List all labels in Home Assistant. "
        "Labels are used to tag entities, devices, and areas for cross-domain grouping"
    ),
    input_schema={
        "type": "object",
        "properties": {},
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_labels(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List all labels."""
    registry = lr.async_get(hass)
    labels = [label_json(label) for label in registry.async_list_labels()]

    return {"content": [{"type": "text", "text": dumps(labels)}]}


@register_tool(
    name="batch_get_state",
    description=(
        "Get the state of several entities whose IDs you know, in one call. "
        "To find entities by name or room, use search_entities"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of entity IDs to get state for (max 50)",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Limit which attribute keys are included in each entity's response "
                    "(e.g., ['brightness', 'color_temp']). Omit for all attributes"
                ),
            },
        },
        "required": ["entity_ids"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def batch_get_state(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get state for multiple entities."""
    entity_ids = arguments["entity_ids"]
    fields = arguments.get("fields")

    if len(entity_ids) > 50:
        return error_result("Error: maximum 50 entity IDs per request")

    registry = er.async_get(hass)
    results = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None:
            results.append({"entity_id": entity_id, "error": "not found"})
            continue

        entry = registry.async_get(entity_id)
        aliases = _get_aliases(hass, entry) if entry else []

        attributes = dict(state.attributes)
        if fields is not None:
            attributes = {k: v for k, v in attributes.items() if k in fields}

        results.append(
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "attributes": attributes,
                "aliases": aliases,
                "last_changed": state.last_changed.isoformat(),
                "last_updated": state.last_updated.isoformat(),
            }
        )

    return {"content": [{"type": "text", "text": dumps(results)}]}
