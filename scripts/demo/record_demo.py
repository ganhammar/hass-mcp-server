"""Record the README demo GIF against a throwaway Home Assistant.

The script sets up a fresh Home Assistant with the made-up home in ``home/``, this
integration and the OIDC Provider, completes onboarding, and then tells one story
in a real browser:

1. Connecting: the illustrated assistant (``assistant.html``) adds the server.
2. Choosing access: Home Assistant's real sign-in and consent screen, driven
   through the provider's real authorization flow.
3. Asking: the assistant redeems the code for a token, calls the MCP server's
   tools with it, and writes the answer from what they return.

Screenshots are taken at twice the size and composed into the GIF with Pillow:
a caption band above each frame, a drawn pointer, crossfades between the scenes,
one shared palette and duplicate frames merged. ``make_demo.sh`` sets up the two
virtualenvs and runs this with the tooling one.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
from playwright.sync_api import Locator, Page, sync_playwright

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# Sizes in CSS pixels. Screenshots are taken at SCALE and scaled down at the end.
WIDTH, HEIGHT, BAND = 900, 560, 40
SCALE = 2

# The assistant lives at a reserved example domain that the browser context serves
# from assistant.html, so the provider redirects back to a page this script owns.
ASSISTANT = "https://assistant.example"
REDIRECT_URI = f"{ASSISTANT}/oauth/callback"
SHOWN_SERVER_URL = "https://your-home-assistant/api/mcp_http"
CLIENT_NAME = "Claude"
QUESTION = "Describe my home"

PERSON = "Sam Lindqvist"
USERNAME = "sam"

# What the consent screen calls each level, by the scope a token for it carries.
LEVEL_TITLES = {
    "mcp:read": "Look only",
    "mcp:control": "Control devices",
    "mcp:configure": "Edit the setup",
    "mcp:admin": "Administer",
}

CAPTIONS = {
    "connect": ("1", "Add Home Assistant to the assistant", "Assistant: illustrated"),
    "signin": ("2", "Sign in to Home Assistant", "Real Home Assistant screen"),
    "consent": ("2", "Home Assistant asks what Claude may do", "Real Home Assistant screen"),
    "ask": ("3", "Ask about your home", "Assistant: illustrated · facts: real tool calls"),
}

ACCENT = (15, 118, 110)


# ---- HTTP ---------------------------------------------------------------------


def http(method, url, *, body=None, form=None, token=None, headers=None):
    """Make a request and return the status, headers and body, whatever the status."""
    sent = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        sent["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        sent["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=sent, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def http_json(method, url, **kwargs):
    status, _, raw = http(method, url, **kwargs)
    if status >= 300:
        raise RuntimeError(f"{method} {url} answered {status}: {raw[:500]!r}")
    return json.loads(raw) if raw else None


# ---- The throwaway Home Assistant -----------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def prepare_config(work: Path, oidc_provider: Path) -> tuple[Path, int]:
    """Build a fresh config directory with the demo home and both integrations."""
    config = work / "config"
    if config.exists():
        shutil.rmtree(config)
    skip = shutil.ignore_patterns("__pycache__")
    shutil.copytree(HERE / "home", config, ignore=skip)
    components = config / "custom_components"
    shutil.copytree(
        REPO / "custom_components/mcp_server_http_transport",
        components / "mcp_server_http_transport",
        ignore=skip,
    )
    shutil.copytree(oidc_provider, components / "oidc_provider", ignore=skip)
    port = free_port()
    (config / "http.yaml").write_text(f"server_host: 127.0.0.1\nserver_port: {port}\n")
    return config, port


def write_storage(config: Path, key: str, version: int, minor: int, data: dict) -> None:
    storage = config / ".storage"
    storage.mkdir(exist_ok=True)
    document = {"version": version, "minor_version": minor, "key": key, "data": data}
    (storage / key).write_text(json.dumps(document, indent=2))


class HomeAssistant:
    """Home Assistant running in a subprocess on the config directory."""

    def __init__(self, python: Path, config: Path, port: int, log: Path) -> None:
        self.python, self.config, self.log = python, config, log
        self.base = f"http://127.0.0.1:{port}"
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        log = self.log.open("a")
        # Isolated and inside the config folder, so the custom_components on the path
        # is the config's, never one in the folder the script was started from.
        self.process = subprocess.Popen(
            [str(self.python), "-I", "-m", "homeassistant", "-c", str(self.config)],
            cwd=self.config,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Home Assistant exited, see {self.log}")
            try:
                if http("GET", f"{self.base}/api/onboarding")[0] < 500:
                    return
            except OSError:
                pass
            time.sleep(1)
        raise RuntimeError(f"Home Assistant did not start, see {self.log}")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(60)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None


def wait_until_running(home: HomeAssistant, token: str) -> None:
    """Wait until Home Assistant has set up everything it loads on start."""
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status, _, raw = http("GET", f"{home.base}/api/config", token=token)
        if status == 200 and json.loads(raw).get("state") == "RUNNING":
            return
        time.sleep(1)
    raise RuntimeError(f"Home Assistant never finished starting, see {home.log}")


def wait_for_description(home: HomeAssistant) -> None:
    """Wait until the provider advertises the access levels this server describes."""
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status, _, raw = http("GET", f"{home.base}/oidc/.well-known/openid-configuration")
        if status == 200 and "mcp:read" in json.loads(raw).get("scopes_supported", []):
            return
        time.sleep(1)
    raise RuntimeError(f"The provider never described the server's access, see {home.log}")


def onboard(base: str, password: str) -> str:
    """Create the owner through onboarding and return an access token for them."""
    client_id = f"{base}/"
    user = http_json(
        "POST",
        f"{base}/api/onboarding/users",
        body={
            "client_id": client_id,
            "name": PERSON,
            "username": USERNAME,
            "password": password,
            "language": "en",
        },
    )
    return http_json(
        "POST",
        f"{base}/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": user["auth_code"],
            "client_id": client_id,
        },
    )["access_token"]


def finish_onboarding(config: Path) -> None:
    """Mark the steps after the owner as done, with Home Assistant stopped.

    The location step would add the integrations onboarding suggests, such as a
    weather service and radio browser, which reach the internet. The location is in
    configuration.yaml and analytics stay off, so nothing is lost.
    """
    steps = ["user", "core_config", "analytics", "integration"]
    write_storage(config, "onboarding", 4, 1, {"done": steps})


def add_integration(base: str, token: str, handler: str, answers: dict) -> None:
    flows = f"{base}/api/config/config_entries/flow"
    step = http_json("POST", flows, token=token, body={"handler": handler})
    if step["type"] == "form":
        step = http_json("POST", f"{flows}/{step['flow_id']}", token=token, body=answers)
    if step["type"] != "create_entry":
        raise RuntimeError(f"Adding {handler} ended with {step}")


# ---- OAuth and MCP ------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class McpClient:
    """A minimal Streamable HTTP client: initialize once, then call tools."""

    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url, token
        self.headers = {"Accept": "application/json, text/event-stream"}
        self.next_id = 0
        result, headers = self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "readme-demo", "version": "1"},
            },
        )
        self.headers["MCP-Protocol-Version"] = result["protocolVersion"]
        if session := headers.get("Mcp-Session-Id"):
            self.headers["Mcp-Session-Id"] = session
        http(
            "POST",
            url,
            body={"jsonrpc": "2.0", "method": "notifications/initialized"},
            token=token,
            headers=self.headers,
        )

    def _rpc(self, method: str, params: dict):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        status, headers, raw = http(
            "POST", self.url, body=message, token=self.token, headers=self.headers
        )
        if status != 200:
            raise RuntimeError(f"{method} answered {status}: {raw[:500]!r}")
        text = raw.decode()
        if headers.get_content_type() == "text/event-stream":
            text = next(
                line[5:]
                for line in text.splitlines()
                if line.startswith("data:") and '"id"' in line
            )
        reply = json.loads(text)
        if "error" in reply:
            raise RuntimeError(f"{method} failed: {reply['error']}")
        return reply["result"], headers

    def call(self, name: str, **arguments):
        result, _ = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"{name} failed: {result['content'][0]['text']}")
        return json.loads(result["content"][0]["text"])


# ---- Frames -------------------------------------------------------------------


@dataclass
class Frame:
    image: Image.Image
    ms: int
    caption: str
    cursor: tuple[float, float] | None
    ring: float | None = None
    fade: bool = False


class Reel:
    """The frames of the story, with the pointer kept apart to be drawn later."""

    def __init__(self) -> None:
        self.frames: list[Frame] = []
        self.caption = "connect"
        self.cursor: tuple[float, float] | None = None
        self.fade_next = False

    def grab(self, page: Page) -> Image.Image:
        return Image.open(io.BytesIO(page.screenshot())).convert("RGB")

    def shot(self, page: Page, ms: int) -> None:
        self.add(self.grab(page), ms)

    def add(self, image: Image.Image, ms: int, ring: float | None = None) -> None:
        self.frames.append(Frame(image, ms, self.caption, self.cursor, ring, self.fade_next))
        self.fade_next = False

    def cut(self, caption: str) -> None:
        """Start a new scene: the next frame fades in under a new caption."""
        self.caption = caption
        self.fade_next = True
        self.cursor = None

    def hold(self, ms: int) -> None:
        self.frames[-1].ms += ms

    def point(self, page: Page, target: Locator, ms: int = 500, ring: bool = True) -> None:
        """Move the pointer onto an element, and show a click ring there."""
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        self.glide(page, (box["x"] + min(box["width"] / 2, 60), box["y"] + box["height"] / 2), ms)
        if ring:
            base = self.frames[-1].image
            self.add(base, 90, ring=0.45)
            self.add(base, 110, ring=1.0)

    def glide(self, page: Page, end: tuple[float, float], ms: int = 500) -> None:
        """Move the pointer to a point over the page as it is now."""
        start = self.cursor or (end[0] + 140, end[1] + 120)
        base = self.grab(page)
        steps = 8
        for step in range(1, steps + 1):
            t = step / steps
            t = t * t * (3 - 2 * t)
            self.cursor = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
            self.add(base, ms // steps)

    def scroll(self, page: Page, distance: int, steps: int = 5) -> None:
        """Scroll the page under the pointer smoothly, a frame per step."""
        for _ in range(steps):
            page.mouse.wheel(0, distance / steps)
            page.wait_for_timeout(60)
            self.shot(page, 50)

    def scroll_to(self, page: Page, target: Locator) -> None:
        """Scroll smoothly until an element is in view with some room below it."""
        box = target.bounding_box()
        overflow = box["y"] + box["height"] + 70 - HEIGHT
        if overflow > 0:
            self.scroll(page, overflow)

    def click(self, page: Page, target: Locator, ms: int = 500) -> None:
        self.point(page, target, ms)
        target.click()


def draw_pointer(canvas: Image.Image, at: tuple[float, float], ring: float | None) -> Image.Image:
    """Draw an arrow pointer, and a click ring around its tip, at screenshot scale."""
    x, y = at
    if ring is not None:
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        radius = (10 + 12 * ring) * SCALE
        ImageDraw.Draw(overlay).ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(*ACCENT, int(70 * (1.2 - ring))),
            outline=(*ACCENT, 200),
            width=2 * SCALE,
        )
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    shape = [(0, 0), (0, 17), (4.3, 13.2), (7.2, 19.6), (9.8, 18.5), (7.0, 12.2), (12.4, 12.2)]
    points = [(x + px * SCALE * 1.1, y + py * SCALE * 1.1) for px, py in shape]
    ImageDraw.Draw(canvas).polygon(points, fill=(20, 24, 28), outline=(255, 255, 255), width=SCALE)
    return canvas


def render_captions(browser) -> dict[str, Image.Image]:
    """Render each caption band once, as HTML, at screenshot scale."""
    context = browser.new_context(
        viewport={"width": WIDTH, "height": BAND}, device_scale_factor=SCALE
    )
    page = context.new_page()
    bands = {}
    for key, (number, text, source) in CAPTIONS.items():
        page.set_content(
            f"""<body style="margin:0;height:{BAND}px;display:flex;align-items:center;gap:10px;
            padding:0 18px;box-sizing:border-box;background:#1f2933;color:#fff;
            font:600 15px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif">
            <span style="display:grid;place-items:center;width:22px;height:22px;border-radius:50%;
            background:#fff;color:#1f2933;font-size:13px">{number}</span>
            <span>{html.escape(text)}</span><span style="flex:1"></span>
            <span style="font-weight:500;font-size:12.5px;color:#c9d2dc">
            {html.escape(source)}</span>
            </body>"""
        )
        bands[key] = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    context.close()
    return bands


def compose(frames: list[Frame], bands: dict[str, Image.Image], out: Path, colors: int) -> dict:
    """Write the GIF and return what it holds."""
    images: list[tuple[Image.Image, int]] = []
    for frame in frames:
        canvas = Image.new("RGB", (WIDTH * SCALE, (HEIGHT + BAND) * SCALE), "white")
        canvas.paste(bands[frame.caption], (0, 0))
        canvas.paste(frame.image, (0, BAND * SCALE))
        if frame.cursor:
            x, y = frame.cursor
            canvas = draw_pointer(canvas, (x * SCALE, (y + BAND) * SCALE), frame.ring)
        small = canvas.resize((WIDTH, HEIGHT + BAND), Image.Resampling.LANCZOS)
        if frame.fade and images:
            # The picture crossfades under the new caption, which shows at once.
            previous, band = images[-1][0], small.crop((0, 0, WIDTH, BAND))
            for alpha in (0.25, 0.5, 0.75):
                blend = Image.blend(previous, small, alpha)
                blend.paste(band, (0, 0))
                images.append((blend, 60))
        images.append((small, frame.ms))

    merged: list[list] = []
    for image, ms in images:
        if merged and ImageChops.difference(merged[-1][0], image).getbbox() is None:
            merged[-1][1] += ms
        else:
            merged.append([image, ms])

    # One palette for every frame, taken from the frames that hold still longest.
    keys = sorted(merged, key=lambda item: -item[1])[:12]
    sample = Image.new("RGB", (WIDTH, (HEIGHT + BAND) * len(keys)))
    for index, (image, _) in enumerate(keys):
        sample.paste(image, (0, index * (HEIGHT + BAND)))
    palette = sample.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    indexed = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image, _ in merged]
    durations = [max(20, round(ms / 10) * 10) for _, ms in merged]
    indexed[0].save(
        out,
        save_all=True,
        append_images=indexed[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    return {"frames": len(indexed), "seconds": sum(durations) / 1000, "size": out.stat().st_size}


# ---- The answer ---------------------------------------------------------------


def lower(name: str) -> str:
    return name[:1].lower() + name[1:]


def join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def describe(found: dict) -> str:
    """Write the answer as HTML from the tool results and nothing else."""
    e = html.escape
    floors = sorted(found["list_floors"], key=lambda floor: floor["level"] or 0)
    areas = found["list_areas"]
    parts = []
    for floor in floors:
        rooms = sorted(lower(a["name"]) for a in areas if a["floor_id"] == floor["floor_id"])
        if rooms:
            parts.append(f"<b>{e(floor['name'])}:</b> {e(join(rooms))}.")
    rooms_line = (
        f"<b>{e(found['get_config']['location_name'])}</b> has {len(areas)} rooms on "
        f"{len(floors)} floors. " + " ".join(parts)
    )

    lights = found["lights"]
    on = [lower(light["friendly_name"]) for light in lights if light["state"] == "on"]
    on_line = f"<b>On right now:</b> {len(on)} of {len(lights)} lights"
    on_line += f" (the {e(join(on))})." if on else "."
    speaker = found["get_state"]
    if speaker["state"] == "playing":
        title = speaker["attributes"].get("media_title")
        on_line += f" The {e(lower(speaker['attributes']['friendly_name']))} is playing"
        on_line += f" “{e(title)}”." if title else "."

    sensors = found["sensors"]
    humidity = {s["area"]: s for s in sensors if s["device_class"] == "humidity"}
    readings = []
    for sensor in sensors:
        if sensor["device_class"] != "temperature":
            continue
        reading = (
            f"{sensor['state']} {sensor['unit_of_measurement']} in the {lower(sensor['area'])}"
        )
        if moist := humidity.get(sensor["area"]):
            reading += f" ({moist['state']} {moist['unit_of_measurement']} humidity)"
        readings.append(reading)
    climate_line = f"<b>Temperature:</b> {e(join(readings))}."

    doors = [d for d in found["doors"] if d["device_class"] == "door"]
    opened = [d["friendly_name"] for d in doors if d["state"] == "on"]
    closed = [lower(d["friendly_name"]) for d in doors if d["state"] == "off"]
    notes = []
    if opened:
        notes.append(f"The {lower(join(opened))} {'is' if len(opened) == 1 else 'are'} open")
        if closed:
            notes[-1] += f", the {join(closed)} {'is' if len(closed) == 1 else 'are'} closed"
    automations = found["automations"]
    enabled = sum(1 for a in automations if a["state"] == "on")
    if enabled == len(automations) == 2:
        notes.append("Both automations are on")
    else:
        notes.append(f"{enabled} of {len(automations)} automations are on")
    # Unknown is what a sensor reports before it has a value, such as backups that
    # have never run; unavailable is a device that is not answering.
    offline = [
        p for p in found["get_system_status"]["problem_entities"] if p["state"] == "unavailable"
    ]
    notes[-1] += ", and no device is unavailable" if not offline else ""
    if offline:
        notes.append(f"{len(offline)} entities are unavailable")
    notes_line = "<b>Worth knowing:</b> " + e(". ".join(notes)) + "."

    return "".join(f"<p>{line}</p>" for line in (rooms_line, on_line, climate_line, notes_line))


# ---- The story ----------------------------------------------------------------


def type_into(reel: Reel, page: Page, script: str, text: str, ms: int = 45) -> None:
    for end in range(2, len(text) + 2, 2):
        page.evaluate(script, text[:end])
        reel.shot(page, ms)


WEBSOCKET_SCRIPT = """async ([url, token, commands]) => {
  const socket = new WebSocket(url);
  const next = () => new Promise((resolve) =>
    socket.addEventListener("message", (event) => resolve(JSON.parse(event.data)), { once: true }));
  await next();
  socket.send(JSON.stringify({ type: "auth", access_token: token }));
  const auth = await next();
  if (auth.type !== "auth_ok") throw new Error(`websocket ${auth.type}`);
  const results = [];
  for (const [index, command] of commands.entries()) {
    socket.send(JSON.stringify({ id: index + 1, ...command }));
    results.push(await next());
  }
  socket.close();
  return results;
}"""


def websocket(browser, base: str, token: str, *commands: dict) -> list:
    """Send commands over Home Assistant's websocket API, from a page on its origin."""
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{base}/auth/providers")
    url = base.replace("http", "ws", 1) + "/api/websocket"
    replies = page.evaluate(WEBSOCKET_SCRIPT, [url, token, list(commands)])
    context.close()
    for command, reply in zip(commands, replies, strict=True):
        if not reply.get("success"):
            raise RuntimeError(f"{command['type']} failed: {reply.get('error')}")
    return [reply.get("result") for reply in replies]


def set_up(browser, home: HomeAssistant, config: Path, password: str) -> None:
    """Onboard, add both integrations, and restart into a tidy instance."""
    home.start()
    token = onboard(home.base, password)
    wait_until_running(home, token)
    add_integration(
        home.base,
        token,
        "oidc_provider",
        {"allow_dynamic_registration": True, "allow_client_metadata_documents": True},
    )
    add_integration(home.base, token, "mcp_server_http_transport", {})
    # Home Assistant moves http.yaml into storage on the first start as a trial, and
    # goes back to its defaults (port 8123 on every interface) unless it is promoted.
    websocket(browser, home.base, token, {"type": "http/config/promote"})

    # The provider collects what the server offers when Home Assistant starts.
    print("Restarting with both integrations set up")
    home.stop()
    finish_onboarding(config)
    home.start()
    wait_until_running(home, token)
    wait_for_description(home)

    # A throwaway instance in a virtualenv is a "Core" install with HTTP settings in
    # YAML, which Repairs reports. Ignoring those keeps a badge out of the picture.
    issues = websocket(browser, home.base, token, {"type": "repairs/list_issues"})[0]["issues"]
    open_issues = [issue for issue in issues if not issue.get("ignored")]
    websocket(
        browser,
        home.base,
        token,
        *(
            {
                "type": "repairs/ignore_issue",
                "domain": issue["domain"],
                "issue_id": issue["issue_id"],
                "ignore": True,
            }
            for issue in open_issues
        ),
    )


def record(browser, home: HomeAssistant, password: str, still: Path | None, work: Path):
    """Tell the story in a fresh browser context and return the reel and the tool calls."""
    base = home.base
    registration = http_json(
        "POST",
        f"{base}/oidc/register",
        body={
            "client_name": CLIENT_NAME,
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
        },
    )
    verifier, challenge = pkce_pair()
    resource = f"{base}/api/mcp_http"
    authorize = f"{base}/oidc/authorize?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT_URI,
            "scope": "openid",
            "state": secrets.token_urlsafe(8),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        }
    )
    assistant_page = (HERE / "assistant.html").read_text()
    callbacks: list[str] = []

    blocked: list[str] = []

    def stay_local(route):
        """Serve the assistant, pass Home Assistant through, and refuse anything else."""
        url = route.request.url
        if url.startswith(f"{ASSISTANT}/"):
            if url.startswith(REDIRECT_URI):
                callbacks.append(url)
            route.fulfill(status=200, content_type="text/html", body=assistant_page)
        elif url.startswith((f"{base}/", base.replace("http", "ws", 1))):
            route.continue_()
        else:
            blocked.append(url)
            route.abort()

    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=SCALE,
        color_scheme="light",
        locale="en-US",
    )
    context.route("**/*", stay_local)
    page = context.new_page()
    reel = Reel()
    try:
        connect(reel, page)
        choose_access(reel, page, authorize, password, still)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(callbacks[-1]).query)["code"][0]
        tokens = http_json(
            "POST",
            f"{base}/oidc/token",
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": registration["client_id"],
                "code_verifier": verifier,
            },
        )
        levels = [LEVEL_TITLES[s] for s in tokens.get("scope", "").split() if s in LEVEL_TITLES]
        if levels != ["Look only"]:
            raise RuntimeError(f"The token carries {tokens.get('scope')!r}, not Look only")
        calls = ask(reel, page, McpClient(resource, tokens["access_token"]), levels[0])
    except Exception:
        page.screenshot(path=str(work / "failure.png"))
        raise
    finally:
        context.close()
    if blocked:
        print(f"Kept {len(blocked)} requests from leaving the machine: {sorted(set(blocked))}")
    return reel, calls


def connect(reel: Reel, page: Page) -> None:
    """Scene 1: the illustrated assistant adds the server."""
    page.goto(f"{ASSISTANT}/")
    reel.shot(page, 900)
    reel.click(page, page.locator("#connectors"))
    page.evaluate("demo.openDialog()")
    reel.shot(page, 450)
    type_into(reel, page, "text => demo.typeUrl(text)", SHOWN_SERVER_URL)
    reel.hold(700)
    reel.click(page, page.locator("#connect"))
    page.evaluate("demo.connecting()")
    reel.shot(page, 700)


def save_still(page: Page, path: Path, width: int = 540) -> None:
    """Save the consent card as Home Assistant shows it, for the documentation.

    The whole column is taken from the page around it, so the card keeps a margin.
    """
    outer, inner = page.locator(".oidc-page"), page.locator(".oidc-content")
    image = Image.open(io.BytesIO(outer.screenshot())).convert("RGB")
    left = (inner.bounding_box()["x"] - outer.bounding_box()["x"] - 16) * SCALE
    right = left + (inner.bounding_box()["width"] + 32) * SCALE
    image = image.crop((max(0, left), 0, min(image.width, right), image.height))
    image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    image.quantize(colors=128, dither=Image.Dither.NONE).save(path, optimize=True)


def choose_access(reel: Reel, page: Page, authorize: str, password: str, still) -> None:
    """Scene 2: Home Assistant's real sign-in and consent screen."""
    page.goto(authorize)
    for name, value in (("username", USERNAME), ("password", password)):
        field = page.locator(f"input[name='{name}']")
        field.click()
        field.fill(value)
    page.locator("h1").first.click()
    page.wait_for_timeout(300)
    reel.cut("signin")
    reel.shot(page, 1000)
    reel.click(page, page.get_by_role("button", name="Log in"))
    approve = page.locator("#oidc-approve")
    approve.wait_for()
    page.wait_for_timeout(700)
    reel.cut("consent")
    reel.shot(page, 1600)
    if still:
        save_still(page, still)
    reel.point(page, page.locator("label.level").first)
    reel.hold(1200)
    reel.click(page, page.locator("#oidc-to-detailed"))
    page.locator("#oidc-back").wait_for()
    page.wait_for_timeout(300)
    reel.shot(page, 1400)
    reel.glide(page, (WIDTH - 60, reel.cursor[1]), 350)
    reel.scroll(page, 330)
    reel.hold(1400)
    reel.scroll(page, -330)
    reel.click(page, page.locator("#oidc-back"))
    approve.wait_for()
    page.wait_for_timeout(300)
    reel.shot(page, 400)
    reel.scroll_to(page, approve)
    reel.hold(300)
    reel.click(page, approve)
    page.wait_for_url(f"{REDIRECT_URI}*")
    page.wait_for_load_state()


def ask(reel: Reel, page: Page, mcp: McpClient, level: str) -> Counter:
    """Scene 3: ask, call the tools for real, and answer from what they return."""
    page.evaluate("level => demo.connected(level)", level)
    reel.cut("ask")
    reel.shot(page, 900)
    reel.click(page, page.locator("#composer"))
    type_into(reel, page, "text => demo.type(text)", QUESTION, ms=60)
    reel.hold(300)
    reel.click(page, page.locator("#send"))
    page.evaluate("text => demo.send(text)", QUESTION)
    reel.cursor = None
    reel.shot(page, 300)

    found: dict = {}
    calls: Counter = Counter()

    def call(key: str, name: str, **arguments):
        calls[name] += 1
        page.evaluate("([name, count]) => demo.tool(name, count)", [name, calls[name]])
        found[key] = mcp.call(name, **arguments)
        reel.shot(page, 200)
        return found[key]

    call("get_config", "get_config")
    call("list_floors", "list_floors")
    call("list_areas", "list_areas")
    call("lights", "search_entities", domain="light")
    call("sensors", "search_entities", domain="sensor")
    call("doors", "search_entities", domain="binary_sensor")
    players = call("players", "search_entities", domain="media_player")
    call("get_state", "get_state", entity_id=players[0]["entity_id"])
    call("automations", "search_entities", domain="automation")
    call("get_system_status", "get_system_status")
    page.evaluate("demo.toolDone()")
    page.evaluate("answer => demo.answer(answer)", describe(found))
    reel.shot(page, 7000)
    return calls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True, help="folder for the instance")
    parser.add_argument("--ha-python", type=Path, required=True, help="Home Assistant's Python")
    parser.add_argument(
        "--oidc-provider", type=Path, required=True, help="custom_components/oidc_provider"
    )
    parser.add_argument("--out", type=Path, default=REPO / "docs/images/demo.gif")
    parser.add_argument("--still", type=Path, help="also save the consent screen as a PNG")
    parser.add_argument("--colors", type=int, default=128)
    args = parser.parse_args()

    args.work.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    config, port = prepare_config(args.work, args.oidc_provider.resolve())
    log = args.work / "home-assistant.log"
    log.write_text("")
    home = HomeAssistant(args.ha_python, config, port, log)
    password = secrets.token_urlsafe(12)
    print(f"Starting Home Assistant on {home.base}, log in {log}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            set_up(browser, home, config, password)
            print("Recording")
            reel, calls = record(browser, home, password, args.still, args.work)
        finally:
            home.stop()
        bands = render_captions(browser)
        browser.close()
    stats = compose(reel.frames, bands, args.out, args.colors)
    print(
        f"Wrote {args.out}: {stats['size'] / 1e6:.2f} MB, {stats['frames']} frames, "
        f"{stats['seconds']:.1f} s, tools called {dict(calls)}"
    )


if __name__ == "__main__":
    main()
