# The README demo

`make_demo.sh` regenerates `docs/images/demo.gif`, the demo at the top of the README, and `docs/images/consent.png`, the consent screen in `docs/connecting.md`. Run it again whenever the consent screen, the tools or the answer they give change.

```sh
scripts/demo/make_demo.sh
```

It needs [uv](https://docs.astral.sh/uv/) and git, and nothing else from the project: it does not use the project's Poetry environment and is not part of CI.

## What it does

1. Sets up a work folder, `~/.cache/hass-mcp-server-demo` unless `DEMO_WORK` names another, with:
   - `ha-venv`: Home Assistant at the version in `poetry.lock`, and the `home-assistant-frontend` that version pins.
   - `tools-venv`: Playwright and Pillow, at the versions at the top of `make_demo.sh`. Home Assistant pins its own Pillow, so the two cannot share a venv.
   - `browsers`: Playwright's headless Chromium.
   - `oidc`: the OIDC Provider from the `main` branch of [hass-oidc-server](https://github.com/ganhammar/hass-oidc-server), cloned shallow. With `OIDC_CHECKOUT=/path/to/hass-oidc-server` it is taken from that checkout's `origin/main` with `git archive` instead, without touching its branch. `OIDC_REF` picks another branch.
2. Runs `record_demo.py`, which builds a throwaway Home Assistant in `run/config` from `home/` (a made-up home: two floors, five rooms, lights, temperature and humidity sensors, two door sensors, a speaker, two automations), this checkout's integration and the provider. It listens on 127.0.0.1 only, on a free port.
3. Creates the owner through onboarding, adds both integrations, restarts, and records the story in a real browser:
   - **Connecting.** `assistant.html` is a plain, illustrated chat window, labelled as an illustration. The address typed into it is the one the README gives, not the local one.
   - **Choosing access.** Home Assistant's real sign-in page and the provider's real consent screen, reached through the provider's authorization flow: the script registers a client with a made-up name, Wren, whose redirect address is `https://assistant.example/oauth/callback`, which the browser serves from `assistant.html` and never sends anywhere. The browser refuses every request that is not to the local instance.
   - **Asking.** The script redeems the code for a token, checks that it carries Look only, calls the MCP server's tools with it, and writes the answer from what they return. The tool names shown are the calls it made.
4. Composes the GIF with Pillow: screenshots at twice the size scaled down to 900 pixels, a caption band, a drawn pointer, crossfades, one shared palette of 128 colours, and repeated frames merged.

The first run downloads about 1.4 GB into the work folder and takes a few minutes. After that it takes about half a minute. Arguments after `make_demo.sh` go to `record_demo.py`, such as `--colors 96` for a smaller file. Home Assistant's log is at `run/home-assistant.log` in the work folder, and a failure leaves a screenshot of the page at `run/failure.png`.

## When the screens change

- The selectors for the sign-in and consent screens are in `choose_access` in `record_demo.py`: the login form's `username` and `password` inputs and its Log in button, and the panel's `label.level`, `#oidc-to-detailed`, `#oidc-back` and `#oidc-approve`.
- The answer is written by `describe`, from the tool results only. A tool whose output changes shape breaks it with a `KeyError` rather than a wrong sentence.
- The made-up home is in `home/custom_components/demo_home/__init__.py`.
