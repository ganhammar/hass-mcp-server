# Configuration

[Back to the README](../README.md)

Everything here is set when you add the integration and can be changed later under Settings > Devices & Services > MCP Server > Configure.

## Options at a glance

| Option | Default | What it does |
|---|---|---|
| Enable native Home Assistant authentication | Off | Accepts long-lived access tokens, see [Connecting a client](connecting.md) |
| Require administrator account | On | Only administrators may connect, see [Security and auditing](security.md) |
| Read-only mode | Off | Offers only the tools that read, see [Limiting the tools](#limiting-the-tools) |
| Disabled tools | None | Switches off individual tools |
| Enable config file access | Off | Reading and editing YAML files, see [Config files](#config-files) |
| Enable camera image access | Off | Capturing a frame from a camera, see [Cameras and images](#cameras-and-images) |
| Enable image file access | Off | Reading saved image files, see [Cameras and images](#cameras-and-images) |
| Enable template rendering | Off | `render_template`, see [Template rendering](#template-rendering) |
| Enable integration management | Off | Adding, configuring and removing integrations, see [Integration management](#integration-management) |
| Enable AppDaemon app file access | Off | Reading and editing AppDaemon apps, see [AppDaemon app files](#appdaemon-app-files) |
| AppDaemon apps root | `/addon_configs/a0d7b954_appdaemon/apps` | The folder those tools work in |
| MCP server name | `home-assistant-mcp-server` | The name clients see, see [Server name](#server-name) |
| Use the newest MCP protocol revision | On | Serves MCP 2026-07-28, see [Protocol revision](#protocol-revision) |

## Limiting the tools

Two options decide which tools a client is offered at all. A tool they withhold is left out of the tool list and refused if a client calls it anyway. They apply to every connection, whatever it was granted when it connected.

- **Read-only mode** keeps only the tools that read: listing, searching, and fetching state, history, configuration and logs. Everything that can change something is withheld, including `call_service`.
- **Disabled tools** switches off individual tools by name, for example `restart_ha`.

A tool whose own option is off is left out of the list as well, and so are the KNX tools while KNX is not set up. On a default setup that is 35 of the 117 tools, which a client would otherwise carry in its context without being able to use them.

An application that connects through the OIDC Provider can be limited further, per connection: the consent screen asks what it may do, as a level such as Look only or area by area. See [Choosing what an application may do](security.md#choosing-what-an-application-may-do). The options stay the outer limit: no choice there turns on a tool they keep off. In read-only mode the consent screen offers nothing above Read.

Worth knowing:

- A client usually reads the tool list once, when it connects. Reconnect it after changing an option.
- Read-only mode and the disabled tools cover tools only. Resources such as `hass://entities` are still served, so disabling a listing tool does not hide the data it lists. The access chosen on the consent screen covers resources and prompts as well.
- A call to a withheld tool is logged and fires the audit event, even for a tool that only reads.

## Template rendering

`render_template` is off by default. A template can read every state, attribute, device and area on the instance in one call, and can call functions such as `states`, `state_attr`, `device_attr` and `integration_entities`, which is why Home Assistant limits its own template developer tool to administrators. Turn on **Enable template rendering** only for clients you trust with that access.

## Integration management

With **Enable integration management** on, a client can add an integration, change its settings, reload it, disable it, or remove it along with its devices and entities. The tools run the same flows as the "Add integration" dialog and an integration's Configure button. `delete_device` is behind the same option, because some integrations remove a device from their network when asked. Turn it on only for clients you trust with that.

What to know before you do:

- **Credentials pass through the conversation.** Whatever a flow asks for is typed by the model, so a password or API key entered this way ends up in the client's transcript. The audit log records only the field names.
- **Stored secrets are masked.** An options or reconfigure form comes filled in with the current values. Password fields, fields with a secret-like name and URLs that carry credentials are shown as `***`, and are kept when sent back that way.
- **Browser sign-ins cannot be finished here.** For a flow that needs OAuth, the tool reports the step and its URL so you can complete it yourself or abort it.
- **The MCP Server and its OIDC provider are off limits**, because their options decide who may connect and what a client may do.
- **Template helpers are refused while template rendering is off.** This follows that option's intent and is not a hard limit: scripts and some other integrations' flows also accept templates.

## Config files

With **Enable config file access** on, a client can list, read, write and delete YAML files in the config directory. A save is followed by a Home Assistant config check, reported with the result, unless the assistant asks to skip it.

What it can and cannot touch:

- Only `.yaml` and `.yml` files inside the config directory. Paths with `..`, absolute paths and symlinks that lead outside are rejected.
- `secrets.yaml` and `secrets.yml` are never readable, at any depth, and the backup folder `mcp_backups` cannot be edited directly.
- The top-level `automations.yaml`, `scenes.yaml` and `scripts.yaml` are edited through the automation, scene and script tools instead, so entries managed in the UI stay consistent. They are still part of every backup.
- Split configurations work: listing with `recursive=true` finds files in subfolders. Hidden folders, `custom_components`, `deps`, `www`, `node_modules`, `tts` and `mcp_backups` are skipped, and a missing folder is not created for you.

Backups:

- Before every save or delete, all first-level YAML files except `secrets.yaml` are copied to `config/mcp_backups/<timestamp>/`. A batch edit takes one snapshot for the whole batch.
- Files in subfolders are **not** in the snapshot, so keep your own copy before editing split configuration.
- `restore_config_backup` restores a whole snapshot, takes a snapshot of the current state first, and runs a config check afterwards. Files created after the snapshot are left in place. To get a single file back, copy it from the snapshot folder by hand.
- `cleanup_config_backups` removes snapshots older than 30 days, or the age you give it.
- If Home Assistant does not start after a bad edit, the snapshots are still on disk in `config/mcp_backups/` and can be copied back over SSH or Samba.

## Cameras and images

Two separate options. **Enable camera image access** lets a client capture the current frame of a camera entity. **Enable image file access** lets it read image files (JPEG, PNG, GIF, WebP) from the folders Home Assistant lets integrations read: `www` in the config directory, the media directories, and anything you add to `allowlist_external_dirs`. Those are the places `camera.snapshot` may write to. The image goes straight to the model, so it can describe what it sees.

## AppDaemon app files

With **Enable AppDaemon app file access** on, a client can read and edit the files of your AppDaemon apps. Those files are Python code that AppDaemon runs, so this amounts to letting the client run code inside AppDaemon with whatever access AppDaemon has to Home Assistant. AppDaemon normally reloads an app when its file changes, so treat a saved file as running. Look through the apps folder and its secrets before you enable it.

- **AppDaemon apps root** sets the folder. The default is the add-on's legacy private folder, which only works on installations that mount it into Home Assistant Core themselves. On Home Assistant OS it cannot be reached, because Core only sees `/config`, `/share`, `/media` and `/ssl`: set `app_dir: /share/appdaemon/apps` (or a folder under `/media`) in `appdaemon.yaml`, move the apps there, and enter the same path here.
- Only the legacy root and paths under `/share/` or `/media/` are accepted. Traversal, symlinks and paths that leave the root are rejected.
- Only `.py`, `.yaml`, `.yml` and `.json` files can be written or deleted. Every change takes a snapshot first, a restore snapshots the current state before it overwrites anything, and `cleanup_appdaemon_backups` prunes old ones. Files of other types are included in snapshots but never written back.
- There is no tool to run a shell command or to restart AppDaemon.

## Server name

By default, every instance of this integration reports the name `home-assistant-mcp-server` to MCP clients (`serverInfo.name`). That only matters when one client connects to more than one Home Assistant instance: some clients derive the namespace they show for a server's tools from that name, so two servers reporting the same name become one and only one instance stays reachable. VS Code, for example, keeps the first 13 characters of the name before using it.

Set **MCP server name** to something distinct per instance when a client connects to several. Keep the names distinct within their first 13 characters if your client shortens them. A single-instance setup can leave it alone.

## Protocol revision

The server speaks MCP 2026-07-28, in which a client sends each request on its own with no handshake, and the four revisions before it, which start with a handshake. A client that knows 2026-07-28 asks for it first and uses it from then on, and one that does not falls back to the handshake.

Turn off **Use the newest MCP protocol revision** if a client that used to work now connects but lists no tools, or fails on every call. With it off, the server answers a 2026-07-28 request the way a server that has never heard of that revision does, so the client falls back to the handshake, which keeps working either way. Reconnect the client after changing the option, since it may remember for a while which revision the server spoke. Turn it back on once the client is updated.
