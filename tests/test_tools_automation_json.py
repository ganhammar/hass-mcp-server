"""Regression tests for no-reload JSON automation updates."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml

from custom_components.mcp_server_http_transport.tools import TOOLS
from custom_components.mcp_server_http_transport.tools.config import update_automation_json

AUTOMATION_ID = "existing-id"
OLD_YAML = (
    "- id: existing-id\n"
    "  alias: Original\n"
    "  triggers: []\n"
    "  conditions: []\n"
    "  actions: []\n"
    "  mode: single\n"
)


@pytest.fixture
def mock_hass(tmp_path):
    """Run executor jobs locally against an isolated temporary config folder."""
    hass = Mock()
    hass.config.config_dir = str(tmp_path)
    hass.config.path = Mock(side_effect=lambda filename: str(tmp_path / filename))
    (tmp_path / "automations.yaml").write_text(OLD_YAML, encoding="utf-8")

    async def execute(function, *args):
        return function(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=execute)
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def existing_config():
    return {
        "id": AUTOMATION_ID,
        "alias": "Original",
        "triggers": [],
        "conditions": [],
        "actions": [],
        "mode": "single",
    }


def arguments(existing_config, **kwargs):
    changed = {**existing_config, "alias": "Updated"}
    return {
        "automation_id": AUTOMATION_ID,
        "config_json": json.dumps(changed),
        **kwargs,
    }


def message(response):
    return response["content"][0]["text"]


async def test_schema_uses_string_instead_of_unconstrained_nested_object():
    """Clients can send complete JSON without an MCP object schema conversion."""
    schema = TOOLS["update_automation_json"]["schema"]["inputSchema"]
    assert schema["properties"]["config_json"]["type"] == "string"
    assert "apply" not in schema["required"]


async def test_default_is_preview_only(mock_hass, existing_config, tmp_path):
    """No apply flag must not write, back up, validate, or reload."""
    with (
        patch(
            "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
            new_callable=AsyncMock,
            return_value=existing_config,
        ),
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files._create_backup_sync"
        ) as backup,
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
            new_callable=AsyncMock,
        ) as validation,
    ):
        response = await update_automation_json(mock_hass, arguments(existing_config))

    assert "PREVIEW ONLY" in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    backup.assert_not_called()
    validation.assert_not_awaited()
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize(
    "replacement,expected",
    [
        ("not json", "No automation update applied"),
        (json.dumps({"alias": "Updated"}), "Complete config must include triggers"),
        (json.dumps({"triggers": []}), "Complete config must include actions"),
        (
            json.dumps({"id": "other-id", "triggers": [], "actions": []}),
            "config_json ID differs",
        ),
    ],
)
async def test_invalid_payloads_never_write(mock_hass, replacement, expected, tmp_path):
    response = await update_automation_json(
        mock_hass,
        {"automation_id": AUTOMATION_ID, "config_json": replacement, "apply": True},
    )
    assert response.get("isError") is True
    assert expected in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    mock_hass.services.async_call.assert_not_awaited()


async def test_rejects_incomplete_replacement(mock_hass, existing_config, tmp_path):
    """Existing keys cannot be silently dropped from the replacement."""
    missing_mode = {key: value for key, value in existing_config.items() if key != "mode"}
    missing_mode["alias"] = "Updated"
    with patch(
        "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
        new_callable=AsyncMock,
        return_value=existing_config,
    ):
        response = await update_automation_json(
            mock_hass,
            {
                "automation_id": AUTOMATION_ID,
                "config_json": json.dumps(missing_mode),
                "apply": True,
            },
        )
    assert response.get("isError") is True
    assert "original fields missing" in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("config_valid", [True, False])
async def test_apply_backs_up_validates_and_never_reloads(
    mock_hass, existing_config, tmp_path, config_valid
):
    """Failed checks must restore the original YAML; successful writes stay staged."""
    with (
        patch(
            "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
            new_callable=AsyncMock,
            return_value=existing_config,
        ),
        patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            return_value=[existing_config.copy()],
        ),
        patch(
            "custom_components.mcp_server_http_transport.config_manager."
            "yaml_dumper.save_yaml",
            side_effect=lambda path, rows: Path(path).write_text(
                yaml.safe_dump(rows), encoding="utf-8"
            ),
        ),
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
            new_callable=AsyncMock,
            return_value={
                "valid": config_valid,
                "errors": [] if config_valid else ["Invalid configuration"],
            },
        ) as validation,
    ):
        response = await update_automation_json(mock_hass, arguments(existing_config, apply=True))

    saved = (tmp_path / "automations.yaml").read_text(encoding="utf-8")
    if config_valid:
        assert "Saved automation" in message(response)
        assert "Updated" in saved
    else:
        assert response.get("isError") is True
        assert "Backup restored without reload" in message(response)
        assert saved == OLD_YAML

    backups = list((tmp_path / "mcp_backups").glob("*/automations.yaml"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == OLD_YAML
    validation.assert_awaited_once()
    mock_hass.services.async_call.assert_not_awaited()


async def test_concurrent_change_is_refused(mock_hass, existing_config, tmp_path):
    """Never overwrite an automation that changed after the initial read."""
    with (
        patch(
            "custom_components.mcp_server_http_transport.config_manager.read_list_entry",
            new_callable=AsyncMock,
            return_value=existing_config,
        ),
        patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            return_value=[{**existing_config, "alias": "Changed concurrently"}],
        ),
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
            new_callable=AsyncMock,
        ) as validation,
    ):
        response = await update_automation_json(
            mock_hass, arguments(existing_config, apply=True)
        )

    assert response.get("isError") is True
    assert "changed since it was read" in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    validation.assert_not_awaited()
    mock_hass.services.async_call.assert_not_awaited()
