---
name: pipefy-admin
description: Administer a Pipefy ORGANIZATION with a Personal Access Token (PAT only — no service-account creds). Use to create a service account (with an org role like super_admin), retrieve its client_id/secret, add/remove it as a member of specific pipes (pipe role "admin" = a pipe's super admin), clone pipes (WITH automations) or delete pipes, and enable iPaaS on a pipe. Covers the org/pipe admin GraphQL on app.pipefy.com/internal_api. This is the Pipefy admin plane — distinct from the iPaaS/Activepieces skills (ipaas etc.).
---

# pipefy-admin — service accounts & pipe membership (PAT only)

Operates Pipefy's **org admin** GraphQL via `app.pipefy.com/internal_api`, authenticated
with a **Personal Access Token** (this skill never uses service-account credentials). Read
`reference.md` (next to this file) for the exact captured operations and the role model.

## Prerequisites
A `.env` in the working directory (or `IPAAS_ENV`) with:
```
PIPEFY_PAT=...     # https://app.pipefy.com/tokens — must belong to an org admin
```
The PAT user needs `canManageServiceAccounts` (org admin / company admin).

## Client
`admin_client.py` (next to this file, pure stdlib). Locate it portably:
```bash
for d in "$CLAUDE_PLUGIN_ROOT/skills/pipefy-admin" .claude/skills/pipefy-admin ~/.claude/skills/pipefy-admin; do
  [ -f "$d/admin_client.py" ] && ADMIN="$d/admin_client.py" && break
done
python3 "$ADMIN" provision --pipe 307173482 --name my-bot
```
Library use:
```python
import os, sys
sys.path.insert(0, next(d for d in (os.environ.get("CLAUDE_PLUGIN_ROOT","")+"/skills/pipefy-admin",
    ".claude/skills/pipefy-admin", os.path.expanduser("~/.claude/skills/pipefy-admin")) if os.path.exists(d+"/admin_client.py")))
from admin_client import AdminClient
c = AdminClient()
```

## The goal flow — create a service account & add it as (pipe) super admin
`provision` does it end to end:
```python
result = c.provision(pipe_id="307173482", name="my-bot",
                     org_role="super_admin",   # org-level role of the SA
                     pipe_role="admin")          # pipe-level role (admin = a pipe's super admin)
# result.client = {id, secret}; result.token.endpoint = https://app.pipefy.com/oauth/token
```
Steps it performs (all individually available as methods — see reference.md):
1. **Resolve org** from the pipe: public API `pipe(id){organizationId}` → `organization(id){uuid}`.
   (Pass `org_id=` to skip if you already know it.)
2. **Create** the service account: `createServiceAccount(role: "super_admin")`.
3. **Fetch credentials**: `getServiceAccountByUuid` → `client{id,secret}`, `token{endpoint}`.
4. **Map the pipe id → uuid** and check roles: `pipesAvailableToServiceAccount`.
5. **Add to the pipe**: `addServiceAccountToPipes([{uuid, roleName:"admin"}])`.

## Roles (important distinction)
- **Org roles** (the SA's account-wide role): `super_admin`, `company_admin`, `company_member`.
- **Pipe roles** (membership in one pipe): `admin`, `member`, `read_and_comment`, `my_cards_only`.
  **There is no "super_admin" at pipe level** — `admin` is the highest ("Pipe administrator").
  So "make it super admin on a pipe" = org `super_admin` and/or pipe `admin`.

## Pipe lifecycle (clone / delete)
- `c.clone_repo(pipe_id, org_id=None, with_cards=False)` — **full clone INCLUDING automations**
  (internal_api `cloneRepo`). Returns `{success, repo:{id,name}}`. Use this, **not** the public
  `clonePipes` (template-only — drops automations), and not the `/pipes/{id}/clone` link
  (session-only, 500s with a PAT). `org_id` defaults to the source pipe's org.
- `c.delete_pipe(pipe_id)` — delete a pipe (public API `deletePipe`). Irreversible.
- `c.enable_ipaas(pipe_id)` — enable iPaaS on a pipe (enables the "Integration" platform app,
  slug `pipefy-integration-app`, via `enablePlatformApp` + `optedIn:true`). Idempotent-guarded
  (returns `{alreadyEnabled:True}` if on); re-enabling raw would 500.
- `c.ipaas_enabled(pipe_id)` — check whether iPaaS is already enabled (`enabledPlatformApps`).

## Other operations
- `c.list_pipes(sa_uuid)` — pipes the SA belongs to (with `roleName`).
- `c.remove_from_pipe(pipe_uuid, sa_uuid)` — detach from a pipe (`removeMembersFromPipe`).
- `c.pipes_available(sa_uuid, search)` — searchable pipe list with `availableRoles`.
- `c.delete_service_account(org_uuid, sa_uuid)` — delete the SA (invalidates its token).

## Safety
- Creates a **real, privileged credential** (a super_admin service account) and returns a
  **client secret** — handle it like a password; never print/commit it. Confirm with the user
  before creating in production; prefer a clear, non-"test" name for real use.
- The Pipefy admin UI's "Remove from pipe" confirmation is mislabeled "Delete service account"
  but performs a pipe removal; the API calls (`removeMembersFromPipe` vs `deleteServiceAccount`)
  are unambiguous — use them.
- All operations are PAT-verified end to end. `delete_service_account` is real and final
  (invalidates the token) — confirm before using.
