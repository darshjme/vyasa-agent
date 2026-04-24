#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Vyasa Agent one-liner host installer. Safe to re-run.
#
# macOS   -> writes LaunchAgent plist at ~/Library/LaunchAgents
# Linux   -> writes systemd-user unit at ~/.config/systemd/user
#
# The installer does NOT start the service. It prints the exact command
# the operator should run once they are ready.
#
# Usage:
#   curl -fsSL https://vyasa.graymatteronline.com/install.sh | bash
#   bash scripts/install.sh

set -euo pipefail

log()  { printf '\033[1;34m[vyasa-install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[vyasa-install]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[vyasa-install]\033[0m %s\n' "$*" >&2; exit 1; }

REPO_ROOT_FROM_SCRIPT=""
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$SCRIPT_DIR/../pyproject.toml" ]]; then
    REPO_ROOT_FROM_SCRIPT="$(cd "$SCRIPT_DIR/.." && pwd)"
  fi
fi

VYASA_HOME="${VYASA_HOME:-$HOME/.vyasa}"
EMPLOYEES_DIR="$VYASA_HOME/employees"
LOG_DIR="$VYASA_HOME/logs"

log "VYASA_HOME=$VYASA_HOME"

# ---------------------------------------------------------------------------
# 1. Preflight
# ---------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required. Install Python 3.11+ and re-run."
fi

PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_MAJ="${PYV%%.*}"
PY_MIN="${PYV##*.}"
if (( PY_MAJ < 3 )) || (( PY_MAJ == 3 && PY_MIN < 11 )); then
  die "python3 $PYV is too old. Vyasa Agent requires >= 3.11."
fi
log "python3 $PYV OK"

# ---------------------------------------------------------------------------
# 2. Install binary (uv tool -> pipx fallback)
# ---------------------------------------------------------------------------
install_with_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    log "installing uv"
    curl -fsSL https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    [[ -f "$HOME/.local/bin/env" ]] && . "$HOME/.local/bin/env"
    export PATH="$HOME/.local/bin:$PATH"
  fi
  if ! command -v uv >/dev/null 2>&1; then
    return 1
  fi
  log "uv tool install vyasa-agent[all]"
  uv tool install --force "vyasa-agent[all]" || return 1
  return 0
}

install_with_pipx() {
  if ! command -v pipx >/dev/null 2>&1; then
    log "pipx not present; attempting 'python3 -m pip install --user pipx'"
    python3 -m pip install --user pipx >/dev/null 2>&1 || return 1
    python3 -m pipx ensurepath >/dev/null 2>&1 || true
    export PATH="$HOME/.local/bin:$PATH"
  fi
  log "pipx install vyasa-agent[all]"
  pipx install --force "vyasa-agent[all]" || return 1
  return 0
}

if ! install_with_uv; then
  warn "uv install failed or unavailable; falling back to pipx"
  install_with_pipx || die "Unable to install vyasa-agent with uv or pipx."
fi

VYASA_BIN="$(command -v vyasa || true)"
if [[ -z "$VYASA_BIN" ]]; then
  # uv tool bin path fallback.
  if command -v uv >/dev/null 2>&1; then
    UV_BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
    if [[ -x "$UV_BIN_DIR/vyasa" ]]; then
      VYASA_BIN="$UV_BIN_DIR/vyasa"
    fi
  fi
fi
[[ -z "$VYASA_BIN" ]] && VYASA_BIN="$HOME/.local/bin/vyasa"
log "vyasa executable resolved at: $VYASA_BIN"

# ---------------------------------------------------------------------------
# 3. Home directory layout
# ---------------------------------------------------------------------------
mkdir -p "$VYASA_HOME" "$EMPLOYEES_DIR" "$LOG_DIR"
chmod 700 "$VYASA_HOME"

# Seed employees/*.yaml templates the first time.
seed_employees() {
  local src=""
  if [[ -n "$REPO_ROOT_FROM_SCRIPT" && -d "$REPO_ROOT_FROM_SCRIPT/employees" ]]; then
    src="$REPO_ROOT_FROM_SCRIPT/employees"
  else
    local pkg_dir
    pkg_dir="$(python3 -c 'import importlib.util, pathlib, sys
spec = importlib.util.find_spec("vyasa_agent")
print(pathlib.Path(spec.origin).parent if spec and spec.origin else "", end="")' 2>/dev/null || true)"
    if [[ -n "$pkg_dir" && -d "$pkg_dir/../employees" ]]; then
      src="$(cd "$pkg_dir/../employees" && pwd)"
    fi
  fi

  if [[ -z "$src" || ! -d "$src" ]]; then
    warn "could not locate bundled employees/ templates; skipping seed"
    return 0
  fi

  local seeded=0
  while IFS= read -r -d '' f; do
    local base
    base="$(basename "$f")"
    if [[ ! -e "$EMPLOYEES_DIR/$base" ]]; then
      cp "$f" "$EMPLOYEES_DIR/$base"
      seeded=$((seeded + 1))
    fi
  done < <(find "$src" -maxdepth 1 -type f -name '*.yaml' -print0 2>/dev/null)

  if (( seeded > 0 )); then
    log "seeded $seeded employee template(s) into $EMPLOYEES_DIR"
  else
    log "employees/ already populated (no seeds needed)"
  fi
}
seed_employees

# ---------------------------------------------------------------------------
# 4. Platform service unit (written, NOT started)
# ---------------------------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
  Darwin)
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_PATH="$PLIST_DIR/com.graymatteronline.vyasa.plist"
    mkdir -p "$PLIST_DIR"
    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.graymatteronline.vyasa</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VYASA_BIN}</string>
        <string>gateway</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>VYASA_HOME</key><string>${VYASA_HOME}</string>
        <key>PATH</key><string>${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>${LOG_DIR}/vyasa.out.log</string>
    <key>StandardErrorPath</key><string>${LOG_DIR}/vyasa.err.log</string>
    <key>WorkingDirectory</key><string>${VYASA_HOME}</string>
</dict>
</plist>
PLIST
    log "wrote LaunchAgent: $PLIST_PATH"
    cat <<NEXT

Next steps (macOS):
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  launchctl load   "$PLIST_PATH"

NEXT
    ;;

  Linux)
    UNIT_DIR="$HOME/.config/systemd/user"
    UNIT_PATH="$UNIT_DIR/vyasa.service"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_PATH" <<UNIT
# SPDX-License-Identifier: Apache-2.0
[Unit]
Description=Vyasa Agent gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=VYASA_HOME=${VYASA_HOME}
Environment=PATH=${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
WorkingDirectory=${VYASA_HOME}
ExecStart=${VYASA_BIN} gateway serve
Restart=on-failure
RestartSec=5
StandardOutput=append:${LOG_DIR}/vyasa.out.log
StandardError=append:${LOG_DIR}/vyasa.err.log

[Install]
WantedBy=default.target
UNIT
    log "wrote systemd-user unit: $UNIT_PATH"
    cat <<NEXT

Next steps (Linux):
  systemctl --user daemon-reload
  systemctl --user enable --now vyasa

NEXT
    ;;

  *)
    warn "unrecognised OS '$OS'; service unit not written"
    ;;
esac

log "install complete. VYASA_HOME=$VYASA_HOME"
