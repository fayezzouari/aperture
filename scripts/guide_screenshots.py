"""Screenshots for the user guide (landing/docs), taken in the `e2e` workspace.

Run `make e2e` first: it builds the `e2e` workspace with a deployed detector, a
second model, a published two-stage workflow and some traffic. This script then
drives Chrome through every page and dialog the guide walks through and writes
one image per step to docs/screenshots/guide/.

    uv run --with playwright python scripts/guide_screenshots.py

Uses the locally installed Google Chrome (channel="chrome"). Images are 1440x900
viewports at 2x, converted to WebP with `cwebp` when it is on the PATH (PNG
otherwise). Names of models outside the e2e workspace - which instance-wide pages
such as Audit can show - and the shown-once secrets are blurred before capture.

Override BASE / OUT / WORKSPACE / AP_EMAIL / AP_PASSWORD / ONLY (comma-separated
shot names) through the environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_onnx import build_detector, sample_image  # noqa: E402

BASE = os.environ.get("BASE", "http://localhost:8080")
OUT = os.environ.get("OUT", os.path.join(os.path.dirname(__file__), "..", "docs", "screenshots", "guide"))
WORKSPACE = os.environ.get("WORKSPACE", "e2e")
EMAIL = os.environ.get("AP_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("AP_PASSWORD", "aperture")
ONLY = {s for s in os.environ.get("ONLY", "").split(",") if s}
MODEL = "e2e-part-detection"
WORKFLOW = "e2e-part-detection-pipeline"
WEBP = shutil.which("cwebp")

os.makedirs(OUT, exist_ok=True)
TMP = tempfile.mkdtemp(prefix="aperture-guide-")
IMAGE = sample_image(os.path.join(TMP, "sample.jpg"))
WEIGHTS = build_detector(os.path.join(TMP, "detector-v3.onnx"))


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


login = urllib.request.Request(BASE + "/v1/auth/login", headers={"content-type": "application/json"},
                               data=json.dumps({"email": EMAIL, "password": PASSWORD}).encode())
TOKEN = json.load(urllib.request.urlopen(login))["access_token"]

# Instance-wide pages (audit, fleet, alerts) can name models from other
# workspaces; those names are blurred, e2e's own are not.
SECRETS: set[str] = set()
for ws in call("GET", "/v1/workspaces", TOKEN, workspace=None):
    if ws["slug"] == WORKSPACE:
        continue
    for model in call("GET", "/v1/models?limit=500&include_archived=true", TOKEN, workspace=ws["slug"]):
        SECRETS |= {s for s in (model["slug"], model["name"]) if s and len(s) >= 4}
    for flow in call("GET", "/v1/workflows", TOKEN, workspace=ws["slug"]):
        SECRETS |= {s for s in (flow["slug"], flow["name"]) if s and len(s) >= 4}
# a name the e2e workspace uses too is e2e's to show
OWN = {s for model in call("GET", "/v1/models?limit=500&include_archived=true", TOKEN)
       for s in (model["slug"], model["name"])}
OWN |= {s for flow in call("GET", "/v1/workflows", TOKEN) for s in (flow["slug"], flow["name"])}
SECRETS -= OWN | {"other", "detection", "segmentation", "classification"}

# A job and an alert rule, so those pages are not empty. Both are harmless: the
# export only reads, and the rule's webhook is a placeholder that never fires.
try:
    call("POST", "/v1/jobs/exports/flagged?fmt=coco&since_hours=48", TOKEN)
except Exception as exc:  # noqa: BLE001 - a missing worker should not stop the screenshots
    print("could not queue an export:", exc, file=sys.stderr)
if not any(rule["name"] == "e2e error rate" for rule in call("GET", "/v1/alerts", TOKEN)):
    call("POST", "/v1/alerts", TOKEN, {
        "name": "e2e error rate", "metric": "error_rate", "comparator": ">", "threshold": 0.05,
        "window_minutes": 5, "min_samples": 20, "channel": "webhook",
        "destination": "https://example.invalid/aperture-alerts", "enabled": True,
    })

BLUR_JS = """
(terms) => {
  if (!terms.length) return;
  const esc = s => s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  // whole names only: another workspace's `part-detection` must not blur half of `e2e-part-detection`
  const re = new RegExp('(?<![\\w-])(' + terms.sort((a, b) => b.length - a.length).map(esc).join('|') + ')(?![\\w-])', 'g');
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const hits = [];
  while (walker.nextNode()) { const n = walker.currentNode; re.lastIndex = 0; if (re.test(n.nodeValue)) hits.push(n); }
  for (const n of hits) {
    const frag = document.createDocumentFragment(); const s = n.nodeValue; let last = 0, m; re.lastIndex = 0;
    while ((m = re.exec(s))) {
      frag.appendChild(document.createTextNode(s.slice(last, m.index)));
      const span = document.createElement('span'); span.textContent = m[0];
      span.style.filter = 'blur(5px)'; frag.appendChild(span); last = m.index + m[0].length;
    }
    frag.appendChild(document.createTextNode(s.slice(last))); n.parentNode.replaceChild(frag, n);
  }
}
"""

SECRET_CODE_JS = "() => document.querySelectorAll('[role=dialog] pre').forEach(p => p.style.filter = 'blur(6px)')"

taken: list[str] = []
failed: list[tuple[str, str]] = []


def shot(page: Page, name: str, settle_ms: int = 500) -> None:
    page.wait_for_timeout(settle_ms)
    page.evaluate(BLUR_JS, sorted(SECRETS))
    png = os.path.join(TMP, f"{name}.png")
    page.screenshot(path=png)
    if WEBP:
        subprocess.run([WEBP, "-quiet", "-q", "88", "-m", "6", png, "-o", os.path.join(OUT, f"{name}.webp")],
                       check=True)
    else:
        shutil.copy(png, os.path.join(OUT, f"{name}.png"))
    taken.append(name)
    print("shot", name, file=sys.stderr)


def step(name: str):
    """Run one capture; a failure is reported and the rest carry on."""
    def wrap(fn):
        if ONLY and name not in ONLY:
            return fn
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((name, str(exc).splitlines()[0][:200]))
            print("FAILED", name, exc, file=sys.stderr)
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
        return fn
    return wrap


def goto(path: str, wait: str | None = None) -> None:
    page.goto(BASE + path)
    page.wait_for_load_state("networkidle")
    if wait:
        page.get_by_text(wait, exact=False).first.wait_for(timeout=15000)


def dialog(title: str):
    box = page.get_by_role("dialog").filter(has=page.get_by_role("heading", name=title))
    box.wait_for(timeout=10000)
    return box


def close_dialog() -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2,
                                  color_scheme="dark")
    page = context.new_page()

    # ------------------------------------------------------------ getting started
    @step("login")
    def _():
        goto("/login")
        page.locator("#email").fill(EMAIL)
        shot(page, "login")

    page.goto(BASE + "/login")
    page.locator("#email").fill(EMAIL)
    page.locator("#password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    # open on the e2e workspace, the way a user who switched to it last would
    call("POST", f"/v1/workspaces/{WORKSPACE}/activate", TOKEN, workspace=None)

    @step("overview")
    def _():
        goto("/", wait="Latency over time")
        shot(page, "overview", 1500)

    @step("workspace-switcher")
    def _():
        goto("/", wait="Latency over time")
        page.get_by_role("button", name="Active workspace").click()
        page.get_by_role("listbox", name="Workspaces").wait_for()
        shot(page, "workspace-switcher")
        page.keyboard.press("Escape")

    @step("workspace-new")
    def _():
        goto("/", wait="Latency over time")
        page.get_by_role("button", name="Active workspace").click()
        page.get_by_role("button", name="+ Add new workspace").click()
        box = dialog("New workspace")
        box.get_by_placeholder("Cloud").fill("Cloud")
        box.locator("textarea").fill("Models served to the cloud product.")
        shot(page, "workspace-new")
        close_dialog()

    @step("workspace-first")
    def _():
        # what someone with no workspace yet sees: the list answered empty
        page.route("**/v1/workspaces", lambda route: route.fulfill(json=[]))
        goto("/", wait="Create your first workspace")
        shot(page, "workspace-first")
        page.unroute("**/v1/workspaces")

    @step("account-menu")
    def _():
        goto("/", wait="Latency over time")
        page.get_by_role("button", name=EMAIL).click()
        page.get_by_role("button", name="Sign out").wait_for()
        shot(page, "account-menu")
        page.get_by_role("button", name=EMAIL).click()

    @step("settings-workspace")
    def _():
        goto("/settings", wait="Settings")
        page.get_by_role("button", name="Workspace", exact=True).click()
        page.get_by_text("Members").first.wait_for()
        shot(page, "settings-workspace", 800)

    # -------------------------------------------------------------------- models
    @step("models-list")
    def _():
        goto("/models", wait="E2E part detection")
        shot(page, "models-list", 1000)

    @step("model-new")
    def _():
        goto("/models", wait="E2E part detection")
        page.get_by_role("button", name="New model", exact=True).first.click()
        box = dialog("New model")
        box.get_by_placeholder("Front bumper part detection").fill("Rear bumper part detection")
        box.get_by_placeholder("perception team").fill("perception team")
        shot(page, "model-new")
        close_dialog()

    @step("model-import")
    def _():
        goto("/models", wait="E2E part detection")
        page.get_by_role("button", name="Import from Roboflow").click()
        box = dialog("Import from Roboflow")
        box.get_by_placeholder("https://app.roboflow.com/workspace/project/models/project/13").fill(
            "https://app.roboflow.com/acme/rear-bumper/models/rear-bumper/4")
        shot(page, "model-import")
        close_dialog()

    @step("model-versions")
    def _():
        goto(f"/models/{MODEL}", wait="Published weights are immutable")
        shot(page, "model-versions", 800)

    @step("version-upload")
    def _():
        goto(f"/models/{MODEL}", wait="Published weights are immutable")
        page.get_by_role("button", name="New version", exact=True).click()
        box = page.get_by_role("dialog").filter(has=page.get_by_role("heading", name="New version"))
        box.wait_for()
        box.locator("input[type=file]").set_input_files(WEIGHTS)
        shot(page, "version-upload", 600)
        close_dialog()

    @step("version-tag")
    def _():
        goto(f"/models/{MODEL}", wait="Published weights are immutable")
        page.get_by_role("button", name="Tag", exact=True).first.click()
        box = page.get_by_role("dialog").filter(has_text="Point a tag at")
        box.wait_for()
        box.locator("input").first.fill("staging")
        shot(page, "version-tag")
        close_dialog()

    @step("playground")
    def _():
        goto(f"/models/{MODEL}?tab=playground", wait="Call it from code")
        page.locator("input[type=file][accept='image/*']").set_input_files(IMAGE)
        page.get_by_role("button", name="Run inference").click()
        page.get_by_text("served by").first.wait_for(timeout=120000)
        shot(page, "playground", 1200)

    @step("model-config")
    def _():
        goto(f"/models/{MODEL}?tab=config", wait="Pre/post-processing")
        shot(page, "model-config", 800)

    @step("model-deploy")
    def _():
        goto(f"/models/{MODEL}?tab=deploy", wait="How rollback works")
        shot(page, "model-deploy", 1000)

    @step("deploy-dialog")
    def _():
        goto(f"/models/{MODEL}?tab=deploy", wait="How rollback works")
        page.get_by_role("button", name="Deploy a version").first.click()
        dialog("Deploy a version")
        shot(page, "deploy-dialog")
        close_dialog()

    @step("model-traffic")
    def _():
        goto(f"/models/{MODEL}?tab=traffic", wait="Stage cost")
        shot(page, "model-traffic", 1500)

    @step("model-compare")
    def _():
        goto(f"/models/{MODEL}?tab=compare", wait="Config differences")
        shot(page, "model-compare", 1000)

    # ----------------------------------------------------------------- workflows
    @step("workflows-list")
    def _():
        goto("/workflows", wait="Calling a workflow")
        shot(page, "workflows-list", 800)

    @step("workflows-new")
    def _():
        goto("/workflows", wait="Calling a workflow")
        page.get_by_role("button", name="New workflow").first.click()
        box = dialog("New workflow")
        box.get_by_placeholder("Damage report").fill("Damage report")
        shot(page, "workflows-new")
        close_dialog()

    @step("workflow-editor")
    def _():
        goto(f"/workflows/{WORKFLOW}")
        page.locator(".react-flow__node").first.wait_for(timeout=15000)
        shot(page, "workflow-editor", 900)

    @step("workflow-inspector")
    def _():
        goto(f"/workflows/{WORKFLOW}")
        page.locator(".react-flow__node").first.wait_for(timeout=15000)
        page.wait_for_timeout(500)
        page.locator(".react-flow__node").filter(has_text=MODEL).first.click()
        page.get_by_text("Run a deployed model on whatever reaches it.").wait_for()
        shot(page, "workflow-inspector", 600)

    @step("workflow-try")
    def _():
        goto(f"/workflows/{WORKFLOW}")
        page.locator(".react-flow__node").first.wait_for(timeout=15000)
        page.get_by_role("group", name="Editor mode").get_by_role("button", name="Try it").click()
        page.locator("input[type=file][accept='image/*']").set_input_files(IMAGE)
        page.get_by_role("button", name="Run workflow").click()
        page.get_by_role("heading", name="Where the time went").wait_for(timeout=120000)
        shot(page, "workflow-try", 1200)

    # ------------------------------------------------------------------- operate
    @step("traces")
    def _():
        goto("/traces", wait="request")
        shot(page, "traces", 1200)

    @step("trace-detail")
    def _():
        goto("/traces", wait="request")
        page.get_by_role("combobox", name="Capture").select_option(label="With image")
        page.get_by_role("combobox", name="Outcome").select_option(label="Succeeded")
        page.wait_for_load_state("networkidle")
        page.locator("table.data-table tbody tr").first.click()
        box = page.get_by_role("dialog")
        box.get_by_text("Predictions").first.wait_for(timeout=15000)
        shot(page, "trace-detail", 1500)
        close_dialog()

    @step("fleet")
    def _():
        goto("/fleet", wait="How placement works")
        shot(page, "fleet", 1000)

    @step("jobs")
    def _():
        goto("/jobs", wait="Submitting a job from code")
        shot(page, "jobs", 800)

    @step("reports")
    def _():
        goto("/reports", wait="Scheduled reports")
        shot(page, "reports", 1500)

    @step("report-schedule")
    def _():
        goto("/reports", wait="Scheduled reports")
        page.get_by_role("button", name="Schedule", exact=True).click()
        box = dialog("Schedule this report")
        box.get_by_placeholder("Weekly usage by consumer").fill("Weekly usage by consumer")
        shot(page, "report-schedule")
        close_dialog()

    @step("alerts")
    def _():
        goto("/alerts", wait="Recent alert events")
        shot(page, "alerts", 800)

    @step("alert-new")
    def _():
        goto("/alerts", wait="Recent alert events")
        page.get_by_role("button", name="New rule").first.click()
        box = dialog("New alert rule")
        box.get_by_placeholder("Part detection error rate").fill("Part detection p99")
        shot(page, "alert-new")
        close_dialog()

    # -------------------------------------------------------------------- manage
    @step("keys")
    def _():
        goto("/keys", wait="Calling the API")
        shot(page, "keys", 800)

    @step("key-new")
    def _():
        goto("/keys", wait="Calling the API")
        page.get_by_role("button", name="New key").first.click()
        box = dialog("New API key")
        box.get_by_placeholder("claims-web").fill("claims-web")
        box.get_by_role("checkbox", name="Every model").uncheck()
        box.get_by_text(MODEL, exact=True).first.click()
        shot(page, "key-new")
        box.get_by_role("button", name="Create key").click()
        once = dialog("Copy this key now")
        page.evaluate(SECRET_CODE_JS)
        shot(page, "key-created")
        once.get_by_role("button", name="Done").click()
        # tidy up: the demo key never leaves this run
        for key in call("GET", "/v1/keys", TOKEN):
            if key["name"] == "claims-web" and not key["revoked_at"]:
                call("POST", f"/v1/keys/{key['id']}/revoke", TOKEN)

    @step("audit")
    def _():
        goto("/audit", wait="Audit log")
        page.get_by_role("group", name="Time window").get_by_role("button", name="24 h").click()
        page.wait_for_load_state("networkidle")
        shot(page, "audit", 1000)

    @step("settings-users")
    def _():
        goto("/settings", wait="Settings")
        page.get_by_role("button", name="Users", exact=True).click()
        page.get_by_text("Invite a user").wait_for()
        shot(page, "settings-users", 600)

    @step("settings-capture")
    def _():
        goto("/settings", wait="Settings")
        page.get_by_role("button", name="Capture & retention", exact=True).click()
        page.get_by_text("Payload capture").wait_for()
        shot(page, "settings-capture", 600)

    @step("settings-instance")
    def _():
        goto("/settings", wait="Settings")
        page.get_by_role("button", name="Instance", exact=True).click()
        page.get_by_text("Limits").first.wait_for()
        shot(page, "settings-instance", 600)

    @step("tokens")
    def _():
        goto("/account/tokens", wait="Connect an agent")
        shot(page, "tokens", 800)

    @step("token-new")
    def _():
        goto("/account/tokens", wait="Connect an agent")
        page.get_by_role("button", name="New token").first.click()
        box = dialog("New personal access token")
        box.get_by_placeholder("claude-code on my laptop").fill("claude-code on my laptop")
        shot(page, "token-new")
        close_dialog()

    browser.close()

shutil.rmtree(TMP, ignore_errors=True)
print(f"{len(taken)} screenshots in {os.path.abspath(OUT)}", file=sys.stderr)
for name, reason in failed:
    print(f"  failed {name}: {reason}", file=sys.stderr)
sys.exit(1 if failed else 0)
