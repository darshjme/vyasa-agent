# API contract

Vyasa Agent exposes a small HTTP surface via the FastAPI gateway. v0.1-alpha
ships a skeleton with ten routes; the full OpenAPI document arrives in v0.2.

Base URL defaults to `http://127.0.0.1:8080`. Override with the `VYASA_HTTP_BIND`
environment variable or the `gateway.bind` key in `vyasa.yaml`.

## Authentication

- `Authorization: Bearer <token>` where `<token>` is read from
  `~/.vyasa/admin.token` (created on first boot, chmod 600).
- Telegram webhook requests are authenticated by a rotating per-bot secret
  path segment, not by the bearer token.

## Routes

| # | Method | Path | Partner owner | Purpose |
|---|--------|------|---------------|---------|
| 1 | GET  | `/healthz`                 | Indra      | Liveness probe. Always 200 once the process is up. |
| 2 | GET  | `/readyz`                  | Indra      | Readiness. 200 only if graph + warm partners loaded. |
| 3 | POST | `/v1/messages`             | Dr. Sarabhai | Submit a message to the fleet (console adapter uses this). |
| 4 | GET  | `/v1/messages/{id}`        | Dr. Sarabhai | Fetch a message and its reply thread. |
| 5 | POST | `/v1/telegram/webhook/{secret}` | Dr. Sarabhai | Telegram bot webhook receiver. |
| 6 | GET  | `/v1/fleet`                | Vyasa      | List partners with warm/cold state, capability flags. |
| 7 | GET  | `/v1/graph/query`          | Dr. Bose   | Query the Graphify v2 store by intent or node id. |
| 8 | POST | `/v1/graph/nodes`          | Dr. Bose   | Upsert a node. Owner and checksum enforced server-side. |
| 9 | GET  | `/v1/admin/license`        | Dr. Reddy  | License state. Scaffolded; live Envato check in v0.2. |
| 10 | GET | `/v1/doctor`               | Vishwakarma | Machine-readable form of `vyasa doctor`. |

## Request and response shape

All routes use JSON. Success responses carry `{"ok": true, "data": ...}`;
errors carry `{"ok": false, "error": {"code": "...", "message": "..."}}`.

Timestamps are ISO-8601 UTC with a trailing `Z`. Money values are decimal
strings, never floats.

## Rate limits

In v0.1-alpha there are no hard rate limits, but the capability matrix
caps per-partner tool invocation to 64 calls per minute. The gateway
returns HTTP 429 with a `retry_after` header when the cap trips.

## OpenAPI

`vyasa gateway serve --console` does not expose a Swagger UI in
v0.1-alpha. The full OpenAPI 3.1 document (including request/response
schemas and error catalogue) lands in v0.2 at `/openapi.json`.

## Reference

This skeleton is the user-facing extract of **design-06 (API contract)**.
The full design doc lives in `~/repos/vyasa-agent-kb/design-06-api.md`
during development and will ship in-repo at v1.0.
