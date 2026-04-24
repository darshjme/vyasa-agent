#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Prometheus vendor-fork fetcher (design-10 Dr. Rao decision).
#
# One-shot, idempotent copy of the minimum upstream surface required for
# Phase-1 Duo mode. Source tree is expected at $SRC (default
# ~/repos/hermes-agent). Target is vendor/vyasa_internals/ in this repo.
#
# Dry-run by default. Pass --apply to mutate. Safe to re-run — files are
# overwritten each time, no merging.
#
#   scripts/fetch-vendor.sh                  # show planned actions
#   scripts/fetch-vendor.sh --apply          # copy + rename
#   SRC=/custom/path scripts/fetch-vendor.sh --apply
#
# Rename map (applied after copy, before white-label-check):
#   HERMES_           -> VYASA_
#   hermes_constants  -> vyasa_internals.constants
#   hermes_cli.*      -> vyasa_internals.*            (stubbed)
#   hermes_state      -> vyasa_internals.state
#   hermes_time       -> vyasa_internals.time_utils
#   hermes_logging    -> vyasa_internals.logging_utils
#   ~/.hermes/        -> ~/.vyasa/
#   "Hermes Agent"    -> "Vyasa Agent"
#   "hermes-agent"    -> "vyasa-agent"
#   from agent.*      -> from vyasa_internals.agent.*
#   from tools.*      -> from vyasa_internals.tools.*
#   from model_tools  -> from vyasa_internals.model_tools
#   from toolsets     -> from vyasa_internals.toolsets
#   from utils        -> from vyasa_internals.utils
#
# After --apply, scripts/white-label-check.sh must pass cleanly (the
# vendored tree is scanned — only vendor/vyasa_internals/NOTICE.md is
# allowlisted for factual donor attribution).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="${SRC:-$HOME/repos/hermes-agent}"
DEST="$REPO_ROOT/vendor/vyasa_internals"

APPLY=0
while (( $# > 0 )); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --help|-h)
      sed -n '3,45p' "$0"
      exit 0
      ;;
    *)
      echo "fetch-vendor: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$SRC" ]]; then
  echo "fetch-vendor: source tree not found at $SRC" >&2
  echo "fetch-vendor: set SRC=/path/to/upstream or clone the donor first." >&2
  exit 2
fi

# macOS BSD sed needs -i '' ; GNU sed accepts -i<suffix> with empty suffix.
if sed --version >/dev/null 2>&1; then
  SED_INPLACE=(sed -i)
else
  SED_INPLACE=(sed -i '')
fi

# (src_relative_path, dest_relative_path) copy plan. Keep the set minimal —
# only files the Phase-1 Duo AIAgent shell directly imports. Deeper
# toolchain (agent/*, hermes_cli/*) is stubbed in-tree rather than copied.
COPY_PLAN=(
  "hermes_constants.py:constants.py"
  "hermes_time.py:time_utils.py"
  "utils.py:utils.py"
  "toolsets.py:toolsets.py"
  "model_tools.py:model_tools.py"
  "tools/__init__.py:tools/__init__.py"
  "tools/registry.py:tools/registry.py"
)

# Rename map. Order matters — longer patterns first so prefixes don't
# swallow them. TAB-separated for readability.
RENAME_MAP=(
  $'hermes_constants\tvyasa_internals.constants'
  $'hermes_cli.env_loader\tvyasa_internals.env_loader'
  $'hermes_cli.timeouts\tvyasa_internals.timeouts'
  $'hermes_cli.config\tvyasa_internals.config_stub'
  $'hermes_cli\tvyasa_internals'
  $'hermes_state\tvyasa_internals.state'
  $'hermes_time\tvyasa_internals.time_utils'
  $'hermes_logging\tvyasa_internals.logging_utils'
  $'HERMES_\tVYASA_'
  $'~/.hermes/\t~/.vyasa/'
  $'.hermes/\t.vyasa/'
  $'hermes-agent\tvyasa-agent'
  $'Hermes Agent\tVyasa Agent'
  $'Hermes agent\tVyasa agent'
  $'Hermes\tVyasa'
  $'NousResearch\tGraymatterOnline'
  $'"Nous Research"\t"Graymatter Online LLP"'
  $'from agent.\tfrom vyasa_internals.agent.'
  $'from tools.\tfrom vyasa_internals.tools.'
  $'from model_tools\tfrom vyasa_internals.model_tools'
  $'from toolsets\tfrom vyasa_internals.toolsets'
  $'from utils\tfrom vyasa_internals.utils'
  $'import model_tools\timport vyasa_internals.model_tools as model_tools'
  $'import toolsets\timport vyasa_internals.toolsets as toolsets'
  $'AsyncOpenAI\tAsyncUpstreamClient'
  $'OpenAI-compatible\tupstream-compatible'
  $'OpenAI-format\tupstream-format'
  $'OpenAI SDK\tupstream SDK'
  $', OpenAI,\t, upstream vendor,'
  $'OpenAI\tupstream-vendor'
  $'get_hermes_home\tget_vyasa_home'
  $'get_default_hermes_root\tget_default_vyasa_root'
  $'get_optional_skills_dir\tget_optional_skills_dir'
  $'get_hermes_dir\tget_vyasa_dir'
  $'display_hermes_home\tdisplay_vyasa_home'
  $'hermes_home\tvyasa_home'
  $'_hermes_ipv4_patched\t_vyasa_ipv4_patched'
  $'Hermes home\tVyasa home'
  $'Hermes directory\tVyasa directory'
  $'Hermes subdirectory\tVyasa subdirectory'
  $'Hermes Agent\tVyasa Agent'
  $'Hermes logging\tVyasa logging'
  $'Hermes never\tVyasa never'
  $'Hermes requires\tVyasa requires'
  $'hermes\tvyasa'
  $'LLM\tlanguage model'
)

apply_renames() {
  local file="$1"
  local entry pattern replacement esc_pat esc_rep
  for entry in "${RENAME_MAP[@]}"; do
    pattern="${entry%%$'\t'*}"
    replacement="${entry##*$'\t'}"
    esc_pat=$(printf '%s' "$pattern" | sed 's/[]\/$*.^[]/\\&/g')
    esc_rep=$(printf '%s' "$replacement" | sed 's/[\/&]/\\&/g')
    "${SED_INPLACE[@]}" "s/${esc_pat}/${esc_rep}/g" "$file"
  done
}

planned=0
for entry in "${COPY_PLAN[@]}"; do
  src_rel="${entry%%:*}"
  dest_rel="${entry##*:}"
  src="$SRC/$src_rel"
  dest="$DEST/$dest_rel"

  if [[ ! -f "$src" ]]; then
    echo "fetch-vendor: WARN — source missing, skipping: $src" >&2
    continue
  fi

  planned=$((planned + 1))
  if (( APPLY == 1 )); then
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    apply_renames "$dest"
    printf 'fetch-vendor: copied %-32s -> %s\n' "$src_rel" "$dest_rel"
  else
    printf 'fetch-vendor: plan   %-32s -> %s\n' "$src_rel" "$dest_rel"
  fi
done

if (( APPLY == 1 )); then
  echo "fetch-vendor: ${planned} file(s) copied + renamed."
  echo "fetch-vendor: run scripts/white-label-check.sh to verify the vendored tree is clean."
else
  echo "fetch-vendor: dry-run complete (${planned} copy+rename operation(s) pending). Re-run with --apply."
fi
