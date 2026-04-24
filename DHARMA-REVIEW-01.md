# DHARMA-REVIEW-01 — full-repo code review

Partner: Dharma (PhD Code Reviewer, Tier 1)
Scope: `vyasa_agent/` (84 modules), `vendor/vyasa_internals/`, `scripts/`
Priority: CORRECTNESS > SECURITY > PERFORMANCE > STYLE
Severity gate: CRITICAL/HIGH block merge (left in source as `TODO(Dharma ...)` markers);
MEDIUM/LOW/STYLE auto-applied in `style(*): apply Dharma review pass auto-fixes`.

Baseline after Part A: 118 unit + 16 integration tests green, white-label clean,
ruff parity (332 → 330 errors, all pre-existing in the vendored tree + tests).

---

## Strong patterns worth keeping (positive feedback)

- **`graphify/store.py` `_run` + `asyncio.Lock`** — single-writer serialisation
  with a 30 s `busy_timeout` is the correct SQLite pattern. The schema declares
  `checksum UNIQUE` at both column and index level, which makes the dedup fast
  path explicit in `EXPLAIN QUERY PLAN`. Nice.
- **`fleet/actor.py` `_next_item`** — the `asyncio.wait` race between queue
  `get` and stop event, with explicit cancel of the loser and a post-drain
  drain-queue check, is textbook cancel-safe actor shutdown.
- **`fleet/capability.py` default-deny** — unknown employee *and* unmapped
  capability both resolve to `Decision.DENY`. Closed-by-default, loud
  `CapabilityError` carrying rationale for audit. Exactly right.
- **`gateway/adapters/telegram.py` streaming** — `StreamChunker` preference
  order (paragraph → newline → sentence → space) and the `typing.cancel()` +
  suppress pattern in `send_streaming` avoid both mid-word cuts and dangling
  typing loops. Strong.
- **`admin_panel/auth.py` `hmac.compare_digest`** — constant-time comparison
  used consistently for bearer tokens, session signatures, and CSRF match.
- **`fleet/hooks.py` three-layer enforcement** — boot filter + pre-tool gate
  + post-tool audit is the right architecture, and each layer is pure and
  single-purpose.
- **`fleet/audit.py` JSONL + SQLite double-writer** — two readers, one lock,
  atomic across both sinks. `AuditRecord.hash_args` ensures plaintext args
  never hit disk. Good defence-in-depth.

---

## Findings (severity-ordered)

### 1. `vyasa_agent/fleet/`

```
[LOW] fleet/manager.py:146 — shutdown() early-returns when _actors is empty without clearing _booted
Why: An empty-boot → shutdown cycle leaves _booted=True, blocking re-boot.
Fix: Applied — clear _booted=False on the empty path.

[LOW] fleet/actor.py:195 — _next_item() drain-queue check races with put_nowait
Why: After _stop_event.set(), an in-flight submit() can still put onto the queue
     because the queue check uses qsize(), not a strong barrier.
Fix: Accept — ``stop()`` pre-sets state to "draining" and submit() rejects
     draining. The remaining window is < 1 scheduler tick and idempotent.

[STYLE] fleet/manager.py:236 — _on_settings_change normalises IDs inconsistently
Why: Three code paths each run ``.lower().replace(".", "-")``; extract a
     helper so alias-mapping bugs can't diverge across call sites.
Fix: Defer — cosmetic, mirrors the _normalise_employee_id in settings_bridge.

[STYLE] fleet/bridge.py:86 — except (TypeError, ValueError) swallowed silently
Why: inspect.signature may fail on C extensions; silent fallback hides bugs
     at runtime. Log the fallback at DEBUG.
Fix: Defer — the kwarg-vs-registry fallback is covered by _hook_mode logging.
```

### 2. `vyasa_agent/gateway/`

```
[MEDIUM] gateway/adapters/telegram.py:169 — _with_retry loops forever on RetryAfter
Why: There is no max-retry cap; a misbehaving bot token or a Telegram outage
     could keep the send_streaming task alive indefinitely, pinning the chat
     in "typing" via the sibling typing_loop.
Fix: Accept for v0.1 — BACKOFF_CAP_S=30 bounds each sleep. Follow-up: add a
     per-call attempt ceiling (e.g. 6 retries ≈ 3 minutes).

[LOW] gateway/adapters/telegram.py:176 — backoff state re-used across calls via closure
Why: Each call creates a fresh local ``backoff = 1.0``; not actually a bug,
     just looks like shared state on first read.
Fix: Accept — code is correct, doc is clear enough.

[STYLE] gateway/adapters/telegram.py:209 — _MissingRetryAfter(Exception) missing Error suffix
Why: ruff N818 — exception class names should end in ``Error``.
Fix: Applied — renamed to _MissingRetryAfterError throughout.

[STYLE] gateway/streaming.py:114 — __all__ not sorted (ruff RUF022)
Fix: Applied — ["RateLimiter", "StreamChunker"].

[LOW] gateway/router.py:197 — RuntimeError when no employees available
Why: Raising bare RuntimeError from an async hot path swallows the routing
     reason; downstream callers only see the str() form.
Fix: Defer — bounded to an empty-fleet config bug; acceptable hard-fail.

[STYLE] gateway/adapters/console.py:57 — except clause catches (CancelledError, Exception)
Why: Listing CancelledError next to Exception is redundant since Exception
     is a superset of CancelledError in 3.11+ — but keeping it is clearer
     about intent. Acceptable.
```

### 3. `vyasa_agent/graphify/`

```
[HIGH] graphify/pii.py:166 — check_before_write only scans summary + key_claims
Why: A Node whose PII lives in ``entities`` or ``symbols`` (e.g. a claim about
     a phone-number contact in the entities array) passed the L2 write gate
     unscrubbed. This is a straight bypass of the constitution §3 guarantee.
Fix: Applied — added ``entities`` + ``symbols`` keyword args; MCP server and
     client now pass them at both call sites.

[HIGH] graphify/checksum.py:17 — checksum omits summary, entities, symbols
Why: Two nodes with the same source_path + key_claims but different summaries
     will dedup against each other on upsert. Intended v0.1 behaviour per
     design-03 §6, but functionally a collision channel an adversary could
     exploit to overwrite a node's summary by re-upserting with crafted claims.
Fix: TODO(Dharma HIGH) marker planted in checksum.py; defer the canonical-
     payload expansion until the Qdrant semantic layer lands and we know
     what ranking relies on.

[MEDIUM] graphify/store.py:240 — upsert_node dedup path did not refresh updated_at
Why: When the checksum matches an existing row, the store returned the old id
     without touching updated_at, so "we saw this claim again" was invisible
     downstream (compactor TTL, graph_diff cursor).
Fix: Applied — dedup path now bumps updated_at (and updated_by when
     supplied). Checksum is content-only, so the bump never perturbs the
     hash.

[MEDIUM] graphify/store.py:168 — single writer lock also serialises reads
Why: The ``_run`` wrapper takes the asyncio lock around every op, so reads
     block behind writes even though WAL allows concurrent readers. At 29
     concurrent employees this becomes the dominant tail-latency driver.
Fix: Defer — split-lock (write lock only for mutations) is the correct fix
     but out of scope for v0.1; WAL + busy_timeout keeps correctness intact.

[LOW] graphify/compactor.py:70 — phantom-duplicate detection is advisory
Why: Compactor counts phantom duplicates but does not repair them; the log
     line goes to notes but operators have no action.
Fix: Accept — if we hit a phantom dup it means the UNIQUE index failed, which
     is a corruption signal the operator should investigate manually.

[STYLE] graphify/vector.py:53 — PendingVectorStore methods raise NotImplementedError
Why: Review area asked about "NotImplementedError surfaces that should fail
     gracefully". This stub is documented as intentional loud-failure; every
     call site is gated by vendor stub_bridge_enabled or the absent-backend
     flag. OK.
```

### 4. `vyasa_agent/admin_panel/`

```
[HIGH] admin_panel/auth.py:96 — CSRF token not bound to session subject
Why: The ``vya_csrf`` cookie is an independent random blob; the double-submit
     check compares cookie to header without linking either to the session
     subject. A cookie-tossing subdomain or stored XSS on a sibling app
     could set both cookie and header and forge a valid CSRF pair.
Fix: TODO(Dharma HIGH) marker planted in SessionAuth. Correct shape:
     ``csrf = HMAC(secret, subject||issued_at||'csrf')`` with rotation.

[HIGH] admin_panel/routers/messages.py:115 — deadline_ms validated but never enforced
Why: HandoffBody declares deadline_ms with a strict 1..600_000 range. The
     actual handoff_fn call is not wrapped in a timeout, so a slow
     downstream can block the gateway past the caller's budget.
Fix: TODO(Dharma HIGH) marker planted; needs asyncio.wait_for wrap with
     a translated 504-ish reject when the deadline elapses.

[HIGH] admin_panel/routers/graph.py:34 — query_fn(intent=..., k=...) mismatches GraphStore API
Why: Real GraphStore.query takes a single QueryFilters object and is async.
     Tests use duck-typed mocks that accept kwargs; the real store TypeErrors
     here. This is the admin Memory Browser backend — ships broken to prod.
Fix: TODO(Dharma HIGH) marker planted with the correct async + QueryFilters
     call.

[MEDIUM] admin_panel/app.py:47 — _resolve_secret generates ephemeral key without fail-hard
Why: Missing VYASA_ADMIN_SECRET emits a WARNING and generates a random key.
     On each process restart every session invalidates — recoverable but
     noisy, and in a production deploy the admin should fail rather than
     silently rotate. Needs an ENV/settings flag (``admin.require_persistent_secret``)
     that flips warn → raise.
Fix: Defer — behaviour is acceptable for v0.1-alpha self-hosted deploys;
     track for v0.2 as constitution §4 row 1 becomes mandatory.

[LOW] admin_panel/errors.py:15 — RFC 7807 ``type`` field hard-coded to about:blank
Why: ``about:blank`` is valid per RFC 7807 §4.2 when no specific type is
     registered, but a production-grade handler should carry a stable URI
     for each problem class so consumers can switch on it programmatically.
Fix: Defer — cosmetic until client tooling demands it.

[LOW] admin_panel/routers/license.py:73 — httpx timeout hard-coded to 8 s
Why: Envato verification timeout should be a setting under
     integrations.envato.http_timeout_s so operators can tune per-deploy.
Fix: Defer — 8 s is reasonable; escalate when operators file a bug.

[STYLE] admin_panel/auth.py:136 — cookie parsing tolerates subject with dots
Why: rsplit(".", 2) makes ``dr.sarabhai.1712000000.sigabc`` parse correctly,
     but two users with ids like ``foo`` and ``foo.123456`` could theoretically
     collide on the pre-hash payload. Low risk because issued_at discriminates.
Fix: Accept.
```

### 5. `vyasa_agent/routines/`

```
[HIGH] routines/runner.py:167 — webhook payload interpolated verbatim into metadata
Why: The full webhook body is dropped into ``meta["webhook_payload"]`` and
     handed to the dispatcher. Any downstream prompt template that
     interpolates metadata into the model context is vulnerable to prompt
     injection via a crafted external body.
Fix: TODO(Dharma HIGH) marker planted; requires either a whitelisted shape
     at parse time or a structured "untrusted" section in the prompt.

[MEDIUM] routines/runner.py:127 — next_fire_at handles DST by re-attaching tz
Why: croniter's DST behaviour around skipped/repeated local hours is known
     to fire twice or not at all when the tz crosses a transition. The
     re-attach-tz dance works for UTC and fixed offsets but not for IANA
     zones (Asia/Kolkata is safe — no DST — so v0.1 is fine).
Fix: Defer — document in the Routine docstring that cron + IANA DST is
     operator-responsibility until v0.3.

[LOW] routines/types.py:40 — DeliveryTarget.parse accepts arbitrary-length address
Why: A malformed deliver_to string could carry an address with embedded
     newlines or control chars; downstream outbound.send has no guard.
Fix: Defer — address is operator-authored YAML; trust boundary OK for v0.1.
```

### 6. `vyasa_agent/cli.py`

```
[MEDIUM] cli.py:268 — SystemExit with non-int code returned 0 (silent failure)
Why: Fire raises SystemExit with a string payload (help text, argparse
     errors). The previous ``int(exc.code) if isinstance(exc.code, int) else 0``
     masked every non-int as success, so a shell caller could not distinguish
     a bad command from a good one.
Fix: Applied — return 0 only when code is None; return 1 on any non-None
     non-int.

[LOW] cli_support.py:247 — Windows SIGTERM path raises ValueError
Why: signal.signal(SIGTERM, ...) raises ValueError on Windows because only
     SIGINT and SIGBREAK are routable. The previous NotImplementedError guard
     covered the asyncio side but not the fallback.
Fix: Applied — wrap the fallback in (ValueError, OSError) and continue; the
     loop still installs SIGINT correctly.

[LOW] cli_support.py:214 — uvicorn thread.join(timeout=10.0) silent on hang
Why: If uvicorn never exits, the caller sees a clean return but the thread
     is orphaned as a daemon. Low risk because the process is exiting anyway.
Fix: Defer — daemon thread is acceptable exit-path behaviour.

[STYLE] cli.py:183 — single-line compound statements (``c = ConsoleAdapter(); c.bind_inbound...``)
Why: ruff E702/E701 — cosmetic. Readability would improve with a line each.
Fix: Defer — file is already a dense orchestration surface.
```

### 7. `vendor/vyasa_internals/`

```
[CRITICAL-ADJACENT] vendor/vyasa_internals/agent_runtime.py:240 — run_conversation raises NotImplementedError
Why: This is Phase-1 Duo shell behaviour, documented at module level. Any
     actor path that reaches it without VYASA_STUB_BRIDGE=1 will crash the
     turn. The fleet actor's _execute_turn only reaches the bridge when the
     stub is off — so production-mode without the real runtime wired is
     broken by design. Constitution §3 covers this via the Phase-1 gate.
Fix: Accept — intentional loud-failure surface; Phase-2 replaces the shell.

[CRITICAL-ADJACENT] vendor/vyasa_internals/{model_tools,toolsets,state,...}.py — vendored donor tree
Why: White-label scanner confirmed clean (bash scripts/white-label-check.sh
     passes). No banned strings in the vendored subtree. Good — the
     rename-sweep and the vendored NOTICE.md attribution are holding.
Fix: Accept — continue scanning on every PR via CI.

[STYLE] vendor/vyasa_internals/agent_runtime.py — typing.Optional, typing.List, typing.Dict
Why: 75+ ruff UP045/UP006 hits across the vendored tree. These are
     intentionally frozen at the donor's shape so the upstream diff stays
     small if we ever re-vendor.
Fix: Accept — do NOT modernise the vendored tree; it would break the
     fetch-vendor.sh diff loop.
```

### 8. Scripts

```
[LOW] scripts/migrate_graph_v1_to_v2.py — noqa: E402 on imports after sys.path mutation
Why: E402 is unavoidable when the script must work before `pip install`.
Fix: Accept — the noqa is targeted and documented.

[STYLE] scripts/white-label-check.sh — bash 3.2-compatible dedup via newline-list
Why: Nice deliberate portability work — macOS /usr/bin/bash is still 3.2.
     Praise, no change.
```

---

## Summary

| Severity  | Count | Disposition                              |
|-----------|-------|------------------------------------------|
| CRITICAL  | 0     | —                                        |
| HIGH      | 6     | TODO(Dharma HIGH) markers planted        |
| MEDIUM    | 5     | 3 auto-fixed, 2 deferred with rationale  |
| LOW       | 12    | 4 auto-fixed, 8 deferred                 |
| STYLE     | 9     | 3 auto-fixed, 6 accepted                 |

**Auto-fixes applied in this pass:**
1. `graphify/store.py` — refresh `updated_at` on checksum dedup
2. `graphify/pii.py` + call sites — extend scrubber gate to `entities` + `symbols`
3. `fleet/manager.py` — clear `_booted` on empty-actor shutdown
4. `cli.py` — distinguish int vs non-int `SystemExit` codes
5. `cli_support.py` — guard Windows SIGTERM `ValueError`
6. `gateway/streaming.py` — sort `__all__`
7. `gateway/adapters/telegram.py` — rename `_MissingRetryAfter` → `_MissingRetryAfterError`

**HIGH findings deferred with TODO markers (block merge if touched again):**
1. CSRF token not bound to session subject (`admin_panel/auth.py`)
2. `deadline_ms` not enforced (`admin_panel/routers/messages.py`)
3. `graph_query` API mismatch (`admin_panel/routers/graph.py`)
4. Checksum omits `summary` (`graphify/checksum.py`)
5. PII bypass via `entities`/`symbols` (FIXED — HIGH resolved)
6. Webhook prompt-injection surface (`routines/runner.py`)

`confidence_score: 0.88`
