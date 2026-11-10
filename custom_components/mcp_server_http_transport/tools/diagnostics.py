"""Read-only diagnostics: repair issues, system health and backups.

None of these has a service. Repairs come from the issue registry, a helper. System
health and backups are read the way core's websocket commands read them, through
the system_health component's get_info and the backup manager in hass.data, so each
is guarded for its component not being loaded.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import translation

from . import ANNOTATION_READ_ONLY, dumps, error_result, register_tool
from .system_admin import redact_log_text

DEFAULT_BACKUP_LIMIT = 20
MAX_BACKUP_LIMIT = 200

# Integrations answer system health one after another, and each of their
# reachability checks may wait several seconds on an unreachable server.
SYSTEM_HEALTH_TIMEOUT = 30


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _fill(template: str | None, placeholders: dict[str, str] | None) -> str | None:
    """Fill a translated text's {placeholders}, leaving unknown ones as they are."""
    if template is None:
        return None
    for key, value in (placeholders or {}).items():
        template = template.replace(f"{{{key}}}", str(value))
    return template


@register_tool(
    name="list_repairs",
    description=(
        "List the repair issues Home Assistant currently raises (Settings > Repairs): "
        "deprecations, misconfigurations and problems integrations report, each with its "
        "severity, title and description. Ignored issues are left out unless asked for"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "include_ignored": {
                "type": "boolean",
                "description": "Also list issues the user has ignored (default false)",
            }
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_repairs(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List active repair issues with their translated text."""
    include_ignored = arguments.get("include_ignored") is True
    issues = [
        issue
        for issue in ir.async_get(hass).issues.values()
        if issue.active and (include_ignored or issue.dismissed_version is None)
    ]
    # An issue's text is in the translations of the integration that raised it.
    # issue_domain only names the integration the issue is about.
    domains = {issue.domain for issue in issues}
    texts = (
        await translation.async_get_translations(hass, hass.config.language, "issues", domains)
        if domains
        else {}
    )

    def text(issue: ir.IssueEntry, part: str) -> str | None:
        """Return an issue's title or description, or that of its fix flow's first step.

        A fixable issue often has no description of its own: what it says is on
        the steps of the flow that fixes it.
        """
        prefix = f"component.{issue.domain}.issues.{issue.translation_key or issue.issue_id}."
        found = texts.get(f"{prefix}{part}")
        if found is None and issue.is_fixable:
            steps = f"{prefix}fix_flow.step."
            candidates = {
                key[len(steps) :].split(".")[0]: value
                for key, value in texts.items()
                if key.startswith(steps) and key.endswith(f".{part}")
            }
            for step in ("init", "confirm", *sorted(candidates)):
                if step in candidates:
                    found = candidates[step]
                    break
        return _fill(found, issue.translation_placeholders)

    result = [
        {
            "domain": issue.domain,
            "issue_id": issue.issue_id,
            "issue_domain": issue.issue_domain,
            "severity": issue.severity,
            "title": text(issue, "title"),
            "description": text(issue, "description"),
            "is_fixable": bool(issue.is_fixable),
            "ignored": issue.dismissed_version is not None,
            "breaks_in_ha_version": issue.breaks_in_ha_version,
            "learn_more_url": issue.learn_more_url,
            "created": issue.created,
        }
        for issue in sorted(issues, key=lambda issue: (issue.created, issue.domain, issue.issue_id))
    ]
    return _text(dumps(result))


@register_tool(
    name="get_system_health",
    description=(
        "Get Home Assistant's system health report (Settings > System > Repairs > System "
        "information): what each integration reports about itself, such as installation "
        "type, versions, cloud and server reachability, and database size. It makes the "
        "same network checks as that page, one integration after another, so it can take "
        f"a while when servers are unreachable and gives up after {SYSTEM_HEALTH_TIMEOUT} "
        "seconds"
    ),
    input_schema={"type": "object", "properties": {}},
    annotations=ANNOTATION_READ_ONLY,
)
async def get_system_health(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the system health information of every integration that reports any."""
    if "system_health" not in hass.config.components:
        return error_result(
            "System health is not available: the system_health integration is not loaded."
        )

    from homeassistant.components import system_health

    try:
        async with asyncio.timeout(SYSTEM_HEALTH_TIMEOUT):
            report = await system_health.get_info(hass)
    except TimeoutError:
        return error_result(
            f"System health did not answer within {SYSTEM_HEALTH_TIMEOUT} seconds, usually "
            "because an integration is waiting on a server it cannot reach."
        )
    return _text(dumps(_redacted(report)))


def _redacted(value: Any) -> Any:
    """Mask credentials in the texts integrations report, such as an error they hit."""
    if isinstance(value, str):
        return redact_log_text(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value


def _backup_time(backup: Any) -> datetime:
    """Return when a backup was made, comparable across UTC offsets."""
    try:
        made = datetime.fromisoformat(backup.date)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, UTC)
    return made if made.tzinfo else made.astimezone()


def _backup_json(backup: Any) -> dict[str, Any]:
    return {
        "backup_id": backup.backup_id,
        "name": backup.name,
        "date": backup.date,
        "automatic": backup.with_automatic_settings,
        "homeassistant_version": backup.homeassistant_version,
        "database_included": backup.database_included,
        "addons": [addon.name for addon in backup.addons],
        "folders": list(backup.folders),
        "locations": {
            agent_id: {"size": status.size, "protected": status.protected}
            for agent_id, status in backup.agents.items()
        },
        "failed_locations": list(backup.failed_agent_ids),
    }


@register_tool(
    name="list_backups",
    description=(
        "List Home Assistant's backups, newest first, with where each is stored and its "
        "size in bytes there, along with when the last automatic backup ran and when the "
        "next is due. It asks every backup location for its list, cloud and network "
        "storage included. Read-only: it cannot create, restore or delete a backup"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_BACKUP_LIMIT,
                "description": (
                    f"Maximum backups to return (default {DEFAULT_BACKUP_LIMIT}, "
                    f"max {MAX_BACKUP_LIMIT})"
                ),
            }
        },
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def list_backups(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """List backups and the state of automatic backups."""
    limit = arguments.get("limit", DEFAULT_BACKUP_LIMIT)
    if type(limit) is not int or not 1 <= limit <= MAX_BACKUP_LIMIT:
        return error_result(f"limit must be an integer between 1 and {MAX_BACKUP_LIMIT}.")

    from homeassistant.components.backup.const import DATA_MANAGER

    manager = hass.data.get(DATA_MANAGER)
    if manager is None:
        return error_result("Backups are not available: the backup integration is not loaded.")

    backups, agent_errors = await manager.async_get_backups()
    newest_first = sorted(backups.values(), key=_backup_time, reverse=True)
    config = manager.config.data
    return _text(
        dumps(
            {
                "backups": [_backup_json(backup) for backup in newest_first[:limit]],
                "total": len(newest_first),
                "state": manager.state,
                "last_attempted_automatic_backup": config.last_attempted_automatic_backup,
                "last_completed_automatic_backup": config.last_completed_automatic_backup,
                "next_automatic_backup": config.schedule.next_automatic_backup,
                "location_errors": {
                    agent_id: redact_log_text(str(error))
                    for agent_id, error in agent_errors.items()
                },
            }
        )
    )
