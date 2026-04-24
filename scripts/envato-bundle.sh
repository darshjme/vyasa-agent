#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the CodeCanyon ZIP for Vyasa Agent.
#
# Produces dist/vyasa-agent-<version>-envato.zip containing:
#   - source tree (minus developer-only paths)
#   - scripts/install.sh
#   - docs/html/ (pre-rendered HTML; placeholder stub acceptable)
#   - .env.example
#   - LICENSE, NOTICE, README.md, CHANGELOG.md
#   - SHA256SUMS.txt manifest
#
# Runs scripts/white-label-check.sh against the staged tree before zipping.
#
# Usage:
#   VYASA_VERSION=0.1.0 bash scripts/envato-bundle.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

VERSION="${VYASA_VERSION:-}"
if [[ -z "$VERSION" ]]; then
  # Fall back to pyproject.toml.
  VERSION="$(python3 -c 'import re,sys,pathlib
txt = pathlib.Path("pyproject.toml").read_text()
m = re.search(r"(?m)^version\s*=\s*\"([^\"]+)\"", txt)
print(m.group(1) if m else "0.0.0")' 2>/dev/null || echo "0.0.0")"
fi

BUNDLE_NAME="vyasa-agent-${VERSION}-envato"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

STAGE_ROOT="$STAGE_DIR/$BUNDLE_NAME"
mkdir -p "$STAGE_ROOT"

echo "envato-bundle: staging v${VERSION} at $STAGE_ROOT"

# ---------------------------------------------------------------------------
# Copy source tree with excludes.
# ---------------------------------------------------------------------------
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.git' \
    --exclude '.github' \
    --exclude 'tests' \
    --exclude 'vendor' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'dist' \
    --exclude 'build' \
    --exclude 'node_modules' \
    --exclude '.pytest_cache' \
    --exclude '.ruff_cache' \
    --exclude '.mypy_cache' \
    --exclude 'scripts/white-label-check.sh' \
    ./ "$STAGE_ROOT/"
else
  # POSIX fallback.
  (cd "$REPO_ROOT" && \
    find . \
      -path './.git' -prune -o \
      -path './.github' -prune -o \
      -path './tests' -prune -o \
      -path './vendor' -prune -o \
      -path './.venv' -prune -o \
      -path './dist' -prune -o \
      -path './build' -prune -o \
      -path './node_modules' -prune -o \
      -path './.pytest_cache' -prune -o \
      -path './.ruff_cache' -prune -o \
      -path './.mypy_cache' -prune -o \
      -path './scripts/white-label-check.sh' -prune -o \
      -type f -print0 | \
    while IFS= read -r -d '' f; do
      rel="${f#./}"
      mkdir -p "$STAGE_ROOT/$(dirname "$rel")"
      cp "$f" "$STAGE_ROOT/$rel"
    done)
fi

# ---------------------------------------------------------------------------
# Required extras.
# ---------------------------------------------------------------------------
mkdir -p "$STAGE_ROOT/docs/html"
if [[ ! -f "$STAGE_ROOT/docs/html/index.html" ]]; then
  cat > "$STAGE_ROOT/docs/html/index.html" <<'HTML'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Vyasa Agent — Documentation</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body>
  <h1>Vyasa Agent Documentation</h1>
  <p>This offline HTML docs bundle is a placeholder. Replace with the rendered
     user manual before Envato submission.</p>
</body>
</html>
HTML
fi

if [[ ! -f "$STAGE_ROOT/.env.example" ]]; then
  cat > "$STAGE_ROOT/.env.example" <<'ENV'
# SPDX-License-Identifier: Apache-2.0
# Vyasa Agent runtime configuration (copy to .env and fill in).

VYASA_HOME=~/.vyasa
VYASA_GATEWAY_HOST=127.0.0.1
VYASA_GATEWAY_PORT=8644
VYASA_ADMIN_PORT=8645

# Messaging channels (optional)
VYASA_TELEGRAM_BOT_TOKEN=
VYASA_WHATSAPP_ACCESS_TOKEN=

# Model provider credentials
VYASA_PROVIDER_API_KEY=
ENV
fi

# ---------------------------------------------------------------------------
# White-label gate against the staged tree.
# ---------------------------------------------------------------------------
echo "envato-bundle: running white-label-check against staged tree"
bash "$REPO_ROOT/scripts/white-label-check.sh" "$STAGE_ROOT"

# ---------------------------------------------------------------------------
# SHA256 manifest (relative paths inside bundle root).
# ---------------------------------------------------------------------------
echo "envato-bundle: computing SHA256 manifest"
(
  cd "$STAGE_ROOT"
  if command -v sha256sum >/dev/null 2>&1; then
    find . -type f ! -name 'SHA256SUMS.txt' -print0 | \
      xargs -0 sha256sum | sort -k2 > SHA256SUMS.txt
  else
    # macOS fallback: shasum -a 256
    find . -type f ! -name 'SHA256SUMS.txt' -print0 | \
      xargs -0 shasum -a 256 | sort -k2 > SHA256SUMS.txt
  fi
)

# ---------------------------------------------------------------------------
# Produce ZIP.
# ---------------------------------------------------------------------------
mkdir -p "$REPO_ROOT/dist"
OUT_ZIP="$REPO_ROOT/dist/${BUNDLE_NAME}.zip"
rm -f "$OUT_ZIP"

( cd "$STAGE_DIR" && zip -qr "$OUT_ZIP" "$BUNDLE_NAME" )

echo "envato-bundle: wrote $OUT_ZIP"
ls -lh "$OUT_ZIP"
