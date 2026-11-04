---
name: ipaas-connection
description: Create, list, and reference connections (credentials/secrets) in a Pipefy iPaaS (Activepieces) project. Use to move a hardcoded credential out of a flow into a managed connection, create OAuth2/secret/custom-auth connections, or wire a connection into a step correctly. Covers the connection-resolution gotchas (bare ref, props nesting, connectionIds). Requires the ipaas hub client.
---

# ipaas-connection — manage & reference connections

Uses the hub client. Read `.claude/skills/ipaas/reference.md` §gotchas 7–8 — the
resolution rules are non-obvious and were verified the hard way.

```python
import os, sys
sys.path.insert(0, next(d for d in (os.environ.get("CLAUDE_PLUGIN_ROOT","")+"/skills/ipaas",
    ".claude/skills/ipaas", os.path.expanduser("~/.claude/skills/ipaas")) if os.path.exists(d+"/ipaas_client.py")))
from ipaas_client import Client
c = Client(repo_id="<PIPE_ID>")
```

## Inspect what auth a piece expects
A piece's `auth` may be a single object OR an **array** of auth options (e.g. BigQuery
offers OAUTH2 *and* a "Service Account" CUSTOM_AUTH). Pick the right one and note its
`type` and `props`.
```python
code, p = c.req("GET", "/api/v1/pieces/@activepieces/piece-google-bigquery")
print(p["auth"])   # list -> inspect each option's type + props
```

## Create a connection
```python
body = {
  "externalId": "bigquery_pipefy_dw",          # the name you reference: {{connections['bigquery_pipefy_dw']}}
  "displayName": "BigQuery pipefy-dw (service account)",
  "pieceName": "@activepieces/piece-google-bigquery",
  "projectId": c.project_id,
  "type": "CUSTOM_AUTH",                         # OAUTH2 | SECRET_TEXT | BASIC_AUTH | CUSTOM_AUTH
  "value": {"type": "CUSTOM_AUTH", "props": {"serviceAccountJson": "<secret JSON>"}},
}
code, conn = c.req("POST", "/api/v1/app-connections", body=body)   # 201, status ACTIVE
```
- Value shape by type: `SECRET_TEXT` → `{"type":"SECRET_TEXT","secret_text":"..."}`;
  `CUSTOM_AUTH` → `{"type":"CUSTOM_AUTH","props":{...the piece's auth props...}}`.
- Never print the secret. Read it from its current location and pass it through.

## Reference it from a step (THE tricky part)
- Use the **bare** token: `{{connections['<externalId>']}}`.
- It resolves to the connection's **full value object**. For `CUSTOM_AUTH` that's
  `{"type":"CUSTOM_AUTH","props":{...}}` — your field is under `.props`.
- The template engine does **NOT** evaluate nested paths like
  `{{connections['x']['props']['field']}}` (returns empty). So either:
  - **CODE step:** reference bare and dig into the object in JS
    (`inputs.x.props.serviceAccountJson`), or add a tiny normalization shim; **or**
  - use a **`SECRET_TEXT`** connection — its bare ref resolves to a raw **string**
    (simplest for code that wants a plain secret), but it requires a SECRET_TEXT-auth piece.
- `connectionIds` on the flow version is **not required** and is **not auto-populated** —
  connections resolve on-demand by `externalId` in TESTING and PRODUCTION. Don't block on it.

### Example: moving a hardcoded key into a connection (CUSTOM_AUTH, code step)
1. Read the inline secret from the step input (don't print it).
2. Create the CUSTOM_AUTH connection (above).
3. `UPDATE_ACTION` the step: set the input to bare `{{connections['<externalId>']}}` and,
   if the code did `JSON.parse(inputs.key)`, prepend a shim:
   ```js
   if (inputs.key && typeof inputs.key === 'object') {
     inputs.key = (inputs.key.props && inputs.key.props.serviceAccountJson) || inputs.key.serviceAccountJson || inputs.key;
   }
   ```
4. Verify with **ipaas-test** `test-step` (connection auth resolves), then publish.

## List / delete
```python
code, d = c.req("GET", "/api/v1/app-connections", {"projectId": c.project_id, "limit": 50})
c.req("DELETE", f"/api/v1/app-connections/{conn_id}")
```

## Limits
- `global-connections` → **403** for the MEMBER-scoped token; only project-scoped
  `app-connections` are available.
- OAuth2 connections that need an interactive consent flow can't be fully minted headlessly
  — create those in the UI; this skill is for secret/custom-auth and referencing.
