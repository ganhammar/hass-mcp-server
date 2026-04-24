"""Tests for the shared _HAJSONEncoder."""

import json
from datetime import date, datetime

import pytest

from custom_components.mcp_server_http_transport.json_utils import _HAJSONEncoder


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
