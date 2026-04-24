# Troubleshooting

Common failures in v0.1-alpha and the exact command that fixes each.
If a symptom is not here, open a GitHub Issue with the output of
`vyasa doctor`.

## Doctor fails on vendored runtime import

**Symptom.** `vyasa doctor` prints `bundled runtime: FAIL — module not found`.

**Fix.** The bundled runtime was not fetched or was stripped in a dirty
checkout. Restore it:

```bash
bash scripts/fetch-vendor.sh --apply
vyasa doctor
```

The `--apply` flag writes into `vendor/`; without it the script runs in
dry-run mode.

## Telegram bot does not reply

**Symptom.** Messages sent to the bot from your phone get no response; the
gateway logs show nothing.

**Fix.** Walk three checks in order:

1. Bot token. `@BotFather` rotates tokens on request; make sure the value
   in `VYASA_TELEGRAM_BOT_TOKEN` matches the current one.
2. Allowlist. Your chat id must appear in
   `VYASA_TELEGRAM_ALLOWLIST` (comma-separated). Use `@userinfobot` to
   look up your chat id.
3. Self-check. `vyasa doctor` flags missing or unreadable env vars.

After fixing env vars, restart the gateway:

```bash
# macOS
launchctl kickstart -k gui/$(id -u)/com.graymatteronline.vyasa
# Linux
systemctl --user restart vyasa
```

## Partner times out

**Symptom.** `/ask <partner> ...` hangs for more than 30 seconds, or replies
with `partner timed out`.

**Fix.** Two likely causes:

1. The capability matrix denies a tool the partner tried to invoke. Check
   `capabilities.yaml` for the partner's row and widen the scope if the
   deny is intentional-but-wrong.
2. The partner's state DB is oversized. A runaway session can bloat
   `~/.vyasa/employees/<id>/state.db`. Size it and prune:

```bash
du -h ~/.vyasa/employees/*/state.db | sort -h | tail
# If one is > 500 MB, archive and let Vyasa reseed on next turn:
mv ~/.vyasa/employees/<id>/state.db ~/.vyasa/employees/<id>/state.db.bak
```

## Graph query returns nothing

**Symptom.** `vyasa graph query --intent="..."` returns an empty result,
even for topics you know the fleet has discussed.

**Fix.** The graph schema was not migrated to the v2 layout (SQLite + WAL +
node checksums). Run:

```bash
vyasa graph migrate
```

The command is idempotent. It prints the schema version before and after.

## Permission denied on SQLite

**Symptom.** `vyasa doctor` fails with
`graph sqlite: FAIL — permission denied on ~/.vyasa/graph.sqlite`.

**Fix.** `~/.vyasa/` must be owned by the user running the gateway and
chmod 700. If you installed as `root` but run as your user (or the other
way around), fix ownership:

```bash
sudo chown -R "$(whoami):$(id -gn)" ~/.vyasa
chmod 700 ~/.vyasa
```

Re-run `vyasa doctor`.
