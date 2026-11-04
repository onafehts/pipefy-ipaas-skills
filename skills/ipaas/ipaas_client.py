#!/usr/bin/env python3
"""Self-contained client for Pipefy's iPaaS (white-labeled Activepieces).

Pure standard library — no pip installs. Runs the full auth chain and caches the
Activepieces session token, then exposes a tiny request helper + CLI.

Auth chain (see reference.md):
  Service Account client_credentials      -> SA bearer (24h)
    -> internal_api advancedAutomationsToken(repoId)  -> embed token (24h)
      -> ipaas /managed-authn/external-token          -> AP session token (7d) + projectId
        -> ipaas /api/v1/*                            -> the management API

Credentials come from a .env file ($IPAAS_ENV, else ./.env). Provide EITHER:
  PIPEFY_PAT                              (Personal Access Token, https://app.pipefy.com/tokens)
OR a Service Account:
  PIPEFY_SA_CLIENT_ID, PIPEFY_SA_CLIENT_SECRET, PIPEFY_SA_TOKEN_URL
(PAT wins if both are present.)
Optional:
  PIPEFY_IPAAS_URL        (default https://ipaas.pipefy.com)
  PIPEFY_INTERNAL_API_URL (default https://app.pipefy.com/internal_api)
  PIPEFY_REPO_ID          (default repo/pipe id if --repo not passed)

CLI:
  python ipaas_client.py token   --repo 307171773
  python ipaas_client.py GET  /api/v1/flows --param projectId=auto --param limit=5
  python ipaas_client.py POST /api/v1/flows --body '{"displayName":"x","projectId":"auto"}'
  python ipaas_client.py flow-op <flowId> IMPORT_FLOW --body @def.json

`auto` anywhere a projectId is expected is replaced with the resolved projectId.
Library use:
  from ipaas_client import Client
  c = Client(repo_id="307171773")
  code, data = c.req("GET", "/api/v1/flows", {"projectId": c.project_id})
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_IPAAS_URL = "https://ipaas.pipefy.com"
DEFAULT_INTERNAL_API = "https://app.pipefy.com/internal_api"
TOKEN_SKEW_SECONDS = 600  # refresh if the AP token expires within this window


def _load_env(path=None):
    path = path or os.environ.get("IPAAS_ENV") or os.path.join(os.getcwd(), ".env")
    env = {}
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip("'").strip('"')
    # environment variables win over the file
    for k in ("PIPEFY_SA_CLIENT_ID", "PIPEFY_SA_CLIENT_SECRET", "PIPEFY_SA_TOKEN_URL",
              "PIPEFY_IPAAS_URL", "PIPEFY_INTERNAL_API_URL", "PIPEFY_REPO_ID"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def _http(method, url, headers=None, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(headers or {})
    headers.setdefault("Accept", "application/json")
    if data is not None:  # Fastify 400s on Content-Type:json with an empty body
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else None)
        except Exception:
            return e.code, {"_raw": raw.decode("utf-8", "replace")[:1000]}


def _jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return None


class Client:
    def __init__(self, repo_id=None, env_path=None):
        self.env = _load_env(env_path)
        self.repo_id = str(repo_id or self.env.get("PIPEFY_REPO_ID") or "").strip()
        if not self.repo_id:
            raise SystemExit("repo_id required (pass --repo or set PIPEFY_REPO_ID)")
        self.ipaas_url = self.env.get("PIPEFY_IPAAS_URL", DEFAULT_IPAAS_URL).rstrip("/")
        self.internal_api = self.env.get("PIPEFY_INTERNAL_API_URL", DEFAULT_INTERNAL_API)
        # persist token cache in the plugin's data dir if running as a plugin, else ./.cache
        _data = os.environ.get("CLAUDE_PLUGIN_DATA")
        self.cache_dir = os.path.join(_data, "cache") if _data else os.path.join(os.getcwd(), ".cache")
        self._token = None
        self.project_id = None
        self._resolve()

    # ---- auth chain -----------------------------------------------------
    def _cache_path(self):
        return os.path.join(self.cache_dir, f"ipaas-{self.repo_id}.json")

    def _resolve(self):
        cached = self._read_cache()
        if cached:
            self._token, self.project_id = cached["token"], cached["projectId"]
            return
        bearer = self._pipefy_bearer()
        embed = self._embed_token(bearer)
        ex = self._exchange(embed)
        self._token, self.project_id = ex["token"], ex["projectId"]
        self._write_cache()

    def _read_cache(self):
        p = self._cache_path()
        if not os.path.exists(p):
            return None
        try:
            data = json.load(open(p))
        except Exception:
            return None
        exp = data.get("exp") or 0
        if exp - time.time() > TOKEN_SKEW_SECONDS and data.get("token") and data.get("projectId"):
            return data
        return None

    def _write_cache(self):
        os.makedirs(self.cache_dir, exist_ok=True)
        p = self._cache_path()
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"token": self._token, "projectId": self.project_id,
                       "exp": _jwt_exp(self._token)}, fh)

    def _need(self, key):
        v = self.env.get(key)
        if not v:
            raise SystemExit(f"missing {key} in .env")
        return v

    def _pipefy_bearer(self):
        """Pipefy bearer used to call internal_api (step 2 of the chain).

        Prefers a Personal Access Token (PIPEFY_PAT, from https://app.pipefy.com/tokens)
        if provided; otherwise runs the Service Account client_credentials exchange.
        """
        pat = self.env.get("PIPEFY_PAT")
        if pat:
            return pat
        return self._sa_token()

    def _sa_token(self):
        code, data = _http("POST", self._need("PIPEFY_SA_TOKEN_URL"), body={
            "grant_type": "client_credentials",
            "client_id": self._need("PIPEFY_SA_CLIENT_ID"),
            "client_secret": self._need("PIPEFY_SA_CLIENT_SECRET"),
        })
        if code != 200 or not data or "access_token" not in data:
            raise SystemExit(f"SA token failed [{code}]: {json.dumps(data)[:300]}")
        return data["access_token"]

    def _embed_token(self, sa_token):
        query = ("query getAdvancedAutomationsToken($repoId: ID!) { "
                 "advancedAutomationsToken(repoId: $repoId) { token __typename } }")
        code, data = _http("POST", self.internal_api,
                           headers={"Authorization": f"Bearer {sa_token}"},
                           body={"query": query, "variables": {"repoId": self.repo_id}})
        try:
            return data["data"]["advancedAutomationsToken"]["token"]
        except Exception:
            raise SystemExit(f"embed token failed [{code}]: {json.dumps(data)[:300]}")

    def _exchange(self, embed_token):
        code, data = _http("POST", f"{self.ipaas_url}/api/v1/managed-authn/external-token",
                           body={"externalAccessToken": embed_token})
        if code != 200 or not data or "token" not in data:
            raise SystemExit(f"managed-authn exchange failed [{code}]: {json.dumps(data)[:300]}")
        return data

    # ---- requests -------------------------------------------------------
    def req(self, method, path, params=None, body=None):
        url = self.ipaas_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return _http(method, url, headers={"Authorization": f"Bearer {self._token}"}, body=body)

    def flow_op(self, flow_id, op_type, request=None):
        return self.req("POST", f"/api/v1/flows/{flow_id}",
                        body={"type": op_type, "request": request or {}})


def _sub_auto(obj, project_id):
    if isinstance(obj, dict):
        return {k: _sub_auto(v, project_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sub_auto(v, project_id) for v in obj]
    return project_id if obj == "auto" else obj


def _main(argv):
    args = list(argv)
    repo = None
    if "--repo" in args:
        i = args.index("--repo")
        repo = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    c = Client(repo_id=repo)

    if cmd == "token":
        print(json.dumps({"projectId": c.project_id, "ipaasUrl": c.ipaas_url,
                          "token": c._token}))
        return 0

    # parse --param k=v and --body json|@file
    params, body = {}, None
    i = 1
    positional = []
    while i < len(args):
        a = args[i]
        if a == "--param":
            k, _, v = args[i + 1].partition("=")
            params[k] = c.project_id if v == "auto" else v
            i += 2
        elif a == "--body":
            raw = args[i + 1]
            if raw.startswith("@"):
                raw = open(raw[1:]).read()
            body = _sub_auto(json.loads(raw), c.project_id)
            i += 2
        else:
            positional.append(a)
            i += 1

    if cmd in ("GET", "POST", "DELETE", "PATCH", "PUT"):
        code, data = c.req(cmd, positional[0], params or None, body)
    elif cmd == "flow-op":
        # flow-op <flowId> <OP_TYPE> [--body request]
        code, data = c.flow_op(positional[0], positional[1], body)
    else:
        print(f"unknown command: {cmd}\n{__doc__}")
        return 2
    print(f"HTTP {code}")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:6000] if data is not None else "")
    return 0 if code in (200, 201, 204) else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
