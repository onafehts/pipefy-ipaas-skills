---
name: ipaas-test
description: Test Pipefy iPaaS (Activepieces) flows. Use to validate a single step on a DRAFT before publishing (test-step), or to run a published flow in production and inspect the per-step result. Covers reading run outputs and the run-environment / polling gotchas. Whole-flow draft test (socket.io) is deferred to v2. Requires the ipaas hub client.
---

# ipaas-test — test flows

Uses the hub client. Read `.claude/skills/ipaas/reference.md` §gotchas first —
the run-environment split is the thing people get wrong.

```python
import os, sys
sys.path.insert(0, next(d for d in (os.environ.get("CLAUDE_PLUGIN_ROOT","")+"/skills/ipaas",
    ".claude/skills/ipaas", os.path.expanduser("~/.claude/skills/ipaas")) if os.path.exists(d+"/ipaas_client.py")))
from ipaas_client import Client
c = Client(repo_id="<PIPE_ID>")
```

## Test ONE step on a draft (pre-publish, no side effects to the published version)
Runs in the `TESTING` environment against the draft. Best for validating a piece/code
step or a connection without going live.
```python
code, full = c.req("GET", f"/api/v1/flows/{fid}")
vid = full["version"]["id"]
code, run = c.req("POST", "/api/v1/sample-data/test-step",
                  body={"projectId": c.project_id, "flowVersionId": vid, "stepName": "step_1"})
rid = run["id"]                                  # status starts QUEUED
# poll the run by id (TESTING runs are NOT in GET /flow-runs)
import time
for _ in range(20):
    code, fr = c.req("GET", f"/api/v1/flow-runs/{rid}")
    if fr["status"] not in ("QUEUED", "RUNNING"): break
    time.sleep(1)
s = fr["steps"]["step_1"]
print(s["status"], s.get("output"), s.get("errorMessage"))
```
Notes:
- `test-step` runs the step but does **not persist** sample data — read the output from
  the returned **run id** (above), not from `GET /sample-data` (which stays empty until a
  `SAVE_SAMPLE_DATA` op).
- A step that references trigger/upstream data may fail for lack of it (e.g. a query using
  `{{trigger.queryParams.x}}` with no trigger sample data). That's expected — the point is
  to confirm the step's own logic / connection auth resolves.

## Run a flow in PRODUCTION and inspect (full end-to-end)
The live webhook runs the **published** version. So: publish → enable → trigger → inspect
→ (disable). **Confirm with the user before enabling/firing** — it has real side effects.

```python
# 0) read the trigger's auth (e.g. webhook header auth) from the flow if needed
tin = full["version"]["trigger"]["settings"]["input"]

# 1) baseline existing run ids BEFORE firing (avoids reading a stale run)
code, d = c.req("GET", "/api/v1/flow-runs", {"projectId": c.project_id, "flowId": fid, "limit": 50})
seen = {r["id"] for r in d["data"]}

# 2) publish + enable
c.flow_op(fid, "LOCK_AND_PUBLISH"); c.flow_op(fid, "CHANGE_STATUS", {"status": "ENABLED"})

# 3) fire the trigger. For a webhook trigger, POST the live URL with any required
#    query params / auth header (NOT through the AP token — it's a public webhook):
import urllib.request, urllib.parse
url = f"{c.ipaas_url}/api/v1/webhooks/{fid}?" + urllib.parse.urlencode({"org_ids": "..."})
req = urllib.request.Request(url, data=b"{}", method="POST",
        headers={"Content-Type": "application/json", "authorization": "<header value if any>"})
urllib.request.urlopen(req, timeout=40)

# 4) poll for a run whose id is NOT in `seen`
run = None
for _ in range(30):
    code, d = c.req("GET", "/api/v1/flow-runs", {"projectId": c.project_id, "flowId": fid, "limit": 50})
    fresh = [r for r in d["data"] if r["id"] not in seen]
    if fresh and fresh[0]["status"] not in ("RUNNING", "QUEUED", "PAUSED"):
        run = fresh[0]; break
    time.sleep(1.5)

# 5) inspect, then disable to leave it safe
code, fr = c.req("GET", f"/api/v1/flow-runs/{run['id']}")
for name, s in fr["steps"].items(): print(name, s["status"], s.get("output"))
c.flow_op(fid, "CHANGE_STATUS", {"status": "DISABLED"})
```

Run statuses: `SUCCEEDED, FAILED, RUNNING, PAUSED, QUEUED, CANCELED, TIMEOUT, ...`. On
`FAILED`, the offending step has `errorMessage`.

## Deferred to v2
Whole-flow draft test ("Test Flow") is a **socket.io** event (`TEST_FLOW_RUN
{flowVersionId}` on `/api/socket.io/`), not REST. Until v2: test each step individually,
publish, then run in production. (The UI also only enables "Test Flow" after every step
has sample data.)
