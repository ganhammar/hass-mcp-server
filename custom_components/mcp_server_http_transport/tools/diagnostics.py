"""Read-only diagnostics: repair issues, system health and backups.

None of these has a service. Repairs come from the issue registry, a helper. System
health and backups are read the way core's websocket commands read them, through
the system_health component's get_info and the backup manager in hass.data, so each
is guarded for its component not being loaded.
"""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import translation

from . import ANNOTATION_READ_ONLY, dumps, register_tool

DEFAULT_BACKUP_LIMIT = 20
MAX_BACKUP_LIMIT = 200


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
    # An issue's text lives in the translations of the integration it is about.
    domains = {issue.issue_domain or issue.domain for issue in issues}
    texts = (
        await translation.async_get_translations(hass, hass.config.language, "issues", domains)
        if domains
        else {}
    )

    def text(issue: ir.IssueEntry, part: str) -> str | None:
        key = (
            f"component.{issue.issue_domain or issue.domain}.issues."
            f"{issue.translation_key or issue.issue_id}.{part}"
        )
        return _fill(texts.get(key), issue.translation_placeholders)

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
        "same network checks as that page, so it can take a few seconds"
    ),
    input_schema={"type": "object", "properties": {}},
    annotations=ANNOTATION_READ_ONLY,
)
async def get_system_health(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the system health information of every integration that reports any."""
    if "system_health" not in hass.config.components:
        return _text("System health is not available: the system_health integration is not loaded.")

    from homeassistant.components import system_health

    return _text(dumps(await system_health.get_info(hass)))


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
        "next is due. Read-only: it cannot create, restore or delete a backup"
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
        return _text(f"limit must be an integer between 1 and {MAX_BACKUP_LIMIT}.")

    from homeassistant.components.backup.const import DATA_MANAGER

    manager = hass.data.get(DATA_MANAGER)
    if manager is None:
        return _text("Backups are not available: the backup integration is not loaded.")

    backups, agent_errors = await manager.async_get_backups()
    newest_first = sorted(backups.values(), key=lambda backup: backup.date, reverse=True)
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
                    agent_id: str(error) for agent_id, error in agent_errors.items()
                },
            }
        )
    )
