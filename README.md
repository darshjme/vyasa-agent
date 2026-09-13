# Vyasa Agent

A self-hosted fleet of 29 specialist personas, connected to [DJCode](https://cli.darshj.ai/vyasa). Ask from your terminal, Telegram, or an authenticated HTTP client. Each specialist has a defined prompt, capability policy, and persistent conversation history.

Vyasa 0.2 provides model inference through OpenAI-compatible APIs, conversation-scoped graph memory tools, and an authenticated DJCode integration. Specialist names describe software personas, not human employees or professional credentials.

## Start the fleet

Requires Python 3.11 or newer.

```sh
uv tool install 'git+https://github.com/darshjme/vyasa-agent.git'
vyasa doctor
export VYASA_PROVIDER=openrouter
export VYASA_MODEL='your-provider-model-id'
# Set VYASA_API_KEY securely in your environment.
vyasa gateway serve --console
```

The gateway listens on `127.0.0.1:19000`. Set `VYASA_BASE_URL` for another OpenAI-compatible endpoint. `VYASA_PROVIDER=ollama` uses `http://127.0.0.1:11434/v1` and does not require an API key. Model requests send the prompt and recent conversation history to your configured provider.

State is stored under `VYASA_HOME` (default `~/.vyasa`). The install includes all 29 descriptors and prompts; it does not require a separate company repository. Set `VYASA_FLEET_ROOT` to use your own `vyasa.yaml`, `capabilities.yaml`, and `employees/` directory.

## Connect DJCode

Use DJCode 4.3 or newer. Generate a token on the fleet host:

```sh
vyasa token
```

Store that output securely as `VYASA_TOKEN` in your DJCode terminal. Then:

```sh
export VYASA_URL=http://127.0.0.1:19000
# Set VYASA_TOKEN securely; do not commit it.
djcode --vyasa
djcode --vyasa --vyasa-employee prometheus --vyasa-session release 'Review this release plan'
djcode --vyasa --vyasa-employee sarabhai --vyasa-session launch 'Prioritize these launch tasks'
```

Without a prompt, `--vyasa` lists the fleet. Omit `--vyasa-employee` to route automatically. A named session retains recent turns across gateway restarts. Each token, session, and specialist has separate history. Remote connections require HTTPS; use a reverse proxy or an SSH tunnel to the loopback port. The public DJCode website provides documentation, not shared access credentials.

Older DJCode releases tied updates to GitHub Actions. Re-run the installer once to migrate; subsequent 4.3 updates use repository release tags and wheel checksums with local/server verification.

## Channels and API

- `vyasa gateway serve --console`: terminal messages and replies.
- `vyasa gateway serve --telegram`: text via Telegram. Install the `messaging` extra and set `VYASA_TELEGRAM_BOT_TOKEN`, `VYASA_TELEGRAM_ALLOWLIST`, and `VYASA_OWNER_CHAT_ID`.
- `GET /v1/fleet`: bearer-authenticated directory.
- `POST /v1/chat`: bearer-authenticated `{text, employee?, session?}`; returns the completed reply or an error.
- `GET /healthz`: process health. A healthy process alone does not prove model credentials or inference work.
- Administrative graph and settings routes require a signed session; writes additionally require session-bound CSRF.

Tokens created by `vyasa token` grant access to the configured fleet. Treat them as secrets. To revoke a token, remove it from `channels.gateway.tokens` in the host settings store. Do not publish port 19000 without TLS and access controls.

## Memory and execution

Recent history is bounded and persisted in per-specialist SQLite databases. The model can request `graph_read` and `graph_write` only when its descriptor and capability matrix allow them. Memory is scoped to the current conversation and specialist; tool operations are audited. The model loop is limited to eight rounds and each actor has a turn deadline.

Vyasa currently exposes memory tools, not host shell or file-editing tools. Use DJCode's local coding workflow to edit, run, and review repository changes. WhatsApp, voice, vector recall, and autonomous deployment remain future work. The retained vendor shell is historical compatibility code; the live fleet uses `vyasa_agent.runtime`.

## Docker

```sh
# Set provider environment variables first.
docker compose up -d --build
docker compose exec vyasa vyasa token
```

The container runs as an unprivileged user and persists state in `vyasa_home`. The host port stays on loopback. Add a TLS reverse proxy for remote access.

## Development

```sh
uv sync --extra dev --extra admin --extra messaging
uv run pytest -m ''
uv run ruff check vyasa_agent --select E9,F63,F7,F82
uv build
```

GitHub Actions remains disabled. Run checks and publishing locally or on an authorized server. Existing audit documents describe the older alpha and are historical evidence, not a current certification.

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for required notices.
