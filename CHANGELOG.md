# Changelog

All notable changes to Vyasa Agent are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0a1] — 2026-04-24

First alpha of Vyasa Agent — the Duo drop. Built for Indian indie operators,
self-hosters, and anyone who wants a named specialist fleet reachable from
their phone without their data leaving the house. This release ships the
scaffolding: console and Telegram adapters, a 29-partner roster with a
3-layer capability matrix, Graphify v2 on SQLite+WAL with a PII scrubber,
a FastAPI admin backend with settings-backed zero-hardcoding, and a vendored
runtime shell that Phase 2 will wire to real inference. Warm by default are
Dr. Sarabhai and Prometheus; the remaining 27 lazy-load on first address.
Docked for v0.2: real inference wiring, WhatsApp adapter via Baileys, the
React admin UI, and Envato buyer-license verification on the installer build.
Install: `uv tool install vyasa-agent && vyasa doctor`.

### Added
- 29-partner fleet registry: 19 Vyasa mythic partners (1 orchestrator + 18
  tiered specialists) plus 10 Graymatter Doctors, loaded from per-employee
  YAML under `employees/` with deep-merge inheritance (`46b4b13`).
- `FleetManager` + `EmployeeActor` asyncio coroutines with per-employee
  queues, concurrent-limit gating, and shared graph/audit sinks (`46b4b13`).
- `AgentRuntimeBridge` lazily constructs a scoped runtime per employee,
  installs pre/post tool-call enforcement, and resolves the verbatim system
  prompt via `registry_resolver`; `VYASA_STUB_BRIDGE=1` short-circuits to a
  reversed-text stub for offline tests (`6395007`).
- Capability matrix: `capabilities.yaml` 29x20 grid with every cell
  decisioned, 3-layer enforcement (boot filter, runtime hook, audit log),
  and handoff routing on denial (`46b4b13`).
- Graphify v2: `GraphStore` on stdlib `sqlite3` in executor with WAL,
  foreign-keys, and busy-timeout; schema v2 adds visibility,
  `owner_employee_id`, `episode_id`, `supersedes`, `status`, `ttl_days`;
  8 typed edge kinds; BFS subgraph queries (`75c5843`).
- `PIIScrubber` catches +91 phone, email, PAN, Aadhaar, and OTP context;
  fail-closed on write (`75c5843`).
- `Compactor` pipeline: checksum dedup, supersede-collapse, TTL archive
  (`75c5843`).
- MCP stdio server + `GraphifyClient` with inproc or stdio transport
  exposing graph_read / graph_query / graph_write / graph_diff (`75c5843`).
- v1-to-v2 migration script remaps legacy edge kinds, dry-run by default
  (`75c5843`).
- `MessageRouter` with `/ask`, `@mention`, sticky binding (10-min TTL), and
  orchestrator-routed default; `AliasResolver` folds case and separators;
  `StickyBindingStore` is thread-safe via `asyncio.Lock` (`d457e85`).
- Telegram adapter on python-telegram-bot v22 with chat-id allowlist, plus
  a console adapter for local dev (`d457e85`).
- Telegram streaming replies via the edit-message pattern: placeholder
  post, 1.0s edit cadence (configurable), overflow to a fresh message at
  4096 chars, 4s typing indicator, exponential-backoff `RetryAfter`,
  `[interrupted]` on cancellation (`46d1d31`).
- `StreamChunker` (paragraph → newline → sentence → word → cut) and
  `RateLimiter` (async-safe min-interval) (`46d1d31`).
- `RoutineRunner` discovers `plans/<employee_id>/*.yaml`, registers cron
  loops via `croniter` + `asyncio` and webhook callbacks on the gateway;
  every fire writes a `routine_fired` node to Graphify (`54e9b44`).
- 5 shipped routines: `vyasa/daily-morning-briefing.yaml` (08:00 IST,
  Telegram); `dr-sarabhai/weekly-retro.yaml` (Sunday 21:00 IST, graph);
  `dr-reddy/envato-license-health.yaml` (every 6h, graph);
  `kavach/capability-audit.yaml` (03:00 IST daily, graph);
  `dr-bose/graph-compaction.yaml` (every 72h, runs Compactor) (`54e9b44`).
- `SettingsOverlay` caches fleet/branding/channel keys over YAML-declared
  descriptors; `apply_overlay` merges admin-panel edits without mutating
  the YAML source; `POST /v1/admin/settings` notifies watchers; actor
  enabled flags and model preference hot-reload without restart
  (`9ee06cd`).
- FastAPI admin panel scaffold with settings-backed zero-hardcoding:
  `create_app` factory, three auth planes (gateway bearer, admin session
  cookie + double-submit CSRF), routers for messages / dispatch / handoff
  / employees / graph / admin / license; RFC 7807 problem+json error
  envelope and trace-id middleware (`fdc42cd`).
- `SettingsStore` SQLite-backed key-value with section grouping and audit;
  24 default settings across Fleet / Channels / Memory / Integrations /
  Branding (`fdc42cd`).
- Vendored runtime under `vendor/vyasa_internals`: signature-faithful shell
  of the inference loop (`AIAgent`, `run_conversation`,
  `get_tool_definitions`, `set_session_context`, `state`, `constants`);
  runtime bodies stubbed for Phase-1 Duo, real inference restored in v0.2
  (`a547bfc`).
- `scripts/fetch-vendor.sh` idempotent `--dry-run`/`--apply` sweeper
  (`a547bfc`).
- Fire-based CLI with 8 subcommands: `vyasa gateway serve`
  (`--telegram|--console`), `vyasa employee list|show|enable|disable`,
  `vyasa graph query|migrate`, `vyasa doctor` (4 offline checks, exits
  0/2 for CI), `vyasa version`; SIGTERM drains 30s (`0dc5bb1`).
- 7 CI workflows: `test.yml` (pytest on ubuntu+macos x py3.11/py3.12 via
  uv, coverage gate); `lint.yml` (ruff check + format);
  `white-label-check.yml` (donor-string scanner, 13-term blocklist);
  `license-check.yml` (SPDX headers, NOTICE protection);
  `docker-publish.yml` (multi-arch amd64+arm64, cosign OIDC keyless);
  `release.yml` (tag-driven PyPI trusted publisher, GitHub release with
  SHA256SUMS); `envato-bundle.yml` (CodeCanyon ZIP) (`643fec2`).
- Multi-stage `Dockerfile` and `docker-compose.yml` with Qdrant v1.12.5
  pinned; dependabot weekly for pip + github-actions + docker (`643fec2`).
- Brand assets: `hero.svg` (continuous quill stroke to 29-node network to
  phone silhouette), `architecture.svg` (5-tier topology), `logo.svg`,
  `logo-dark.svg`, `favicon.svg` in midnight teal / ivory / saffron;
  29 tier-coloured roster badges with 2-letter monograms (`d9c0780`).
- `README.md` with hero block, mermaid fleet topology, mermaid message
  journey, and quick-start; `docs/install.md`, `docs/roster.md`,
  `docs/api.md` (10-route skeleton), `docs/troubleshooting.md` (5
  canonical failure modes) (`d9c0780`, `bb032c5`).
- `CONTRIBUTING.md` with Conventional Commits, DCO, and quality gate
  (`bb032c5`).
- Integration suite under `tests/integration/`: 16 golden smoke tests for
  Duo mode (console, Telegram mock, graph round-trip, doctor health),
  `pytest` marker `integration` opt-in (`bb032c5`).
- Apache-2.0 `LICENSE`, `NOTICE` for upstream credits, Graymatter
  contributor constitution, `pyproject.toml` with 15 core deps and 5
  extras (`9ab89a5`).

### Changed
- n/a (first alpha).

### Deprecated
- n/a (first alpha).

### Removed
- n/a (first alpha).

### Fixed
- Graphify MCP server and client reconciled against landed `GraphStore`
  method surface (`upsert_node` / `get_node` / `query`); subprocess wire
  test deferred to Phase 2 (`3c1a6a7`).
- `PIILeakError` moved from `graphify.store` to `graphify.types`; `Node`
  imports corrected in `mcp_server` and `mcp_client` (`bb032c5`).

### Security
- Kavach sign-off: capability matrix enforcement is active across all
  three layers (boot filter, runtime hook, audit log) on every employee
  turn (`46b4b13`, `6395007`).
- `white-label-check.yml` scans source tree + built wheel/zip against a
  13-term donor-string blocklist; one hit fails the job (`643fec2`).
- Admin panel enforces bearer + session cookie + double-submit CSRF on
  write routes (`fdc42cd`).
- `PIIScrubber` fail-closed on graph write (`75c5843`).
