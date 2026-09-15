"""Screenshot every console page with model names blurred.

Drives a running stack (web on :8080, api on :8000) through Chrome via
Playwright, signs in as the bootstrap admin, and writes one PNG per page to
docs/screenshots/. Model names, slugs and Roboflow project refs are blurred in
the DOM before capture so the images are safe to publish.

    python -m venv .venv-shots && .venv-shots/bin/pip install playwright
    .venv-shots/bin/python scripts/screenshots.py

Uses the locally installed Google Chrome (channel="chrome"); run
`playwright install chromium` and drop the channel argument if there is none.
Override BASE / API / OUT / AP_EMAIL / AP_PASSWORD through the environment.
"""
import json, os, re, sys, urllib.request
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://localhost:8080")
API = os.environ.get("API", "http://localhost:8000")
OUT = os.environ.get("OUT", os.path.join(os.path.dirname(__file__), "..", "docs", "screenshots"))
EMAIL = os.environ.get("AP_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("AP_PASSWORD", "aperture")
os.makedirs(OUT, exist_ok=True)

def api(path, tok):
    r=urllib.request.Request(API+path, headers={"authorization":"Bearer "+tok})
    return json.load(urllib.request.urlopen(r))

req=urllib.request.Request(API+"/v1/auth/login", data=json.dumps({"email":EMAIL,"password":PASSWORD}).encode(), headers={"content-type":"application/json"})
tok=json.load(urllib.request.urlopen(req))["access_token"]
models=api("/v1/models", tok)
secrets=set()
for m in models:
    for k in ("name","slug"):
        if m.get(k): secrets.add(m[k])
    d=m.get("description") or ""
    # Roboflow workspace/project references inside descriptions
    for s in re.findall(r"[\w-]+/[\w-]+(?:/\d+)?", d): secrets.add(s)
extra=set()
for j in api("/v1/jobs", tok):
    m=(j.get("result") or {}).get("model")
    if m: secrets.add(m)
for a in api("/v1/audit?limit=500", tok):
    if str(a.get("action","")).startswith(("model.","tag.","deployment.","version.")):
        for k in ("target_label",):
            if a.get(k): secrets.add(a[k].split(" ")[0])
        pl=a.get("payload") or {}
        for k in ("slug","model","model_slug","ref"):
            if isinstance(pl.get(k),str) and len(pl[k])>=4: secrets.add(pl[k])
for s in list(secrets):
    if "/" in s:
        for seg in s.split("/"):
            if len(seg)>=4 and not seg.isdigit(): extra.add(seg)
        extra.add(s.split("/",1)[1])  # project/version without workspace
    if "-" in s: extra.add(s.replace("-","_"))
secrets|=extra
secrets={s for s in secrets if len(s)>=4 and s.lower() not in {"other","segmentation","detection","classification"}}
print("blur terms:", sorted(secrets), file=sys.stderr)
model_slug=models[0]["slug"] if models else None
wfs=api("/v1/workflows", tok)
wf_slug=wfs[0]["slug"] if wfs else None

BLUR_JS = """
(terms) => {
  const esc = s => s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  const re = new RegExp('(' + terms.sort((a,b)=>b.length-a.length).map(esc).join('|') + '|(?<=model )[A-Za-z0-9][\\w-]{3,}|(?<=/v1/models/)[\\w-]+)', 'g');
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) { const n = walker.currentNode; if (re.test(n.nodeValue)) nodes.push(n); re.lastIndex = 0; }
  for (const n of nodes) {
    if (n.parentElement && n.parentElement.closest('script,style')) continue;
    const frag = document.createDocumentFragment();
    let last = 0; const s = n.nodeValue; let m; re.lastIndex = 0;
    while ((m = re.exec(s))) {
      frag.appendChild(document.createTextNode(s.slice(last, m.index)));
      const span = document.createElement('span');
      span.textContent = m[0];
      span.style.filter = 'blur(5px)'; span.style.userSelect = 'none';
      frag.appendChild(span); last = m.index + m[0].length;
    }
    frag.appendChild(document.createTextNode(s.slice(last)));
    n.parentNode.replaceChild(frag, n);
  }
  for (const el of document.querySelectorAll('input,textarea')) {
    if (terms.some(t => el.value && el.value.includes(t))) el.style.filter = 'blur(5px)';
  }
  document.querySelectorAll('svg text').forEach(t => { if (terms.some(x => t.textContent.includes(x))) t.style.filter='blur(5px)'; });
}
"""

NODE_BLUR_JS = """
() => {
  for (const node of document.querySelectorAll('.react-flow__node')) {
    for (const el of node.querySelectorAll('span, p')) {
      if (el.getAttribute('aria-hidden') === 'true') continue;   // the type icon
      // Nested elements were already blurred by the name pass; un-blur them so
      // the wrapper's single blur covers the whole line evenly.
      for (const inner of el.querySelectorAll('span, p')) inner.style.filter = '';
      el.style.filter = 'blur(5px)'; el.style.userSelect = 'none';
    }
  }
}
"""

pages = [("login","/login",False),("overview","/",True),("models","/models",True)]
if model_slug: pages.append(("model-detail", f"/models/{model_slug}", True))
pages += [("workflows","/workflows",True)]
if wf_slug: pages.append(("workflow-editor", f"/workflows/{wf_slug}", True))
pages += [(n, "/"+n, True) for n in ["traces","fleet","jobs","reports","alerts","keys","audit","settings"]]

with sync_playwright() as p:
    b=p.chromium.launch(channel="chrome", headless=True)
    ctx=b.new_context(viewport={"width":1440,"height":900}, device_scale_factor=2, color_scheme="dark")
    pg=ctx.new_page()
    pg.goto(BASE+"/login"); pg.wait_for_load_state("networkidle")
    pg.screenshot(path=f"{OUT}/login.png")
    pg.fill("#email",EMAIL); pg.fill("#password",PASSWORD)
    pg.keyboard.press("Enter"); pg.wait_for_url(lambda u: "/login" not in u, timeout=15000)
    for name,path,auth in pages:
        if name=="login": continue
        pg.goto(BASE+path); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
        pg.evaluate(BLUR_JS, sorted(secrets))
        if name == "workflow-editor":
            # The whole body of every node on the canvas, not just the model
            # names: gate conditions and result keys name the parts too.
            pg.evaluate(NODE_BLUR_JS)
        pg.wait_for_timeout(200)
        pg.screenshot(path=f"{OUT}/{name}.png")
        print("shot", name, pg.url, file=sys.stderr)
    b.close()
