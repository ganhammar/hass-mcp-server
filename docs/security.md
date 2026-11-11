# Security and auditing

[Back to the README](../README.md)

Who may use the server, and how to see what a client did with it. What a client is offered in the first place is covered in [Configuration](configuration.md#limiting-the-tools).

## Authorization

By default only Home Assistant users with administrator rights can use the MCP endpoint, whichever way they authenticate. A valid token for any other user is refused with `403 insufficient_scope`. This mirrors Home Assistant itself, which restricts most of what the tools do to administrators: changing automations, scripts and scenes, calling admin-only services such as restart, rendering templates, and reading the error log. A token whose user has been deleted or deactivated is always refused.

To let non-admin users in, turn off **Require administrator account** (Settings > Devices & Services > MCP Server > Configure). Do this only if you understand that any user who can get a token then has the same access through the tools as an administrator. The one exception is an action a tool calls as the user (see [Who made a change](#who-made-a-change)): Home Assistant checks that against the user's own permissions, so a non-administrator cannot restart Home Assistant or call another admin-only action that way.

## Who made a change

`call_service`, `fire_event` and `restart_ha` call the action or fire the event as the user who called the tool, the way Home Assistant's own app does. Home Assistant records that user as the one behind the change, so the logbook can say who it was, and an automation can read the user ID from the new state's context (`trigger.to_state.context.user_id`).

Other tools change things directly, such as an automation's YAML, a dashboard or the entity registry. Home Assistant does not record a user for those, so the audit log and event below are where to see who made them.

## Audit log

Every tool call is logged with the calling user's ID, how they authenticated (`oidc` or `native`), the app they connected with, the tool, the outcome and its arguments. The app is the client ID the OAuth sign-in was issued to. An app that signs in with a Client ID Metadata Document, as Claude does, is named by that document's address, an `https` URL. A Long-Lived Access Token has no app. The app chooses its own client ID, so it is logged in quotes, with any line break escaped, and cut to 200 characters. The outcome is `ok`, `error` when the tool reports it could not do what was asked (an unknown ID, a refused operation, a missing `confirm`) or failed unexpectedly, or `invalid` for a call rejected before it ran. Values under keys that look like secrets (`password`, `token`, `api_key`, `secret` and similar) are masked, and the arguments are shortened to about 500 characters. Calls to tools that can change something are logged at `info`, and read-only tools at `debug`. Home Assistant's default log level is `warning`, so raise this logger to see them:

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
