# Security and auditing

[Back to the README](../README.md)

Who may use the server, what each application may do, and how to see what a client did with it. What every client is offered in the first place is covered in [Configuration](configuration.md#limiting-the-tools).

## Authorization

By default only Home Assistant users with administrator rights can use the MCP endpoint, whichever way they authenticate. A valid token for any other user is refused with `403 insufficient_scope`. This mirrors Home Assistant itself, which restricts most of what the tools do to administrators: changing automations, scripts and scenes, calling admin-only services such as restart, rendering templates, and reading the error log. A token whose user has been deleted or deactivated is always refused.

To let non-admin users in, turn off **Require administrator account** (Settings > Devices & Services > MCP Server > Configure). Do this only if you understand that any user who can get a token then has the same access through the tools as an administrator. The one exception is an action a tool calls as the user (see [Who made a change](#who-made-a-change)): Home Assistant checks that against the user's own permissions, so a non-administrator cannot restart Home Assistant or call another admin-only action that way. Firing an event is not checked.

## Choosing what an application may do

When an application such as Claude connects through the [OIDC Provider](https://github.com/ganhammar/hass-oidc-server), the consent screen asks what it may do here. Pick one of four levels, or **Choose in detail** to set each of thirteen areas to None, Read or Full. The server then offers the application only what you chose, and refuses the rest. This needs the OIDC Provider release after 1.8.0. With an older provider the consent screen only asks whether to approve, and a connection can use every tool the options allow, as before.

| Level | What it allows |
|---|---|
| Look only | See devices, history and settings, without changing anything. Config files and AppDaemon apps stay closed. This is preselected the first time. |
| Control devices | Also perform actions, and add and delete calendar events. |
| Edit the setup | Also change automations, scripts, scenes, dashboards, helpers and KNX entities, and organize areas, floors, devices, labels and entities. |
| Administer | Also restart Home Assistant and perform system actions, fire events, correct or clear statistics, manage integrations and remove devices, and read and change config files and AppDaemon apps. |

Each level sets every area, and choosing in detail starts from a level:

| Area | Read lets it | Full also lets it | Look only | Control devices | Edit the setup | Administer |
|---|---|---|---|---|---|---|
| Entities and their states | See states, history, the logbook and statistics | Perform actions | Read | Full | Full | Full |
| Calendars | See calendar events | Add and delete events | Read | Full | Full | Full |
| Cameras and images | See camera frames and saved images | (nothing more) | Read | Read | Read | Read |
| Automations, scripts and scenes | See their configuration and traces | Create, change and delete them | Read | Read | Full | Full |
| Dashboards | See how dashboards are set up | Create, change and delete them | Read | Read | Full | Full |
| Helpers | See helpers and their settings | Create, change and delete them | Read | Read | Full | Full |
| Areas, floors, devices and labels | See them, the entity registry and categories | Change them, rename and remove entities, set icons and categories | Read | Read | Full | Full |
| Templates | Render templates | (nothing more) | Read | Read | Read | Read |
| KNX | See telegrams and group addresses | Create and change KNX entities | Read | Read | Full | Full |
| Diagnostics | See the version and location, system status and health, repairs, the list of backups and the error log, and check the configuration | Restart, perform system actions, fire events, correct or clear statistics | Read | Read | Read | Full |
| Integrations | See which integrations are set up | Add, configure, turn off and remove integrations, remove devices | Read | Read | Read | Full |
| Config files | Read YAML files, never `secrets.yaml` | Change, delete and restore them | None | None | None | Full |
| AppDaemon apps | Read the apps' code and settings | Change, delete and restore them | None | None | None | Full |

The [tool reference](tools.md) gives the area of every tool, resource and prompt.

### What still applies

- **The options come first.** Read-only mode, the disabled tools and the opt-in groups limit every connection as before. A choice on the consent screen never turns on something they keep off. An area whose option is off shows as not available on the consent screen and can still be chosen; the application gets it once you turn the option on.
- **In read-only mode** the consent screen offers no area above Read, and only Look only among the levels.
- **Administrators only**, unless you turned that off, and Home Assistant still checks the actions a tool performs as the user against that user's permissions.
- **Long-lived access tokens are not affected.** They carry no choice and are limited only by the options.

### Actions that reach the system

Performing actions needs Entities and their states at Full. Two kinds of action need Diagnostics at Full as well, the area that holds restart:

- **Actions Home Assistant lets only administrators perform**, such as `zha.permit`, `file.read_file` or `downloader.download_file`. Home Assistant's own check does not help here, since the user who connected the application is an administrator, so the server looks at how each action was registered.
- **System actions**, which reach Home Assistant itself or a whole bus rather than a device: every action of the `homeassistant` domain except `turn_on`, `turn_off`, `toggle` and `update_entity`; every action of `hassio`, `shell_command`, `python_script`, `pyscript`, `recorder`, `backup`, `update`, `cloud`, `system_log`, `logger`, `profiler`, `debugpy`, `mqtt` and `synology_dsm`; `homematic.set_install_mode`; and the `reload` action of every domain. Some of these Home Assistant does not reserve for administrators at all, and some only from a later version than the oldest this server supports.

Below that, the assistant is told which access the action needs. Every other action an integration offers is allowed at Entities and their states at Full, and what it does is not looked into: `modbus.write_register` writes any register, for example.

### What the choice does not cover

Be clear about these before you rely on a lower level:

- **A script or automation does whatever is written in it.** The filter checks the action the application performs, not what a script does once it runs. An application that may perform actions can run your existing scripts, and one that may change automations and scripts can write one that performs any action, restarting Home Assistant included, whatever the other areas say.
- **Other actions are not looked into.** Below Diagnostics at Full the filter holds back the actions Home Assistant reserves for administrators and the system actions listed above. Any other action an integration offers is allowed with Entities and their states at Full, whatever it does.
- **A camera snapshot can be made public.** An application that may perform actions can have `camera.snapshot` write an image under `www/` in your configuration folder, and Home Assistant serves everything there at `/local/` without sign-in.
- **None hides an area's tools, not the things in it.** With Cameras and images at None, your cameras still show up as entities, and with Entities and their states at Full an action can still take a snapshot. Keeping an application away from particular entities is not part of this.
- **Templates can read every state**, whatever Entities and their states is set to.
- **Config files and AppDaemon apps reach past the other areas.** With Config files at Full an application can change any YAML file, such as a dashboard kept in YAML. AppDaemon apps are code, and a changed app can do anything AppDaemon can do.
- **A listing can name things from another area**, such as the area an entity is in, or the entities that are unavailable in the system status.
- **The choice holds only while the OIDC Provider has this server's description.** The provider collects it when Home Assistant starts, and logs a warning when that fails or times out. For the rest of that run the consent screen only asks whether to approve, and a token that carries no choice of this server's gets what the options allow. That includes a token from an earlier run whose user set every area to None, since such a token carries no choice either. An application cannot bring this about: whether tokens are limited depends only on what the provider has collected, never on the address or headers of a request.

### Connections from before

When you update, a connection made before keeps what it could do, which is Administer, still limited by the options. The next time the application renews its sign-in, which happens within the hour, the OIDC Provider records that, and Connected applications shows it. To choose for an existing connection, open Connected applications (`https://your-home-assistant/oidc_login`) and use Change access. The change applies the next time the application renews its sign-in, which happens within the hour.

### What an application sees when it is refused

- **A tool, resource or prompt outside its access** is left out of the lists it gets. Called anyway, it is refused with a message saying that the access granted to the application does not include it, which access it needs, and how to change it. The refused call is logged and fires the audit event, as a call to a disabled tool does.
- **A system action, or one Home Assistant reserves for administrators,** without Diagnostics at Full comes back as a failed `call_service` with the same kind of message.
- **A token that grants nothing here**, because every area was set to None or the connection predates the choice, is answered with `401` and `error="invalid_token"`. The application renews its token; a connection from before gets Administer that way, and one that still has nothing goes back to the consent screen. This does not count as a failed login for Home Assistant's IP ban.

## Who made a change

`call_service`, `fire_event` and `restart_ha` call the action or fire the event as the user who called the tool, the way Home Assistant's own app does. Home Assistant records that user as the one behind the change, so the logbook can say who it was, and an automation can read the user ID from the new state's context (`trigger.to_state.context.user_id`).

Other tools change things directly, such as an automation's YAML, a dashboard or the entity registry. Home Assistant does not record a user for those, so the audit log and event below are where to see who made them.

## Audit log

Every tool call is logged with the calling user's ID, how they authenticated (`oidc` or `native`), the app they connected with, the tool, the outcome and its arguments. The app is the client ID the OAuth sign-in was issued to. An app that signs in with a Client ID Metadata Document, as Claude does, is named by that document's address, an `https` URL. A Long-Lived Access Token has no app. The app chooses its own client ID, so it is logged in quotes, with a newline in it escaped, and cut to 200 characters. Naming every app needs OIDC Provider 1.8 or newer. Versions from 1.5 name an app that asks for a token for this server specifically, as Claude does, and older versions name none. The outcome is `ok`, `error` when the tool reports it could not do what was asked (an unknown ID, a refused operation, a missing `confirm`) or failed unexpectedly, or `invalid` for a call rejected before it ran. Values under keys that look like secrets (`password`, `token`, `api_key`, `secret` and similar) are masked, and the arguments are shortened to about 500 characters. Calls to tools that can change something are logged at `info`, and read-only tools at `debug`. Home Assistant's default log level is `warning`, so raise this logger to see them:

```yaml
logger:
  logs:
    custom_components.mcp_server_http_transport.http: info
```

A call through an app, and one with a Long-Lived Access Token:

```text
MCP tool call by user 4085008599514f948d7a206e18b780ed (oidc auth, client "https://app.example.com/oauth/client.json"): call_service -> ok, arguments {"domain":"light","service":"turn_on","entity_id":"light.kitchen"}
MCP tool call by user 4085008599514f948d7a206e18b780ed (native auth): restart_ha -> error, arguments {"confirm":false}
```

Tool calls that can change something also fire a `mcp_server_http_transport_tool_called` event, with `tool`, `user_id`, `auth_method`, `client_id`, `outcome` and the masked `arguments` as its data. `client_id` is the app as above, and null for a Long-Lived Access Token. The event's context carries the calling user's ID as well. For a tool that acts as the user, it is the same context the action ran under, so the event and the state changes the call caused share a context ID. Read-only tools fire no event, so they do not fill the recorder database. To act on the event, for example to be notified whenever a tool changes something:

```yaml
triggers:
  - trigger: event
    event_type: mcp_server_http_transport_tool_called
actions:
  - action: notify.notify
    data:
      message: "MCP: {{ trigger.event.data.tool }} ({{ trigger.event.data.outcome }})"
```
