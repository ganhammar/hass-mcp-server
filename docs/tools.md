# Tools, resources and prompts

[Back to the README](../README.md)

Everything the server offers a client. Tools marked opt-in, and the KNX tools while KNX is not set up, are left out of the list a client sees until they can be used: see [Configuration](configuration.md#limiting-the-tools).

You do not call these yourself: the assistant picks them from what you ask. The list is here so you can see what it is able to do, and which tools to switch off if you want it to do less.

**Experimental tools.** `create_dashboard`, `update_dashboard`, `delete_dashboard`, the helper tools other than `list_helpers`, and the three KNX entity tools use Home Assistant internals that have no stable API, so a Home Assistant update can break them. The helper tools only manage helpers created in the UI, not ones defined in YAML.

## Tools

### Entities and state

| Tool | Description |
|------|-------------|
| `get_state` | Get the current state of any entity (optional `fields` to limit attributes) |
| `batch_get_state` | Get state for multiple entities in one call (max 50) |
| `list_entities` | List all entities, with optional `domain`, `detailed`, and `fields` parameters |
| `list_entity_registry` | List registered entities (including disabled ones), filter by `domain`, page with `limit` (default 100, max 500) and `offset`; returns total count and `has_more` |
| `get_entity_registry` | Read registry metadata for an entity, including disabled or unavailable entities |
| `update_entity_registry` | Preview by default or apply (`apply=true`) a change to an entity's `name`, same-domain `new_entity_id`, area, labels, or whether it is disabled or hidden. HA validates IDs on apply; **references in automations, scripts, dashboards and templates are not migrated** |
| `delete_entity` | Delete the registry entry of a leftover entity that nothing provides any more; an entity still provided is refused (requires explicit confirmation) |
| `search_entities` | Find entities from plain words such as "office lights" and return their states. Every word has to match the entity ID, name or an alias, or the name or an alias of its area, floor or device, in any order; a trailing "s" is optional. Filters by `domain`, `device_class` and area (ID, name or alias). The best matches come first, with configuration, diagnostic and hidden entities last, and the reply says when `limit` cut the list |
| `get_device_details` | Get a device and every entity registered to it (all domains), with optional states |
| `call_service` | Call any Home Assistant service |
| `create_calendar_event` | Create a one-off calendar event (local calendars; entity API) |
| `create_recurring_calendar_event` | Create a recurring calendar series with RRULE (local calendars) |
| `list_calendar_events` | List events in a time window (uid for deletes; descriptions omitted by default) |
| `delete_calendar_events` | Delete events by uid or summary filter (supports dry_run preview) |
| `fire_event` | Fire a custom event on the Home Assistant event bus |
| `get_history` | Get state history of an entity over a time range |
| `get_logbook` | Fetch logbook entries for an entity or time range |
| `get_statistics` | Fetch long-term statistics (energy, climate) with configurable period |
| `list_statistic_ids` | List statistic IDs and their metadata (source, unit, whether they carry a mean/sum) |
| `validate_statistics` | Report statistics issues the recorder detected, the same list as "Fix issues" in Developer Tools |
| `adjust_statistics` | Correct a statistic's sum from a point in time onward (spike/bad reset); sum statistics only |
| `clear_statistics` | Permanently delete a statistic's history so it can start clean (irreversible; requires `confirm=true`) |
| `render_template` | Evaluate a Jinja2 template (requires "Enable template rendering") |

### Automations, scenes and scripts

| Tool | Description |
|------|-------------|
| `search_config` | Find every automation, script, scene and dashboard whose config mentions some text (an entity ID, service, device, area, label or helper), with a JSON Pointer and snippet per match; narrow with `types`, cap with `limit` (default 20 items) and `matches_per_item` (default 5) |
| `list_automations` | List all automations with full configuration |
| `get_automation_config` | Get full configuration of a single automation |
| `create_automation` | Create a new automation, optionally in a category and with an icon |
| `update_automation` | Update an automation with `dry_run`, `expected_hash`, optional reload and opt-in validation/rollback; supports icon updates |
| `delete_automation` | Delete an automation |
| `list_scenes` | List all scenes with full configuration |
| `get_scene_config` | Get full configuration of a single scene |
| `create_scene` | Create a new scene, optionally in a category and with an icon |
| `update_scene` | Update an existing scene, optionally changing its icon |
| `delete_scene` | Delete a scene |
| `list_scripts` | List all scripts with full configuration |
| `get_script_config` | Get full configuration of a single script |
| `create_script` | Create a new script, optionally in a category and with an icon |
| `update_script` | Update an existing script, optionally changing its icon |
| `delete_script` | Delete a script |
| `list_traces` | List recent execution traces for an automation/script (or a whole domain), newest first |
| `get_trace` | Get the full step-by-step execution trace of one run: which trigger fired, which conditions passed or failed, and the variables at each step (`summary=true` for an outline of large traces) |

`update_automation` can preview a change (`dry_run`), refuse to overwrite a newer version (`expected_hash`), and check the whole configuration before reloading (`validate`), restoring the file if the check fails.

### Helpers

| Tool | Description |
|------|-------------|
| `list_helpers` | List all helper entities, optionally filtered by domain |
| `get_helper_config` | Get the raw stored configuration of a UI-managed helper (experimental) |
| `create_helper` | Create a new helper (input_boolean, input_number, input_text, input_select, input_datetime, input_button, counter, timer, schedule), optionally in a category and with an icon (experimental) |
| `update_helper` | Update an existing UI-managed helper by entity ID, optionally changing its icon (experimental) |
| `delete_helper` | Delete a UI-managed helper by entity ID (experimental) |

### Categories

| Tool | Description |
|------|-------------|
| `list_categories` | List the categories for automations, scripts, scenes or helpers, with the entities in each |
| `set_category` | Put an automation, script, scene or helper in a category (created by name if new), or take it out of one |

### Labels

| Tool | Description |
|------|-------------|
| `list_labels` | List all labels for cross-domain grouping |
| `create_label` | Create a label with an optional color, icon and description |
| `update_label` | Rename a label or change its color, icon or description; an empty string clears an optional field |
| `delete_label` | Delete a label and remove it from every entity, device and area that carries it |

### Icons

| Tool | Description |
|------|-------------|
| `set_icon` | Set or remove the icon override of any registered entity, such as an automation, without changing its config |

### Config files (opt-in)

| Tool | Description |
|------|-------------|
| `list_config_files` | List YAML files in the config directory (first level by default, `recursive=true` for split configs; secrets excluded) |
| `get_config_file` | Read the contents of a YAML config file (max 1 MB) |
| `save_config_file` | Write or replace a YAML config file; auto-backs up all files first, then validates config |
| `delete_config_file` | Delete a YAML config file; auto-backs up all files first |
| `batch_edit_config_files` | Write and/or delete multiple YAML files in one call; one backup and one config check for the whole batch |
| `backup_config_files` | Manually snapshot all YAML files into `mcp_backups/<timestamp>/` |
| `list_config_backups` | List all available backup snapshots, newest first |
| `restore_config_backup` | Restore files from the latest or a specific backup; creates a pre-restore snapshot of the current state and runs config validation after restoring |
| `cleanup_config_backups` | Delete backup snapshots older than N days (default 30); keeps the folder from growing indefinitely |

### AppDaemon files (opt-in)

| Tool | Description |
|------|-------------|
| `list_appdaemon_files` | List regular files under the configured AppDaemon apps root with SHA-256 hashes |
| `get_appdaemon_file` | Read one relative AppDaemon app file with its SHA-256 hash |
| `save_appdaemon_file` | Atomically save one AppDaemon app file after creating a rollback snapshot |
| `delete_appdaemon_file` | Delete one AppDaemon app file after creating a rollback snapshot |
| `backup_appdaemon_files` | Create a controlled snapshot of the AppDaemon apps tree |
| `list_appdaemon_backups` | List available AppDaemon snapshots |
| `cleanup_appdaemon_backups` | Delete AppDaemon snapshots older than N days (default 30) |
| `restore_appdaemon_backup` | Restore a named AppDaemon snapshot with rollback protection |

Files up to 1 MB can be read or saved. Snapshots copy every file, whatever its size.

### Dashboards

| Tool | Description |
|------|-------------|
| `list_dashboards` | List all Lovelace dashboards with metadata |
| `get_dashboard_config` | Get a dashboard configuration; `summary=true` returns an outline of views and cards, `path` returns just the part a JSON Pointer addresses |
| `patch_dashboard_config` | Apply JSON Patch operations to a dashboard: move, add, remove, or edit individual cards without resending the whole config |
| `save_dashboard_config` | Save (replace) full dashboard configuration |
| `delete_dashboard_config` | Reset a dashboard configuration to empty |
| `create_dashboard` | Create a new Lovelace dashboard (experimental) |
| `update_dashboard` | Update dashboard metadata (experimental) |
| `delete_dashboard` | Delete a dashboard and its config (experimental) |

A real dashboard can be over 100 KB, too much to read and write back for a one-card change. `get_dashboard_config` with `summary=true` returns an outline with a JSON Pointer per view and card. Pass a pointer as `path` to read one card, and to `patch_dashboard_config` to change it with [RFC 6902](https://datatracker.ietf.org/doc/html/rfc6902) operations. Operations apply in order and all or nothing, so a failing one leaves the dashboard untouched. A leading `test` operation makes the patch fail, rather than edit the wrong card, if the dashboard changed since it was read.

### System and diagnostics

| Tool | Description |
|------|-------------|
| `get_config` | Get Home Assistant configuration (version, location, units, timezone) |
| `get_system_status` | System overview: version, domain counts, entity totals, problem entities |
| `get_domain_stats` | Aggregate stats for a single domain (count, state breakdown, examples) |
| `check_config` | Validate Home Assistant configuration without restarting |
| `restart_ha` | Restart Home Assistant (requires explicit confirmation) |
| `get_error_log` | Fetch the Home Assistant error log (last N lines); tokens, API keys, and URL credentials are redacted |
| `list_repairs` | List the repair issues Home Assistant raises, with severity, title and description |
| `get_system_health` | System health report: what each integration says about itself (versions, reachability, database size) |
| `list_backups` | List backups, newest first, with where each is stored, and when automatic backups last ran and run next |
| `list_services` | List available services, optionally filtered by domain |
| `describe_service` | Get a service's full parameter schema: fields, selectors, examples, and targets |
| `list_integrations` | List installed integrations with their status, whether each is disabled, and whether it has an options or reconfigure flow |

### Areas, floors and devices

| Tool | Description |
|------|-------------|
| `list_areas` | List all areas |
| `list_devices` | List devices with their area, labels and whether they are disabled, optionally filtered by area |
| `update_device` | Rename a device, move it to an area, set its labels, or disable or enable it |
| `delete_device` | Delete a device and its entities, if its integration supports and allows it (requires explicit confirmation and "Enable integration management") |
| `create_area` | Create an area with an optional floor, icon, labels and aliases |
| `update_area` | Rename an area or change its floor, icon, labels or aliases; an empty value clears a field |
| `delete_area` | Delete an area, leaving its devices and entities without one |
| `list_floors` | List all floors |
| `create_floor` | Create a floor with an optional level, icon and aliases |
| `update_floor` | Rename a floor or change its level, icon or aliases; `clear_level` removes the level |
| `delete_floor` | Delete a floor, leaving its areas without one |

### Integrations (opt-in)

| Tool | Description |
|------|-------------|
| `start_config_flow` | Start an integration's config flow and get its first step: form schema, menu, abort reason or created entry (requires "Enable integration management") |
| `continue_config_flow` | Submit a config flow step and get the next one, validation errors, or the created entry (requires "Enable integration management") |
| `abort_config_flow` | Abort a config flow in progress (requires "Enable integration management") |
| `reload_integration` | Reload a config entry by ID (requires "Enable integration management") |
| `delete_integration` | Remove a config entry with its devices and entities; requires `confirm=true` (requires "Enable integration management") |
| `start_options_flow` | Start a config entry's options flow, the one behind its Configure button; continue with `continue_config_flow` (requires "Enable integration management") |
| `start_reconfigure_flow` | Start a config entry's reconfigure flow to change what was entered at setup (requires "Enable integration management") |
| `set_integration_enabled` | Disable or enable a config entry, keeping its configuration (requires "Enable integration management") |

### KNX

| Tool | Description |
|------|-------------|
| `knx_recent_telegrams` | Read Home Assistant's KNX group-monitor telegram history: recent bus telegrams with their **source device** and decoded value; regex-filter by group address / name, with a result limit. Retrospective (reads the stored buffer), ideal for finding which KNX device wrote a given group address |
| `knx_get_base_data` | KNX connection + project info: bus connection status, gateway address, xknx version, loaded ETS project metadata, and UI-creatable platforms |
| `knx_get_entities` | List KNX group addresses and the entities bound to each (the KNX-specific group-address to entity binding view); optional regex filter on the group address |
| `knx_create_entity` | Create a KNX entity in the KNX UI config (config_store) from `platform` + `data` (experimental) |
| `knx_update_entity` | Update a UI-managed KNX entity by `entity_id` (experimental) |
| `knx_delete_entity` | Delete a UI-managed KNX entity by `entity_id` (experimental) |

### Cameras and images (opt-in)

These tools are disabled by default; enable them per capability via Settings > Devices & Services > MCP Server > Configure. They return images directly to the model for visual analysis.

| Tool | Description |
|------|-------------|
| `get_camera_image` | Capture the current frame from a camera entity (optional `width`/`height` to downscale); no snapshot file is written (requires "Enable camera image access") |
| `get_image_file` | Read an image file (JPEG, PNG, GIF, WebP) from an allowed directory, e.g. a snapshot saved by `camera.snapshot` (requires "Enable image file access") |

## Resources

| URI | Description |
|-----|-------------|
| `hass://config` | Home Assistant configuration |
| `hass://areas` | All areas |
| `hass://devices` | All registered devices |
| `hass://services` | All available services by domain |
| `hass://floors` | All configured floors |
| `hass://entities` | All entities organized by domain |
| `hass://labels` | All labels |
| `hass://integrations` | Installed integrations with status |
| `hass://entity/{entity_id}` | State and attributes of a specific entity |
| `hass://dashboard/{url_path}` | Full configuration of a specific dashboard |
| `hass://entities/domain/{domain}` | Entities filtered by a specific domain |

## Prompts

| Prompt | Description |
|--------|-------------|
| `troubleshoot_device` | Diagnose issues with a specific entity |
| `daily_summary` | Summarize recent activity across all entities |
| `automation_review` | Review an automation's config for issues and improvements |
| `energy_report` | Summarize energy consumption data over a time range |
| `setup_guide` | Guided troubleshooting for an entity in a problem state |
| `automation_builder` | Step-by-step guided automation creation |
| `automation_debugger` | Debug why an automation is not firing or misbehaving |
| `automation_audit` | Audit all automations for conflicts, redundancies, and anti-patterns |
| `schedule_optimizer` | Analyze automation schedules and suggest timing improvements |
| `naming_conventions` | Scan entity names for inconsistencies and suggest standardization |
| `dashboard_builder` | Suggest a Lovelace dashboard layout for given entities or area |
| `change_validator` | Pre-flight check after creating or modifying configurations |
| `security_review` | Scan for security issues in entities, integrations, and configuration |

## Completions

Clients that support it get autocompletion for entity IDs, domains, services, areas, dashboards, and automation, scene and script IDs.
