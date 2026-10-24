"""Regression tests for optional automation preview, reload and validation."""

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
    """Execute executor jobs locally, with a real automation YAML file."""
    hass = Mock()
    hass.config.path = Mock(side_effect=lambda filename: str(tmp_path / filename))
    (tmp_path / "automations.yaml").write_text(OLD_YAML, encoding="utf-8")

    async def execute(function, *args, **kwargs):
        return function(*args, **kwargs)

    hass.async_add_executor_job = AsyncMock(side_effect=execute)
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture(autouse=True)
def yaml_io():
    """Use PyYAML in isolated tests for both config read and write."""
    with (
        patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            side_effect=lambda path: yaml.safe_load(Path(path).read_text(encoding="utf-8")),
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


def arguments(config, **options):
    return {
        "automation_id": AUTOMATION_ID,
        "config": {**config, "alias": "Updated"},
        **options,
    }


def message(response):
    assert "isError" not in response
    return response["content"][0]["text"]


def test_schema_keeps_original_and_optional_flags():
    schema = TOOLS["update_automation"]["schema"]["inputSchema"]
    assert schema["required"] == ["automation_id", "config"]
    assert schema["properties"]["config"]["type"] == "object"
    assert schema["properties"]["icon"]["type"] == "string"
    for option in ("dry_run", "reload", "validate"):
        assert schema["properties"][option]["type"] == "boolean"
    assert schema["properties"]["expected_hash"]["type"] == "string"
    assert "update_automation_json" not in TOOLS


async def test_dry_run_does_not_write_validate_or_reload(mock_hass, existing_config, tmp_path):
    with patch(
        "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
        new_callable=AsyncMock,
    ) as unused:
        response = await update_automation(mock_hass, arguments(existing_config, dry_run=True))
    assert "PREVIEW ONLY" in message(response)
    assert "alias" in message(response)
    assert re.search(r"Original hash: [0-9a-f]{64}", message(response))
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    assert not list(tmp_path.glob(".automations.mcp_backup_*"))
    unused.assert_not_awaited()
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize(
    "bad_config,expected",
    [
        ("json text", "config must be a non-empty object"),
        ({}, "config must be a non-empty object"),
        ({"id": "other"}, "config ID differs"),
    ],
)
async def test_invalid_config_not_written(mock_hass, bad_config, expected, tmp_path):
    response = await update_automation(
        mock_hass, {"automation_id": AUTOMATION_ID, "config": bad_config}
    )
    assert expected in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML


@pytest.mark.parametrize("field,value", [("dry_run", "true"), ("reload", 1), ("validate", 1)])
async def test_flags_must_be_booleans(mock_hass, existing_config, field, value):
    response = await update_automation(mock_hass, arguments(existing_config, **{field: value}))
    assert "must be booleans" in message(response)
    mock_hass.async_add_executor_job.assert_not_awaited()


async def test_default_update_uses_legacy_fast_path(existing_config):
    """The existing integration CRUD test's mocked /config path must still work."""
    hass = Mock()
    hass.services.async_call = AsyncMock()
    with (
        patch(
            "custom_components.mcp_server_http_transport.config_manager.update_list_entry",
            new_callable=AsyncMock,
        ) as legacy_update,
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
            new_callable=AsyncMock,
        ) as check,
    ):
        response = await update_automation(hass, arguments(existing_config))
    assert "Successfully updated automation" in message(response)
    legacy_update.assert_awaited_once()
    check.assert_not_awaited()
    hass.config.path.assert_not_called()


async def test_keys_can_be_removed(mock_hass, existing_config, tmp_path):
    new_config = {k: v for k, v in existing_config.items() if k != "conditions"}
    response = await update_automation(mock_hass, arguments(new_config, reload=False))
    assert "no reload performed" in message(response)
    saved = yaml.safe_load((tmp_path / "automations.yaml").read_text(encoding="utf-8"))
    assert "conditions" not in saved[0]
    assert saved[0]["alias"] == "Updated"
    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("valid", [True, False])
async def test_opt_in_validation_and_atomic_rollback(mock_hass, existing_config, tmp_path, valid):
    other_file = tmp_path / "scripts.yaml"
    other_file.write_text("unchanged\n", encoding="utf-8")
    with patch(
        "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
        new_callable=AsyncMock,
        return_value={"valid": valid, "errors": [] if valid else ["bad config"]},
    ) as check:
        response = await update_automation(
            mock_hass, arguments(existing_config, validate=True, reload=False)
        )
    contents = (tmp_path / "automations.yaml").read_text(encoding="utf-8")
    if valid:
        assert "Successfully updated automation" in message(response)
        assert "Updated" in contents
    else:
        assert "restored without reload" in message(response)
        assert contents == OLD_YAML
    assert other_file.read_text(encoding="utf-8") == "unchanged\n"
    assert not list(tmp_path.glob(".automations.mcp_backup_*"))
    check.assert_awaited_once()
    mock_hass.services.async_call.assert_not_awaited()


async def test_default_reload_without_validation(mock_hass, existing_config):
    """The optional special path reloads normally if expected_hash is supplied."""
    preview = await update_automation(mock_hass, arguments(existing_config, dry_run=True))
    original_hash = re.search(r"Original hash: ([0-9a-f]{64})", message(preview)).group(1)
    with patch(
        "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
        new_callable=AsyncMock,
    ) as check:
        response = await update_automation(
            mock_hass, arguments(existing_config, expected_hash=original_hash)
        )
    assert "automations reloaded" in message(response)
    check.assert_not_awaited()
    mock_hass.services.async_call.assert_awaited_once_with(
        "automation", "reload", blocking=True,
    )


async def test_stale_preview_hash_is_rejected(mock_hass, existing_config, tmp_path):
    preview = await update_automation(mock_hass, arguments(existing_config, dry_run=True))
    original_hash = re.search(r"Original hash: ([0-9a-f]{64})", message(preview)).group(1)
    changed_file = OLD_YAML.replace("Original", "Changed elsewhere")
    (tmp_path / "automations.yaml").write_text(changed_file, encoding="utf-8")
    response = await update_automation(
        mock_hass, arguments(existing_config, expected_hash=original_hash)
    )
    assert "changed since preview" in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == changed_file
    mock_hass.services.async_call.assert_not_awaited()


async def test_write_refuses_concurrent_changes(mock_hass, existing_config, tmp_path):
    with (
        patch(
            "custom_components.mcp_server_http_transport.config_manager._load_yaml_list",
            side_effect=[
                [existing_config.copy()],
                [{**existing_config, "alias": "Changed concurrently"}],
            ],
        ),
        patch(
            "custom_components.mcp_server_http_transport.tools.config_files._run_config_check",
            new_callable=AsyncMock,
        ) as check,
    ):
        response = await update_automation(
            mock_hass, arguments(existing_config, reload=False, validate=True)
        )
    assert "changed since it was read" in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    check.assert_not_awaited()
    assert not list(tmp_path.glob(".automations.mcp_backup_*"))


async def test_noop_without_reloading(mock_hass, existing_config, tmp_path):
    response = await update_automation(
        mock_hass, {"automation_id": AUTOMATION_ID, "config": existing_config, "reload": False}
    )
    assert "unchanged" in message(response)
    assert (tmp_path / "automations.yaml").read_text(encoding="utf-8") == OLD_YAML
    mock_hass.services.async_call.assert_not_awaited()
