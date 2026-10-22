"""Record a walkthrough of the console as an MP4 with music, in the `fruit` workspace.

Run `make seed-fruit` first, and install the landing page's packages
(`cd landing && npm ci`): the captions and the opening and closing cards are set
in its fonts. The script drives Chrome through a real session - sign in, the
overview, models, the playground, deploy, a workflow run, the inference
explorer, API keys, members, and an agent connecting over MCP - with a visible
cursor and captions drawn in the landing page's style. It then encodes the
recording with ffmpeg (from imageio-ffmpeg) over music from demo_music.py.

    uv run --with playwright --with imageio-ffmpeg --with numpy python scripts/demo_video.py

Writes docs/demo/aperture-demo.mp4. It changes nothing it does not undo: the API
key and the agent's OAuth grant it creates on screen are revoked at the end.
Override BASE / OUT / WORKSPACE / AP_EMAIL / AP_PASSWORD through the environment.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import imageio_ffmpeg
from playwright.sync_api import Locator, Page, Route, sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo_music

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = os.environ.get("BASE", "http://localhost:8080")
OUT = os.environ.get("OUT", os.path.join(ROOT, "docs", "demo", "aperture-demo.mp4"))
WORKSPACE = os.environ.get("WORKSPACE", "fruit")
EMAIL = os.environ.get("AP_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("AP_PASSWORD", "aperture")
MODEL = "fruit-bowl-seg"
MODEL_NAME = "Fruit bowl segmentation"
WORKFLOW_NAME = "Fruit sorter"
SIZE = {"width": 1920, "height": 1080}
SHOWN = {WORKSPACE, "general"}
IMAGE = os.path.join(ROOT, "docs", "screenshots", "landing", "fruit-bowl.jpg")
# where a local MCP client listens for the OAuth answer; the browser never leaves Chrome
CALLBACK = "http://localhost:33418/callback"

TMP = tempfile.mkdtemp(prefix="aperture-demo-")

# the landing page's fonts, served to every page under /__demo/fonts/
FONT_DIR = os.path.join(ROOT, "landing", "node_modules", "@fontsource-variable")
FONTS = {
    "inter-tight": ("Inter Tight Variable", "inter-tight/files/inter-tight-latin-wght-normal.woff2"),
    "instrument-sans": ("Instrument Sans Variable", "instrument-sans/files/instrument-sans-latin-wght-normal.woff2"),
    "geist-mono": ("Geist Mono Variable", "geist-mono/files/geist-mono-latin-wght-normal.woff2"),
}
FONT_CSS = "".join(
    f"@font-face {{ font-family: '{family}'; src: url('/__demo/fonts/{key}.woff2') format('woff2');"
    " font-weight: 100 900; font-display: block; }"
    for key, (family, _) in FONTS.items()
)
# the console's pixel mark, read from its component so the two never drift
with open(os.path.join(ROOT, "frontend", "src", "components", "Mark.tsx")) as source:
    MARK = re.search(r' d="([^"]+)"', source.read())[1]


def call(method: str, path: str, token: str, body: dict | None = None, workspace: str | None = WORKSPACE):
    headers = {"authorization": f"Bearer {token}"}
    if workspace:
        headers["x-aperture-workspace"] = workspace
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


# ---------------------------------------------------------------------- cards

def heptagon(cx: float, cy: float, r: float, rot: float) -> str:
    """The landing page's one shape (landing/src/lib/heptagon.ts)."""
    points = []
    for k in range(7):
        a = math.radians(rot + k * 360 / 7 - 90)
        points.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
    return " ".join(points)


def rings() -> str:
    """The hero's opening: 44 rings in 4 layers, the fourth ring bright (Spiral.tsx)."""
    layers, total, per = 4, 44, 11
    out = []
    for layer in range(layers):
        polys = []
        for i in range(layer * per, min(total, (layer + 1) * per)):
            production = i == 3
            opacity = 1 if production else max(0.04, 0.85 * max(0, 1 - i / 26) ** 1.7)
            polys.append(f'<polygon class="ring{" production" if production else ""}" opacity="{opacity:.3f}"'
                         f' points="{heptagon(960, 540, 350 + i * 17, i * 3)}"/>')
        out.append(f'<g class="layer" style="--l:{layer}">{"".join(polys)}</g>')
    return f'<svg class="rings" viewBox="0 0 1920 1080">{"".join(out)}</svg>'


CARD_CSS = """
:root { --bg: #0b0b0c; --ink: #ededed; --white: #fff; --muted: #9a9a9e; --line-2: #26262a;
  --display: 'Inter Tight Variable', sans-serif; --sans: 'Instrument Sans Variable', sans-serif;
  --mono: 'Geist Mono Variable', ui-monospace, monospace; }
html, body { margin: 0; height: 100%; overflow: hidden; background: var(--bg); color: var(--ink);
  font-family: var(--sans); -webkit-font-smoothing: antialiased; }
body::before { content: ""; position: absolute; inset: 0;
  background: radial-gradient(circle at 50% 50%, #141416 0%, rgba(11, 11, 12, 0) 38%); }
.rings { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.ring { fill: none; stroke: var(--ink); stroke-width: 0.8; }
.ring.production { stroke: var(--white); stroke-width: 1.6; }
.layer { transform-box: view-box; transform-origin: 960px 540px; }
.copy { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 26px; text-align: center; }
.copy > * { opacity: 0; transform: translateY(14px); transition: opacity 900ms ease, transform 900ms ease; }
.go .copy > * { opacity: 1; transform: none; }
.go .copy > :nth-child(2) { transition-delay: 150ms; } .go .copy > :nth-child(3) { transition-delay: 300ms; }
.go .copy > :nth-child(4) { transition-delay: 450ms; } .go .copy > :nth-child(5) { transition-delay: 600ms; }
.label { font-size: 17px; font-weight: 500; color: var(--muted); }
h1 { margin: 0; font-family: var(--display); font-weight: 700; letter-spacing: -0.045em; line-height: 0.98;
  color: var(--white); }
p { margin: 0; max-width: 560px; color: var(--muted); font-size: 21px; line-height: 32px; }
.brand { display: flex; align-items: center; gap: 22px; }
.brand h1 { font-size: 112px; }
.cmd { font-family: var(--mono); font-size: 18px; color: var(--ink); padding: 12px 20px;
  border: 1px solid var(--line-2); border-radius: 999px; background: rgba(17, 17, 19, 0.8); }
.cmd span { color: var(--muted); }

/* the opening: rings settle in, then open outward the way the hero does on scroll */
.intro .layer { opacity: 0; transform: rotate(-12deg) scale(0.86);
  transition: transform 2600ms cubic-bezier(.2, .7, .2, 1), opacity 1600ms ease; }
.intro.go .layer { opacity: 1; transform: none; }
.intro.open .layer { transform: rotate(calc(16deg + var(--l) * 4deg)) scale(calc(1.32 + var(--l) * 0.06));
  opacity: 0; transition: transform 1500ms cubic-bezier(.5, 0, .7, .4), opacity 1500ms ease-in; }
.intro.open .copy > * { opacity: 0; transform: translateY(-80px); transition: opacity 900ms ease-in, transform 1200ms ease-in; }

/* the close: the rings fold back in around the copy, like the landing page's echo */
.outro .layer { opacity: 0; transform: rotate(calc(16deg + var(--l) * 4deg)) scale(calc(1.32 + var(--l) * 0.06));
  transition: transform 3000ms cubic-bezier(.2, .7, .2, 1), opacity 1800ms ease; }
.outro.go .layer { opacity: 1; transform: none; }
.outro h1 { font-size: 88px; }
"""


def card(kind: str) -> str:
    if kind == "intro":
        copy = (f'<span class="label">a registry and gateway for vision models</span>'
                f'<div class="brand"><svg width="104" height="104" viewBox="0 0 59 59" shape-rendering="crispEdges">'
                f'<path fill="#ededed" d="{MARK}"/></svg><h1>Aperture</h1></div>'
                f'<p>A tour of the console on a real fruit-stand workspace: models, workflows, traffic and agents.</p>')
    elif kind == "outro":
        copy = (f'<svg width="64" height="64" viewBox="0 0 59 59" shape-rendering="crispEdges">'
                f'<path fill="#ededed" d="{MARK}"/></svg>'
                '<h1>Many versions.<br>One opening.</h1>'
                '<p>Every model you ship stays in the registry. Apps only see the one tagged production.</p>'
                '<div class="cmd"><span>$</span> git clone https://github.com/fayezzouari/aperture</div>'
                '<span class="label">The full guide is at /docs on the landing site.</span>')
    else:  # what the OAuth callback shows in place of the agent's own page
        copy = (f'<svg width="64" height="64" viewBox="0 0 59 59" shape-rendering="crispEdges">'
                f'<path fill="#ededed" d="{MARK}"/></svg>'
                '<h1 style="font-size:64px">Approved.</h1>'
                '<p>The agent has a token of its own. Back in the terminal, its tools now act as you.</p>')
        kind = "outro"
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{FONT_CSS}{CARD_CSS}</style></head>'
            f'<body class="{kind}">{rings()}<div class="copy">{copy}</div>'
            '<script>document.fonts.ready.then(() => requestAnimationFrame(() => requestAnimationFrame(() =>'
            ' { document.body.classList.add("go"); window.__ready = true; })));</script></body></html>')


def serve_demo(route: Route) -> None:
    path = urllib.parse.urlparse(route.request.url).path
    if path.startswith("/__demo/fonts/"):
        key = path.rsplit("/", 1)[1].removesuffix(".woff2")
        with open(os.path.join(FONT_DIR, FONTS[key][1]), "rb") as font:
            return route.fulfill(body=font.read(), content_type="font/woff2")
    return route.fulfill(body=card(path.rsplit("/", 1)[1]), content_type="text/html")


# -------------------------------------------------------------------- overlay

# A cursor, a click ripple and a caption, drawn in the landing page's style.
# They sit above the page with pointer-events off, so they never get in the way.
OVERLAY_JS = """
(() => {
  if (location.pathname.startsWith('/__demo') || location.port === '33418') return;
  const install = () => {
    if (document.getElementById('demo-cursor')) return;
    const style = document.createElement('style');
    style.textContent = `__FONT_CSS__
      #demo-cursor { position: fixed; z-index: 2147483647; left: 0; top: 0; width: 22px; height: 22px;
        pointer-events: none; transform: translate(-3px, -2px); transition: transform 40ms linear; }
      .demo-ripple { position: fixed; z-index: 2147483646; width: 34px; height: 34px; margin: -17px 0 0 -17px;
        border-radius: 50%; border: 1.5px solid rgba(255,255,255,.9); pointer-events: none;
        animation: demo-ripple 520ms ease-out forwards; }
      @keyframes demo-ripple { from { transform: scale(.3); opacity: 1 } to { transform: scale(1.6); opacity: 0 } }
      #demo-caption { position: fixed; z-index: 2147483645; left: 264px; bottom: 40px; width: 640px;
        padding: 22px 26px 24px; border-radius: 18px; pointer-events: none;
        background: rgba(11,11,12,.9); border: 1px solid #26262a; box-shadow: 0 24px 60px rgba(0,0,0,.55);
        backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
        font-family: 'Instrument Sans Variable', ui-sans-serif, system-ui, sans-serif; color: #ededed;
        -webkit-font-smoothing: antialiased; transition: opacity 260ms ease, transform 260ms ease; }
      #demo-caption.hidden { opacity: 0; transform: translateY(10px); }
      #demo-caption .meta { display: flex; align-items: center; gap: 14px; }
      #demo-caption .step { font: 500 12px 'Geist Mono Variable', ui-monospace, monospace; letter-spacing: .06em;
        color: #9a9a9e; white-space: nowrap; }
      #demo-caption .track { flex: 1; height: 1px; background: #26262a; }
      #demo-caption .track i { display: block; height: 1px; background: #ffffff; transition: width 600ms ease; }
      #demo-caption .title { margin-top: 12px; font-family: 'Inter Tight Variable', ui-sans-serif, sans-serif;
        font-size: 30px; font-weight: 700; letter-spacing: -0.035em; line-height: 1.08; color: #fff; }
      #demo-caption .body { margin-top: 9px; font-size: 16.5px; line-height: 1.55; color: #9a9a9e; }
      #demo-caption .body code { font: 14.5px 'Geist Mono Variable', ui-monospace, monospace; color: #ededed;
        background: #111113; border: 1px solid #26262a; border-radius: 6px; padding: 1px 6px; }
      .demo-secret { filter: blur(7px) !important; }
    `;
    document.documentElement.appendChild(style);
    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.innerHTML = '<svg viewBox="0 0 22 22" width="22" height="22"><path d="M3 2 L3 18 L7.5 13.8 L10.6 20.4 L13.4 19.1 L10.4 12.6 L16.6 12.6 Z" fill="#fff" stroke="#000" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(cursor);
    const caption = document.createElement('div');
    caption.id = 'demo-caption';
    caption.className = 'hidden';
    document.documentElement.appendChild(caption);
    const at = (x, y) => { cursor.style.left = x + 'px'; cursor.style.top = y + 'px'; };
    const last = JSON.parse(sessionStorage.getItem('demo-cursor') || '[960, 540]');
    at(last[0], last[1]);
    window.addEventListener('mousemove', e => { at(e.clientX, e.clientY);
      sessionStorage.setItem('demo-cursor', JSON.stringify([e.clientX, e.clientY])); }, true);
    window.addEventListener('mousedown', e => {
      const r = document.createElement('div'); r.className = 'demo-ripple';
      r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
      document.documentElement.appendChild(r); setTimeout(() => r.remove(), 600);
    }, true);
    window.__caption = (step, title, body, done) => {
      if (!title) { caption.className = 'hidden'; return; }
      caption.innerHTML = `<div class="meta"><span class="step">${step}</span>` +
        `<span class="track"><i style="width:${done}%"></i></span></div><div class="title">${title}</div>` +
        (body ? `<div class="body">${body}</div>` : '');
      caption.className = '';
    };
    const saved = sessionStorage.getItem('demo-caption');
    if (saved) window.__caption(...JSON.parse(saved));
  };
  if (document.documentElement) install(); else document.addEventListener('DOMContentLoaded', install);
  document.addEventListener('DOMContentLoaded', install);
})();
""".replace("__FONT_CSS__", FONT_CSS)


class Demo:
    def __init__(self, page: Page, steps: int):
        self.page = page
        self.steps = steps
        self.step = 0

    def say(self, title: str, body: str = "", hold: float = 0.0) -> None:
        """Show a caption; it stays across navigations until the next one replaces it."""
        self.step += 1
        label = f"{self.step:02d} / {self.steps:02d}"
        args = [label, title, body, round(100 * self.step / self.steps)]
        self.page.evaluate("args => { sessionStorage.setItem('demo-caption', JSON.stringify(args));"
                           " window.__caption && window.__caption(...args); }", args)
        self.wait(1.8 + hold)

    def hide(self) -> None:
        """Clear the caption, so one never lingers onto the next page."""
        # try: the blank tab before the first page has no storage to clear
        self.page.evaluate("() => { try { sessionStorage.removeItem('demo-caption'); } catch (e) {}"
                           " window.__caption && window.__caption(); }")

    def wait(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))

    def point(self, target: Locator, steps: int = 28) -> tuple[float, float]:
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        if box is None:
            raise RuntimeError(f"nothing to point at: {target}")
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(x, y, steps=steps)
        self.wait(0.25)
        return x, y

    def click(self, target: Locator, after: float = 0.8) -> None:
        self.point(target)
        target.click()
        self.wait(after)

    def type(self, target: Locator, text: str) -> None:
        self.click(target, after=0.2)
        target.press_sequentially(text, delay=55)
        self.wait(0.4)

    def nav(self, target: Locator, after: float = 1.0) -> None:
        """Click something that changes page: the old caption goes first."""
        self.hide()
        self.click(target, after=after)

    def go(self, path: str, wait_for: str | None = None, settle: float = 1.0) -> None:
        self.hide()
        self.page.goto(BASE + path)
        self.page.wait_for_load_state("networkidle")
        if wait_for:
            self.page.get_by_text(wait_for).first.wait_for(timeout=20000)
        self.wait(settle)

    def card(self, kind: str) -> None:
        self.page.goto(f"{BASE}/__demo/{kind}")
        self.page.wait_for_function("window.__ready === true", timeout=15000)

    def scroll(self, pixels: int, seconds: float = 1.2) -> None:
        steps = 24
        for _ in range(steps):
            self.page.mouse.wheel(0, pixels / steps)
            self.page.wait_for_timeout(int(seconds * 1000 / steps))
        self.wait(0.4)


def record(video_dir: str) -> tuple[str, float]:
    login = urllib.request.Request(BASE + "/v1/auth/login", headers={"content-type": "application/json"},
                                   data=json.dumps({"email": EMAIL, "password": PASSWORD}).encode())
    token = json.load(urllib.request.urlopen(login))["access_token"]
    # open on the fruit workspace, the way someone who switched to it last would
    call("POST", f"/v1/workspaces/{WORKSPACE}/activate", token)

    with sync_playwright() as p:
        # a window exactly the size of the video, or the screencast can come out short
        # and leave a grey band at the bottom of every frame
        browser = p.chromium.launch(channel="chrome", headless=True,
                                    args=[f"--window-size={SIZE['width']},{SIZE['height']}"])
        context = browser.new_context(viewport=SIZE, device_scale_factor=1, color_scheme="dark",
                                      record_video_dir=video_dir, record_video_size=SIZE)
        context.add_init_script(OVERLAY_JS)
        context.route("**/__demo/**", serve_demo)
        page = context.new_page()

        def only_shown(route: Route) -> None:
            response = route.fetch()
            route.fulfill(response=response, json=[ws for ws in response.json() if ws["slug"] in SHOWN])

        def live_tokens(route: Route) -> None:
            # every token this account ever made, test runs' included; keep the live ones, one per name
            response = route.fetch()
            now = datetime.now(UTC).isoformat()
            seen, keep = set(), []
            for row in response.json():
                if (row["revoked_at"] or (row["expires_at"] and row["expires_at"] < now)
                        or row["name"].startswith("e2e") or row["name"] in seen):
                    continue
                seen.add(row["name"])
                keep.append(row)
            route.fulfill(response=response, json=keep)

        page.route("**/v1/workspaces", only_shown)
        page.route("**/v1/auth/tokens*", live_tokens)
        page.route(CALLBACK + "*", lambda route: route.fulfill(body=card("approved"), content_type="text/html"))
        started = time.monotonic()
        demo = Demo(page, steps=19)

        # ------------------------------------------------------------- opening
        demo.card("intro")
        trim = time.monotonic() - started  # everything before the opening card is a blank tab
        demo.wait(4.6)
        page.evaluate("document.body.classList.add('open')")
        demo.wait(1.5)

        # ------------------------------------------------------------- sign in
        demo.go("/login", settle=0.6)
        demo.say("Sign in", "With a local account here; SSO and LDAP use the same page.")
        demo.type(page.locator("#email"), EMAIL)
        demo.type(page.locator("#password"), PASSWORD)
        demo.click(page.get_by_role("button", name="Sign in"), after=0.2)
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        page.get_by_text("Latency over time").first.wait_for(timeout=20000)
        demo.wait(1.0)

        # ------------------------------------------------------------ overview
        demo.say("The overview", "Requests, error rate, latency and replicas for the active workspace, "
                                 "refreshed every 15 seconds.", hold=1.5)
        demo.point(page.get_by_text("Where the time goes").first)
        demo.wait(1.2)
        demo.scroll(420)
        demo.wait(1.2)
        demo.scroll(-420, 0.8)

        # ---------------------------------------------------------- workspaces
        demo.say("Workspaces", "Each workspace keeps its own models, workflows, keys and members. "
                               "The console opens on the one you used last.")
        demo.click(page.get_by_role("button", name="Active workspace"), after=1.6)
        demo.point(page.get_by_role("button", name="+ Add new workspace"))
        demo.wait(1.2)
        page.keyboard.press("Escape")
        demo.wait(0.6)

        # -------------------------------------------------------------- models
        demo.nav(page.get_by_role("navigation", name="Main").get_by_role("link", name="Models"), after=1.2)
        page.get_by_text(MODEL_NAME).first.wait_for(timeout=15000)
        demo.say("Models", "Every model in the workspace: its tags, live versions, replicas and traffic.")
        demo.click(page.get_by_role("button", name="New model", exact=True).first, after=0.8)
        demo.say("Create a model", "The slug follows the name. It is what products call, so it cannot change later.")
        demo.type(page.get_by_placeholder("Bird species detector"), "Fruit ripeness grader")
        demo.wait(1.4)
        demo.hide()
        page.keyboard.press("Escape")
        demo.wait(0.6)

        demo.nav(page.get_by_role("link", name=MODEL_NAME).first, after=1.2)
        page.get_by_text("Published weights are immutable").wait_for(timeout=15000)
        demo.say("Versions", "YOLOv8n and YOLOv8s, each a set of weights plus its config. Tags such as "
                             "<code>production</code> point at one; products call the tag.", hold=1.0)

        # ---------------------------------------------------------- playground
        demo.nav(page.get_by_role("button", name="Playground", exact=True), after=1.0)
        page.get_by_text("Call it from code").wait_for(timeout=15000)
        demo.say("Try it in the playground", "Runs a version on your own image through a real replica: "
                                             "the same decoder production uses.")
        page.locator("input[type=file][accept='image/*']").set_input_files(IMAGE)
        demo.wait(1.0)
        demo.click(page.get_by_role("button", name="Run inference"), after=0.2)
        page.get_by_text("served by").first.wait_for(timeout=120000)
        demo.wait(1.0)
        demo.say("The result", "Every fruit masked, with the JSON and the latency of each stage, "
                               "and the runtime that served it.", hold=1.5)
        demo.click(page.get_by_role("button", name="JSON", exact=True), after=1.6)
        demo.click(page.get_by_role("button", name="Overlay", exact=True), after=0.8)

        # -------------------------------------------------------------- deploy
        demo.nav(page.get_by_role("button", name="Deploy", exact=True), after=1.0)
        page.get_by_text("How rollback works").wait_for(timeout=15000)
        demo.say("Deploy and roll back", "Replicas warm up before taking traffic. Moving a tag switches traffic "
                                         "on the next request; Roll back moves it back.", hold=1.0)
        demo.click(page.get_by_role("button", name="Deploy a version").first, after=1.4)
        demo.point(page.get_by_text("Autoscale on queue depth"))
        demo.wait(1.4)
        page.keyboard.press("Escape")
        demo.wait(0.6)

        # ------------------------------------------------------------ workflows
        demo.nav(page.get_by_role("navigation", name="Main").get_by_role("link", name="Workflows"), after=1.0)
        demo.click(page.get_by_role("link", name=WORKFLOW_NAME).first, after=0.6)
        page.locator(".react-flow__node").first.wait_for(timeout=15000)
        demo.wait(0.8)
        demo.say("Workflows", "Several models drawn as one graph and served as one endpoint: segment the bowl, "
                              "crop each fruit, then name its variety.", hold=1.0)
        demo.click(page.locator(".react-flow__node").filter(has_text="Segment").first, after=1.4)
        demo.click(page.locator(".react-flow__node").filter(has_text="Crop").first, after=1.4)
        demo.click(page.get_by_role("group", name="Editor mode").get_by_role("button", name="Try it"), after=0.6)
        page.locator("input[type=file][accept='image/*']").set_input_files(IMAGE)
        demo.wait(0.6)
        demo.say("Run the whole graph", "Every node is timed, and you see each image the stages produced.")
        demo.click(page.get_by_role("button", name="Run workflow"), after=0.2)
        page.get_by_role("heading", name="Where the time went").wait_for(timeout=120000)
        demo.wait(2.6)

        # ------------------------------------------------------------ inferences
        demo.nav(page.get_by_role("navigation", name="Main").get_by_role("link", name="Inferences"), after=1.0)
        page.locator("table.data-table tbody tr").first.wait_for(timeout=15000)
        demo.say("Every request, logged", "Filter by model, outcome, latency or confidence. "
                                          "Failures are always captured with their image.")
        demo.click(page.get_by_role("combobox", name="Outcome"), after=0.3)
        page.get_by_role("combobox", name="Outcome").select_option(label="Failed")
        demo.wait(1.4)
        page.get_by_role("combobox", name="Outcome").select_option(label="All outcomes")
        page.get_by_role("combobox", name="Capture").select_option(label="With image")
        page.get_by_role("combobox", name="Outcome").select_option(label="Succeeded")
        page.wait_for_load_state("networkidle")
        demo.wait(0.6)
        demo.click(page.locator("table.data-table tbody tr").first, after=0.4)
        page.get_by_role("dialog").get_by_text("Predictions").first.wait_for(timeout=15000)
        demo.say("What the model saw", "The captured image with its predictions, the request and the raw output. "
                                       "Flag a bad one for retraining.", hold=1.5)
        demo.hide()
        page.keyboard.press("Escape")
        demo.wait(0.6)

        # --------------------------------------------------------------- keys
        demo.nav(page.get_by_role("navigation", name="Main").get_by_role("link", name="API keys"), after=1.0)
        page.get_by_text("Calling the API").wait_for(timeout=15000)
        demo.say("API keys", "One key per product, limited to this workspace's models, with a rate limit and a quota.")
        demo.click(page.get_by_role("button", name="New key").first, after=0.8)
        dialog = page.get_by_role("dialog").filter(has_text="New API key")
        demo.type(dialog.get_by_placeholder("storefront-app"), "storefront-app")
        demo.click(dialog.get_by_role("checkbox", name="Every model"), after=0.6)
        demo.click(dialog.get_by_text(MODEL, exact=True).first, after=0.8)
        page.add_style_tag(content="[role=dialog] pre { filter: blur(7px) !important; }")
        demo.click(dialog.get_by_role("button", name="Create key"), after=0.4)
        once = page.get_by_role("dialog").filter(has_text="Copy this key now")
        once.wait_for(timeout=10000)
        demo.say("Shown once", "Aperture keeps only a hash. Copy it now; a lost key is replaced, never shown again.")
        demo.click(once.get_by_role("button", name="Done"), after=0.6)

        # ------------------------------------------------------------ members
        demo.go("/settings", wait_for="Settings", settle=0.6)
        demo.click(page.get_by_role("button", name="Workspace", exact=True), after=0.6)
        page.get_by_text("Members").first.wait_for(timeout=15000)
        demo.say("Members and roles", "Viewers read, developers ship models, admins manage members and keys. "
                                      "Instance admins can do everything.", hold=1.0)
        demo.point(page.get_by_role("button", name="Add member"))
        demo.wait(1.2)

        # ------------------------------------------------------------ agents
        demo.go("/account/tokens", wait_for="Connect an agent", settle=0.6)
        demo.say("Agents, over MCP", "Every instance serves its API as MCP tools at <code>/mcp</code>: list models, "
                                     "read traffic and failures, run <code>predict</code>. Nothing to install.", hold=1.0)
        demo.point(page.get_by_text("claude mcp add --transport http aperture").first)
        demo.wait(1.6)

        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        client = call("POST", "/v1/oauth/register", token,
                      {"client_name": "Claude Code", "redirect_uris": [CALLBACK]}, workspace=None)
        query = urllib.parse.urlencode({
            "client_id": client["client_id"], "redirect_uri": CALLBACK, "response_type": "code",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "demo",
            "resource": BASE + "/mcp"})
        demo.go("/oauth/authorize?" + query, wait_for="wants to use Aperture", settle=0.6)
        demo.say("Sign in, no token to copy", "The agent opens the console and asks for your approval. "
                                              "Its tools then act as you, within your roles.", hold=1.0)
        answer: list[str] = []
        page.on("request", lambda request: answer.append(request.url) if request.url.startswith(CALLBACK) else None)
        demo.hide()
        demo.click(page.get_by_role("button", name="Approve"), after=0.2)
        page.wait_for_function("window.__ready === true", timeout=15000)
        demo.wait(3.0)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(answer[0]).query)["code"][0]
        form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
                                       "client_id": client["client_id"], "redirect_uri": CALLBACK,
                                       "code_verifier": verifier}).encode()
        urllib.request.urlopen(urllib.request.Request(BASE + "/v1/oauth/token", data=form))

        demo.go("/account/tokens", wait_for="Connect an agent", settle=0.6)
        demo.say("The grant, marked OAuth", "Revoke it here like any token; it lapses after 30 days unused. "
                                            "Claude Desktop gets a one-click extension instead.", hold=1.0)
        demo.point(page.get_by_text("OAuth", exact=True).first)
        demo.wait(1.2)
        demo.point(page.get_by_text("Download aperture.mcpb").first)
        demo.wait(1.6)

        # ---------------------------------------------------------------- end
        demo.hide()
        demo.card("outro")
        demo.wait(7.5)

        video = page.video.path()
        context.close()
        browser.close()

    for key in call("GET", "/v1/keys", token):
        if key["name"] == "storefront-app" and not key["revoked_at"]:
            call("POST", f"/v1/keys/{key['id']}/revoke", token)
    for grant in call("GET", "/v1/auth/tokens", token, workspace=None):
        if grant.get("oauth") and grant["name"] == "Claude Code" and not grant["revoked_at"]:
            call("POST", f"/v1/auth/tokens/{grant['id']}/revoke", token, workspace=None)
    return video, trim


def encode(webm: str, trim: float) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    _, seconds = imageio_ffmpeg.count_frames_and_secs(webm)
    length = max(seconds - trim, 1.0)
    music = os.path.join(TMP, "music.wav")
    demo_music.write(music, length)
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{max(trim, 0):.2f}", "-i", webm, "-i", music,
                    "-filter_complex", "[0:v]fps=30,format=yuv420p[v];[1:a]volume=0.7[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                    "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", OUT], check=True)


if __name__ == "__main__":
    video_dir = os.path.join(TMP, "video")
    webm, trim = record(video_dir)
    encode(webm, trim)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"wrote {os.path.abspath(OUT)}", file=sys.stderr)
