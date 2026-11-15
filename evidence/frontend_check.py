import json

import httpx

BASE = "http://127.0.0.1:8205"
client = httpx.Client(base_url=BASE, timeout=30)

index = client.get("/")
js = client.get("/static/app.js")
css = client.get("/static/style.css")
html = index.text
print("GET /            -> %d / %d bytes" % (index.status_code, len(html)))
print("GET /static/app.js  -> %d / %d bytes" % (js.status_code, len(js.text)))
print("GET /static/style.css -> %d / %d bytes" % (css.status_code, len(css.text)))
tabs = []
for name in ("datasets", "targets", "runs", "compare"):
    marker = 'data-tab="%s"' % name
    found = marker in html
    tabs.append({"tab": name, "rendered": found})
print("tabs:", json.dumps(tabs, ensure_ascii=False))
required_js = [
    "loadDatasets",
    "loadTargets",
    "submitRun",
    "pollTick",
    "loadFailedResults",
    "runCompare",
    "renderCompare",
    "renderBars",
    "probeTarget",
]
missing = [name for name in required_js if name not in js.text]
print("js handlers missing:", missing)
assert index.status_code == 200 and js.status_code == 200 and css.status_code == 200
assert all(tab["rendered"] for tab in tabs)
assert not missing
print("frontend check OK: 4 tabs rendered, static assets served")
