"""Tests for helper entity CRUD tools."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import MCPEndpointView


def _make_state(entity_id: str, state: str = "on", attributes: dict | None = None) -> Mock:
    """Create a mock state object."""
    s = Mock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = attributes or {"friendly_name": entity_id.split(".")[-1]}
    return s


def _make_registry_entry(entity_id: str, unique_id: str) -> Mock:
    """Create a mock entity registry entry."""
    entry = Mock()
    entry.entity_id = entity_id
    entry.unique_id = unique_id
    return entry


def _make_collection(items: dict | None = None) -> Mock:
    """Create a mock storage collection."""
    collection = Mock()
    collection.data = items or {}
    collection.async_create_item = AsyncMock()
    collection.async_update_item = AsyncMock()
    collection.async_delete_item = AsyncMock()
    return collection


class TestListHelpers:
    """Tests for the list_helpers tool."""

    @pytest.fixture
    def mock_hass(self):
        hass = Mock()
        hass.data = {}
        return hass

    async def test_list_all_helpers(self, mock_hass):
        """list_helpers with no filter returns helpers from all domains."""
        from custom_components.mcp_server_http_transport.tools.helpers import list_helpers

        mock_hass.states.async_all.return_value = [
            _make_state("input_boolean.my_flag"),
            _make_state("counter.my_counter", "5"),
            _make_state("light.living_room"),  # non-helper, should be excluded
        ]

        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry(
            "input_boolean.my_flag", "abc123"
        )

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await list_helpers(mock_hass, {})

        body = json.loads(result["content"][0]["text"])
        entity_ids = [h["entity_id"] for h in body]
        assert "input_boolean.my_flag" in entity_ids
        assert "counter.my_counter" in entity_ids
        assert "light.living_room" not in entity_ids

    async def test_list_helpers_filtered_by_domain(self, mock_hass):
        """list_helpers with domain filter returns only that domain."""
        from custom_components.mcp_server_http_transport.tools.helpers import list_helpers

        mock_hass.states.async_all.return_value = [
            _make_state("input_boolean.flag_a"),
            _make_state("input_boolean.flag_b"),
            _make_state("counter.my_counter", "3"),
        ]

        mock_registry = Mock()
        mock_registry.async_get.return_value = None

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await list_helpers(mock_hass, {"domain": "input_boolean"})

        body = json.loads(result["content"][0]["text"])
        assert len(body) == 2
        assert all(h["domain"] == "input_boolean" for h in body)

    async def test_list_helpers_unknown_domain(self, mock_hass):
        """list_helpers with an unsupported domain returns an error message."""
        from custom_components.mcp_server_http_transport.tools.helpers import list_helpers

        result = await list_helpers(mock_hass, {"domain": "light"})

        text = result["content"][0]["text"]
        assert "Unknown helper domain" in text
        assert "light" in text

    async def test_list_helpers_includes_unique_id(self, mock_hass):
        """list_helpers includes the unique_id from the entity registry."""
        from custom_components.mcp_server_http_transport.tools.helpers import list_helpers

        mock_hass.states.async_all.return_value = [
            _make_state("timer.my_timer"),
        ]
        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry("timer.my_timer", "uid-999")

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await list_helpers(mock_hass, {"domain": "timer"})

        body = json.loads(result["content"][0]["text"])
        assert body[0]["unique_id"] == "uid-999"


class TestGetHelperConfig:
    """Tests for the get_helper_config tool."""

    @pytest.fixture
    def mock_hass(self):
        hass = Mock()
        hass.data = {}
        return hass

    async def test_get_helper_config_success(self, mock_hass):
        """get_helper_config returns the stored config for a known helper."""
        from custom_components.mcp_server_http_transport.tools.helpers import get_helper_config

        stored_item = {"id": "uid-123", "name": "My Flag", "icon": "mdi:flag"}
        collection = _make_collection({"uid-123": stored_item})
        mock_hass.data["input_boolean"] = collection

        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry(
            "input_boolean.my_flag", "uid-123"
        )

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await get_helper_config(mock_hass, {"entity_id": "input_boolean.my_flag"})

        body = json.loads(result["content"][0]["text"])
        assert body["id"] == "uid-123"
        assert body["name"] == "My Flag"

    async def test_get_helper_config_non_helper_domain(self, mock_hass):
        """get_helper_config returns an error for non-helper entities."""
        from custom_components.mcp_server_http_transport.tools.helpers import get_helper_config

        result = await get_helper_config(mock_hass, {"entity_id": "light.living_room"})

        assert "Error getting helper config" in result["content"][0]["text"]
        assert "not a helper entity" in result["content"][0]["text"]

    async def test_get_helper_config_entity_not_in_registry(self, mock_hass):
        """get_helper_config returns an error when entity is missing from registry."""
        from custom_components.mcp_server_http_transport.tools.helpers import get_helper_config

        mock_registry = Mock()
        mock_registry.async_get.return_value = None

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await get_helper_config(mock_hass, {"entity_id": "input_boolean.missing"})

        assert "Error getting helper config" in result["content"][0]["text"]
        assert "not found in entity registry" in result["content"][0]["text"]

    async def test_get_helper_config_not_in_storage(self, mock_hass):
        """get_helper_config returns an error when item is not in storage (YAML-configured)."""
        from custom_components.mcp_server_http_transport.tools.helpers import get_helper_config

        collection = _make_collection({})  # empty storage
        mock_hass.data["input_boolean"] = collection

        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry(
            "input_boolean.yaml_helper", "yaml-uid"
        )

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await get_helper_config(mock_hass, {"entity_id": "input_boolean.yaml_helper"})

        assert "Error getting helper config" in result["content"][0]["text"]
        assert "not found in storage" in result["content"][0]["text"]

    async def test_get_helper_config_domain_not_loaded(self, mock_hass):
        """get_helper_config returns an error when the domain is not loaded in hass."""
        from custom_components.mcp_server_http_transport.tools.helpers import get_helper_config

        # domain not in hass.data
        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry(
            "schedule.my_schedule", "sched-1"
        )

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await get_helper_config(mock_hass, {"entity_id": "schedule.my_schedule"})

        assert "Error getting helper config" in result["content"][0]["text"]
        assert "not available" in result["content"][0]["text"]


class TestCreateHelper:
    """Tests for the create_helper tool."""

    @pytest.fixture
    def mock_hass(self):
        hass = Mock()
        hass.data = {}
        return hass

    async def test_create_helper_success(self, mock_hass):
        """create_helper successfully creates a new helper."""
        from custom_components.mcp_server_http_transport.tools.helpers import create_helper

        collection = _make_collection()
        collection.async_create_item.return_value = {"id": "new-id-456", "name": "My Counter"}
        mock_hass.data["counter"] = collection

        result = await create_helper(
            mock_hass, {"domain": "counter", "config": {"name": "My Counter"}}
        )

        collection.async_create_item.assert_called_once_with({"name": "My Counter"})
        text = result["content"][0]["text"]
        assert "Successfully created counter helper" in text
        assert "new-id-456" in text

    async def test_create_helper_unknown_domain(self, mock_hass):
        """create_helper rejects unsupported domains."""
        from custom_components.mcp_server_http_transport.tools.helpers import create_helper

        result = await create_helper(mock_hass, {"domain": "light", "config": {"name": "My Light"}})

        text = result["content"][0]["text"]
        assert "Unknown helper domain" in text
        assert "light" in text

    async def test_create_helper_domain_not_loaded(self, mock_hass):
        """create_helper returns an error when the domain is not loaded."""
        from custom_components.mcp_server_http_transport.tools.helpers import create_helper

        result = await create_helper(mock_hass, {"domain": "timer", "config": {"name": "My Timer"}})

        assert "Error creating helper" in result["content"][0]["text"]
        assert "not available" in result["content"][0]["text"]

    async def test_create_helper_collection_error(self, mock_hass):
        """create_helper wraps collection errors in a user-friendly message."""
        from custom_components.mcp_server_http_transport.tools.helpers import create_helper

        collection = _make_collection()
        collection.async_create_item.side_effect = ValueError("Invalid config")
        mock_hass.data["input_select"] = collection

        result = await create_helper(
            mock_hass,
            {"domain": "input_select", "config": {"name": "Bad Select"}},
        )

        assert "Error creating helper" in result["content"][0]["text"]
        assert "Invalid config" in result["content"][0]["text"]

    @pytest.mark.parametrize(
        "domain",
        [
            "counter",
            "input_boolean",
            "input_button",
            "input_datetime",
            "input_number",
            "input_select",
            "input_text",
            "schedule",
            "timer",
        ],
    )
    async def test_create_helper_all_supported_domains(self, mock_hass, domain):
        """create_helper accepts all supported helper domains."""
        from custom_components.mcp_server_http_transport.tools.helpers import create_helper

        collection = _make_collection()
        collection.async_create_item.return_value = {"id": "new-id", "name": "Test"}
        mock_hass.data[domain] = collection

        result = await create_helper(mock_hass, {"domain": domain, "config": {"name": "Test"}})

        assert "Successfully created" in result["content"][0]["text"]
        assert domain in result["content"][0]["text"]


class TestUpdateHelper:
    """Tests for the update_helper tool."""

    @pytest.fixture
    def mock_hass(self):
        hass = Mock()
        hass.data = {}
        return hass

    async def test_update_helper_success(self, mock_hass):
        """update_helper calls async_update_item with correct args."""
        from custom_components.mcp_server_http_transport.tools.helpers import update_helper

        collection = _make_collection()
        mock_hass.data["input_text"] = collection

        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry("input_text.my_text", "txt-uid")

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await update_helper(
                mock_hass,
                {"entity_id": "input_text.my_text", "config": {"name": "Renamed"}},
            )

        collection.async_update_item.assert_called_once_with("txt-uid", {"name": "Renamed"})
        assert "Successfully updated helper" in result["content"][0]["text"]

    async def test_update_helper_non_helper_entity(self, mock_hass):
        """update_helper rejects non-helper entity IDs."""
        from custom_components.mcp_server_http_transport.tools.helpers import update_helper

        result = await update_helper(
            mock_hass, {"entity_id": "switch.my_switch", "config": {"name": "x"}}
        )

        assert "Error updating helper" in result["content"][0]["text"]
        assert "not a helper entity" in result["content"][0]["text"]

    async def test_update_helper_entity_not_in_registry(self, mock_hass):
        """update_helper returns an error when entity is not in the registry."""
        from custom_components.mcp_server_http_transport.tools.helpers import update_helper

        mock_registry = Mock()
        mock_registry.async_get.return_value = None

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await update_helper(
                mock_hass,
                {"entity_id": "input_boolean.gone", "config": {"name": "x"}},
            )

        assert "Error updating helper" in result["content"][0]["text"]
        assert "not found in entity registry" in result["content"][0]["text"]

    async def test_update_helper_collection_error(self, mock_hass):
        """update_helper wraps collection errors."""
        from custom_components.mcp_server_http_transport.tools.helpers import update_helper

        collection = _make_collection()
        collection.async_update_item.side_effect = ValueError("Item not found")
        mock_hass.data["counter"] = collection

        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry("counter.my_counter", "cnt-uid")

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await update_helper(
                mock_hass,
                {"entity_id": "counter.my_counter", "config": {"name": "x"}},
            )

        assert "Error updating helper" in result["content"][0]["text"]
        assert "Item not found" in result["content"][0]["text"]


class TestDeleteHelper:
    """Tests for the delete_helper tool."""

    @pytest.fixture
    def mock_hass(self):
        hass = Mock()
        hass.data = {}
        return hass

    async def test_delete_helper_success(self, mock_hass):
        """delete_helper calls async_delete_item with the correct item_id."""
        from custom_components.mcp_server_http_transport.tools.helpers import delete_helper

        collection = _make_collection()
        mock_hass.data["input_number"] = collection

        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry(
            "input_number.my_num", "num-uid"
        )

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await delete_helper(mock_hass, {"entity_id": "input_number.my_num"})

        collection.async_delete_item.assert_called_once_with("num-uid")
        assert "Successfully deleted helper" in result["content"][0]["text"]

    async def test_delete_helper_non_helper_entity(self, mock_hass):
        """delete_helper rejects non-helper entity IDs."""
        from custom_components.mcp_server_http_transport.tools.helpers import delete_helper

        result = await delete_helper(mock_hass, {"entity_id": "sensor.temperature"})

        assert "Error deleting helper" in result["content"][0]["text"]
        assert "not a helper entity" in result["content"][0]["text"]

    async def test_delete_helper_entity_not_in_registry(self, mock_hass):
        """delete_helper returns an error when entity is not in the registry."""
        from custom_components.mcp_server_http_transport.tools.helpers import delete_helper

        mock_registry = Mock()
        mock_registry.async_get.return_value = None

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await delete_helper(mock_hass, {"entity_id": "timer.gone"})

        assert "Error deleting helper" in result["content"][0]["text"]
        assert "not found in entity registry" in result["content"][0]["text"]

    async def test_delete_helper_collection_error(self, mock_hass):
        """delete_helper wraps collection errors."""
        from custom_components.mcp_server_http_transport.tools.helpers import delete_helper

        collection = _make_collection()
        collection.async_delete_item.side_effect = ValueError("Not found")
        mock_hass.data["input_datetime"] = collection

        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry(
            "input_datetime.my_dt", "dt-uid"
        )

        with patch(
            "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
            return_value=mock_registry,
        ):
            result = await delete_helper(mock_hass, {"entity_id": "input_datetime.my_dt"})

        assert "Error deleting helper" in result["content"][0]["text"]
        assert "Not found" in result["content"][0]["text"]


class TestHelperToolsViaHTTP:
    """Integration-style tests for helper tools via the MCP HTTP endpoint."""

    @pytest.fixture
    def mock_server(self):
        return Mock()

    @pytest.fixture
    def mock_hass(self):
        hass = Mock()
        # DOMAIN key must be truthy so _integration_loaded() passes
        hass.data = {"mcp_server_http_transport": Mock()}
        hass.states = Mock()
        hass.services = Mock()
        return hass

    @pytest.fixture
    def view(self, mock_hass, mock_server):
        return MCPEndpointView(mock_hass, mock_server)

    def _make_request(self, method: str, params: dict, request_id: int = 1) -> Mock:
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": request_id,
            }
        )
        return request

    async def test_create_helper_via_http(self, view, mock_hass):
        """create_helper is reachable and returns success via the HTTP endpoint."""
        collection = _make_collection()
        collection.async_create_item.return_value = {"id": "http-id", "name": "HTTP Helper"}
        mock_hass.data["input_boolean"] = collection

        request = self._make_request(
            "tools/call",
            {
                "name": "create_helper",
                "arguments": {"domain": "input_boolean", "config": {"name": "HTTP Helper"}},
            },
        )

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully created input_boolean helper" in body["result"]["content"][0]["text"]
        assert "http-id" in body["result"]["content"][0]["text"]

    async def test_delete_helper_via_http(self, view, mock_hass):
        """delete_helper is reachable via the HTTP endpoint."""
        collection = _make_collection()
        mock_hass.data["counter"] = collection

        mock_registry = Mock()
        mock_registry.async_get.return_value = _make_registry_entry("counter.my_counter", "cnt-uid")

        request = self._make_request(
            "tools/call",
            {"name": "delete_helper", "arguments": {"entity_id": "counter.my_counter"}},
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert "Successfully deleted helper" in body["result"]["content"][0]["text"]

    async def test_list_helpers_via_http(self, view, mock_hass):
        """list_helpers is reachable via the HTTP endpoint."""
        mock_hass.states.async_all.return_value = [
            _make_state("input_boolean.flag"),
        ]

        mock_registry = Mock()
        mock_registry.async_get.return_value = None

        request = self._make_request(
            "tools/call",
            {"name": "list_helpers", "arguments": {}},
        )

        with (
            patch.object(view, "_validate_token", return_value={"sub": "user123"}),
            patch(
                "custom_components.mcp_server_http_transport.tools.helpers.er.async_get",
                return_value=mock_registry,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        result_data = json.loads(body["result"]["content"][0]["text"])
        assert any(h["entity_id"] == "input_boolean.flag" for h in result_data)

    async def test_helper_tools_appear_in_tools_list(self, view, mock_hass):
        """Helper tools are included in the tools/list response."""
        request = self._make_request("tools/list", {})

        with patch.object(view, "_validate_token", return_value={"sub": "user123"}):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        tool_names = [t["name"] for t in body["result"]["tools"]]
        assert "list_helpers" in tool_names
        assert "get_helper_config" in tool_names
        assert "create_helper" in tool_names
        assert "update_helper" in tool_names
        assert "delete_helper" in tool_names
