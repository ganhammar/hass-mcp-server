# FAQ

[Back to the README](../README.md)

## Getting started

### What is MCP?

The Model Context Protocol is a standard way for an AI assistant to use outside tools. This integration is an MCP server: it gives the assistant a set of tools for your Home Assistant, and the assistant decides which to use from what you ask.

### Which assistants can I use?

Any MCP client that supports streamable HTTP with either OAuth or a bearer token. Claude on the web is the one this was built for. See [Connecting a client](connecting.md).

### Does my Home Assistant have to be reachable from the internet?

For Claude on the web, yes, over HTTPS: the connection comes from Claude's servers, not from your browser. A client that runs on your own network can use the local address with a long-lived access token.

### Do I need the OIDC provider?

Only for OAuth, which browser-based clients such as Claude require. With long-lived access tokens alone you can skip [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server).

### How is this different from Home Assistant's built-in MCP Server?

The built-in one gives an assistant what Home Assistant's Assist has: the entities you have exposed to voice assistants and the actions Assist can take. This one is for administering the instance as well: automations, dashboards, the registries, integrations, traces and diagnostics. They can be installed side by side, see [Troubleshooting](troubleshooting.md#another-integration-also-serves-apimcp).

## Safety

### What can the assistant do to my home?

Out of the box: read states and history, call services, and manage automations, scripts, scenes, dashboards, helpers, areas, devices and labels. Config files, cameras, templates, AppDaemon files and integration management are off until you enable them. Some of it cannot be undone, such as `clear_statistics`, the delete tools and `restart_ha`: switch those off if you would rather it could not. The [full list](tools.md) shows every tool.

### Can I let it look but not touch?

Yes, two ways. When an application connects through the OIDC Provider, choose **Look only** on the consent screen and that connection can only read. To hold every connection to reading, long-lived access tokens included, turn on **Read-only mode**. You can also switch off single tools. See [Choosing what an application may do](security.md#choosing-what-an-application-may-do) and [Configuration](configuration.md#limiting-the-tools).

### Can I give one application less than another?

Yes. The consent screen asks each time an application connects, so one can control devices while another only looks. **Choose in detail** sets each area separately, such as dashboards read-only and no cameras. A level below Administer is not a sandbox, though: an application that may change automations and scripts can write one that does anything. [What the choice does not cover](security.md#what-the-choice-does-not-cover) lists the limits.

### What changes for my existing connection when I update?

Nothing you would notice: a connection made before keeps what it could do, which is Administer, still limited by the options. To choose a level for it, sign it out under Connected applications (`https://your-home-assistant/oidc_login`) and connect it again. This needs the OIDC Provider release after 1.8.0.

### Can I see what it did?

Yes. Every call that changes something is logged and fires an event. See [Security and auditing](security.md#audit-log).

### Where does my data go?

The integration sends nothing anywhere on its own. What the assistant reads through the tools goes to the client you connected and the AI service behind it, so what that service does with it is governed by its terms, not by this integration.

### Can other people in my household use it?

Only administrators can connect by default. You can lift that, but then any user with a token has the same reach through the tools as an administrator, apart from the actions `call_service` and `restart_ha` call as that user, which Home Assistant checks against their own permissions. See [Security and auditing](security.md#authorization).

## Using it

### I enabled an option but the tools do not show up.

Clients read the tool list when they connect. Disconnect and reconnect the client.

### The assistant says a tool is disabled.

The tool belongs to a group that is off. The message names the option, and [Configuration](configuration.md) describes each one.

### The assistant says the access granted to it does not include a tool.

You chose a level or area settings without it when the application connected. The message names the area and setting the tool needs. To change it, sign the application out under Connected applications (`https://your-home-assistant/oidc_login`) and connect it again.

### Can it edit my YAML configuration?

Yes, with **Enable config file access** on. Every change is preceded by a backup of the top-level files and followed by a config check. Files in subfolders are not backed up. See [Config files](configuration.md#config-files).

### Can it see my cameras?

Yes, with **Enable camera image access** on. See [Cameras and images](configuration.md#cameras-and-images).

### How do I find everything that uses an entity before I rename it?

Ask. The assistant uses `search_config`, which searches automations, scripts, scenes and dashboards for the entity ID and returns where each reference is.

### Some tools are called experimental. What does that mean?

They rely on parts of Home Assistant that have no stable API, so an update can break them. See [the tool reference](tools.md).

### Which Home Assistant versions are supported?

2025.6 and newer.
