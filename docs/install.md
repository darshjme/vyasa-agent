# Installation

Four supported targets. Pick one. Each section ends with a verification
command you can run before messaging the bot.

Python 3.11 or newer is required on every target. `scripts/install.sh`
prefers `uv` and falls back to `pipx`.

## Mac Mini (launchd)

```bash
# One-liner from a clean Mac Mini
curl -fsSL https://vyasa.graymatteronline.com/install.sh | bash

# Or, from a local checkout
bash scripts/install.sh

# Load and start the LaunchAgent (the installer prints this too)
launchctl unload ~/Library/LaunchAgents/com.graymatteronline.vyasa.plist 2>/dev/null || true
launchctl load   ~/Library/LaunchAgents/com.graymatteronline.vyasa.plist
```

What the script does:

1. Preflight: verifies `python3 >= 3.11`.
2. Installs the `vyasa-agent[all]` wheel with `uv tool install`; falls back
   to `pipx install` if `uv` is missing or unavailable.
3. Resolves the `vyasa` executable path.
4. Creates `~/.vyasa/`, `~/.vyasa/employees/`, and `~/.vyasa/logs/` with
   mode 700.
5. Seeds `~/.vyasa/employees/*.yaml` from the bundled templates the first
   time only (never overwrites existing files).
6. Writes `~/Library/LaunchAgents/com.graymatteronline.vyasa.plist` with
   `RunAtLoad=true`, `KeepAlive=true`, and stdout / stderr pinned to
   `~/.vyasa/logs/`.
7. Prints the exact `launchctl` commands to load the agent.

Logs land at `~/.vyasa/logs/vyasa.out.log` and `~/.vyasa/logs/vyasa.err.log`.
Tail them with `tail -F ~/.vyasa/logs/vyasa.*.log`.

## Linux (systemd-user)

```bash
# One-liner
curl -fsSL https://vyasa.graymatteronline.com/install.sh | bash

# Or, from a local checkout
bash scripts/install.sh

# Enable and start the user service (the installer prints this too)
systemctl --user daemon-reload
systemctl --user enable --now vyasa

# Optional: keep the service running after logout
loginctl enable-linger "$(whoami)"
```

What the script does:

1. Same preflight, install, home-dir layout, and seed steps as macOS.
2. Writes `~/.config/systemd/user/vyasa.service` with `Type=simple`,
   `Restart=on-failure`, `RestartSec=5`, and logs appended to
   `~/.vyasa/logs/`.
3. Prints the exact `systemctl --user` commands to enable the service.

Tested on Debian 12 and 13. The `loginctl enable-linger` step is only
needed if you want the service to keep running when you log out.

Logs with `journalctl --user -u vyasa -f` or tail
`~/.vyasa/logs/vyasa.*.log`.

## Docker Compose

```bash
cp .env.example .env   # edit secrets
docker compose up -d
docker compose logs -f vyasa
```

The shipped `docker-compose.yml` runs a single service (`vyasa`) with:

- `~/.vyasa/` bind-mounted into the container so graph state, employee
  templates, and logs persist across restarts.
- Port 8080 exposed for the admin HTTP surface.
- Environment pulled from `.env` (Telegram token, allowlist, owner chat id).

Stop with `docker compose down`; graph data survives because it lives on
the bind-mount.

## Fly.io

Deferred to v0.2.

A Fly.io target, with a single-region machine, a 10 GB volume for the
SQLite graph, and `flyctl secrets` integration, is planned for v0.2. The
v0.1-alpha channels (console + Telegram) run fine on Fly.io in principle,
but the deploy helper (`fly.toml`, volume sizing guidance, scaling floor)
is not shipped yet.

Track the work at the [v0.2 milestone](https://github.com/darshjme/vyasa-agent/milestones).

## Telegram

Streaming replies are enabled by default. The adapter posts a single "⋯"
placeholder message and edits it in-place as each chunk of the reply arrives
instead of flooding the chat with a new message per token. The edit cadence
is governed by `channels.telegram.edit_interval_seconds` (default `1.0`) —
Telegram rejects more than one edit per second per chat, so do not drop below
that floor. Replies over 4096 characters split across fresh messages at the
nearest paragraph break; the latest message remains the edit target.

## Verification

```bash
vyasa doctor
```

`vyasa doctor` prints pass / fail per check:

- Python version (requires 3.11+).
- Bundled runtime import (vendored modules resolve).
- Graph SQLite at `~/.vyasa/graph.sqlite` is writable (creates on first run).
- Employee YAML files parse cleanly.
- Capability matrix (`capabilities.yaml`) loads and every partner has a
  defined scope.

If any check fails, the output points at the exact remediation step.
Common failures are in [`troubleshooting.md`](troubleshooting.md).

## Environment variables

| Variable            | Purpose                                                                                                                                                                                             |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `VYASA_STUB_BRIDGE` | Set to `1` to bypass the runtime bridge and return a deterministic offline stub from every employee turn. Intended for tests and local smoke runs where you do not want to spin up real inference or bill a provider. Unset, or any value other than `1`, keeps normal behaviour. |
