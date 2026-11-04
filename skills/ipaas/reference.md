# Pipefy iPaaS / Activepieces — operational reference

Everything here was verified live (Activepieces **0.83.0, edition `ee`**, behind
`ipaas.pipefy.com`). Endpoints are under `/api/v1`. Pagination: `?limit=&cursor=`,
response `{data:[], next, previous}`.

## Auth chain (the client does all of this)
```
1. Obtain a Pipefy bearer — EITHER works:
   (A) Personal Access Token (PIPEFY_PAT) — used directly as the bearer. No call needed.
   (B) Service Account client_credentials                       -> SA bearer (24h)
       POST https://app.pipefy.com/oauth/token
       {grant_type:"client_credentials", client_id, client_secret}
   The PAT and the SA bearer are interchangeable for step 2.

2. Mint the iPaaS embed token (per pipe)                       -> embed token (24h, RS256)
   POST https://app.pipefy.com/internal_api   [Bearer SA token]
   query getAdvancedAutomationsToken($repoId: ID!){ advancedAutomationsToken(repoId:$repoId){ token } }
   repoId = the Pipe ID with iPaaS enabled

3. Exchange for an Activepieces session token                  -> AP token (7d) + projectId
   POST https://ipaas.pipefy.com/api/v1/managed-authn/external-token
   {externalAccessToken: <embed token>}
   -> {token, projectId, platformId, platformRole:"MEMBER", id}

4. Call the management API                                     [Bearer AP token]
   e.g. GET https://ipaas.pipefy.com/api/v1/flows?projectId=<projectId>
```
The AP token is **project-scoped** (role MEMBER) but authorizes the standard `/api/v1/*`
management API for that one project. Other projects/tenants are not visible.

## Capability matrix (verified)
| Capability | Endpoint / op | Notes |
|---|---|---|
| List/get flows | `GET /flows`, `GET /flows/{id}` | |
| Create flow | `POST /flows {displayName, projectId}` | returns flow w/ EMPTY trigger, DRAFT version |
| Bulk-load definition | `POST /flows/{id}` `{type:"IMPORT_FLOW", request:{displayName, trigger}}` | pin EXACT piece versions (wildcards stripped) |
| Edit | `UPDATE_TRIGGER`, `ADD_ACTION`, `UPDATE_ACTION`, `CHANGE_NAME`, `CHANGE_FOLDER` | |
| Delete step(s) | `DELETE_ACTION {names:[...]}` | **plural array**, not `{name}` |
| Publish (= commit) | `LOCK_AND_PUBLISH` | each publish = a new immutable Version #N |
| Enable/disable | `CHANGE_STATUS {status:"ENABLED"\|"DISABLED"}` | |
| Delete flow | `DELETE /flows/{id}` | |
| Runs | `GET /flow-runs` (PRODUCTION only), `GET /flow-runs/{id}` | TESTING runs are NOT listed |
| Test a step | `POST /sample-data/test-step {projectId, flowVersionId, stepName}` | TESTING env; see ipaas-test |
| Connections | `GET/POST /app-connections`, `DELETE /app-connections/{id}` | see ipaas-connection |
| Pieces | `GET /pieces` (625), `GET /pieces/{name}` | piece `auth` may be an **array** of options |
| Folders/Projects/Templates | `GET/POST .../folders`, `GET /projects`, `GET /templates` | |
| **Blocked** | `project-releases`, `git-repos` → **402** (Enterprise, not in plan); `global-connections` → **403** (MEMBER) | no native cross-env promotion / git sync |

## Gotchas (each cost us a debugging cycle — heed them)
1. **Bodyless requests must NOT send `Content-Type: application/json`** (Fastify 400s
   `FST_ERR_CTP_EMPTY_JSON_BODY`). The client omits it automatically.
2. **`DELETE_ACTION` needs `{names:["step_x", ...]}`** (plural array). `{name}` → 400.
3. **Publish = commit.** Editing a LOCKED (published) flow auto-creates a new DRAFT.
   The published version is unaffected until you publish again.
4. **Two run environments:**
   - PRODUCTION: firing the live webhook `POST /api/v1/webhooks/{flowId}` runs the
     **published** version. NEVER use it to validate a draft.
   - TESTING: the `test-step` (and whole-flow test) endpoints run the **draft**.
5. **`test-step` runs the step but does NOT persist sample data** — read the result from
   the returned run id (`GET /flow-runs/{id}.steps[stepName].output`). `SAVE_SAMPLE_DATA`
   is what persists it (and what marks a step "tested" so the UI's whole-flow Test unlocks).
6. **TESTING runs do not appear in `GET /flow-runs`** (that list is PRODUCTION). Read them
   by the id returned from the test call.
7. **Connections resolve on-demand by `externalId` in BOTH environments.**
   `version.connectionIds` is NOT required for resolution and is NOT auto-populated by API
   ops — it's a frontend hint, not a runtime dependency.
8. **A connection resolves to its full value object**; for `CUSTOM_AUTH` that's
   `{type, props:{...}}`. The template engine resolves only the **bare** `{{connections['id']}}`
   — it will NOT evaluate nested paths like `{{connections['id']['props']['x']}}` (those
   come back empty). Dig into the object in code, or use a `SECRET_TEXT` connection (bare
   ref resolves to a raw string).
9. **Run-poll baseline bug:** when smoke-testing a published flow, capture the SET of
   existing run ids before firing, then wait for a run id not in that set. A naive
   "newest run" or "!= latest" check reads a stale run.

## Flow definition model
`Flow {id, projectId, status:ENABLED|DISABLED, publishedVersionId, version:FlowVersion}`.
`FlowVersion {id, displayName, trigger, state:DRAFT|LOCKED, valid, connectionIds}`.
Steps are a **linked list** (`nextAction`), not an array. Trigger: `PIECE_TRIGGER`
(`settings:{pieceName, pieceVersion, triggerName, input, propertySettings, inputUiInfo}`).
Actions: `CODE`, `PIECE`, `LOOP_ON_ITEMS`, `ROUTER` (router has `children[]` aligned to
`settings.branches[]`; data refs use `{{step_slug.path}}`).
