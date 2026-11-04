# pipefy-ipaas-skills

Claude Code skills for operating Pipefy iPaaS (white-labeled Activepieces at ipaas.pipefy.com) and administering Pipefy organizations.

| Skill | Purpose |
|---|---|
| [`ipaas`](skills/ipaas) | Hub skill: auth chain + shared client (`ipaas_client.py`). Invoke first. |
| [`ipaas-flow`](skills/ipaas-flow) | Create, import, edit, publish, enable/disable and delete flows. |
| [`ipaas-connection`](skills/ipaas-connection) | Create, list and reference connections (credentials). |
| [`ipaas-test`](skills/ipaas-test) | Test a single step on a draft, or run a published flow and inspect results. |
| [`pipefy-admin`](skills/pipefy-admin) | Org admin via PAT: service accounts, pipe membership, clone/delete pipes, enable iPaaS. |

## Install

Copy (or symlink) the folders you want into `~/.claude/skills/` (or a project's `.claude/skills/`):

```bash
cp -R skills/* ~/.claude/skills/
```

Credentials are read from environment variables (`PIPEFY_PAT`, or `PIPEFY_SA_CLIENT_ID` / `PIPEFY_SA_CLIENT_SECRET`). See `skills/ipaas/SKILL.md`. Never commit `.env*` or `.cache/`.
