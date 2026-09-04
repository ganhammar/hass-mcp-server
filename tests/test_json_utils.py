"""Tests for the shared JSON helpers."""

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from uuid import UUID

from custom_components.mcp_server_http_transport.json_utils import _HAJSONEncoder, dumps


class TestHAJSONEncoder:
    """Test _HAJSONEncoder handles HA state attribute types."""

    def test_encodes_datetime_as_isoformat(self):
        value = datetime(2024, 6, 15, 8, 30, 45)
        assert json.dumps(value, cls=_HAJSONEncoder) == '"2024-06-15T08:30:45"'

    def test_encodes_date_as_isoformat(self):
        value = date(2024, 6, 15)
        assert json.dumps(value, cls=_HAJSONEncoder) == '"2024-06-15"'

    def test_encodes_string_set_as_sorted_array(self):
        # Regression: Hue Bridge Pro groups expose `hue_scenes` as a set of
        # strings. Sorting gives stable, diff-friendly output.
        value = {"Entspannen", "Energie tanken", "Frühlingsblüten"}
        assert json.loads(json.dumps(value, cls=_HAJSONEncoder)) == [
            "Energie tanken",
            "Entspannen",
            "Frühlingsblüten",
        ]

    def test_encodes_string_frozenset_as_sorted_array(self):
        value = frozenset({"b", "a", "c"})
        assert json.loads(json.dumps(value, cls=_HAJSONEncoder)) == ["a", "b", "c"]

    def test_encodes_empty_set_as_empty_array(self):
        assert json.dumps(set(), cls=_HAJSONEncoder) == "[]"

    def test_encodes_mixed_type_set_as_unsorted_array(self):
        # Mixed types can't be sorted across types in Python 3, so we fall
        # back to list() without guaranteeing order.
        value = {1, "two"}
        decoded = json.loads(json.dumps(value, cls=_HAJSONEncoder))
        assert sorted(decoded, key=str) == [1, "two"]

    def test_encodes_nested_set_inside_dict(self):
        value = {"hue_scenes": {"a", "b"}, "brightness": 255}
        decoded = json.loads(json.dumps(value, cls=_HAJSONEncoder))
        assert decoded == {"hue_scenes": ["a", "b"], "brightness": 255}

    def test_encodes_time_as_isoformat(self):
        assert json.dumps(time(8, 30), cls=_HAJSONEncoder) == '"08:30:00"'

    def test_encodes_enum_as_its_value(self):
        class Mode(Enum):
            HEAT = "heat"

        assert json.dumps(Mode.HEAT, cls=_HAJSONEncoder) == '"heat"'

    def test_encodes_dataclass_as_dict(self):
        @dataclass
        class Point:
            x: int
            y: int

        assert json.loads(json.dumps(Point(1, 2), cls=_HAJSONEncoder)) == {"x": 1, "y": 2}

    def test_encodes_as_dict_objects(self):
        class WithAsDict:
            def as_dict(self):
                return {"kind": "custom"}

        assert json.loads(json.dumps(WithAsDict(), cls=_HAJSONEncoder)) == {"kind": "custom"}

    def test_delegates_paths_to_home_assistant_default(self):
        assert json.dumps(Path("/config/www"), cls=_HAJSONEncoder) == '"/config/www"'

    def test_falls_back_to_str_for_unhandled_types(self):
        # One exotic attribute must not blank a whole response: the tool wrapper
        # reports a raised TypeError as isError and the model gets nothing.
        assert json.dumps(timedelta(minutes=5), cls=_HAJSONEncoder) == '"0:05:00"'
        assert json.loads(json.dumps(UUID(int=1), cls=_HAJSONEncoder)) == str(UUID(int=1))


class TestDumps:
    """Test dumps emits compact JSON through the HA encoder."""

    def test_omits_indentation_and_separator_whitespace(self):
        value = {"entity_id": "light.a", "attributes": {"effect_list": ["x", "y"], "brightness": 1}}
        text = dumps(value)
        expected = '{"entity_id":"light.a","attributes":{"effect_list":["x","y"],"brightness":1}}'
        assert text == expected
        assert json.loads(text) == value

    def test_is_smaller_than_pretty_printed_output(self):
        value = [{"entity_id": f"light.l{i}", "effect_list": list("abcdef")} for i in range(20)]
        assert len(dumps(value)) < 0.7 * len(json.dumps(value, indent=2))

    def test_uses_ha_encoder_by_default(self):
        value = {"when": datetime(2024, 6, 15, 8, 30, 45), "scenes": {"b", "a"}}
        assert dumps(value) == '{"when":"2024-06-15T08:30:45","scenes":["a","b"]}'

    def test_keeps_non_ascii_text(self):
        # A \u escape is six characters the model pays for; the JSON-RPC envelope
        # re-escapes the text on the wire, so the HTTP response stays ASCII anyway.
        assert dumps({"name": "Kök"}) == '{"name":"Kök"}'

    def test_never_fails_on_an_exotic_attribute(self):
        payload = {"attributes": {"duration": timedelta(minutes=5), "path": Path("/x")}}
        assert json.loads(dumps(payload)) == {"attributes": {"duration": "0:05:00", "path": "/x"}}

    def test_honours_encoder_override(self):
        class Wrapper:
            pass

        class WrapperEncoder(json.JSONEncoder):
            def default(self, o):
                return "wrapped" if isinstance(o, Wrapper) else super().default(o)

        assert dumps({"w": Wrapper()}, cls=WrapperEncoder) == '{"w":"wrapped"}'
