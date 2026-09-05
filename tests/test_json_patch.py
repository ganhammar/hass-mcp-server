"""Tests for the JSON Pointer / JSON Patch helpers."""

import pytest

from custom_components.mcp_server_http_transport.json_patch import (
    JsonPatchError,
    _describe,
    apply_patch,
    format_pointer,
    parse_pointer,
    resolve_pointer,
)


def _dashboard() -> dict:
    """A small dashboard config shaped like a real Lovelace storage config."""
    return {
        "views": [
            {
                "title": "Living Room",
                "path": "living",
                "cards": [
                    {"type": "tile", "entity": "light.sofa"},
                    {"type": "tile", "entity": "fan.air_purifier"},
                ],
            },
            {
                "title": "Bedroom",
                "path": "bedroom",
                "cards": [{"type": "tile", "entity": "light.bed"}],
            },
        ]
    }


class TestParsePointer:
    """Tests for parse_pointer."""

    def test_empty_pointer_is_the_whole_document(self):
        assert parse_pointer("") == []

    def test_splits_tokens(self):
        assert parse_pointer("/views/0/cards/1") == ["views", "0", "cards", "1"]

    def test_leading_slash_is_optional(self):
        assert parse_pointer("views/0") == ["views", "0"]

    def test_unescapes_tokens(self):
        assert parse_pointer("/a~1b/c~0d") == ["a/b", "c~d"]

    def test_rejects_non_string(self):
        with pytest.raises(JsonPatchError, match="must be a string"):
            parse_pointer(5)


class TestFormatPointer:
    """Tests for format_pointer."""

    def test_empty_tokens(self):
        assert format_pointer([]) == ""

    def test_roundtrips_escaped_tokens(self):
        pointer = "/a~1b/c~0d"
        assert format_pointer(parse_pointer(pointer)) == pointer


class TestDescribe:
    """Tests for _describe, which renders values into error messages."""

    def test_renders_json(self):
        assert _describe({"entity": "light.a"}) == '{"entity":"light.a"}'

    def test_truncates_long_values(self):
        text = _describe(["light.a"] * 50)
        assert len(text) == 121
        assert text.endswith("…")

    def test_falls_back_to_repr_when_json_fails(self):
        circular: dict = {}
        circular["self"] = circular
        assert _describe(circular) == "{'self': {...}}"


class TestResolvePointer:
    """Tests for resolve_pointer."""

    def test_empty_pointer_returns_document(self):
        doc = _dashboard()
        assert resolve_pointer(doc, "") is doc

    def test_resolves_nested_value(self):
        assert resolve_pointer(_dashboard(), "/views/1/cards/0/entity") == "light.bed"

    def test_missing_key_lists_available_keys(self):
        with pytest.raises(JsonPatchError, match="available: cards, path, title"):
            resolve_pointer(_dashboard(), "/views/0/nope")

    def test_index_out_of_range(self):
        with pytest.raises(JsonPatchError, match="out of range"):
            resolve_pointer(_dashboard(), "/views/7")

    def test_non_numeric_array_index(self):
        with pytest.raises(JsonPatchError, match="not a valid array index"):
            resolve_pointer(_dashboard(), "/views/first")

    def test_superscript_is_not_an_array_index(self):
        with pytest.raises(JsonPatchError, match="not a valid array index"):
            resolve_pointer(_dashboard(), "/views/²")

    def test_dash_does_not_resolve(self):
        with pytest.raises(JsonPatchError, match="end of an array"):
            resolve_pointer(_dashboard(), "/views/-")

    def test_descending_into_scalar(self):
        with pytest.raises(JsonPatchError, match="inside a str"):
            resolve_pointer(_dashboard(), "/views/0/title/0")


class TestApplyPatch:
    """Tests for apply_patch."""

    def test_replace_leaves_rest_untouched(self):
        result = apply_patch(
            _dashboard(),
            [{"op": "replace", "path": "/views/0/cards/1/entity", "value": "fan.new"}],
        )

        assert result["views"][0]["cards"][1]["entity"] == "fan.new"
        assert result["views"][0]["cards"][0]["entity"] == "light.sofa"
        assert result["views"][1] == _dashboard()["views"][1]

    def test_does_not_mutate_the_input(self):
        doc = _dashboard()
        apply_patch(doc, [{"op": "remove", "path": "/views/0/cards/0"}])
        assert len(doc["views"][0]["cards"]) == 2

    def test_add_appends_with_dash(self):
        card = {"type": "tile", "entity": "light.lamp"}
        result = apply_patch(
            _dashboard(), [{"op": "add", "path": "/views/1/cards/-", "value": card}]
        )

        assert result["views"][1]["cards"][-1] == card

    def test_add_inserts_at_index(self):
        card = {"type": "tile", "entity": "light.lamp"}
        result = apply_patch(
            _dashboard(), [{"op": "add", "path": "/views/0/cards/0", "value": card}]
        )

        assert result["views"][0]["cards"][0] == card
        assert len(result["views"][0]["cards"]) == 3

    def test_add_sets_object_key(self):
        result = apply_patch(
            _dashboard(), [{"op": "add", "path": "/views/0/icon", "value": "mdi:sofa"}]
        )
        assert result["views"][0]["icon"] == "mdi:sofa"

    def test_add_index_out_of_range(self):
        with pytest.raises(JsonPatchError, match="out of range"):
            apply_patch(_dashboard(), [{"op": "add", "path": "/views/0/cards/9", "value": {}}])

    def test_remove_card(self):
        result = apply_patch(_dashboard(), [{"op": "remove", "path": "/views/0/cards/0"}])

        assert len(result["views"][0]["cards"]) == 1
        assert result["views"][0]["cards"][0]["entity"] == "fan.air_purifier"

    def test_remove_missing_key(self):
        with pytest.raises(JsonPatchError, match="not found"):
            apply_patch(_dashboard(), [{"op": "remove", "path": "/views/0/icon"}])

    def test_remove_whole_document_is_rejected(self):
        with pytest.raises(JsonPatchError, match="Cannot remove the whole document"):
            apply_patch(_dashboard(), [{"op": "remove", "path": ""}])

    def test_replace_requires_existing_location(self):
        with pytest.raises(JsonPatchError, match="not found"):
            apply_patch(
                _dashboard(), [{"op": "replace", "path": "/views/0/icon", "value": "mdi:x"}]
            )

    def test_add_into_a_scalar_is_rejected(self):
        with pytest.raises(JsonPatchError, match="cannot add to a str"):
            apply_patch(_dashboard(), [{"op": "add", "path": "/views/0/title/x", "value": 1}])

    def test_remove_from_a_scalar_is_rejected(self):
        with pytest.raises(JsonPatchError, match="cannot remove from a str"):
            apply_patch(_dashboard(), [{"op": "remove", "path": "/views/0/title/x"}])

    def test_replace_whole_document(self):
        result = apply_patch(_dashboard(), [{"op": "replace", "path": "", "value": {"views": []}}])
        assert result == {"views": []}

    def test_move_card_between_views(self):
        result = apply_patch(
            _dashboard(),
            [{"op": "move", "from": "/views/0/cards/1", "path": "/views/1/cards/-"}],
        )

        assert [c["entity"] for c in result["views"][0]["cards"]] == ["light.sofa"]
        assert [c["entity"] for c in result["views"][1]["cards"]] == [
            "light.bed",
            "fan.air_purifier",
        ]

    def test_move_reorders_within_a_view(self):
        result = apply_patch(
            _dashboard(),
            [{"op": "move", "from": "/views/0/cards/1", "path": "/views/0/cards/0"}],
        )

        assert [c["entity"] for c in result["views"][0]["cards"]] == [
            "fan.air_purifier",
            "light.sofa",
        ]

    def test_move_into_own_child_is_rejected(self):
        with pytest.raises(JsonPatchError, match="into its own child"):
            apply_patch(
                _dashboard(),
                [{"op": "move", "from": "/views/0", "path": "/views/0/cards/-"}],
            )

    def test_move_to_the_root_replaces_the_document(self):
        result = apply_patch(_dashboard(), [{"op": "move", "from": "/views/1", "path": ""}])

        assert result["title"] == "Bedroom"

    def test_copy_duplicates_a_card(self):
        result = apply_patch(
            _dashboard(),
            [{"op": "copy", "from": "/views/0/cards/0", "path": "/views/1/cards/-"}],
        )

        assert result["views"][0]["cards"][0]["entity"] == "light.sofa"
        assert result["views"][1]["cards"][-1]["entity"] == "light.sofa"

    def test_copy_is_deep(self):
        result = apply_patch(
            _dashboard(),
            [
                {"op": "copy", "from": "/views/0/cards/0", "path": "/views/1/cards/-"},
                {"op": "replace", "path": "/views/1/cards/1/entity", "value": "light.other"},
            ],
        )

        assert result["views"][0]["cards"][0]["entity"] == "light.sofa"

    def test_test_passes_and_continues(self):
        result = apply_patch(
            _dashboard(),
            [
                {"op": "test", "path": "/views/0/cards/1/entity", "value": "fan.air_purifier"},
                {"op": "remove", "path": "/views/0/cards/1"},
            ],
        )

        assert len(result["views"][0]["cards"]) == 1

    def test_test_failure_reports_both_values(self):
        with pytest.raises(JsonPatchError) as excinfo:
            apply_patch(
                _dashboard(),
                [{"op": "test", "path": "/views/0/cards/1/entity", "value": "fan.other"}],
            )

        message = str(excinfo.value)
        assert "fan.other" in message
        assert "fan.air_purifier" in message

    def test_failure_part_way_through_discards_earlier_operations(self):
        doc = _dashboard()

        with pytest.raises(JsonPatchError, match="Operation 1"):
            apply_patch(
                doc,
                [
                    {"op": "remove", "path": "/views/0/cards/0"},
                    {"op": "remove", "path": "/views/0/cards/5"},
                ],
            )

        assert len(doc["views"][0]["cards"]) == 2

    def test_error_names_the_failing_operation(self):
        with pytest.raises(JsonPatchError, match=r"Operation 0 \(replace\)"):
            apply_patch(_dashboard(), [{"op": "replace", "path": "/nope", "value": 1}])

    def test_unknown_op(self):
        with pytest.raises(JsonPatchError, match="Unknown op"):
            apply_patch(_dashboard(), [{"op": "upsert", "path": "/views", "value": []}])

    def test_operation_must_be_an_object(self):
        with pytest.raises(JsonPatchError, match="must be an object"):
            apply_patch(_dashboard(), ["remove /views/0"])

    def test_missing_path(self):
        with pytest.raises(JsonPatchError, match="requires a 'path'"):
            apply_patch(_dashboard(), [{"op": "remove"}])

    def test_missing_value(self):
        with pytest.raises(JsonPatchError, match="requires a 'value'"):
            apply_patch(_dashboard(), [{"op": "add", "path": "/views/0/icon"}])

    def test_missing_from(self):
        with pytest.raises(JsonPatchError, match="requires a 'from'"):
            apply_patch(_dashboard(), [{"op": "move", "path": "/views/0/cards/-"}])

    def test_operations_must_be_a_list(self):
        with pytest.raises(JsonPatchError, match="must be a list"):
            apply_patch(_dashboard(), {"op": "remove", "path": "/views/0"})

    def test_empty_operations(self):
        with pytest.raises(JsonPatchError, match="at least one operation"):
            apply_patch(_dashboard(), [])

    def test_added_value_is_copied_from_the_caller(self):
        card = {"type": "tile", "entity": "light.lamp"}
        result = apply_patch(
            _dashboard(), [{"op": "add", "path": "/views/0/cards/-", "value": card}]
        )

        card["entity"] = "light.changed"
        assert result["views"][0]["cards"][-1]["entity"] == "light.lamp"
