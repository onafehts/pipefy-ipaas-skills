# Pipefy admin (service accounts + pipe membership) — captured operations

All against **`POST https://app.pipefy.com/internal_api`** with header
`Authorization: Bearer <PAT>` (except `pipe→org` which uses the public
`https://api.pipefy.com/graphql`). Captured live from the admin UI (org 300793280).

## Role model
- **Org roles** (service account account-wide): `super_admin`, `company_admin`, `company_member`.
- **Pipe roles** (one pipe's membership): `member`, `admin`, `read_and_comment`, `my_cards_only`.
  No pipe-level `super_admin`; **`admin` = "Pipe administrator"** (a pipe's super admin).

## 0. Resolve org (PAT user's org)
The mutations need the **organization UUID**, but ids in URLs are numeric. Resolve:
```graphql
# internal_api
query Organization($id: ID!) {
  organization(id: $id) { id name uuid role permissions { canManageServiceAccounts } }
}
```
To get the numeric org id from a pipe (public API):
```graphql
# api.pipefy.com/graphql
query($id: ID!) { pipe(id: $id) { id name organizationId } }
```

## 1. Create service account
```graphql
mutation createServiceAccount($organizationUuid: ID!, $name: String!, $description: String,
                              $role: String!, $expirationTime: ExpirationTimeInput) {
  createServiceAccount(input: {organizationUuid: $organizationUuid, name: $name,
    description: $description, role: $role, expirationTime: $expirationTime}) {
    success
    serviceAccount { name description uuid email id role { uuid name title isCustom } }
  }
}
```
Variables: `{organizationUuid, name, description, role:"super_admin", expirationTime:{unit:"hours", value:24}}`.
- The generated login email is `<name>-<orgNumericId>@service-account.pipefy.com`.
- Response does **not** include client_id/secret — fetch them with #2.

## 2. Get credentials (client_id / client_secret / token endpoint)
```graphql
query getServiceAccountByUuid($organizationUuid: ID!, $serviceAccountUuid: ID!) {
  serviceAccount(organizationUuid: $organizationUuid, serviceAccountUuid: $serviceAccountUuid) {
    id uuid name email description
    role { uuid name title }
    client { id secret }
    token { endpoint expirationTime { value unit } }
  }
}
```
`client.id` = OAuth Client ID, `client.secret` = Client Secret, `token.endpoint` =
`https://app.pipefy.com/oauth/token` (use with `grant_type=client_credentials`).

## 3. List pipes available to a service account (id → uuid + roles)
```graphql
query pipesAvailableToServiceAccount($serviceAccountUuid: ID!, $searchTerm: String) {
  pipesAvailableToServiceAccount(serviceAccountUuid: $serviceAccountUuid, searchTerm: $searchTerm) {
    edges { node { pipe { id uuid name icon color canBeManaged } availableRoles } }
  }
}
```
Use this to map a numeric pipe id → its `uuid` and confirm `admin` ∈ `availableRoles`.

## 4. Add service account to pipe(s)
```graphql
mutation addServiceAccountToPipes($serviceAccountUuid: ID!, $pipes: [MemberRoleInPipesInput!]!) {
  addUserToPipes(input: {userUuid: $serviceAccountUuid, pipes: $pipes}) {
    success
    pipes { pipe { id } }
  }
}
```
Variables: `{serviceAccountUuid, pipes: [{uuid: "<pipeUuid>", roleName: "admin"}]}`.
(Resolver is `addUserToPipes`; a service account is treated as a user via `userUuid`.)

## 5. List the SA's current pipes
```graphql
query pipesByServiceAccount($serviceAccountUuid: ID!, $first: Int, $after: String, $searchTerm: String) {
  pipesByServiceAccount(serviceAccountUuid: $serviceAccountUuid, first: $first, after: $after, searchTerm: $searchTerm) {
    edges { node { pipe { id uuid name } roleName availableRoles } }
    pageInfo { hasNextPage endCursor }
  }
}
```

## 6. Remove from a pipe
```graphql
mutation RemoveMemberFromPipe($pipeUuid: ID!, $usersUuids: [ID], $groupsUuids: [ID]) {
  removeMembersFromPipe(input: {groupsUuids: $groupsUuids, usersUuids: $usersUuids, pipeUuid: $pipeUuid}) {
    removedUsersUuids removedGroupsUuids errors
  }
}
```
Variables: `{pipeUuid, usersUuids: ["<serviceAccountUuid>"], groupsUuids: []}`.

## 7. Delete a service account
```graphql
mutation deleteServiceAccount($organizationUuid: ID!, $serviceAccountUuid: ID!) {
  deleteServiceAccount(input: {organizationUuid: $organizationUuid, serviceAccountUuid: $serviceAccountUuid}) {
    success
  }
}
```
Variables: `{organizationUuid, serviceAccountUuid}`. Invalidates the SA's token and detaches it
from all pipes. (Reach via the SA detail → "Service account" tab → "Delete service account",
NOT the row's "Remove from pipe" — that one is the mislabeled remove flow.)

## 8. Clone a pipe (WITH automations) — and delete a pipe
**Use `cloneRepo` (internal_api), NOT the public `clonePipes`.** `clonePipes` is template-based
and **drops automations**; `cloneRepo` is the full "repo clone" the UI's clone link performs
(verified: automations come along). The Rails link `/pipes/{id}/clone?...` is **session-only**
(returns HTTP 500 with a PAT) — don't use it.
```graphql
# internal_api
mutation cloneRepo($input: CloneRepoInput!) {
  cloneRepo(input: $input) { success repo { ... on Pipe { id name } } }
}
```
`CloneRepoInput`: `{ id: ID!  (source pipe id), organizationId: ID (target org, numeric),
withCards: Boolean }`. Returns the new pipe id/name. (Cloning is effectively async server-side
but the new pipe id is returned immediately.)

Delete a pipe (public API; irreversible):
```graphql
# api.pipefy.com/graphql
mutation($input: DeletePipeInput!) { deletePipe(input: {id: <pipeId>}) { success } }
```

## 9. Enable iPaaS on a pipe
iPaaS is delivered as a Pipefy **platform app** named "Integration" (slug
`pipefy-integration-app`; id was `_4BVY8-u` here — **resolve by slug, don't hardcode**).
Enabling it on a pipe = enabling that platform app with `optedIn: true` (accepts the ToS the
UI checkbox covers).
```graphql
# 1) resolve the app id for the pipe (internal_api)
query($repoId: ID!, $slug: String) { platformApps(repoId: $repoId, slug: $slug) { id name slug } }
#    → pick the one whose slug == "pipefy-integration-app"

# 2) enable it (internal_api)
mutation enablePlatformApp($appId: ID!, $repoId: ID!, $optedIn: Boolean) {
  enablePlatformApp(input: {appId: $appId, repoId: $repoId, optedIn: $optedIn}) {
    platformApp { id name slug url public }
  }
}
```
Variables: `{appId, repoId: <pipeId>, optedIn: true}`.
- **NOT idempotent** — re-enabling an already-enabled app returns `INTERNAL_SERVER_ERROR`.
  Guard first with `query { enabledPlatformApps(repoId) { id slug } }` and skip if present.
- **Independent from the Activepieces flows API (verified).** The AP project + token chain
  (`advancedAutomationsToken` → managed-authn, used by the `ipaas` skill) provisions/reaches a
  pipe's AP project **on demand regardless of this toggle** — confirmed by reaching `/api/v1/flows`
  for a pipe with the Integration app OFF. So this toggle is the **UI/official enablement**, not a
  prerequisite for importing/running flows via the API. Enable it for the product experience;
  don't treat it as a gate for the iPaaS API.
- Inverse mutation: `disablePlatformApp(input)`.
- Same pattern enables other platform apps, e.g. **"Document Generator"** (slug `pdf-automator`)
  — the likely path for the runbook's PDF-template step.

## Supporting queries (read-only; the UI calls these too)
- `Me { ... uuid currentOrganizationRole }`
- `serviceAccountConstants(organizationUuid) { maxExpirationTime minExpirationTime defaultExpirationTime emailDomain }`
- `rolesByOrganization(orgUuid) { edges { node { name uuid title isCustom } } }` — incl. custom roles.

## Notes / gotchas
- **PAT works on `internal_api`** for these admin ops *if* the PAT user is an org admin
  (`permissions.canManageServiceAccounts == true`). Verify via #0.
- GraphQL may return HTTP 200 with an `errors[]` array — always inspect the body.
- The admin UI's "Remove from pipe" reuses a confirmation titled "Delete service account" but
  actually performs `removeMembersFromPipe`. The API is unambiguous — prefer it. The genuine
  delete is `deleteServiceAccount` (#7), reached from the SA detail's "Service account" tab.
- All operations here are **PAT-verified** end to end (create, credentials, available-pipes,
  add-to-pipe, remove-from-pipe, delete) against org 300793280 with `canManageServiceAccounts`.
