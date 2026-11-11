#!/bin/sh
# Regenerate docs/images/demo.gif and docs/images/consent.png. See README.md here.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
WORK=${DEMO_WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/hass-mcp-server-demo}
OIDC_REPO=${OIDC_REPO:-https://github.com/ganhammar/hass-oidc-server.git}
OIDC_REF=${OIDC_REF:-main}
PLAYWRIGHT_VERSION=1.65.0
PILLOW_VERSION=13.0.0

command -v uv >/dev/null || { echo "make_demo.sh needs uv: https://docs.astral.sh/uv/" >&2; exit 1; }
mkdir -p "$WORK"

# Home Assistant at the version the project's tests run against, with the frontend
# that version pins. Home Assistant pins Pillow too, so the recorder gets its own venv.
HA_VERSION=$(awk '/^name = "homeassistant"$/ { getline; print }' "$REPO/poetry.lock" | cut -d'"' -f2)
[ -x "$WORK/ha-venv/bin/python" ] || uv venv --quiet --python 3.14 "$WORK/ha-venv"
uv pip install --quiet --python "$WORK/ha-venv/bin/python" "homeassistant==$HA_VERSION"
FRONTEND=$("$WORK/ha-venv/bin/python" -I -c '
import json, pathlib, homeassistant
manifest = pathlib.Path(homeassistant.__file__).parent / "components/frontend/manifest.json"
print(next(r for r in json.loads(manifest.read_text())["requirements"] if r.startswith("home-assistant-frontend==")))
')
uv pip install --quiet --python "$WORK/ha-venv/bin/python" "$FRONTEND"

[ -x "$WORK/tools-venv/bin/python" ] || uv venv --quiet --python 3.14 "$WORK/tools-venv"
uv pip install --quiet --python "$WORK/tools-venv/bin/python" \
  "playwright==$PLAYWRIGHT_VERSION" "pillow==$PILLOW_VERSION"
PLAYWRIGHT_BROWSERS_PATH="$WORK/browsers" "$WORK/tools-venv/bin/playwright" install --only-shell chromium

# The OIDC Provider, from a local checkout when OIDC_CHECKOUT names one, else from GitHub.
rm -rf "$WORK/oidc"
mkdir -p "$WORK/oidc"
if [ -n "${OIDC_CHECKOUT:-}" ]; then
  git -C "$OIDC_CHECKOUT" archive "origin/$OIDC_REF" custom_components/oidc_provider |
    tar -x -C "$WORK/oidc"
else
  git clone --quiet --depth 1 --branch "$OIDC_REF" "$OIDC_REPO" "$WORK/oidc/source"
  mv "$WORK/oidc/source/custom_components" "$WORK/oidc/custom_components"
  rm -rf "$WORK/oidc/source"
fi

echo "Home Assistant $HA_VERSION with $FRONTEND, work folder $WORK"
PLAYWRIGHT_BROWSERS_PATH="$WORK/browsers" "$WORK/tools-venv/bin/python" -I "$HERE/record_demo.py" \
  --work "$WORK/run" \
  --ha-python "$WORK/ha-venv/bin/python" \
  --oidc-provider "$WORK/oidc/custom_components/oidc_provider" \
  --out "$REPO/docs/images/demo.gif" \
  --still "$REPO/docs/images/consent.png" \
  "$@"
