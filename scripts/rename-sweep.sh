#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Kavach rename sweep (recon-07 §4). Idempotent rg + sed pass that renames
# donor identifiers to the Vyasa Agent namespace.
#
# Dry-run by default. Use --apply to mutate the working tree.
#
# Usage:
#   scripts/rename-sweep.sh              # dry-run, prints what would change
#   scripts/rename-sweep.sh --apply      # perform replacements in place
#   scripts/rename-sweep.sh --apply DIR  # operate on DIR instead of repo root

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

APPLY=0
TARGET="$REPO_ROOT"

while (( $# > 0 )); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --help|-h)
      sed -n '3,16p' "$0"
      exit 0
      ;;
    *)
      TARGET="$1"
      shift
      ;;
  esac
done

cd "$TARGET"

if ! command -v rg >/dev/null 2>&1; then
  echo "rename-sweep: ripgrep (rg) is required" >&2
  exit 2
fi

# macOS BSD sed needs -i '' ; GNU sed accepts -i<suffix> with empty suffix.
if sed --version >/dev/null 2>&1; then
  SED_INPLACE=(sed -i)
else
  SED_INPLACE=(sed -i '')
fi

# Rename map, ordered so longer substrings fire before their shorter prefixes.
# Each entry: "PATTERN<TAB>REPLACEMENT" (TAB separator for sed friendliness).
MAP=(
  'HERMES_	VYASA_'
  'hermes_agent\.	vyasa_agent.'
  'hermes_agent	vyasa_agent'
  'hermes_cli	vyasa_cli'
  'hermes_state	vyasa_state'
  'hermes_time	vyasa_time'
  'hermes_logging	vyasa_logging'
  'hermes_constants	vyasa_constants'
  'hermes-acp	vyasa-acp'
  'hermes-agent	vyasa-agent'
  '~/\.hermes/	~/.vyasa/'
  '\.hermes/	.vyasa/'
  'OPENCLAW_	VYASA_'
  'openclaw	vyasa'
  '"Nous Research"	"Graymatter Online LLP"'
  'NousResearch	GraymatterOnline'
)

# Globs intentionally excluded from mutation (Kavach guardrails).
RG_IGNORES=(
  --hidden
  -g '!.git'
  -g '!.venv'
  -g '!node_modules'
  -g '!tests/fixtures/**'
  -g '!NOTICE'
  -g '!CHANGELOG.md'
  -g '!README.md'
  -g '!scripts/white-label-check.sh'
  -g '!scripts/rename-sweep.sh'
)

total_changes=0

for entry in "${MAP[@]}"; do
  pattern="${entry%%	*}"
  replacement="${entry##*	}"

  # rg prints matching file paths once each.
  mapfile -t files < <(rg -l --fixed-strings "${RG_IGNORES[@]}" -- "$pattern" 2>/dev/null || true)

  if (( ${#files[@]} == 0 )); then
    continue
  fi

  echo "rename-sweep: '$pattern' -> '$replacement' (${#files[@]} file(s))"
  total_changes=$((total_changes + ${#files[@]}))

  if (( APPLY == 1 )); then
    # Escape pattern + replacement for sed basic regex.
    esc_pat=$(printf '%s' "$pattern" | sed 's/[]\/$*.^[]/\\&/g')
    esc_rep=$(printf '%s' "$replacement" | sed 's/[\/&]/\\&/g')
    for f in "${files[@]}"; do
      "${SED_INPLACE[@]}" "s/${esc_pat}/${esc_rep}/g" "$f"
    done
  else
    for f in "${files[@]}"; do
      printf '  dry-run: %s\n' "$f"
    done
  fi
done

if (( APPLY == 1 )); then
  echo "rename-sweep: applied ${total_changes} file-level edit(s). Run scripts/white-label-check.sh to verify."
else
  echo "rename-sweep: dry-run complete (${total_changes} file-level edit(s) pending). Re-run with --apply."
fi
