"""Tests for the shared JSON helpers."""

import json
from datetime import date, datetime

import pytest

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

    def test_raises_type_error_for_unhandled_types(self):
        class CustomType:
            pass

        with pytest.raises(TypeError):
            json.dumps(CustomType(), cls=_HAJSONEncoder)


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

    def test_honours_encoder_override(self):
        class Wrapper:
            pass

        class WrapperEncoder(json.JSONEncoder):
            def default(self, o):
                return "wrapped" if isinstance(o, Wrapper) else super().default(o)

        with pytest.raises(TypeError):
            dumps(Wrapper())
        assert dumps({"w": Wrapper()}, cls=WrapperEncoder) == '{"w":"wrapped"}'
