# Third-Party Notice — `vyasa_internals`

This vendored package contains code derived from the following MIT-licensed
upstream work:

  * **hermes-agent** — Copyright (c) 2025 Nous Research.
    Source: <https://github.com/NousResearch/hermes-agent>

The full MIT licence text is reproduced in the top-level `NOTICE` file at
the repository root. The copy here exists so the attribution travels with
the vendored tree in every built artefact.

## What was taken

The following source files were copied from the upstream tree and renamed
under the `vyasa_internals` namespace:

| Upstream path            | Vendored path                                |
|--------------------------|----------------------------------------------|
| `hermes_constants.py`    | `vyasa_internals/constants.py`               |
| `hermes_time.py`         | `vyasa_internals/time_utils.py`              |
| `utils.py`               | `vyasa_internals/utils.py`                   |
| `toolsets.py`            | `vyasa_internals/toolsets.py`                |
| `model_tools.py`         | `vyasa_internals/model_tools.py`             |
| `tools/__init__.py`      | `vyasa_internals/tools/__init__.py`          |
| `tools/registry.py`      | `vyasa_internals/tools/registry.py`          |

All `HERMES_*` environment variables, `~/.hermes/` path tokens and
donor-name identifiers were rewritten to the Vyasa namespace via
`scripts/fetch-vendor.sh`, which is the authoritative record of the
rename map.

## What was stubbed rather than copied

Phase-1 Duo mode has a 3000 line-of-code vendor budget. The upstream
runtime and its transitive dependency graph exceed that budget by an
order of magnitude. The following modules are therefore re-implemented
as minimal shells in-tree rather than vendored verbatim:

| Module                                  | Reason                                    |
|-----------------------------------------|-------------------------------------------|
| `vyasa_internals/agent_runtime.py`      | Donor `run_agent.py` is ~12k LoC and pulls in `agent/` (~25k LoC). Phase-1 Duo only needs the `AIAgent.__init__` signature; `run_conversation` is a placeholder that raises. |
| `vyasa_internals/logging_utils.py`      | Donor `hermes_logging.py` layered profile-aware, managed-deployment and gateway-component behaviour. Phase-1 Duo keeps rotating `agent.log` + session-tag context only. |
| `vyasa_internals/state.py`              | Donor `hermes_state.py` is a 1600 LoC SQLite / FTS5 store. Phase-1 Duo uses DarshJDB for persistence; this shell preserves the `SessionDB` interface so callers compile. |
| `vyasa_internals/env_loader.py`         | Donor `hermes_cli/env_loader.py`. Minimal dotenv layering preserved. |
| `vyasa_internals/timeouts.py`           | Donor `hermes_cli/timeouts.py`. Returns sensible defaults, honours two env vars. |
| `vyasa_internals/config_stub.py`        | Donor `hermes_cli/config.py`. `is_managed()` returns False; everything else is unused at Phase-1. |

Every stub is flagged in its own module docstring with a direct pointer to
the upstream file so Phase-2 can re-vendor a fuller slice when required.

## What was explicitly not taken

The upstream gateway, MCP server, ACP adapter, plugin loader, optional
skills, batch runner, RL CLI, mini-SWE runner, environments harness and
trajectory compressor are all **out of scope** for Phase-1 Duo and are
not present anywhere in this package.

## License

Both the upstream code and the stubs in this package are distributed
under the terms reproduced in the top-level `NOTICE` at the repo root.
