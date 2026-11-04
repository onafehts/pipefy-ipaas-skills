#!/usr/bin/env python3
"""PAT-only client for Pipefy org administration (service accounts + pipe membership).

Uses ONLY a Personal Access Token (https://app.pipefy.com/tokens) as the bearer — no
service-account credentials. Talks to two endpoints:
  - https://app.pipefy.com/internal_api   (admin GraphQL: service accounts, membership)
  - https://api.pipefy.com/graphql         (public GraphQL: resolve pipe -> organization)

Credentials from a .env file ($IPAAS_ENV, else ./.env):
  PIPEFY_PAT=...            (required)
Optional:
  PIPEFY_INTERNAL_API_URL  (default https://app.pipefy.com/internal_api)
  PIPEFY_GRAPHQL_URL       (default https://api.pipefy.com/graphql)

CLI examples:
  python admin_client.py org-uuid --org 300793280
  python admin_client.py create-sa --org 300793280 --name my-bot --role super_admin
  python admin_client.py credentials --org 300793280 --sa <serviceAccountUuid>
  python admin_client.py add-to-pipe --sa <uuid> --pipe 307173482 --role admin
  python admin_client.py provision --pipe 307173482 --name my-bot           # full flow

`provision` does the whole goal: resolve org from pipe -> create service account
(org role super_admin) -> fetch its client_id/secret -> add it to the pipe (role admin).
"""
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_INTERNAL = "https://app.pipefy.com/internal_api"
DEFAULT_PUBLIC = "https://api.pipefy.com/graphql"

# org-level roles: super_admin | company_admin | company_member
# pipe-level roles (a pipe's "super admin" is `admin`): member | admin | read_and_comment | my_cards_only


def _load_env(path=None):
    path = path or os.environ.get("IPAAS_ENV") or os.path.join(os.getcwd(), ".env")
    env = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip("'").strip('"')
    for k in ("PIPEFY_PAT", "PIPEFY_INTERNAL_API_URL", "PIPEFY_GRAPHQL_URL"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


class AdminClient:
    def __init__(self, env_path=None):
        self.env = _load_env(env_path)
        self.pat = self.env.get("PIPEFY_PAT")
        if not self.pat:
            raise SystemExit("PIPEFY_PAT required in .env (this skill is PAT-only)")
        self.internal = self.env.get("PIPEFY_INTERNAL_API_URL", DEFAULT_INTERNAL)
        self.public = self.env.get("PIPEFY_GRAPHQL_URL", DEFAULT_PUBLIC)

    def _gql(self, url, query, variables=None, operation_name=None):
        body = {"query": query, "variables": variables or {}}
        if operation_name:
            body["operationName"] = operation_name
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": f"Bearer {self.pat}",
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                out = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise SystemExit(f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:400]}")
        if out.get("errors"):
            raise SystemExit("GraphQL errors: " + json.dumps(out["errors"])[:500])
        return out["data"]

    # ---- resolution -----------------------------------------------------
    def org_uuid(self, org_id):
        q = ("query Organization($id: ID!) { organization(id: $id) { id name uuid role "
             "permissions { canManageServiceAccounts } } }")
        org = self._gql(self.internal, q, {"id": str(org_id)}, "Organization")["organization"]
        return org

    def org_id_from_pipe(self, pipe_id):
        # public API: a pipe exposes its organization
        q = "query($id: ID!) { pipe(id: $id) { id name organizationId } }"
        return self._gql(self.public, q, {"id": str(pipe_id)})["pipe"]

    # ---- service accounts ----------------------------------------------
    def create_service_account(self, org_uuid, name, role="super_admin",
                               description="", exp_value=24, exp_unit="hours"):
        q = ("mutation createServiceAccount($organizationUuid: ID!, $name: String!, "
             "$description: String, $role: String!, $expirationTime: ExpirationTimeInput) {"
             " createServiceAccount(input: {organizationUuid: $organizationUuid, name: $name, "
             "description: $description, role: $role, expirationTime: $expirationTime}) {"
             " success serviceAccount { name description uuid email id "
             "role { uuid name title isCustom } } } }")
        v = {"organizationUuid": org_uuid, "name": name, "description": description,
             "role": role, "expirationTime": {"unit": exp_unit, "value": exp_value}}
        return self._gql(self.internal, q, v, "createServiceAccount")["createServiceAccount"]

    def service_account(self, org_uuid, sa_uuid):
        """Returns the SA incl. client { id, secret } and token { endpoint }."""
        q = ("query getServiceAccountByUuid($organizationUuid: ID!, $serviceAccountUuid: ID!) {"
             " serviceAccount(organizationUuid: $organizationUuid, serviceAccountUuid: $serviceAccountUuid) {"
             " id uuid name email description role { uuid name title } "
             "client { id secret } token { endpoint expirationTime { value unit } } } }")
        v = {"organizationUuid": org_uuid, "serviceAccountUuid": sa_uuid}
        return self._gql(self.internal, q, v, "getServiceAccountByUuid")["serviceAccount"]

    def delete_service_account(self, org_uuid, sa_uuid):
        q = ("mutation deleteServiceAccount($organizationUuid: ID!, $serviceAccountUuid: ID!) {"
             " deleteServiceAccount(input: {organizationUuid: $organizationUuid, "
             "serviceAccountUuid: $serviceAccountUuid}) { success } }")
        v = {"organizationUuid": org_uuid, "serviceAccountUuid": sa_uuid}
        return self._gql(self.internal, q, v, "deleteServiceAccount")["deleteServiceAccount"]

    # ---- pipe lifecycle -------------------------------------------------
    def clone_repo(self, pipe_id, org_id=None, with_cards=False):
        """Full clone of a pipe INCLUDING automations (the 'repo clone' the UI link does).

        Uses internal_api `cloneRepo` (NOT the public `clonePipes`, which is template-only and
        drops automations). org_id defaults to the source pipe's organization.
        """
        if not org_id:
            org_id = self.org_id_from_pipe(pipe_id)["organizationId"]
        q = ("mutation cloneRepo($input: CloneRepoInput!) { cloneRepo(input: $input) {"
             " success repo { ... on Pipe { id name } } } }")
        v = {"input": {"id": str(pipe_id), "organizationId": str(org_id), "withCards": with_cards}}
        return self._gql(self.internal, q, v, "cloneRepo")["cloneRepo"]

    def delete_pipe(self, pipe_id):
        """Delete a pipe (public API). Irreversible."""
        q = "mutation($input: DeletePipeInput!) { deletePipe(input: $input) { success } }"
        return self._gql(self.public, q, {"input": {"id": str(pipe_id)}})["deletePipe"]

    # ---- iPaaS enablement (Pipefy "Integration" platform app) ----------
    IPAAS_APP_SLUG = "pipefy-integration-app"

    def platform_app_id(self, pipe_id, slug=IPAAS_APP_SLUG):
        """Resolve a platform-app id for a pipe by slug (don't hardcode the id)."""
        q = "query($repoId: ID!, $slug: String) { platformApps(repoId: $repoId, slug: $slug) { id name slug } }"
        apps = self._gql(self.internal, q, {"repoId": str(pipe_id), "slug": slug})["platformApps"]
        app = next((a for a in apps if a["slug"] == slug), None)
        if not app:
            raise SystemExit(f"platform app with slug {slug!r} not found for pipe {pipe_id}")
        return app["id"]

    def ipaas_enabled(self, pipe_id):
        q = "query($repoId: ID!) { enabledPlatformApps(repoId: $repoId) { id name slug } }"
        apps = self._gql(self.internal, q, {"repoId": str(pipe_id)})["enabledPlatformApps"]
        return next((a for a in apps if a["slug"] == self.IPAAS_APP_SLUG), None)

    def enable_ipaas(self, pipe_id):
        """Enable iPaaS on a pipe = enable the 'Integration' platform app (optedIn = accept ToS).

        Idempotent: re-enabling 500s server-side, so we short-circuit if already enabled.
        """
        already = self.ipaas_enabled(pipe_id)
        if already:
            return {"alreadyEnabled": True, "platformApp": already}
        app_id = self.platform_app_id(pipe_id, self.IPAAS_APP_SLUG)
        q = ("mutation enablePlatformApp($appId: ID!, $repoId: ID!, $optedIn: Boolean) {"
             " enablePlatformApp(input: {appId: $appId, repoId: $repoId, optedIn: $optedIn}) {"
             " platformApp { id name slug url public } } }")
        v = {"appId": app_id, "repoId": str(pipe_id), "optedIn": True}
        return self._gql(self.internal, q, v, "enablePlatformApp")["enablePlatformApp"]

    # ---- pipe membership -----------------------------------------------
    def pipes_available(self, sa_uuid, search=""):
        q = ("query pipesAvailableToServiceAccount($serviceAccountUuid: ID!, $searchTerm: String) {"
             " pipesAvailableToServiceAccount(serviceAccountUuid: $serviceAccountUuid, searchTerm: $searchTerm) {"
             " edges { node { pipe { id uuid name canBeManaged } availableRoles } } } }")
        v = {"serviceAccountUuid": sa_uuid, "searchTerm": search}
        data = self._gql(self.internal, q, v, "pipesAvailableToServiceAccount")
        return [e["node"] for e in data["pipesAvailableToServiceAccount"]["edges"]]

    def pipe_uuid_for(self, sa_uuid, pipe_id):
        for n in self.pipes_available(sa_uuid):
            if str(n["pipe"]["id"]) == str(pipe_id):
                return n["pipe"]["uuid"], n["availableRoles"]
        return None, None

    def add_to_pipes(self, sa_uuid, pipes):
        """pipes = [{"uuid": <pipeUuid>, "roleName": "admin"}, ...]"""
        q = ("mutation addServiceAccountToPipes($serviceAccountUuid: ID!, $pipes: [MemberRoleInPipesInput!]!) {"
             " addUserToPipes(input: {userUuid: $serviceAccountUuid, pipes: $pipes}) {"
             " success pipes { pipe { id } } } }")
        v = {"serviceAccountUuid": sa_uuid, "pipes": pipes}
        return self._gql(self.internal, q, v, "addServiceAccountToPipes")["addUserToPipes"]

    def remove_from_pipe(self, pipe_uuid, sa_uuid):
        q = ("mutation RemoveMemberFromPipe($pipeUuid: ID!, $usersUuids: [ID], $groupsUuids: [ID]) {"
             " removeMembersFromPipe(input: {groupsUuids: $groupsUuids, usersUuids: $usersUuids, pipeUuid: $pipeUuid}) {"
             " removedUsersUuids errors } }")
        v = {"pipeUuid": pipe_uuid, "usersUuids": [sa_uuid], "groupsUuids": []}
        return self._gql(self.internal, q, v, "RemoveMemberFromPipe")["removeMembersFromPipe"]

    def list_pipes(self, sa_uuid):
        q = ("query pipesByServiceAccount($serviceAccountUuid: ID!, $first: Int) {"
             " pipesByServiceAccount(serviceAccountUuid: $serviceAccountUuid, first: $first) {"
             " edges { node { pipe { id uuid name } roleName availableRoles } } } }")
        data = self._gql(self.internal, q, {"serviceAccountUuid": sa_uuid, "first": 50},
                         "pipesByServiceAccount")
        return [e["node"] for e in data["pipesByServiceAccount"]["edges"]]

    # ---- high-level -----------------------------------------------------
    def provision(self, pipe_id, name, org_role="super_admin", pipe_role="admin",
                  description="", exp_value=24, exp_unit="hours", org_id=None):
        """Full goal: create a service account and add it to a pipe as admin."""
        if not org_id:
            org_id = self.org_id_from_pipe(pipe_id)["organizationId"]
        org = self.org_uuid(org_id)
        created = self.create_service_account(org["uuid"], name, org_role, description,
                                              exp_value, exp_unit)
        sa = created["serviceAccount"]
        detail = self.service_account(org["uuid"], sa["uuid"])
        pipe_uuid, roles = self.pipe_uuid_for(sa["uuid"], pipe_id)
        if not pipe_uuid:
            raise SystemExit(f"pipe {pipe_id} not available to this service account")
        if pipe_role not in roles:
            raise SystemExit(f"role {pipe_role!r} not in available {roles}")
        added = self.add_to_pipes(sa["uuid"], [{"uuid": pipe_uuid, "roleName": pipe_role}])
        return {"serviceAccount": sa, "client": detail.get("client"),
                "token": detail.get("token"), "orgRole": org_role,
                "pipe": {"id": pipe_id, "uuid": pipe_uuid, "role": pipe_role},
                "added": added.get("success")}


def _main(argv):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("cmd")
    p.add_argument("--org"); p.add_argument("--pipe"); p.add_argument("--sa")
    p.add_argument("--name"); p.add_argument("--role")
    p.add_argument("--description", default="")
    p.add_argument("--exp-value", type=int, default=24); p.add_argument("--exp-unit", default="hours")
    a = p.parse_args(argv)
    c = AdminClient()
    if a.cmd == "org-uuid":
        out = c.org_uuid(a.org)
    elif a.cmd == "create-sa":
        out = c.create_service_account(c.org_uuid(a.org)["uuid"], a.name,
                                       a.role or "super_admin", a.description, a.exp_value, a.exp_unit)
    elif a.cmd == "credentials":
        out = c.service_account(c.org_uuid(a.org)["uuid"], a.sa)
    elif a.cmd == "pipes-available":
        out = c.pipes_available(a.sa)
    elif a.cmd == "add-to-pipe":
        u, roles = c.pipe_uuid_for(a.sa, a.pipe)
        out = c.add_to_pipes(a.sa, [{"uuid": u, "roleName": a.role or "admin"}])
    elif a.cmd == "list-pipes":
        out = c.list_pipes(a.sa)
    elif a.cmd == "delete-sa":
        out = c.delete_service_account(c.org_uuid(a.org)["uuid"], a.sa)
    elif a.cmd == "clone-repo":
        out = c.clone_repo(a.pipe, a.org)
    elif a.cmd == "delete-pipe":
        out = c.delete_pipe(a.pipe)
    elif a.cmd == "enable-ipaas":
        out = c.enable_ipaas(a.pipe)
    elif a.cmd == "provision":
        out = c.provision(a.pipe, a.name, a.role or "super_admin", org_id=a.org)
    else:
        raise SystemExit(f"unknown cmd {a.cmd}")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _main(sys.argv[1:])
