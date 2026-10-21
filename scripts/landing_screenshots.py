"""Console screenshots for the landing page's three ideas, taken in the `demo` workspace.

The landing page shows each idea on the console screen that proves it, using the
real fruit-bowl-seg model: its Config tab, its Versions tab, and a Playground run
on the fruit-bowl photo. This script signs in, opens those pages in Chrome, runs
the inference, and writes 1440x900 viewports at 2x to docs/screenshots/landing/.

    uv run --with playwright python scripts/landing_screenshots.py

Needs a running stack with the demo workspace's fruit-bowl-seg deployed behind the
production tag. The box positions in landing/src/sections/Ideas.tsx are measured on
these captures, so re-check them if the console layout changes.

Override BASE / AP_EMAIL / AP_PASSWORD / WORKSPACE through the environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = os.environ.get("BASE", "http://localhost:8080")
EMAIL = os.environ.get("AP_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("AP_PASSWORD", "aperture")
WORKSPACE = os.environ.get("WORKSPACE", "demo")
MODEL = "fruit-bowl-seg"
OUT = os.path.join(ROOT, "docs", "screenshots", "landing")
PHOTO = os.path.join(OUT, "fruit-bowl.jpg")
WEBP = shutil.which("cwebp")
TMP = tempfile.mkdtemp(prefix="aperture-landing-")

login = urllib.request.Request(BASE + "/v1/auth/login", data=json.dumps({"email": EMAIL, "password": PASSWORD}).encode(),
                               headers={"content-type": "application/json"})
TOKEN = json.load(urllib.request.urlopen(login))["access_token"]


def save(page, name: str) -> None:
    png = os.path.join(TMP, f"{name}.png")
    page.screenshot(path=png)
    if WEBP:
        subprocess.run([WEBP, "-quiet", "-q", "88", "-m", "6", png, "-o", os.path.join(OUT, f"{name}.webp")], check=True)
    else:
        shutil.copy(png, os.path.join(OUT, f"{name}.png"))
    print("shot", name)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2,
                               color_scheme="dark").new_page()
    page.goto(BASE + "/login")
    page.locator("#email").fill(EMAIL)
    page.locator("#password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    # open on the demo workspace, the way a user who switched to it last would
    urllib.request.urlopen(urllib.request.Request(BASE + f"/v1/workspaces/{WORKSPACE}/activate", method="POST",
                                                  headers={"authorization": f"Bearer {TOKEN}"}))

    def goto(path: str, wait: str) -> None:
        page.goto(BASE + path)
        page.wait_for_load_state("networkidle")
        page.get_by_text(wait, exact=False).first.wait_for(timeout=15000)

    goto(f"/models/{MODEL}?tab=config", "Pre/post-processing")
    page.wait_for_timeout(800)
    save(page, "config")

    goto(f"/models/{MODEL}", "Published weights are immutable")
    page.wait_for_timeout(800)
    save(page, "versions")

    goto(f"/models/{MODEL}?tab=playground", "Call it from code")
    page.locator("input[type=file][accept='image/*']").set_input_files(PHOTO)
    page.get_by_role("button", name="Run inference").click()
    page.get_by_text("served by").first.wait_for(timeout=120000)
    page.wait_for_timeout(1500)
    save(page, "playground")
    browser.close()
