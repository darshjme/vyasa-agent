# REDDY-ENVATO-AUDIT-01 — Envato Compliance Baseline

**Author:** Dr. Meera Reddy — Chief of Security, Graymatter Online LLP
**Date:** 2026-04-24
**Target:** `vyasa-agent` v0.1.0a1 (Apache-2.0, pre-CodeCanyon)
**Authority:** `CLAUDE.md §4` — "FLEET STANDARD (CodeCanyon-grade)"
**Status signal:** `NOT-FOR-ENVATO` (open-source release only; no paid SKU yet)

> *"No product ships to CodeCanyon without my signature. Envato purchase-code
> verification is a live route, fail-closed. The 1-click installer writes
> `.env`, runs migrations + seeders, creates first admin, and LOCKS itself."*

---

## 1. Scope — what Envato ships for SaaS/agent products

Envato Market (CodeCanyon) expects each "PHP Script / App & Plugin" listing
to meet a consistent shape. Vyasa is Python-native, so we submit under the
**"App & Plugin > Other"** category with explicit Python runtime disclosure.

| Dimension | Envato norm | Vyasa policy |
|---|---|---|
| Runtime | PHP is the default; Python/Node are accepted when declared up-front in the listing and documented in the installer. | Python 3.11+ via `uv` / `pipx`. No PHP. |
| Licence | **Regular** (single end-product, no resale as SaaS) vs **Extended** (SaaS OK, still one deployment per licence). | We sell Extended only — Vyasa is a fleet-operator tool; Regular licence is meaningless. |
| Installer | One-click web wizard expected: precheck → DB cred → admin user → licence activate → lock. | Must build in v0.2 (see §4). |
| Documentation | Self-contained HTML bundle inside the ZIP; every admin setting has a screenshot. | Partial (`docs/html/` stub). |
| Licence verification | Live call to Envato Author API on install + periodic re-verify. No `DEV_BYPASS` in shipped build. | Route exists in stub mode (see §2). |
| White-label | Product name, logo, colours, legal text must be admin-configurable (resellers depend on this). | Partial (`branding.*` seeded; renderer missing). |
| Support | Six-month buyer support mandatory; public support URL on listing. | Need `support@graymatteronline.com` gate in place. |

---

## 2. Gap matrix — CLAUDE.md §4 × current state

| # | Directive | Current state | Gap | Owner | Target |
|---|---|---|---|---|---|
| 1 | **Zero hardcoding.** Tunables in `settings` table + Admin Panel. | `vyasa_agent/admin_panel/seeds.py` seeds 24 defaults across `fleet`, `channels`, `memory`, `integrations`, `branding`; `settings_store.py` persists overlays; `SettingsStore` is wired into `FleetManager`. | None for v0.1. Admin UI is JSON-API only — no HTML panel yet (tracked under directive 4). | Dr. Iyer | v0.1 — MET |
| 2 | **Envato buyer-license verification**, live, fail-closed, time-boxed cache. | `vyasa_agent/admin_panel/routers/license.py` exposes `POST /v1/license/verify`. If `ENVATO_PERSONAL_TOKEN` is unset and `integrations.envato.personal_token` is a `secret_ref`, the route returns `{ok:true, mode:"stub"}` — **fail-open**. No cache. No audit trail. No scheduled re-verify. | Enforce fail-closed when installer ran in "paid" mode; add Redis/sqlite TTL cache (24h); persist verification receipts; nightly re-verify cron; remove stub branch in the Envato build. | Dr. Reddy | v0.2 |
| 3 | **1-click web installer.** `install/` wizard: precheck, DB migrate, asset publish, licence activate, first-admin create, self-lock. | `scripts/install.sh` is a host-side bash installer (uv/pipx + LaunchAgent/systemd unit). There is **no web wizard**, no DB-cred form, no admin-user form, no self-lock, no `install/` directory. | Build `install/` blueprint as a FastAPI sub-app mounted at `/install` (see §4). Lock to `install.locked.<timestamp>/` on success. Gate all admin routes behind lock-file presence. | Dr. Rao | v0.2 |
| 4 | **Dynamic branding.** Name, logo, colour, favicon, legal text — all admin settings. | `branding.*` keys seeded (`product_name`, `logo_url`, `primary_color`, `accent_color`, `ivory_color`, `typography.primary`, `white_label.enabled`, `locale.default`, `locale.enabled`). No renderer consumes them — Admin Panel has no templates (`vyasa_agent/admin_panel/static/` is empty). | Ship Jinja2 templates for `/admin/*` HTML that read `branding.*` on every render; theme tokens injected as CSS custom properties; logo upload endpoint; favicon served from settings; footer legal text block. | Dr. Krishnan | v0.2 |
| 5 | **Self-contained HTML documentation** in the ZIP; no external CDN; every setting screenshotted. | `docs/*.md` (`api.md`, `install.md`, `roster.md`, `troubleshooting.md`) are markdown only. `scripts/envato-bundle.sh` writes a single placeholder `docs/html/index.html` that literally says *"Replace with the rendered user manual before Envato submission."* Zero screenshots. | Wire `mkdocs` or a Jinja renderer into `envato-bundle.sh`; capture screenshots via Playwright against a seeded demo instance (one per settings section = ~24); bundle fonts locally (no Google Fonts CDN). | Dr. Sharma | v0.2 |
| 6 | **Playwright E2E green + Kavach security sign-off.** | `tests/integration/` contains 4 files (`test_doctor`, `test_duo_console`, `test_duo_telegram`, `test_graph_round_trip`) — all Python/pytest, all stubbed inference. Zero Playwright. Zero security gate in CI. | Add `playwright` dev-dep; write E2E for the 5-step install wizard + 10 admin flows against a demo instance; Kavach / Reddy sign-off workflow posts to the release PR as a required check; evidence URLs attached to the tag description. | Dr. Sharma + Agni | v0.3 |

**Score:** 1 / 6 directives met for v0.1.0a1. This is **expected** — v0.1.0a1 is
the open-source alpha, not the CodeCanyon SKU.

---

## 3. Envato licence-flow spec (the live route)

### Endpoint

```
POST /v1/license/verify
Authorization: Bearer <admin_token>
Content-Type: application/json

{
  "license_code": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "buyer_email":  "buyer@example.com",    // optional, cross-check
  "product_code": "vyasa-agent"           // optional, gate to our SKU
}
```

### Envato upstream call

```
GET https://api.envato.com/v3/market/author/sale?code=<license_code>
Authorization: Bearer <ENVATO_PERSONAL_TOKEN>
User-Agent: vyasa-agent/<version> (+https://vyasa.graymatteronline.com)
```

Required response fields consumed: `item.name`, `item.id`, `buyer`,
`purchase_count`, `sold_at`, `licence`, `supported_until`.

### Cache + fail-closed behaviour

| Condition | Response | Log |
|---|---|---|
| Token missing **and** `VYASA_ENV=production` | `503 license provider unavailable`, do not fall back to stub. | `CRIT license.token_missing` — alert Reddy. |
| Token missing **and** `VYASA_ENV=development` | `{ok:true, mode:"stub"}` (current behaviour). | `WARN license.stub_mode`. |
| Envato returns 404 | `{ok:false, valid:false}` cached 60s (negative cache, short). | `INFO license.invalid` + audit row. |
| Envato returns 5xx or timeout (8s) | `503`; fall back to last-known cached positive receipt if age < 24h. | `WARN license.upstream_degraded`. |
| Envato returns 200 | `{ok:true, valid:true, mode:"live", tier, expires_at, buyer, purchase_count, cached_for_s:86400}`, write receipt, cache 24h. | `INFO license.verified`. |

### Retry policy

- `tenacity.retry` with `stop_after_attempt(3)`, exponential back-off 1s/2s/4s.
- Circuit-breaker opens after 10 consecutive upstream failures in 5min — serves `503` until next 30s probe succeeds.

### Audit trail

- SQLite table `license_receipts` in `~/.vyasa/admin.sqlite`:
  `id TEXT PK, license_code_hash TEXT, buyer_hash TEXT, item_id INT, verified_at TIMESTAMP, expires_at TIMESTAMP, source TEXT (live|cache|stub), receipt_json TEXT`.
- `license_code` is stored as SHA-256, never plaintext.
- Receipts retained `memory.retention_days` (default 365).

### Re-verification cron

- `routines/license_refresh.yaml` fires every 24h at 03:17 local.
- If last positive receipt > 48h old and upstream is green, re-verify and update.
- If upstream has been down > 7d, flip admin banner to *"Licence unverifiable — contact support"*.

---

## 4. 1-click installer spec (v0.2 deliverable)

### Runtime decision: Python + uv (skip PHP)

Vyasa is Python-native. Shipping a PHP wizard would require bundling a PHP
runtime or requiring shared-hosting PHP — either way we'd maintain two code
paths for one form. Instead we ship a **self-contained FastAPI sub-app**
mounted at `/install` that the bootstrap script launches on port 8645 before
the main gateway is configured.

**Precheck justification** table shown to the buyer:

| Check | Requirement | Rationale |
|---|---|---|
| Python | >= 3.11 | `match` statement + asyncio improvements. |
| `uv` or `pipx` | present, installable | Reproducible venv. |
| Disk free | 2 GB in `VYASA_HOME` | Graph + memory + vendor. |
| Outbound HTTPS | 443 to `api.envato.com` | Licence verify. |
| SQLite | 3.35+ (bundled with CPython) | Graphify + admin store. |
| Write permission | `~/.vyasa/` | Config, logs, graph, receipts. |

### User flow (5 steps, ~3 min)

1. **Precheck page** — runs the matrix above; every row must be green to proceed. Amber row → inline fix hint. Red row → block.
2. **DB credentials form** — SQLite path (default `~/.vyasa/admin.sqlite`); optional Postgres DSN for multi-node deployments. Connection test button. On success, runs Alembic migrations and seeds `DEFAULTS` from `admin_panel/seeds.py`.
3. **Admin-create form** — email, password (bcrypt via `passlib`), display name. Writes first row to `admins` table; generates signing key for JWT; stores in `~/.vyasa/secrets/jwt.key` with `0600`.
4. **Licence-activate form** — Envato purchase code + buyer email. Calls `POST /v1/license/verify` live. On `{valid:true, mode:"live"}`, writes receipt and unblocks step 5. In open-source mode, step is skipped but receipt recorded as `source:"oss"`.
5. **Completion + self-lock** — writes `install.locked.<timestamp>` marker at `~/.vyasa/`; installer routes return `410 Gone` on next request; redirects to `/admin/login`.

### Data model

```
admins          (id, email, pwd_bcrypt, display_name, created_at, last_login_at)
license_receipts (see §3)
install_state   (step TEXT, completed_at TIMESTAMP, checksum TEXT)
settings        (key TEXT PK, section TEXT, value_json TEXT, schema_json TEXT, updated_at)
```

All tables created in one Alembic revision `0001_envato_baseline.py`, idempotent.

### Lock enforcement

- `vyasa_agent/admin_panel/app.py` checks `~/.vyasa/install.locked.*` at startup.
- If absent → mount `/install` router only.
- If present → mount `/admin/*` and return `410` on `/install/*`.

---

## 5. Red-flag pre-publish checklist (12 items)

Dr. Reddy runs every item before signing off an Envato submission. One amber = pause, one red = block.

- [ ] **Brand parity** — `branding.product_name` in settings matches listing title, ZIP filename, README hero, `docs/html/`.
- [ ] **Screenshot accuracy** — every screenshot in `docs/html/` matches the current UI (Playwright re-capture + visual diff < 2%).
- [ ] **Licence-flow live test** — submit a real Envato sandbox purchase code against staging; expect `{valid:true, mode:"live"}` end-to-end in < 3s p95.
- [ ] **White-label scanner pass** — `scripts/white-label-check.sh` against the staged ZIP returns 0 hits; CI green.
- [ ] **Version bump** — `pyproject.toml`, `CHANGELOG.md`, `README.md` hero, bundle filename, Envato "Version" field all carry the same string.
- [ ] **Dependency audit** — `uv pip audit` and `pip-audit` both clean; no CVEs ≥ High in the shipped wheel.
- [ ] **SBOM** — `cyclonedx-bom` output attached to release; checksums in `SHA256SUMS.txt`.
- [ ] **Privacy-policy URL** — live at `https://vyasa.graymatteronline.com/privacy`; linked from Envato listing and Admin Panel footer.
- [ ] **Refund-policy URL** — live at `/refund`; linked from listing.
- [ ] **Demo-site parity** — public demo at `demo.vyasa.graymatteronline.com` runs the exact same build hash as the ZIP.
- [ ] **Support-channel live** — `support@graymatteronline.com` auto-responds within 4h; six-month buyer-support clock documented in listing.
- [ ] **Installer smoke test on shared hosting** — full 5-step wizard on a fresh DigitalOcean $6 droplet + a Hetzner shared host; zero manual SSH steps.

---

## 6. Pre-Envato security posture (required before paid SKU)

Before any CodeCanyon submission is even considered, these must hold:

1. **Zero secrets in repo** — `trufflehog` + `gitleaks` both clean on full history.
2. **All admin routes authenticated** — JWT bearer + CSRF token for cookie sessions; anonymous request returns `401` within 50ms, no info leak.
3. **Rate limit on `/v1/license/verify`** — 10 req/min per IP, 60 req/hour per admin token; 429 with `Retry-After`.
4. **Supply-chain lock** — `uv.lock` committed, CI refuses unlocked dep; `vendor/` pinned via `scripts/fetch-vendor.sh` checksums.
5. **Transport** — `docker-compose.yml` terminates TLS via Caddy; HTTP redirects to HTTPS; HSTS 1y preload candidate.
6. **Secret storage** — `vault://` `secret_ref` resolver implemented (currently placeholder in `seeds.py`); secrets never round-trip to JSON API.
7. **Audit log immutability** — append-only file or signed chain for `license_receipts` and `admins` changes.
8. **Backup / DR** — documented restore from `~/.vyasa/admin.sqlite` snapshot in < 15 min.
9. **Incident runbook** — `docs/security/incident.md` covering leaked licence-token rotation.
10. **CVE watch** — Dependabot + `pip-audit` cron; 48h SLA to patch High/Critical.

---

## 7. Verdict

```
Directive match:   1 / 6  (Zero hardcoding — MET)
Open gaps:         5 (licence live route, web installer, branding renderer,
                      HTML docs + screenshots, Playwright E2E + sign-off)
Earliest Envato-ready build:  v0.3
```

### Sign-off

**Release tag v0.1.0a1:** `NOT-FOR-ENVATO` — open-source alpha only. Not a CodeCanyon submission, not BLOCKED. Ship the Apache-2.0 build; keep the gap matrix above as the v0.2 / v0.3 backlog.

— **Dr. Meera Reddy**, Chief of Security, Graymatter Online LLP
  *Envato sign-off deferred; OSS sign-off granted.*
