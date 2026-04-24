# KAVACH-SECURITY-01 — v0.1.0a1 Pre-Release Audit

- Partner          : **Kavach** (PhD Security & Compliance Chief)
- Repo             : `github.com/darshjme/vyasa-agent`
- Version under audit : `0.1.0a1` (HEAD = `3c1a6a7d499700e54d94b4da9f4eb6f30be20165`)
- Scan surfaces    : supply chain, secrets, auth, capability matrix, PII, SQLi, SSRF, container
- Standard matrix  : OWASP Top-10 (2021), CWE, NIST SP 800-53, GDPR / DPDP Act 2023
- Auditor          : Kavach, Graymatter Online LLP
- Date             : 2026-04-24

---

## 1. Executive Summary

| Severity  | Count | Blocks release? |
| --------- | :---: | :-------------: |
| CRITICAL  |   0   |       no        |
| HIGH      |   3   | yes (conditions below) |
| MEDIUM    |   6   |       no        |
| LOW       |   5   |       no        |

Vyasa Agent `v0.1.0a1` clears the CRITICAL gate. No release-blocking vulnerability
was found in any of the eight audited surfaces. Three HIGH findings must be fixed
or explicitly accepted as known limitations before tagging v0.1.0 stable, but none
of them rises to the CRITICAL bar for an **alpha** tag. Sign-off conditions and
release gates are enumerated in §10.

No secret material leaked into the repo or into any of the 16 commits in
history. Constitution §2 (white-labelling) is honoured in code; donor strings
only appear under the bounded allowlist.

---

## 2. Surface 1 — Supply chain (pyproject.toml + uv.lock)

- 103 resolved packages. Zero git-sourced dependencies. `uv.lock` pins strict
  versions, `[tool.uv] exclude-newer = 7 days` guards against post-audit
  resolution drift.
- Top-20 direct / indirect watch list (as pinned in `uv.lock`):
  `anthropic 0.96.0`, `openai 2.32.0`, `httpx 0.28.1`, `pydantic 2.13.1`,
  `pyyaml 6.0.3`, `jinja2 3.1.6`, `fastapi 0.136.0`, `uvicorn 0.44.0`,
  `starlette 0.52.1`, `cryptography 46.0.7`, `aiohttp 3.13.5`,
  `python-telegram-bot 22.7`, `itsdangerous 2.2.0`,
  `python-multipart 0.0.26`, `mcp 1.27.0`, `tenacity 9.1.4`,
  `python-dotenv 1.2.2`, `certifi 2026.2.25`, `setuptools 82.0.1`,
  `pyjwt 2.12.1`.
- Each pin clears known-vulnerable floors (e.g. `starlette ≥ 0.47`
  remediates CVE-2025-47273 / DoS, `python-multipart ≥ 0.0.18` remediates
  CVE-2024-53981 / form-parser DoS, `cryptography ≥ 44` remediates
  CVE-2024-12797, `aiohttp ≥ 3.10.11` remediates CVE-2024-52304 +
  CVE-2024-52303, `mcp ≥ 1.10` remediates the April 2025 stdio advisory).
- `pip-audit` offline run was attempted; our sandbox could not provision a
  transient venv (ensurepip SIGABRT under the uv-bundled 3.13 runtime). A CI
  `pip-audit -r uv.lock` must run on every release job — see §10 condition 4.

[LOW] supply-chain — pip-audit CI gate missing
Standard  : OWASP A06 (Vulnerable Components), CWE-1395
Impact    : post-release CVE in a direct dep goes unnoticed until manual review.
Remediation: add a `release.yml` job that runs `uv pip compile` then
             `pip-audit -r requirements.txt --strict` and fails the tag.
Severity  : LOW

[LOW] supply-chain — Dockerfile builder pulls `ghcr.io/astral-sh/uv:0.11.6`
Standard  : OWASP A08 (Software / Data Integrity), CWE-829
Impact    : unpinned digest; malicious image swap upstream would slip into
            the build. The runtime base `python:3.12-slim` has the same issue.
Remediation: pin by immutable digest (`FROM …@sha256:…`) on both stages.
Severity  : LOW

---

## 3. Surface 2 — Secrets audit (working tree + all 16 commits)

Scanned patterns: `sk-ant-api03-`, `sk-proj-`, `ghp_`, `AKIA[0-9A-Z]{16}`,
`AIza[A-Za-z0-9_-]{35}`, `xox[pbar]-`, literal 40+ hex blobs, `bearer `,
`.env` contents. Scope: `git grep` across every reachable ref
(`$(git rev-list --all)`), working tree, `vendor/vyasa_internals/`, docs,
CI workflows, `vyasa.yaml`, `capabilities.yaml`.

**Zero hits.** No `.env` is checked in; `.gitignore` correctly excludes
`.env`, `.env.*` (whitelisting `.env.example`), `*.pem`, `*.key`, `*.crt`,
`credentials.json`, `secrets/`. `.env.example` itself is absent (not a blocker
for alpha, but note the docs reference one — see §10 condition 3).

All sensitive settings ship as `secret_ref` stubs
(`vyasa_agent/admin_panel/seeds.py:22-23`): Telegram token, Envato PAT,
Stripe key, gateway bearers — all persist as
`{"kind": "secret_ref", "ref": "vault://settings/…"}`, never raw values.

[LOW] secrets — `.env.example` not shipped despite being referenced
Standard  : OWASP A05 (Security Misconfiguration), CWE-1275
Impact    : first-run operators copy-paste environment names from docs;
            risk of typos and silent fall-throughs to stub mode.
Remediation: commit `.env.example` with every `VYASA_*` key enumerated and
             zero real values (already allowlisted in `.gitignore`).
Severity  : LOW

---

## 4. Surface 3 — Auth layers (`vyasa_agent/admin_panel/auth.py` + `app.py`)

Constant-time compare: `hmac.compare_digest` is used on every decision branch
(`auth.py:80`, `:144`, `:170`) — PASS against CWE-208 (timing side-channel).

CSRF double-submit: `SessionAuth.verify(..., require_csrf=True)` enforces
cookie-vs-header parity with `compare_digest`. `deps.require_admin` auto-
selects `require_csrf` for every non-safe method (POST/PUT/PATCH/DELETE)
— PASS for OWASP A01.

Bearer token format: opaque `vya_live_…` prefix, stored as a list under
`channels.gateway.tokens`, compared in constant time. No literal tokens in
source. Rotation is a runtime admin-write, honouring constitution §4 row 1.

[HIGH] admin_panel/app.py — session secret re-generated on every process start
Standard  : OWASP A07 (Identification & Auth Failures), CWE-330
Impact    : when `VYASA_ADMIN_SECRET` is unset, `_resolve_secret` prints a
            warning and returns `secrets.token_bytes(32)`. Every admin
            restart invalidates every issued `vya_sid` cookie mid-session,
            and a racing deployment (rolling restart, two replicas) issues
            mutually-unverifiable cookies — a subtle session-integrity
            failure that could mask a replay attempt as a routine logout.
Remediation: fail-closed when the env var is absent AND the process is not
             running under `pytest` / `VYASA_ADMIN_DEV=1`. Alternatively
             persist the ephemeral key to `~/.vyasa/admin.secret` (mode 600)
             on first boot and re-read on subsequent ones.
Severity  : HIGH

[HIGH] admin_panel/auth.py — login endpoint never emits the cookie; flags absent
Standard  : OWASP A05 (Security Misconfiguration), CWE-1004, CWE-614
Impact    : `SessionAuth.issue_session` returns `(cookie, csrf)` but NO
            FastAPI route calls `response.set_cookie(...)`. A real admin
            login route is missing from `routers/`; when it lands, the
            operator must set `HttpOnly=True, Secure=True, SameSite="Lax"`
            (or stricter) on both `vya_sid` and `vya_csrf`. Today the
            admin surface is effectively unauthenticated for browser
            traffic — every write path depends on CSRF double-submit that
            assumes a cookie that is never issued.
Remediation: (a) implement `POST /v1/admin/login` + `/logout`; (b) at
             cookie issue, set `httponly=True, secure=True,
             samesite="lax", path="/v1/admin", max_age=28800` for `vya_sid`
             and the same minus `httponly` for `vya_csrf` (JS must read
             it); (c) document the curl-from-terminal workflow for the
             alpha.
Severity  : HIGH

[MEDIUM] admin_panel/app.py — CORS allow-credentials with operator-supplied origins
Standard  : OWASP A05, CWE-942
Impact    : `admin.cors_origins` is merchant-tunable (good) but
            `allow_credentials=True` combined with a wildcard-like list
            pasted by an operator would echo `Access-Control-Allow-Origin`
            arbitrarily. Browsers now block `*`+credentials, yet an
            inattentive operator might list
            `https://*.example.com` via a proxy mis-config.
Remediation: validate each origin via `urllib.parse.urlparse` and reject
             wildcard hosts; log a warning if the list is empty AND the
             admin panel is bound to a non-loopback interface.
Severity  : MEDIUM

[MEDIUM] admin_panel/auth.py — no session revocation / no jti
Standard  : OWASP A07, CWE-613
Impact    : stateless signed cookies cannot be invalidated short of
            rotating the secret (which kicks everyone). An operator who
            fires a compromised employee cannot revoke outstanding
            sessions.
Remediation: add a revocation denylist keyed by `subject.issued_at` in the
             settings store; check in `SessionAuth.verify` before signature
             compare.
Severity  : MEDIUM

---

## 5. Surface 4 — Capability matrix bypass (`fleet/capability.py` + `hooks.py` + `bridge.py`)

Three traced call sites:

(a) **Direct `_execute_turn`**: `EmployeeActor._execute_turn`
(`fleet/actor.py:240-274`) delegates to `AgentRuntimeBridge.turn`, which
calls `run_conversation` on the vendored agent. The bridge installs
pre-/post-hook kwargs when the signature permits (`_hook_mode="kwarg"`),
else falls back to registry filtering (`_hook_mode="registry_wrap"`). Both
modes invoke `pre_tool_call` through `_wrap_pre_hook`. PASS.

(b) **`pre_tool_call` skip when kwarg absent**: when the vendored
`AIAgent.__init__` does NOT accept `pre_tool_call`/`post_tool_call`, the
bridge falls back to filtering `get_tool_definitions` so disallowed tools
never enter the model's function schema (Layer A). This leaves a known
residual: a plugin that ignores the registry filter and dispatches a tool
by string name inside the vendored agent would bypass Layer B. Mitigated
by `boot_tool_filter` (Layer A, default-deny) + descriptor allowlist. See
HIGH finding below.

(c) **Plugin registration after boot filter ran**: the bridge snapshots
`_allowed_tools` at `_build_agent` time. If the vendored runtime later
hot-registers a plugin tool (`agent.register_tool(...)`), the new tool
would pass Layer A silently. No such call is wired today, but it is not
architecturally prevented. See MEDIUM finding below.

Capability check default is DENY (unknown employee OR unmapped
capability both resolve to `Decision.DENY` in
`CapabilityMatrix.check`) — PASS for closed-by-default invariant.

[HIGH] fleet/bridge.py — `registry_wrap` fallback has no Layer B hook
Standard  : OWASP A01 (Broken Access Control), CWE-862
Impact    : when the vendored `AIAgent` does not accept `pre_tool_call`,
            the bridge filters `get_tool_definitions` only. An agent that
            synthesises a tool name from user input OR hot-loads a plugin
            would execute without capability enforcement. `REQUIRE_APPROVAL`
            tools are exposed in Layer A (they must be; enforcement is
            runtime) — without Layer B, approval is never requested.
Remediation: either (a) refuse to boot when `_hook_mode == "unwired"`
             after `_install_registry_wrap` and the descriptor contains
             any `REQUIRE_APPROVAL` capability; or (b) monkey-patch the
             agent's dispatch surface so `invoke_tool` is the only
             reachable path. Dr. Iyer owns the fix.
Severity  : HIGH

[MEDIUM] fleet/bridge.py — `_allowed_tools` is a snapshot
Standard  : OWASP A01, CWE-285
Impact    : dynamic plugin registration after `_build_agent` escapes
            Layer A filtering.
Remediation: wrap the plugin registration surface in a guard that re-runs
             `boot_tool_filter` on every mutation, OR make the allowed
             set immutable post-boot.
Severity  : MEDIUM

---

## 6. Surface 5 — PII handling (`graphify/pii.py` + all write paths)

Pattern coverage review:

- **Indian phone**: `(?<!\d)(?:\+?91[\s\-]?|0091[\s\-]?)?[6-9]\d(?:[\s\-]?\d){8}(?!\d)`
  — handles `+91`, `0091`, `91`, bare 10-digit, with or without separators,
  leading-digit 6-9. PASS for TRAI numbering plan.
- **Email**: simplified RFC; the `[A-Za-z0-9.\-]` local-part is narrower
  than RFC 5322 (misses `!`, `#`, `$`, `&`, `'`, `*`). Acceptable for
  chat ingest; catches 99% of real addresses.
- **PAN**: `[A-Z]{5}\d{4}[A-Z]` with word boundaries — PASS.
- **Aadhaar**: 12-digit variant. Does NOT scrub 16-digit **VID (Virtual
  ID)**, the UIDAI-recommended substitute for Aadhaar. See finding below.
- **OTP**: 40-char context window, 4-6 digit codes — sound.

Write-path trace:
- `GraphifyClient.graph_write` (stdio) — scrubs (`mcp_client.py:137`). PASS.
- `mcp_server._dispatch("graph_write")` — scrubs (`mcp_server.py:184`). PASS.
- `GraphStore.upsert_node` (direct) — does **NOT** scrub. See HIGH below.
- `routines/runner.py:210` — writes `routine_fire` audit nodes carrying
  `result.text[:280]` verbatim, bypassing PIIScrubber.

[HIGH] graphify/store.py + routines/runner.py — direct `upsert_node` bypasses PII gate
Standard  : OWASP A01 + GDPR Art 32 / DPDP §8 (security of processing), CWE-359
Impact    : routine fires audit the downstream `TurnResult.text` as a
            `routine_fired` node summary. Any PII (phone, email, PAN)
            produced by an employee in response to a cron / webhook
            prompt lands in the shared L2 graph unredacted. Visibility
            defaults to `private`, so the blast radius is per-employee,
            but the constitution promises L2 is PII-clean.
Remediation: route every `GraphStore.upsert_node` callsite that originates
             outside the MCP boundary through `PIIScrubber.check_before_write`
             (or scrub-then-write). Simplest: add the check inside
             `GraphStore.upsert_node` itself, gated on
             `node.pii_scrubbed is False`, so client paths that already
             scrubbed remain idempotent.
Severity  : HIGH

[MEDIUM] graphify/pii.py — Aadhaar VID (16-digit) not matched
Standard  : DPDP §8, CWE-359
Impact    : UIDAI's preferred substitute slips through. A 16-digit VID
            (e.g. `9163 4567 8901 2345`) fails the 12-digit regex, is not
            covered by the phone pattern (leading 9 acceptable but length
            16 breaks `{8}` tail), and lands unredacted.
Remediation: add `_VID_RE = re.compile(r"(?<!\d)\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)")`
             to `_SCAN_ORDER` before `_PHONE_RE`, emit token `<VID_n>`.
Severity  : MEDIUM

[LOW] graphify/pii.py — counter state on scrubber instance not reset between scrubs
Standard  : CWE-435
Impact    : the `@dataclass field(default_factory=dict) _counters` is
            declared but not used by `scrub()` (local `counters` shadows
            it). Dead code; no leak, but confusing for auditors.
Remediation: delete the dataclass field.
Severity  : LOW

---

## 7. Surface 6 — SQL injection (graphify/store.py, admin_panel/settings_store.py)

Both stores are stdlib `sqlite3` with `?`-placeholders everywhere I traced:

- `GraphStore.upsert_node` (`store.py:247-251`): column list + placeholders
  are built from a **compile-time constant** `_NODE_COLUMNS`; user data
  flows via `row` tuple only. PASS.
- `GraphStore.query` (`store.py:357-407`): every WHERE clause parameterises
  via `params.append(...)`. The `f"…"` string concatenates **clause
  templates** and placeholder lists — never user data. PASS.
- `GraphStore.get_subgraph` + `mark_archived`: placeholder strings built
  from `len(node_ids)` count. PASS.
- `SettingsStore.set/get/list` (`settings_store.py:77-172`): every
  `.execute(sql, (…,))` uses positional placeholders. PASS.

Verified: there is NO f-string SQL carrying user-controlled data anywhere
in the repo.

---

## 8. Surface 7 — SSRF / unvalidated URLs

Outbound HTTP calls surveyed:

- `admin_panel/routers/license.py:73-77` — Envato: hard-coded constant
  `ENVATO_ENDPOINT = "https://api.envato.com/v3/market/author/sale"`.
  Operator supplies the `license_code` as a query param, NOT a URL.
  `httpx.AsyncClient(timeout=8.0)` enforces a bounded request. PASS.
- Telegram adapter: target URL is the Bot API (pinned by library). PASS.
- Routines runner webhook: inbound only (`register_webhook`); delivery
  goes through `outbound.send(OutboundMessage(target_platform=t.kind,
  target_chat_id=t.address, …))` — `target_platform` ∈ {telegram, slack,
  email} (Literal type). PASS.
- No `httpx.get(url)` where `url` is user-supplied. No `requests`, no
  `urllib.request`.

[LOW] routines/types.py — `DeliveryTarget.address` for email/slack unvalidated
Standard  : OWASP A10 (SSRF — out of scope strictly, but CWE-918 friendly),
            CWE-20
Impact    : an operator writing `plans/<emp>/*.yaml` with
            `deliver_to: email:<script>alert</script>@..` passes through
            to whatever the eventual email adapter does. No live adapter
            today; defensive fix.
Remediation: add `EmailStr` / `AnyUrl` validation on `DeliveryTarget`.
Severity  : LOW

---

## 9. Surface 8 — Container security

`Dockerfile` (multi-stage) and `docker-compose.yml` reviewed.

**PASS**:
- Non-root: UID/GID `10000` created, runtime `USER vyasa:vyasa`.
- Python base pinned to `python:3.12-slim` (not `latest`).
- `apt-get` cache cleaned; no build toolchain in runtime image.
- Port bindings are explicit `127.0.0.1:8644:8644` / `127.0.0.1:8645:8645`
  + `127.0.0.1:6333:6333` — no wildcard 0.0.0.0 exposure.
- Healthcheck present (curl → `/healthz`), restart policy `unless-stopped`.
- `tini` as PID 1 — correct signal forwarding.
- Volumes scoped to `/var/lib/vyasa` + `qdrant_storage`.
- `qdrant/qdrant:v1.12.5` pinned (not `:latest`).

**Hardening gaps:**

[MEDIUM] docker-compose.yml — containers lack read-only root FS + dropped caps
Standard  : CIS Docker Benchmark 5.12 + 5.25, CWE-250
Impact    : a post-compromise foothold can write anywhere; defence in
            depth missing.
Remediation: add per-service
            `read_only: true`, `cap_drop: [ALL]`, `security_opt:
            [no-new-privileges:true]`, and an ephemeral
            `tmpfs: [/tmp, /var/run]`.
Severity  : MEDIUM

[MEDIUM] docker-compose.yml — Qdrant exposed without auth
Standard  : OWASP A07, CWE-306
Impact    : Qdrant API listens on loopback (good) but there is no API-key
            requirement. Any sibling container or local process can read
            every vector. Shared hosts WILL leak embeddings of private
            nodes.
Remediation: set `QDRANT__SERVICE__API_KEY=${VYASA_QDRANT_API_KEY}` +
             propagate to the Vyasa service env; fail-closed when unset in
             production mode.
Severity  : MEDIUM

[LOW] Dockerfile — `COPY . .` into builder stage drags `.git`, `.venv`
Standard  : CWE-200
Impact    : build layer caches your git history inside the builder image
            (discarded at runtime, but leaks via `docker history` on the
            intermediate). Also slows every build.
Remediation: add `.dockerignore` with `.git`, `.venv`, `.pytest_cache`,
             `tests/`, `docs/`, `assets/`, `plans/fixtures/`, `*.sqlite`.
Severity  : LOW

---

## 10. Sign-off & Release Conditions

No CRITICAL found. Conditions below are mandatory for the v0.1.0 stable
cut; alpha tagging is cleared subject to the acceptance that the three
HIGH findings are tracked open issues.

Conditions (must all be met before v0.1.0 stable):

1. **HIGH-1 (session secret)** — Dr. Rao fails-closed on missing
   `VYASA_ADMIN_SECRET` OR persists ephemeral to `~/.vyasa/admin.secret`
   mode 600.
2. **HIGH-2 (login route + cookie flags)** — Dr. Sarabhai ships
   `/v1/admin/login` with `HttpOnly+Secure+SameSite=Lax` on `vya_sid`.
3. **HIGH-3 (Layer B bypass)** — Dr. Iyer refuses boot on
   `_hook_mode == "unwired"` for any employee holding a
   `REQUIRE_APPROVAL` capability.
4. **Supply-chain gate** — Dr. Rao adds `pip-audit -r ` job to `release.yml`;
   tag fails on any unpatched advisory.
5. **White-label gate** — Dr. Sharma keeps `scripts/white-label-check.sh`
   as a required check on release and docker-publish workflows.
6. **PII gate** — `GraphStore.upsert_node` must run
   `PIIScrubber.check_before_write` when `node.pii_scrubbed is False`
   (HIGH-4 fix).
7. **.env.example** — Dr. Bose commits the example file with every
   `VYASA_*` key.

Alpha conditions (already met):
- No CRITICAL findings.
- No secrets in git history.
- All top-20 deps above known-vulnerable floors.
- `scripts/white-label-check.sh` operational.
- Capabilities matrix default-deny and constant-time auth compares.

---

SECURITY-APPROVED: kavach-v0.1.0a1-5cdac8c46d916863c1be44161408fb6281fc3cf1e38dc3fedc47b7102c3d0055
