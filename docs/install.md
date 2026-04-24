# Installation

Four supported targets. Pick one. Each section ends with a verification command you can run before messaging the bot.

> TODO: Dr. Rao to flesh out in release PR — version pins, CPU/RAM floors, tested OS matrix.

## Mac Mini (launchd)

```bash
./install.sh --target=launchd
```

What the script does:

1. Runs a preflight (Python version, disk, ports 6379/6333/8080 free).
2. Writes `.env` from prompts.
3. Installs `~/Library/LaunchAgents/online.graymatter.vyasa.plist`.
4. Loads and starts the agent.
5. Prints the admin panel URL.

> TODO: Dr. Rao to flesh out in release PR — plist template, log paths, upgrade story, `vyasa uninstall` reverse-flow.

## Linux (systemd-user)

```bash
./install.sh --target=systemd-user
loginctl enable-linger "$(whoami)"
```

What the script does:

1. Runs preflight.
2. Writes `.env`.
3. Drops `~/.config/systemd/user/vyasa.service`.
4. `systemctl --user daemon-reload && systemctl --user enable --now vyasa`.

> TODO: Dr. Rao to flesh out in release PR — Debian 12/13 matrix, lingering caveat, journalctl cheatsheet.

## Docker Compose

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up -d
```

Services launched: `gateway`, `router`, `whatsapp-sidecar`, `redis`, `qdrant`.

> TODO: Dr. Rao to flesh out in release PR — volume layout, bind-mount vs named volumes, healthcheck grace periods, `compose.override.yml` examples for GPU boxes.

## Fly.io

```bash
flyctl launch --copy-config --yes
flyctl volumes create vyasa_data --size 10
flyctl deploy
```

> TODO: Dr. Rao to flesh out in release PR — single-region vs multi-region story, volume sizing guidance, secret rotation via `flyctl secrets set`, scaling floor.

## Verification

```bash
vyasa health
# expected: employees=28  channels=2  memory=ok
```

If any field reports `err`, open the admin panel `/health` page — each check links to the exact runbook line.
