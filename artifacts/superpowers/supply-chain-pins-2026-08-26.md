# Supply-chain pins — resolved 2026-08-26

Every value here was **read**, not looked up from memory. Method is recorded per
class because the methods differ in what they prove. No container was started or
restarted (plan A3); no image was built (A4).

## Actions — commit SHAs

Resolved via `GET /repos/{repo}/commits/{tag}`, which returns the **commit** a
tag resolves to.

| Action | Tag | Commit SHA (the pin) |
| --- | --- | --- |
| `actions/checkout` | v4 | `11d5960a326750d5838078e36cf38b85af677262` |
| `actions/setup-python` | v5 | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| `astral-sh/setup-uv` | v5 | `d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86` |

**The tag-object trap, verified rather than asserted.** `GET /git/ref/tags/{tag}`
returns the object the ref points at:

```
astral-sh/setup-uv    v5 -> tag    e58605a9b6da7c637471fab8847a5e5a6b8df081
actions/checkout      v4 -> commit 11d5960a326750d5838078e36cf38b85af677262
actions/setup-python  v5 -> commit a26af69be951a213d495a4c3e4e4022e16d87065
```

Only `setup-uv` publishes an **annotated** tag, so only it has two plausible
40-hex values. Pinning `@e58605a9…` would look like a pin and would not be one.
The other two are lightweight tags pointing straight at the commit.

## Third-party images — digests

`docker image inspect --format '{{index .RepoDigests 0}}'` on the copy in use.

| Compose service | Image | Digest | Source |
| --- | --- | --- | --- |
| `git` | `gitea/gitea:1.24` | `sha256:918955f16b1e91732af6c449bb2db3a34271748dbed1ccfbae48f8a2fb5480b8` | running copy |
| `litellm` | `ghcr.io/berriai/litellm:main-stable` | `sha256:af806882b7a6ced41658db5b6a7e98ed7b9b51d03b935e0417bf1c8552d688af` | running copy |
| `postgres` | `pgvector/pgvector:pg16` | `sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b` | running copy |
| `act_runner` | `gitea/act_runner:0.2.11` | `sha256:eabea0883261fa3378849a1b9ce86dce6a2e906c8db48ad8553edc3f2bd9ff68` | **registry index** — see below |

### Finding 1 — the plan's inventory said "3 third-party images". There are 4.

`gitea/act_runner:0.2.11` is declared at `docker-compose.yml:322` and was left
out of revision 3's inventory table. It is the **one image the exposure narrative
is actually about** — the runner holding the Docker socket and the push
credentials. Omitting it from the pin list while writing three paragraphs about
it is the kind of gap that makes an inventory worse than no inventory.

### Finding 2 — act_runner is declared but **not deployed**, so the chain is latent

```
$ docker compose ps -a
basic-memory  running   git  running   host-sync  running   litellm  running
postgres      running   scout running   sync-job   running
postgres-migrate exited
                                        act_runner  ABSENT
```

The runner service has never been brought up on this host: no container, and no
local image either — which is why its digest above had to come from
`docker manifest inspect --verbose` (registry) rather than from a running copy,
a deliberate, single deviation from plan assumption A2, recorded here rather than
smoothed over.

**This changes how the exposure should be described.** Revision 3 says
"one unpinned install script, fetched weekly and unattended, on a runner with a
root-equivalent socket, on a machine holding push credentials." Every element of
that is true of the **configuration**; none of it is currently executing, because
the component that would execute it is not running. The honest statement is:
*the chain is fully assembled in the repository and fires the first time anyone
runs `docker compose up act_runner`.* That is still worth removing — a latent
root-equivalent path is a path — but it is not an active compromise, and the plan
should not have implied otherwise.

## Base images

| Dockerfile | Base | Digest |
| --- | --- | --- |
| `scout/Dockerfile:8` | `python:3.12-slim` | `sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de` |
| `basic-memory/Dockerfile:8` | `python:3.12-slim` | same |
| `scripts/Dockerfile.sync:1` | `python:3.12-slim` | same |

Python runtime in all three running containers: **3.12.13**.

## Dependencies — as installed in the running containers

`docker exec … python -m pip freeze` (reads a running container; no restart).

### `snp-scout` (backs `scout`, `sync-job`, `postgres-migrate`)

| Declared in `requirements.txt` | Installed |
| --- | --- |
| `fastmcp==3.3.1` | `3.3.1` |
| `watchfiles>=0.21` | **`1.2.0`** |
| `asyncpg>=0.29.0` | **`0.31.0`** |
| `pypdf>=4.0.0` | **`6.16.1`** |
| `pyyaml>=6.0` | **`6.0.3`** |

### Finding 3 — the ranges had already drifted across major versions

`watchfiles` is one major above its floor; `pypdf` is **two**. Nobody chose
`pypdf 6.16.1` — a `>=4.0.0` written when 4.x was current selected it silently,
and it is the library that parses every document in the corpus. This is the
concrete answer to "what does pinning buy" for this repository: the deployed
parser is two major versions away from the one the requirement was written
against, and no record of that decision exists because no decision was made.

`pdfplumber` and `pillow` are **absent** from the scout container, which is the
already-recorded reason its capability fingerprint reports tables and figures
unavailable while the host reports them available.

### `host-sync` (`scripts/Dockerfile.sync`)

| Declared | Installed |
| --- | --- |
| `pip install fastapi uvicorn` (**no version at all**) | `fastapi==0.141.1`, `uvicorn==0.52.3` |
| `apt-get install -y git curl` | `git=1:2.47.3-0+deb13u1`, `curl=8.14.1-2+deb13u4` |

The `deb13` suffix records something the Dockerfile does not: `python:3.12-slim`
currently resolves to a **Debian 13 (trixie)** base. A future retag to a newer
Debian would change every apt version in one step, which is what the base-image
digest pin in step 5 stops.

### `basic-memory`

| Declared | Installed |
| --- | --- |
| `basic-memory==0.22.1` | `0.22.1` |
| *(transitive closure — undeclared)* | **163 packages** |

### Finding 4 — one pinned line, 163 unpinned packages behind it

The direct pin is real and the closure is not. This is the gap Codex named, and
the count is the argument: pinning one package out of 163 fixes 0.6% of what the
image installs.

## Installer

`scripts/install-agent.sh:36` — `git clone --depth 1 <repo>` with **no ref**, so
it tracks the default branch; `:6` documents piping that file from `main` into
`bash`. Repository tags available to pin against: **0**.

## What this record does not cover

* `uv.lock` / `pyproject.toml` — the development environment, already locked by
  `uv sync --frozen`, and not an input to any image.
* The Gitea runner's own configuration, which lives on the runner host.
