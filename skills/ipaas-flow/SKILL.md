---
name: ipaas-flow
description: Create, import, edit, publish, and delete flows in a Pipefy iPaaS (Activepieces) project. Use to build or modify automation flows — add/update/delete steps, import a flow definition (JSON template), rename, move to folders, publish a version (commit), enable/disable, or delete a flow. Requires the ipaas hub client for auth.
---

# ipaas-flow — manage & deploy flows

Uses the hub client at `.claude/skills/ipaas/ipaas_client.py`. Read
`.claude/skills/ipaas/reference.md` for the flow model and gotchas. All snippets
assume `c = Client(repo_id=...)`.

```python
import os, sys
sys.path.insert(0, next(d for d in (os.environ.get("CLAUDE_PLUGIN_ROOT","")+"/skills/ipaas",
    ".claude/skills/ipaas", os.path.expanduser("~/.claude/skills/ipaas")) if os.path.exists(d+"/ipaas_client.py")))
from ipaas_client import Client
c = Client(repo_id="<PIPE_ID>")
```

## Inspect
```python
code, d = c.req("GET", "/api/v1/flows", {"projectId": c.project_id, "limit": 50})
code, full = c.req("GET", f"/api/v1/flows/{flow_id}")     # full definition (trigger tree)
vid = full["version"]["id"]                               # flowVersionId for tests
```

## Create + import a definition (the deploy-from-JSON path)
```python
code, f = c.req("POST", "/api/v1/flows", body={"displayName": name, "projectId": c.project_id})
fid = f["id"]
# IMPORT_FLOW request is the flow body {displayName, trigger}; trigger is the linked-list root.
c.flow_op(fid, "IMPORT_FLOW", {"displayName": name, "trigger": trigger_tree})
```
- Pin **exact** piece versions in the definition (`GET /api/v1/pieces/{name}` for the
  current version; wildcards `~`/`^` are stripped).
- A template export shaped `{name, flows:[{displayName, trigger, ...}]}` → pass `flows[0]`
  (it already has `displayName` + `trigger`) as the IMPORT_FLOW request.

## Edit operations  (`c.flow_op(fid, OP, request)`)
| Op | request |
|---|---|
| `UPDATE_TRIGGER` | full trigger object `{name:"trigger", type, valid, displayName, settings}` |
| `ADD_ACTION` | `{parentStep, stepLocationRelativeToParent:"AFTER"\|"INSIDE_BRANCH"\|"INSIDE_LOOP", action:{...}}` |
| `UPDATE_ACTION` | full action object (fetch it, mutate, send back) |
| `DELETE_ACTION` | `{names:["step_1", ...]}`  ← **plural array** |
| `CHANGE_NAME` | `{displayName}` (flow name) |
| `CHANGE_FOLDER` | `{folderId}` |

To edit a step: GET the flow, locate the step in the trigger tree (walk `nextAction` and
router `children`), copy `{name,type,valid,displayName,settings}`, mutate, send via
`UPDATE_ACTION`. To restructure (e.g. remove a router and re-link children), it is often
cleanest to rebuild the `trigger` tree in memory and re-`IMPORT_FLOW`.

## Publish (= commit) and toggle
```python
c.flow_op(fid, "LOCK_AND_PUBLISH")                       # new immutable Version #N
c.flow_op(fid, "CHANGE_STATUS", {"status": "ENABLED"})   # or "DISABLED"
```
Treat publish like a git commit: edit → test (ipaas-test) → publish. Editing a published
flow auto-creates a fresh DRAFT; the live version is untouched until the next publish.

## Delete
```python
c.req("DELETE", f"/api/v1/flows/{fid}")                  # 204
```

## Workflow guidance
- Before destructive edits to a real flow, **back up** its current definition
  (`GET /flows/{id}` → save JSON) so you can restore via IMPORT_FLOW.
- Use throwaway flows (`__test__`) for experiments and delete them after.
- After editing, re-`GET` the flow and check `version.valid` and that every step is valid
  before publishing.
