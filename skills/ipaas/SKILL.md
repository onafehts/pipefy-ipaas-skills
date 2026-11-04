---
name: ipaas
description: Connect to and operate a Pipefy iPaaS project (white-labeled Activepieces at ipaas.pipefy.com). Use for ANY task about managing Pipefy automations/integrations/flows — authenticating, listing/inspecting flows, runs, connections, pieces. This is the HUB skill; it provides the shared auth client used by ipaas-flow, ipaas-test, and ipaas-connection. Invoke it first to establish access on a new instance.
---

# Pipefy iPaaS — hub

Pipefy's iPaaS ("Integration Hub") is a white-labeled **Activepieces** instance at
`https://ipaas.pipefy.com`. This skill set drives its REST management API. Read
`reference.md` (next to this file) for the auth chain and every behavioral gotcha — it
is the source of truth; do not guess endpoints.

## Prerequisites (per instance)
A `.env` file in the working directory (or point `IPAAS_ENV` at it) with **either**
auth method — a Personal Access Token (simplest) **or** a Service Account:

```
# Option A — Personal Access Token (https://app.pipefy.com/tokens). Takes precedence.
PIPEFY_PAT=...

# Option B — Service Account (Company menu → Members and Permissions → Service Accounts)
PIPEFY_SA_CLIENT_ID=...
PIPEFY_SA_CLIENT_SECRET=...
PIPEFY_SA_TOKEN_URL=https://app.pipefy.com/oauth/token

PIPEFY_REPO_ID=<pipe id where iPaaS is enabled>   # default repo; or pass --repo
# optional overrides: PIPEFY_IPAAS_URL, PIPEFY_INTERNAL_API_URL
```
The client uses `PIPEFY_PAT` if present, otherwise the Service Account. Both produce the
same Pipefy bearer used to mint the iPaaS token — the rest of the chain is identical.

`repoId` = the **Pipe ID** that has the iPaaS enabled. Each pipe maps to one Activepieces
project; the client resolves its `projectId` automatically.

## The client
`ipaas_client.py` (next to this file, pure stdlib) runs the full auth chain, caches the
Activepieces session token in `./.cache/ipaas-<repoId>.json` (7-day token, auto-refresh),
and exposes a request helper + CLI. Always go through it — never reimplement auth.

**Locate it** (works for a project skill, a global `~/.claude/skills` skill, or a plugin):
```bash
for d in "$CLAUDE_PLUGIN_ROOT/skills/ipaas" .claude/skills/ipaas ~/.claude/skills/ipaas; do
  [ -f "$d/ipaas_client.py" ] && IPAAS="$d/ipaas_client.py" && break
done
```

**CLI (quick calls):**
```bash
python3 "$IPAAS" token --repo <PIPE_ID>
python3 "$IPAAS" GET /api/v1/flows --param projectId=auto --param limit=20
python3 "$IPAAS" POST /api/v1/flows --body '{"displayName":"x","projectId":"auto"}'
python3 "$IPAAS" flow-op <FLOW_ID> LOCK_AND_PUBLISH
```
`auto` → resolved projectId. `--repo` defaults to `PIPEFY_REPO_ID` from `.env`.

**Library (multi-step work — preferred):**
```python
import os, sys
sys.path.insert(0, next(d for d in (
    os.environ.get("CLAUDE_PLUGIN_ROOT", "") + "/skills/ipaas",
    ".claude/skills/ipaas",
    os.path.expanduser("~/.claude/skills/ipaas")) if os.path.exists(d + "/ipaas_client.py")))
from ipaas_client import Client
c = Client(repo_id="<PIPE_ID>")   # or Client() to use PIPEFY_REPO_ID from .env
code, data = c.req("GET", "/api/v1/flows", {"projectId": c.project_id, "limit": 20})
code, data = c.flow_op("<FLOW_ID>", "LOCK_AND_PUBLISH")
```

## What this hub covers
- Establish access (auth chain) and resolve `projectId`.
- Read-only inventory: `GET /api/v1/flows`, `/flow-runs`, `/app-connections`,
  `/folders`, `/projects`, `/pieces`, `/pieces/{name}`, `/templates`, `/health`.

## Sibling skills
- **ipaas-flow** — create / import / edit / delete flows and publish (= commit a version).
- **ipaas-test** — test a single step pre-publish, and run a flow in production + inspect.
- **ipaas-connection** — create a connection and reference it correctly from a step.

## Safety
- Mutations hit a LIVE tenant. Prefer a staging pipe. Before publishing/enabling a flow,
  confirm with the user.
- Treat **publish as a commit** (each `LOCK_AND_PUBLISH` is an immutable version).
- Firing a flow's webhook runs the **PUBLISHED** version, not your draft — use ipaas-test
  for draft validation.
- Never print service-account keys / tokens; `.cache/` and `.env*` must stay gitignored.
