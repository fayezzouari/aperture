"""Record a narrated walkthrough of the console as an MP4, in the `e2e` workspace.

Run `make e2e` first. The script drives Chrome through a real session - sign in,
switch workspace, models, the playground, deploy, a workflow run, the inference
explorer, API keys and members - with a visible cursor and on-screen captions,
then encodes the recording with ffmpeg (from imageio-ffmpeg).

    uv run --with playwright --with imageio-ffmpeg --with numpy --with pillow --with onnx \\
        python scripts/demo_video.py

Writes docs/demo/aperture-demo.mp4. It changes nothing it does not undo: the one
API key it creates on screen is revoked at the end. Override BASE / OUT /
WORKSPACE / AP_EMAIL / AP_PASSWORD through the environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import imageio_ffmpeg
from playwright.sync_api import Locator, Page, sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_onnx import sample_image  # noqa: E402

BASE = os.environ.get("BASE", "http://localhost:8080")
OUT = os.environ.get("OUT", os.path.join(os.path.dirname(__file__), "..", "docs", "demo", "aperture-demo.mp4"))
WORKSPACE = os.environ.get("WORKSPACE", "e2e")
EMAIL = os.environ.get("AP_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("AP_PASSWORD", "aperture")
MODEL = "e2e-orchard"
WORKFLOW = "e2e-orchard-pipeline"
SIZE = {"width": 1920, "height": 1080}
SHOWN = {WORKSPACE, "general"}

TMP = tempfile.mkdtemp(prefix="aperture-demo-")
IMAGE = sample_image(os.path.join(TMP, "orchard.jpg"))


def call(method: str, path: str, token: str, body: dict | None = None):
    headers = {"authorization": f"Bearer {token}", "x-aperture-workspace": WORKSPACE}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


# A cursor, a click ripple and a caption card, drawn in the console's own style.
# They sit above the page with pointer-events off, so they never get in the way.
OVERLAY_JS = """
(() => {
  const install = () => {
    if (document.getElementById('demo-cursor')) return;
    const style = document.createElement('style');
    style.textContent = `
      #demo-cursor { position: fixed; z-index: 2147483647; left: 0; top: 0; width: 22px; height: 22px;
        pointer-events: none; transform: translate(-3px, -2px); transition: transform 40ms linear; }
      .demo-ripple { position: fixed; z-index: 2147483646; width: 34px; height: 34px; margin: -17px 0 0 -17px;
        border-radius: 50%; border: 2px solid #0072f5; pointer-events: none;
        animation: demo-ripple 520ms ease-out forwards; }
      @keyframes demo-ripple { from { transform: scale(.3); opacity: 1 } to { transform: scale(1.6); opacity: 0 } }
      #demo-caption { position: fixed; z-index: 2147483645; left: 50%; bottom: 28px; transform: translateX(-50%);
        min-width: 560px; max-width: 980px; padding: 14px 20px 15px; border-radius: 12px; pointer-events: none;
        background: rgba(10,10,10,.94); border: 1px solid #2e2e33; box-shadow: 0 18px 50px rgba(0,0,0,.6);
        font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #ededed;
        transition: opacity 240ms ease, transform 240ms ease; }
      #demo-caption.hidden { opacity: 0; transform: translate(-50%, 8px); }
      #demo-caption .step { font: 500 11px ui-monospace, "SF Mono", Menlo, monospace; letter-spacing: .08em;
        color: #6e6e73; text-transform: uppercase; }
      #demo-caption .title { margin-top: 3px; font-size: 19px; font-weight: 600; letter-spacing: -.01em; }
      #demo-caption .body { margin-top: 4px; font-size: 14px; line-height: 1.5; color: #a1a1a6; }
      #demo-caption .body code { font: 13px ui-monospace, Menlo, monospace; color: #ededed; background: #111113;
        border: 1px solid #2e2e33; border-radius: 5px; padding: 0 5px; }
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
    window.__caption = (step, title, body) => {
      if (!title) { caption.className = 'hidden'; return; }
      caption.innerHTML = `<div class="step">${step}</div><div class="title">${title}</div>` +
        (body ? `<div class="body">${body}</div>` : '');
      caption.className = '';
    };
    const saved = sessionStorage.getItem('demo-caption');
    if (saved) window.__caption(...JSON.parse(saved));
  };
  if (document.documentElement) install(); else document.addEventListener('DOMContentLoaded', install);
  document.addEventListener('DOMContentLoaded', install);
})();
"""


class Demo:
    def __init__(self, page: Page, steps: int):
        self.page = page
        self.steps = steps
        self.step = 0

    def say(self, title: str, body: str = "", hold: float = 0.0) -> None:
        """Show a caption; it stays across navigations until the next one replaces it."""
        self.step += 1
        label = f"Step {self.step:02d} / {self.steps:02d}"
        args = [label, title, body]
        self.page.evaluate("args => { sessionStorage.setItem('demo-caption', JSON.stringify(args));"
                           " window.__caption && window.__caption(...args); }", args)
        self.wait(1.6 + hold)

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
    # open on the e2e workspace, the way someone who switched to it last would
    call("POST", f"/v1/workspaces/{WORKSPACE}/activate", token)

    with sync_playwright() as p:
        # a window exactly the size of the video, or the screencast can come out short
        # and leave a grey band at the bottom of every frame
        browser = p.chromium.launch(channel="chrome", headless=True,
                                    args=[f"--window-size={SIZE['width']},{SIZE['height']}"])
        context = browser.new_context(viewport=SIZE, device_scale_factor=1, color_scheme="dark",
                                      record_video_dir=video_dir, record_video_size=SIZE)
        context.add_init_script(OVERLAY_JS)
        page = context.new_page()

        def only_shown(route):
            response = route.fetch()
            route.fulfill(response=response, json=[ws for ws in response.json() if ws["slug"] in SHOWN])

        page.route("**/v1/workspaces", only_shown)
        started = time.monotonic()
        demo = Demo(page, steps=17)

        # ------------------------------------------------------------- sign in
        demo.go("/login", settle=0.6)
        trim = time.monotonic() - started  # everything before the sign-in page is a blank tab
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
        page.get_by_text("Orchard counter").first.wait_for(timeout=15000)
        demo.say("Models", "Every model in the workspace: its tags, live versions, replicas and traffic.")
        demo.click(page.get_by_role("button", name="New model", exact=True).first, after=0.8)
        demo.say("Create a model", "The slug follows the name. It is what products call, so it cannot change later.")
        demo.type(page.get_by_placeholder("Bird species detector"), "Bird species detector")
        demo.wait(1.4)
        demo.hide()
        page.keyboard.press("Escape")
        demo.wait(0.6)

        demo.nav(page.get_by_role("link", name="Orchard counter").first, after=1.2)
        page.get_by_text("Published weights are immutable").wait_for(timeout=15000)
        demo.say("Versions", "Each version is a set of weights plus its config. Tags such as "
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
        demo.say("The result", "The overlay, the JSON, and the latency of every stage with the runtime that served it.",
                 hold=1.5)
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
        demo.click(page.get_by_role("link", name="Orchard pipeline").first, after=0.6)
        page.locator(".react-flow__node").first.wait_for(timeout=15000)
        demo.wait(0.8)
        demo.say("Workflows", "Several models drawn as one graph and served as one endpoint: detect, crop to "
                              "each detection, then grade every crop.", hold=1.0)
        demo.click(page.locator(".react-flow__node").filter(has_text="Detect").first, after=1.4)
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

        # ---------------------------------------------------------------- end
        demo.go("/", wait_for="Latency over time", settle=0.4)
        demo.say("That is the loop", "Upload a version, try it, deploy it, move the tag, watch it. "
                                     "The full guide lives at <code>/docs/</code> on the landing site.", hold=2.5)

        video = page.video.path()
        context.close()
        browser.close()

    for key in call("GET", "/v1/keys", token):
        if key["name"] == "storefront-app" and not key["revoked_at"]:
            call("POST", f"/v1/keys/{key['id']}/revoke", token)
    return video, trim


def encode(webm: str, trim: float) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{max(trim, 0):.2f}", "-i", webm,
                    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                    "-movflags", "+faststart", "-an", OUT], check=True)


if __name__ == "__main__":
    video_dir = os.path.join(TMP, "video")
    webm, trim = record(video_dir)
    encode(webm, trim)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"wrote {os.path.abspath(OUT)}", file=sys.stderr)
