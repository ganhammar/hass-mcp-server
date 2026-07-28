"""JSON Pointer (RFC 6901) and JSON Patch (RFC 6902) support.

Used by the dashboard tools so a single card can be edited without sending the
whole Lovelace config back and forth.
"""

import copy
import json
from typing import Any

# Operations that address a second location in the document.
_OPS_WITH_FROM = {"move", "copy"}
# Operations that carry a value.
_OPS_WITH_VALUE = {"add", "replace", "test"}
_VALID_OPS = _OPS_WITH_FROM | _OPS_WITH_VALUE | {"remove"}


class JsonPatchError(ValueError):
    """Raised when a pointer cannot be resolved or a patch operation fails."""


def parse_pointer(pointer: str) -> list[str]:
    """Split a JSON Pointer into its unescaped reference tokens.

    A leading slash is optional: 'views/0' is accepted as '/views/0' so a
    caller that drops it still gets the location it meant.
    """
    if not isinstance(pointer, str):
        raise JsonPatchError(f"Pointer must be a string, got {type(pointer).__name__}")
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        pointer = "/" + pointer
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def format_pointer(tokens: list[str]) -> str:
    """Join reference tokens back into a JSON Pointer string."""
    return "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in tokens)


def _describe(value: Any, limit: int = 120) -> str:
    """Render a value for an error message, truncated so errors stay readable."""
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _list_index(token: str, length: int, tokens: list[str], allow_end: bool = False) -> int:
    """Convert a reference token to a list index, or raise JsonPatchError."""
    where = format_pointer(tokens)
    if token == "-":
        if not allow_end:
            raise JsonPatchError(f"'{where}': '-' only addresses the end of an array when adding")
        return length
    # isdecimal rather than isdigit: the latter also accepts superscripts such as
    # '²', which int() then rejects with a bare ValueError.
    if not token.isdecimal():
        raise JsonPatchError(f"'{where}': '{token}' is not a valid array index")
    index = int(token)
    limit = length if allow_end else length - 1
    if index > limit:
        raise JsonPatchError(
            f"'{where}': index {index} is out of range (array has {length} item(s))"
        )
    return index


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """Return the value a JSON Pointer addresses.

    Raises JsonPatchError if any step of the pointer does not exist.
    """
    node = doc
    walked: list[str] = []
    for token in parse_pointer(pointer):
        walked.append(token)
        if isinstance(node, list):
            node = node[_list_index(token, len(node), walked)]
        elif isinstance(node, dict):
            if token not in node:
                available = ", ".join(sorted(node)[:10]) or "none"
                raise JsonPatchError(
                    f"'{format_pointer(walked)}': key '{token}' not found (available: {available})"
                )
            node = node[token]
        else:
            raise JsonPatchError(
                f"'{format_pointer(walked)}': cannot look up '{token}' "
                f"inside a {type(node).__name__}"
            )
    return node


def _parent_of(doc: Any, tokens: list[str]) -> Any:
    """Return the container holding the location `tokens` addresses."""
    return resolve_pointer(doc, format_pointer(tokens[:-1]))


def _add(doc: Any, tokens: list[str], value: Any) -> Any:
    """Insert or set `value` at the location, returning the (possibly new) document."""
    if not tokens:
        return value
    parent = _parent_of(doc, tokens)
    if isinstance(parent, list):
        parent.insert(_list_index(tokens[-1], len(parent), tokens, allow_end=True), value)
    elif isinstance(parent, dict):
        parent[tokens[-1]] = value
    else:
        raise JsonPatchError(f"'{format_pointer(tokens)}': cannot add to a {type(parent).__name__}")
    return doc


def _remove(doc: Any, tokens: list[str]) -> Any:
    """Delete the value at the location, returning the (possibly new) document."""
    if not tokens:
        raise JsonPatchError("Cannot remove the whole document; use replace with path ''")
    parent = _parent_of(doc, tokens)
    if isinstance(parent, list):
        parent.pop(_list_index(tokens[-1], len(parent), tokens))
    elif isinstance(parent, dict):
        if tokens[-1] not in parent:
            raise JsonPatchError(f"'{format_pointer(tokens)}': key '{tokens[-1]}' not found")
        del parent[tokens[-1]]
    else:
        raise JsonPatchError(
            f"'{format_pointer(tokens)}': cannot remove from a {type(parent).__name__}"
        )
    return doc


def _apply_operation(doc: Any, operation: Any) -> Any:
    """Apply one patch operation and return the resulting document."""
    if not isinstance(operation, dict):
        raise JsonPatchError(f"Operation must be an object, got {type(operation).__name__}")

    op = operation.get("op")
    if op not in _VALID_OPS:
        raise JsonPatchError(
            f"Unknown op '{op}' (expected one of: {', '.join(sorted(_VALID_OPS))})"
        )
    if "path" not in operation:
        raise JsonPatchError(f"'{op}' requires a 'path'")
    if op in _OPS_WITH_VALUE and "value" not in operation:
        raise JsonPatchError(f"'{op}' requires a 'value'")
    if op in _OPS_WITH_FROM and "from" not in operation:
        raise JsonPatchError(f"'{op}' requires a 'from'")

    tokens = parse_pointer(operation["path"])

    if op == "add":
        return _add(doc, tokens, copy.deepcopy(operation["value"]))

    if op == "remove":
        return _remove(doc, tokens)

    if op == "replace":
        # Resolve first so replacing a location that does not exist is an error
        # rather than a silent insert.
        resolve_pointer(doc, operation["path"])
        if not tokens:
            return copy.deepcopy(operation["value"])
        return _add(_remove(doc, tokens), tokens, copy.deepcopy(operation["value"]))

    if op == "test":
        actual = resolve_pointer(doc, operation["path"])
        if actual != operation["value"]:
            raise JsonPatchError(
                f"test failed at '{operation['path']}': expected "
                f"{_describe(operation['value'])}, found {_describe(actual)}"
            )
        return doc

    from_tokens = parse_pointer(operation["from"])
    value = resolve_pointer(doc, operation["from"])

    if op == "copy":
        return _add(doc, tokens, copy.deepcopy(value))

    # move: refuse to relocate a container into itself, which would otherwise
    # detach the moved subtree from the document.
    if tokens[: len(from_tokens)] == from_tokens and len(tokens) > len(from_tokens):
        raise JsonPatchError(
            f"Cannot move '{operation['from']}' into its own child '{operation['path']}'"
        )
    return _add(_remove(doc, from_tokens), tokens, value)


def apply_patch(doc: Any, operations: Any) -> Any:
    """Apply a list of JSON Patch operations to a copy of `doc`.

    Operations are applied in order to a deep copy, so `doc` is left untouched
    and a failure part way through leaves nothing half-written.
    """
    if not isinstance(operations, list):
        raise JsonPatchError(f"Operations must be a list, got {type(operations).__name__}")
    if not operations:
        raise JsonPatchError("Provide at least one operation")

    result = copy.deepcopy(doc)
    for index, operation in enumerate(operations):
        try:
            result = _apply_operation(result, operation)
        except JsonPatchError as err:
            op_name = operation.get("op") if isinstance(operation, dict) else operation
            raise JsonPatchError(f"Operation {index} ({op_name}): {err}") from err
    return result
