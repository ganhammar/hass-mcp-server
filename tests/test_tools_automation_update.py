"""Regression tests for safe automation updates with optional preview/reload."""

import re
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml

from custom_components.mcp_server_http_transport.tools import TOOLS
from custom_components.mcp_server_http_transport.tools.config import update_automation

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
    """Execute Home Assistant executor jobs locally in a temporary config folder."""
    hass = Mock()
    hass.config.config_dir = str(tmp_path)
    hass.config.path = Mock(side_effect=lambda filename: str(tmp_path / filename))
    (tmp_path / "automations.yaml").write_text(OLD_YAML, encoding="utf-8")

    async def execute(function, *args):
        return function(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=execute)
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture(autouse=True)
def yaml_io():
    """Replace HA YAML access with simple PyYAML for isolated file tests."""
    with (
        patch(
            "custom_components.mcp_server_http_transport.config_manager."
            "_load_yaml_list",
            side_effect=lambda path: yaml.safe_load(
                Path(path).read_text(encoding="utf-8")
            ),
        ),
        patch(
            "custom_components.mcp_server_http_transport.config_manager."
            "yaml_dumper.save_yaml",
            side_effect=lambda path, rows: Path(path).write_text(
                yaml.safe_dump(rows), encoding="utf-8"
            ),
        ),
    ):
        yield


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
    return {
        "automation_id": AUTOMATION_ID,
        "config": {**existing_config, "alias": "Updated"},
        **kwargs,
    }


def message(response):
    return response["content"][0]["text"]


def test_schema_keeps_object_and_optional_flags():
    schema = TOOLS["update_automation"]["schema"]["inputSchema"]
    assert schema["properties"]["config"]["type"] == "object"
    assert schema["required"] == ["automation_id", "config"]
    assert schema["properties"]["dry_run"]["type"] == "boolean"
    assert schema["properties"]["reload"]["type"] == "boolean"
    assert "update_automation_json" not in TOOLS


async def test_dry_run_does_not_write_or_reload(mock_hass, existing_config, tmp_path):
    with (
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files."
            "_create_backup_sync"
        ) as backup,
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files."
            "_run_config_check",
            new_callable=AsyncMock,
        ) as validation,
    ):
        response = await update_automation(
            mock_hass, arguments(existing_config, dry_run=True)
        )

    assert "PREVIEW ONLY" in message(response)
    assert "alias" in message(response)
    assert re.search(r"Original hash: [0-9a-f]{64}", message(response))
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    backup.assert_not_called()
    validation.assert_not_awaited()
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize(
    "bad_config,expected",
    [
        ("json text", "config must be a non-empty object"),
        ({}, "config must be a non-empty object"),
        ({"id": "other-id"}, "config ID differs"),
    ],
)
async def test_invalid_payloads_do_not_write(
    mock_hass, bad_config, expected, tmp_path
):
    response = await update_automation(
        mock_hass, {"automation_id": AUTOMATION_ID, "config": bad_config}
    )
    assert response.get("isError") is True
    assert expected in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML


async def test_removed_keys_are_allowed(mock_hass, existing_config, tmp_path):
    changed = {
        key: value for key, value in existing_config.items() if key != "conditions"
    }
    changed["alias"] = "Updated"
    with patch(
        "custom_components.mcp_server_http_transport.tools.config_files."
        "_run_config_check",
        new_callable=AsyncMock,
        return_value={"valid": True, "errors": []},
    ):
        response = await update_automation(
            mock_hass,
            {"automation_id": AUTOMATION_ID, "config": changed, "reload": False},
        )
    assert response.get("isError") is not True
    saved = yaml.safe_load((tmp_path / "automations.yaml").read_text(encoding="utf-8"))
    assert "conditions" not in saved[0]
    assert saved[0]["alias"] == "Updated"
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("config_valid", [True, False])
async def test_write_validates_and_restores_on_failure(
    mock_hass, existing_config, tmp_path, config_valid
):
    with patch(
        "custom_components.mcp_server_http_transport.tools.config_files."
        "_run_config_check",
        new_callable=AsyncMock,
        return_value={
            "valid": config_valid,
            "errors": [] if config_valid else ["Invalid configuration"],
        },
    ) as validation:
        response = await update_automation(
            mock_hass, arguments(existing_config, reload=False)
        )

    saved = (tmp_path / "automations.yaml").read_text(encoding="utf-8")
    if config_valid:
        assert "Successfully updated automation" in message(response)
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


async def test_default_reload_is_preserved(mock_hass, existing_config):
    with patch(
        "custom_components.mcp_server_http_transport.tools.config_files."
        "_run_config_check",
        new_callable=AsyncMock,
        return_value={"valid": True, "errors": []},
    ):
        response = await update_automation(mock_hass, arguments(existing_config))

    assert "automations reloaded" in message(response)
    mock_hass.services.async_call.assert_awaited_once_with(
        "automation", "reload", blocking=True
    )


async def test_stale_preview_hash_is_rejected(mock_hass, existing_config, tmp_path):
    preview = await update_automation(
        mock_hass, arguments(existing_config, dry_run=True)
    )
    original_hash = re.search(
        r"Original hash: ([0-9a-f]{64})", message(preview)
    ).group(1)
    changed_file = OLD_YAML.replace("Original", "Changed elsewhere")
    (tmp_path / "automations.yaml").write_text(changed_file, encoding="utf-8")
    response = await update_automation(
        mock_hass, arguments(existing_config, expected_hash=original_hash)
    )
    assert response.get("isError") is True
    assert "changed since preview" in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == changed_file
    mock_hass.services.async_call.assert_not_awaited()


async def test_write_refuses_concurrent_changes(mock_hass, existing_config, tmp_path):
    with (
        patch(
            "custom_components.mcp_server_http_transport.config_manager."
            "_load_yaml_list",
            side_effect=[
                [existing_config.copy()],
                [{**existing_config, "alias": "Changed concurrently"}],
            ],
        ),
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files."
            "_run_config_check",
            new_callable=AsyncMock,
        ) as validation,
    ):
        response = await update_automation(mock_hass, arguments(existing_config))

    assert response.get("isError") is True
    assert "changed since it was read" in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    validation.assert_not_awaited()
    mock_hass.services.async_call.assert_not_awaited()


async def test_no_op_neither_backs_up_nor_reloads(mock_hass, existing_config, tmp_path):
    with patch(
        "custom_components.mcp_server_http_transport.tools.config_files."
        "_create_backup_sync"
    ) as backup:
        response = await update_automation(
            mock_hass, {"automation_id": AUTOMATION_ID, "config": existing_config}
        )
    assert "unchanged" in message(response)
    backup.assert_not_called()
    mock_hass.services.async_call.assert_not_awaited()
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
