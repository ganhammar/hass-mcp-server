"""Tests for system admin tool endpoints."""

import json
import tempfile
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mcp_server_http_transport.http import Caller, MCPEndpointView
from custom_components.mcp_server_http_transport.tools.system_admin import (
    _format_system_log_entry,
    redact_log_text,
)

_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJhYmNkZWYxMjM0NTYifQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


class TestRedactLogText:
    """redact_log_text masks credentials and leaves everything else alone."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Bearer abc123def456ghi", "Bearer <redacted>"),
            ("bearer abc123def456ghi", "bearer <redacted>"),
            ("BEARER Abc.Def-123_xyz==", "BEARER <redacted>"),
            ("sent header bearer abcdefghijklmnopqrstuv end", "sent header bearer <redacted> end"),
        ],
    )
    def test_bearer_token(self, text, expected):
        assert redact_log_text(text) == expected

    def test_bearer_prose_is_kept(self):
        text = "the bearer of bad news"
        assert redact_log_text(text) == text

    @pytest.mark.parametrize(
        "name",
        [
            "token",
            "access_token",
            "api_key",
            "apikey",
            "key",
            "password",
            "secret",
            "auth",
            "signature",
            "sig",
            "TOKEN",
            "Api_Key",
            "client_secret",
        ],
    )
    def test_query_parameter_value_is_redacted(self, name):
        out = redact_log_text(f"GET https://example.com/cb?x=1&{name}=s3cr3tvalue&y=2 failed")
        assert out == f"GET https://example.com/cb?x=1&{name}=<redacted>&y=2 failed"

    def test_bare_name_value_pair(self):
        assert redact_log_text("login with password=hunter2 failed") == (
            "login with password=<redacted> failed"
        )

    def test_query_parameter_in_parentheses(self):
        assert redact_log_text("(https://a/b?api_key=XYZ)") == "(https://a/b?api_key=<redacted>)"

    def test_parameter_names_only_match_whole_words(self):
        text = "monkey=1 turkey=2 passcode_hint=3 keyboard=4"
        assert redact_log_text(text) == text

    def test_quoted_mapping_fields(self):
        text = "data {'password': 'hunter2', \"access_token\": \"abc\", 'name': 'x'}"
        assert redact_log_text(text) == (
            "data {'password': '<redacted>', \"access_token\": \"<redacted>\", 'name': 'x'}"
        )

    def test_basic_auth_in_url(self):
        assert redact_log_text("Fetching https://admin:p%40ss@nas.local:8443/api") == (
            "Fetching https://<redacted>@nas.local:8443/api"
        )

    def test_basic_auth_in_url_with_at_in_password(self):
        assert redact_log_text("mqtt://user:p@ss@broker/topic") == "mqtt://<redacted>@broker/topic"

    def test_url_without_credentials_is_kept(self):
        text = "Fetching http://localhost:8123/api/states and ssh://git@host/repo.git"
        assert redact_log_text(text) == text

    def test_jwt_is_redacted(self):
        assert redact_log_text(f"token {_JWT} rejected") == "token <redacted> rejected"

    def test_jwt_without_authorization_context(self):
        assert redact_log_text(f"payload={_JWT};") == "payload=<redacted>;"

    def test_non_jwt_dotted_text_is_kept(self):
        text = "homeassistant.components.mqtt.client and eyJ.only.two. not a token"
        assert redact_log_text(text) == text

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Authorization: Bearer abc123def456ghi", "Authorization: <redacted>"),
            ("authorization: Basic dXNlcjpwYXNz", "authorization: <redacted>"),
            ("AUTHORIZATION=Token abcdef", "AUTHORIZATION=<redacted>"),
            (
                "headers {'Authorization': 'Bearer abc', 'Accept': '*/*'}",
                "headers {'Authorization': '<redacted>', 'Accept': '*/*'}",
            ),
            (
                "Proxy-Authorization: Basic Zm9v\nnext line",
                "Proxy-Authorization: <redacted>\nnext line",
            ),
        ],
    )
    def test_authorization_header(self, text, expected):
        assert redact_log_text(text) == expected

    def test_text_with_nothing_to_redact_is_unchanged(self):
        text = (
            "2024-01-01 12:00:00.123 ERROR (MainThread) [homeassistant.components.hue] "
            "Unable to connect to 192.168.1.20:443: timed out\n"
            "The key to success is a password reset policy; return code is fine.\n"
            "Traceback (most recent call last):\n"
            '  File "/usr/src/homeassistant/core.py", line 42, in run\n'
            "ValueError: invalid literal for int() with base 10: 'x'\n"
        )
        assert redact_log_text(text) == text
        assert redact_log_text("") == ""

    def test_status_codes_are_not_redacted(self):
        text = "Request failed with code=404 and status_code=500"
        assert redact_log_text(text) == text

    def test_redaction_is_idempotent(self):
        text = f"Authorization: Bearer {_JWT} https://u:p@h/x?token=abc&sig=1"
        once = redact_log_text(text)
        assert redact_log_text(once) == once
        assert "abc" not in once and _JWT not in once and "u:p" not in once


# The view resolves every request to an authorized caller before dispatch; these
# tests start from one so they exercise the tool rather than the auth path.
CALLER = Caller(user_id="user123", auth_method="native", user=Mock(is_admin=True))


class TestToolsSystemAdmin:
    """Test the system admin tool endpoints."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock()
        hass.states = Mock()
        hass.services = Mock()
        return hass

    @pytest.fixture
    def view(self, mock_hass):
        """Create an MCPEndpointView instance."""
        return MCPEndpointView(mock_hass)

    async def test_post_tools_call_get_error_log(self, view, mock_hass):
        """Test POST with tools/call for get_error_log."""
        mock_hass.config.path.return_value = "/config/home-assistant.log"
        mock_hass.async_add_executor_job = AsyncMock(
            return_value="2024-01-01 ERROR Something went wrong\n2024-01-01 WARNING Low battery"
        )

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 200,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "ERROR" in text
        assert "WARNING" in text

    async def test_get_error_log_file_path_is_redacted(self, view, mock_hass, tmp_path):
        """The on-disk tail runs through redact_log_text."""
        log = tmp_path / "home-assistant.log"
        log.write_text(
            "ERROR request to https://api.example.com/v1?api_key=SUPERSECRET failed\n"
            f"WARNING rejected Bearer {_JWT}\n"
            "INFO nothing sensitive here\n"
        )
        mock_hass.config.path.return_value = str(log)

        async def run_fn(fn, *args):
            return fn(*args)

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 290,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        text = json.loads(response.body)["result"]["content"][0]["text"]
        assert "api_key=<redacted>" in text
        assert "SUPERSECRET" not in text
        assert _JWT not in text
        assert "INFO nothing sensitive here" in text

    async def test_get_error_log_buffer_fallback_is_redacted(self, view, mock_hass):
        """The in-memory system_log fallback runs through redact_log_text."""
        mock_hass.config.path.return_value = "/nonexistent/home-assistant.log"
        handler = Mock()
        handler.records.to_list.return_value = [
            {
                "name": "homeassistant.components.foo",
                "message": ["Call to https://u:pw@host/x?token=SUPERSECRET failed"],
                "level": "ERROR",
                "source": ["components/foo/__init__.py", 42],
                "timestamp": 1_700_000_000.0,
                "exception": f"RuntimeError: Authorization: Bearer {_JWT}",
                "count": 1,
            }
        ]
        mock_hass.data = {"mcp_server_http_transport": True, "system_log": handler}
        mock_hass.async_add_executor_job = AsyncMock(side_effect=FileNotFoundError())

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 291,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        text = json.loads(response.body)["result"]["content"][0]["text"]
        assert "in-memory system log buffer" in text
        assert "token=<redacted>" in text
        assert "https://<redacted>@host" in text
        assert "SUPERSECRET" not in text and "pw@" not in text
        assert _JWT not in text
        assert "Authorization: <redacted>" in text

    def test_get_error_log_description_mentions_redaction(self):
        from custom_components.mcp_server_http_transport.tools import TOOLS

        description = TOOLS["get_error_log"]["schema"]["description"]
        assert "redacted" in description
        assert "raw log" in description

    async def test_post_tools_call_get_error_log_with_lines(self, view, mock_hass):
        """Test POST with tools/call for get_error_log with lines parameter."""
        mock_hass.config.path.return_value = "/config/home-assistant.log"
        mock_hass.async_add_executor_job = AsyncMock(return_value="Last line only\n")

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {"lines": 1}},
                "id": 201,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Last line only" in text

    async def test_post_tools_call_get_error_log_file_not_found(self, view, mock_hass):
        """Test POST with tools/call for get_error_log when file is missing."""
        mock_hass.config.path.return_value = "/config/home-assistant.log"
        # Integration loaded, but no system_log buffer available.
        mock_hass.data = {"mcp_server_http_transport": True}
        mock_hass.async_add_executor_job = AsyncMock(side_effect=FileNotFoundError())

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 202,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Log file not found" in text

    async def test_post_tools_call_restart_ha_confirmed(self, view, mock_hass):
        """Test POST with tools/call for restart_ha with confirm=true."""
        mock_hass.services.async_call = AsyncMock()

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "restart_ha",
                    "arguments": {"confirm": True},
                },
                "id": 203,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "restart has been initiated" in text

    async def test_post_tools_call_restart_ha_not_confirmed(self, view, mock_hass):
        """Test POST with tools/call for restart_ha with confirm=false."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "restart_ha",
                    "arguments": {"confirm": False},
                },
                "id": 204,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "confirm=true" in text

    async def test_post_tools_call_restart_ha_missing_confirm(self, view, mock_hass):
        """restart_ha declares confirm required, so omitting it is invalid params."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "restart_ha",
                    "arguments": {},
                },
                "id": 205,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        assert body["error"]["code"] == -32602
        assert "confirm" in body["error"]["message"]

    async def test_post_tools_call_get_system_status(self, view, mock_hass):
        """Test POST with tools/call for get_system_status."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living_room"
        mock_state1.state = "on"
        mock_state2 = Mock()
        mock_state2.entity_id = "sensor.temp"
        mock_state2.state = "unavailable"
        mock_state3 = Mock()
        mock_state3.entity_id = "switch.kitchen"
        mock_state3.state = "off"
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2, mock_state3]

        mock_entry = Mock()
        mock_hass.config_entries.async_entries.return_value = [mock_entry, mock_entry]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_system_status", "arguments": {}},
                "id": 206,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        from homeassistant.const import __version__ as HA_VERSION

        assert data["version"] == HA_VERSION
        assert data["total_entities"] == 3
        assert data["domain_counts"]["light"] == 1
        assert data["domain_counts"]["sensor"] == 1
        assert data["domain_counts"]["switch"] == 1
        assert len(data["problem_entities"]) == 1
        assert data["problem_entities"][0]["entity_id"] == "sensor.temp"
        assert data["integration_count"] == 2

    async def test_post_tools_call_get_domain_stats(self, view, mock_hass):
        """Test POST with tools/call for get_domain_stats."""
        mock_state1 = Mock()
        mock_state1.entity_id = "light.living"
        mock_state1.state = "on"
        mock_state1.attributes = {"friendly_name": "Living Light"}
        mock_state2 = Mock()
        mock_state2.entity_id = "light.bedroom"
        mock_state2.state = "off"
        mock_state2.attributes = {"friendly_name": "Bedroom Light"}
        mock_state3 = Mock()
        mock_state3.entity_id = "sensor.temp"
        mock_state3.state = "22"
        mock_state3.attributes = {"friendly_name": "Temperature"}
        mock_hass.states.async_all.return_value = [mock_state1, mock_state2, mock_state3]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_domain_stats",
                    "arguments": {"domain": "light"},
                },
                "id": 207,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["domain"] == "light"
        assert data["total"] == 2
        assert data["state_counts"]["on"] == 1
        assert data["state_counts"]["off"] == 1
        assert len(data["examples"]) == 2

    async def test_post_tools_call_get_domain_stats_empty(self, view, mock_hass):
        """Test POST with tools/call for get_domain_stats with no matching entities."""
        mock_state = Mock()
        mock_state.entity_id = "sensor.temp"
        mock_state.state = "22"
        mock_state.attributes = {"friendly_name": "Temperature"}
        mock_hass.states.async_all.return_value = [mock_state]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_domain_stats",
                    "arguments": {"domain": "light"},
                },
                "id": 208,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["total"] == 0

    async def test_post_tools_call_check_config_valid(self, view, mock_hass):
        """Test POST with tools/call for check_config with valid config."""
        mock_result = Mock()
        mock_result.errors = []

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "check_config", "arguments": {}},
                "id": 209,
            }
        )

        with (
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "homeassistant.helpers.check_config.async_check_ha_config_file",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["valid"] is True
        assert data["errors"] == []

    async def test_post_tools_call_check_config_errors(self, view, mock_hass):
        """Test POST with tools/call for check_config with errors."""
        mock_result = Mock()
        mock_result.errors = ["Invalid automation config", "Missing entity"]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "check_config", "arguments": {}},
                "id": 210,
            }
        )

        with (
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "homeassistant.helpers.check_config.async_check_ha_config_file",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert data["valid"] is False
        assert len(data["errors"]) == 2

    async def test_post_tools_call_list_integrations(self, view, mock_hass):
        """Test POST with tools/call for list_integrations."""
        mock_entry1 = Mock()
        mock_entry1.domain = "hue"
        mock_entry1.title = "Philips Hue"
        mock_entry1.state = "loaded"
        mock_entry1.entry_id = "entry1"

        mock_entry2 = Mock()
        mock_entry2.domain = "zwave"
        mock_entry2.title = "Z-Wave"
        mock_entry2.state = "loaded"
        mock_entry2.entry_id = "entry2"

        mock_hass.config_entries.async_entries.return_value = [mock_entry1, mock_entry2]

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_integrations", "arguments": {}},
                "id": 211,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        data = json.loads(body["result"]["content"][0]["text"])
        assert len(data) == 2
        assert data[0]["domain"] == "hue"
        assert data[0]["title"] == "Philips Hue"
        assert data[1]["domain"] == "zwave"

    async def test_post_tools_call_get_error_log_read_error(self, view, mock_hass):
        """Test get_error_log when async_add_executor_job raises."""
        mock_hass.config.path.return_value = "/config/home-assistant.log"
        mock_hass.async_add_executor_job = AsyncMock(side_effect=Exception("IO error"))

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 250,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Error reading error log" in text

    async def test_post_tools_call_get_error_log_actual_file_read(self, view, mock_hass):
        """Test get_error_log reading actual file content via executor."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("line1\nline2\nline3\n")
            log_path = f.name

        mock_hass.config.path.return_value = log_path

        async def run_fn(fn, *args):
            return fn(*args) if args else fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {"lines": 2}},
                "id": 251,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "line2" in text
        assert "line3" in text

    async def test_post_tools_call_get_error_log_file_missing(self, view, mock_hass):
        """Test get_error_log when log file does not exist (FileNotFoundError)."""
        mock_hass.config.path.return_value = "/nonexistent/path/home-assistant.log"
        # Integration loaded, but no system_log buffer available.
        mock_hass.data = {"mcp_server_http_transport": True}

        async def run_fn(fn, *args):
            return fn(*args) if args else fn()

        mock_hass.async_add_executor_job = AsyncMock(side_effect=run_fn)

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 255,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Log file not found" in text
        # The checked path is included so users can confirm where we looked (#48).
        assert "/nonexistent/path/home-assistant.log" in text
        assert "logger:" in text

    async def test_get_error_log_falls_back_to_system_log_buffer(self, view, mock_hass):
        """When the file is missing, fall back to the in-memory system_log buffer (#48)."""
        mock_hass.config.path.return_value = "/nonexistent/home-assistant.log"

        # Mimic homeassistant.components.system_log: hass.data["system_log"].records.to_list().
        handler = Mock()
        handler.records.to_list.return_value = [
            {
                "name": "homeassistant.components.foo",
                "message": ["Something broke"],
                "level": "ERROR",
                "source": ["components/foo/__init__.py", 42],
                "timestamp": 1_700_000_000.0,
                "exception": "Traceback (most recent call last):\n  ...\nValueError: boom",
                "count": 3,
                "first_occurred": 1_700_000_000.0,
            }
        ]
        mock_hass.data = {"mcp_server_http_transport": True, "system_log": handler}
        mock_hass.async_add_executor_job = AsyncMock(side_effect=FileNotFoundError())

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 256,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "in-memory system log buffer" in text
        assert "Something broke" in text
        assert "ERROR" in text
        assert "components/foo/__init__.py:42" in text
        assert "(3x)" in text
        assert "ValueError: boom" in text

    async def test_get_error_log_empty_buffer_returns_not_found(self, view, mock_hass):
        """An empty system_log buffer falls through to the not-found message."""
        mock_hass.config.path.return_value = "/nonexistent/home-assistant.log"
        handler = Mock()
        handler.records.to_list.return_value = []
        mock_hass.data = {"mcp_server_http_transport": True, "system_log": handler}
        mock_hass.async_add_executor_job = AsyncMock(side_effect=FileNotFoundError())

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 257,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Log file not found" in text
        assert "logger:" in text

    async def test_get_error_log_buffer_read_failure_degrades_to_not_found(self, view, mock_hass):
        """If the system_log buffer raises, degrade to the not-found message, not a 500."""
        mock_hass.config.path.return_value = "/nonexistent/home-assistant.log"
        handler = Mock()
        handler.records.to_list.side_effect = RuntimeError("buffer exploded")
        mock_hass.data = {"mcp_server_http_transport": True, "system_log": handler}
        mock_hass.async_add_executor_job = AsyncMock(side_effect=FileNotFoundError())

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "get_error_log", "arguments": {}},
                "id": 258,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Log file not found" in text
        assert "logger:" in text

    async def test_post_tools_call_restart_ha_error(self, view, mock_hass):
        """Test restart_ha when service call raises."""
        mock_hass.services.async_call = AsyncMock(side_effect=Exception("Service error"))

        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "restart_ha", "arguments": {"confirm": True}},
                "id": 252,
            }
        )

        with patch.object(view, "_authenticate", return_value=CALLER):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Error restarting Home Assistant" in text

    async def test_post_tools_call_check_config_error(self, view, mock_hass):
        """Test check_config when async_check_ha_config_file raises."""
        request = Mock()
        request.headers = {"Authorization": "Bearer valid_token"}
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "check_config", "arguments": {}},
                "id": 253,
            }
        )

        with (
            patch.object(view, "_authenticate", return_value=CALLER),
            patch(
                "homeassistant.helpers.check_config.async_check_ha_config_file",
                new_callable=AsyncMock,
                side_effect=Exception("Config check failed"),
            ),
        ):
            response = await view.post(request)

        assert response.status == 200
        body = json.loads(response.body)
        text = body["result"]["content"][0]["text"]
        assert "Error checking config" in text


class TestFormatSystemLogEntry:
    """Cover the defensive formatting branches for odd-shaped system_log entries."""

    def test_odd_field_shapes(self):
        """Non-numeric timestamp, string source, and string message all render."""
        line = _format_system_log_entry(
            {
                "timestamp": "not-an-epoch",
                "level": "WARNING",
                "name": "custom.logger",
                "source": "single-source",
                "message": "plain string message",
                "count": 1,
            }
        )
        assert "not-an-epoch" in line  # timestamp parse fell back to str()
        assert "WARNING" in line
        assert "(custom.logger)" in line
        assert "[single-source]" in line  # non-tuple source rendered as-is
        assert "plain string message" in line
        assert "(1x)" not in line  # count of 1 adds no suffix

    def test_missing_source_and_message(self):
        """Absent source and message degrade to a placeholder and empty string."""
        line = _format_system_log_entry({"timestamp": 1_700_000_000.0, "level": "ERROR"})
        assert "[?]" in line  # source is None -> "?"
        assert "ERROR" in line
