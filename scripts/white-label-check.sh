#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Vyasa Agent white-label gate.
#
# Scans the repo (plus any built artefacts in dist/) for donor / upstream
# strings that must not leak into shipped surfaces. Exits non-zero on the
# first hit outside the bounded allowlist.
#
# Usage:
#   scripts/white-label-check.sh [ROOT]
#
# ROOT defaults to the repo root resolved from this script's location.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Banned strings. Case-sensitive, matched with `grep -F` (fixed-string).
# ---------------------------------------------------------------------------
BANNED=(
  "Anthropic"
  "Claude"
  "AI-generated"
  "Co-Authored-By"
  "GPT"
  "OpenAI"
  "Llama"
  "LLM"
  "NousResearch"
  "hermes-agent"
  "openclaw"
  "Clawhub"
  "Clawd"
)

# ---------------------------------------------------------------------------
# Allowlisted paths (factual attribution under nominative fair use).
# Matched as literal path prefixes; anything else is in-scope.
# ---------------------------------------------------------------------------
ALLOWLIST=(
  "NOTICE"
  "tests/fixtures/white_label_allowed_examples/"
  "scripts/white-label-check.sh"
  "scripts/rename-sweep.sh"
  "CLAUDE.md"
  "README.md"
)

# ---------------------------------------------------------------------------
# Scan targets (sources + ad-hoc top-level files). Existence is optional —
# missing paths are silently skipped so the script stays useful on partial
# checkouts.
# ---------------------------------------------------------------------------
SCAN_PATHS=(
  "vyasa_agent"
  "scripts"
  ".github"
  "docs"
  "README.md"
  "CHANGELOG.md"
  "CLAUDE.md"
)

is_allowlisted() {
  local path="$1"
  local entry
  for entry in "${ALLOWLIST[@]}"; do
    case "$path" in
      "$entry"|"$entry"*) return 0 ;;
    esac
  done
  return 1
}

scan_file() {
  local file="$1"
  local term="$2"
  local hits
  if hits=$(grep -nF -- "$term" "$file" 2>/dev/null); then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      printf '  %s:%s\n' "$file" "$line"
    done <<< "$hits"
    return 0
  fi
  return 1
}

fail=0
declare -a FILES=()

# Enumerate candidate files once: directories walked recursively, plus any
# additional top-level *.py / *.yaml / *.yml not already inside the scan
# directories above.
collect_files() {
  local path
  for path in "${SCAN_PATHS[@]}"; do
    if [[ -f "$path" ]]; then
      FILES+=("$path")
    elif [[ -d "$path" ]]; then
      while IFS= read -r -d '' f; do
        FILES+=("$f")
      done < <(find "$path" -type f \
        ! -path '*/.git/*' \
        ! -path '*/__pycache__/*' \
        ! -path '*/node_modules/*' \
        ! -path '*/.venv/*' \
        -print0)
    fi
  done
  # Top-level *.py / *.yaml / *.yml (not inside already-scanned dirs).
  local ext
  for ext in py yaml yml; do
    while IFS= read -r -d '' f; do
      FILES+=("$f")
    done < <(find . -maxdepth 1 -type f -name "*.${ext}" -print0)
  done
}

collect_files

# De-dup FILES (preserve order) using a portable newline-separated list so
# the script runs on bash 3.2 (macOS /usr/bin/bash) as well as bash 4+/5+.
UNIQUE=()
seen_blob=$'\n'
for f in "${FILES[@]}"; do
  # Strip leading "./" so allowlist prefix matching works cleanly.
  f="${f#./}"
  case "$seen_blob" in
    *$'\n'"$f"$'\n'*) ;;
    *) UNIQUE+=("$f"); seen_blob+="$f"$'\n' ;;
  esac
done

echo "white-label-check: scanning ${#UNIQUE[@]} file(s) for ${#BANNED[@]} banned string(s)"

for term in "${BANNED[@]}"; do
  term_fail=0
  for file in "${UNIQUE[@]}"; do
    if is_allowlisted "$file"; then
      continue
    fi
    if scan_file "$file" "$term" >/tmp/wl_hits.$$ 2>/dev/null; then
      if [[ -s /tmp/wl_hits.$$ ]]; then
        if (( term_fail == 0 )); then
          echo "::error title=white-label::banned string '${term}' found"
          term_fail=1
        fi
        cat /tmp/wl_hits.$$
        fail=1
      fi
    fi
  done
  rm -f /tmp/wl_hits.$$
done

# ---------------------------------------------------------------------------
# Second pass: scan built artefacts in dist/ if it exists. Binary wheels and
# tarballs are checked via `strings` (wheels = zip, sdist = tar.gz).
# ---------------------------------------------------------------------------
if [[ -d dist ]]; then
  echo "white-label-check: scanning dist/ artefacts"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT

  shopt -s nullglob
  for archive in dist/*.whl dist/*.zip; do
    [[ -e "$archive" ]] || continue
    dest="$tmpdir/$(basename "$archive")_unpacked"
    mkdir -p "$dest"
    if command -v unzip >/dev/null 2>&1; then
      unzip -qq "$archive" -d "$dest" || true
    fi
  done
  for archive in dist/*.tar.gz dist/*.tgz; do
    [[ -e "$archive" ]] || continue
    dest="$tmpdir/$(basename "$archive")_unpacked"
    mkdir -p "$dest"
    tar -xzf "$archive" -C "$dest" || true
  done
  shopt -u nullglob

  for term in "${BANNED[@]}"; do
    hits=$(grep -rIn --binary-files=without-match -F -- "$term" "$tmpdir" 2>/dev/null || true)
    if [[ -n "$hits" ]]; then
      echo "::error title=white-label::banned string '${term}' found inside dist/ artefact"
      echo "$hits"
      fail=1
    fi
  done
fi

if (( fail == 0 )); then
  echo "white-label-check: clean."
fi

exit "$fail"
