# Security and auditing

[Back to the README](../README.md)

Who may use the server, and how to see what a client did with it. What a client is offered in the first place is covered in [Configuration](configuration.md#limiting-the-tools).

## Authorization

By default only Home Assistant users with administrator rights can use the MCP endpoint, whichever way they authenticate. A valid token for any other user is refused with `403 insufficient_scope`. This mirrors Home Assistant itself, which restricts most of what the tools do to administrators: changing automations, scripts and scenes, calling admin-only services such as restart, rendering templates, and reading the error log. A token whose user has been deleted or deactivated is always refused.

To let non-admin users in, turn off **Require administrator account** (Settings > Devices & Services > MCP Server > Configure). Do this only if you understand that any user who can get a token then has the same access through the tools as an administrator.

## Audit log

Every tool call is logged with the calling user's ID, how they authenticated (`oidc` or `native`), the tool, the outcome and its arguments. The outcome is `ok`, `error` when the tool reports it could not do what was asked (an unknown ID, a refused operation, a missing `confirm`) or failed unexpectedly, or `invalid` for a call rejected before it ran. Values under keys that look like secrets (`password`, `token`, `api_key`, `secret` and similar) are masked, and the arguments are shortened to about 500 characters. Calls to tools that can change something are logged at `info`, and read-only tools at `debug`. Home Assistant's default log level is `warning`, so raise this logger to see them:

```yaml
logger:
  logs:
    custom_components.mcp_server_http_transport.http: info
```

Tool calls that can change something also fire a `mcp_server_http_transport_tool_called` event, with `tool`, `user_id`, `auth_method`, `outcome` and the masked `arguments` as its data. The event's context carries the calling user's ID as well. Read-only tools fire no event, so they do not fill the recorder database. To act on the event, for example to be notified whenever a tool changes something:

```yaml
triggers:
  - trigger: event
    event_type: mcp_server_http_transport_tool_called
actions:
  - action: notify.notify
    data:
      message: "MCP: {{ trigger.event.data.tool }} ({{ trigger.event.data.outcome }})"
```
