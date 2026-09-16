# Vyasa Agent

Self-hosted fleet of **29** specialist personas. Version **0.2.1**. Python 3.11+. Apache-2.0.

Ask from a terminal, Telegram, or an authenticated HTTP client. Each specialist has a prompt, a capability policy, and its own conversation history. Names describe software personas, not human employees or professional credentials.

Pairs with [DJcode](https://github.com/darshjme/djcode) 4.3+ (`djcode --vyasa`).

## Start the fleet

```sh
uv tool install 'git+https://github.com/darshjme/vyasa-agent.git'
vyasa doctor
export VYASA_PROVIDER=openrouter
export VYASA_MODEL='your-provider-model-id'
# Set VYASA_API_KEY in the environment. Do not commit it.
vyasa gateway serve --console
```

The gateway listens on `127.0.0.1:19000`. `VYASA_BASE_URL` selects another OpenAI-compatible endpoint. `VYASA_PROVIDER=ollama` uses `http://127.0.0.1:11434/v1` and does not need an API key.

State lives in `VYASA_HOME` (default `~/.vyasa`). The install includes all 29 descriptors under `employees/`. Set `VYASA_FLEET_ROOT` to use your own `vyasa.yaml`, `capabilities.yaml`, and `employees/` directory.

`vyasa doctor` checks Python, imports, graph SQLite, employee YAML, and the capability matrix. A healthy process does not prove model credentials or inference.

## Connect DJcode

On the fleet host:

```sh
vyasa token
```

Then in the DJcode terminal:

```sh
export VYASA_URL=http://127.0.0.1:19000
# Set VYASA_TOKEN from the command above. Do not commit it.
djcode --vyasa
djcode --vyasa --vyasa-employee prometheus --vyasa-session release 'Review this release plan'
```

Without a prompt, `--vyasa` lists the fleet. Omit `--vyasa-employee` to route automatically. Named sessions keep recent turns across gateway restarts. Remote access needs HTTPS (reverse proxy or SSH tunnel). Do not publish port 19000 without TLS and an allowlist.

## Channels and API

- `vyasa gateway serve --console` — terminal
- `vyasa gateway serve --telegram` — text via Telegram (`messaging` extra; `VYASA_TELEGRAM_BOT_TOKEN`, `VYASA_TELEGRAM_ALLOWLIST`, `VYASA_OWNER_CHAT_ID`)
- `GET /v1/fleet` — bearer-authenticated directory
- `POST /v1/chat` — bearer-authenticated `{text, employee?, session?}`
- `GET /healthz` — process health only

Revoke a token by removing it from `channels.gateway.tokens` in the host settings store.

## Memory and limits

Recent history is bounded and stored in per-specialist SQLite databases. The model may call `graph_read` / `graph_write` only when the descriptor and capability matrix allow it. The model loop is limited to eight rounds with a per-actor deadline.

Vyasa does not expose host shell or file-editing tools. Use DJcode for repository edits. WhatsApp, voice, vector recall, and autonomous deployment are not in 0.2.1.

## Docker

```sh
# Set provider environment variables first.
docker compose up -d --build
docker compose exec vyasa vyasa token
```

The container runs unprivileged, persists `vyasa_home`, and binds the host port on loopback.

## Development

```sh
uv sync --extra dev --extra admin --extra messaging
uv run pytest -m ''
uv run ruff check vyasa_agent --select E9,F63,F7,F82
uv build
```

GitHub Actions is disabled. Run checks and publishing from an authorized local environment.

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
