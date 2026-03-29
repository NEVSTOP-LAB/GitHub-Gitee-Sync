#!/bin/sh
set -e

# Bridge script: maps GitHub Action's INPUT_* environment variables
# to the standard environment variables expected by sync.py.
#
# GitHub Actions sets input env vars as INPUT_{NAME} where {NAME} is the
# uppercased input name with hyphens PRESERVED (not converted to underscores).
# Example: input "github-owner" → env var INPUT_GITHUB-OWNER
#
# Because hyphens are not valid POSIX shell variable names, $INPUT_GITHUB-OWNER
# cannot be read via the normal ${VAR} syntax. We use `printenv` instead, with
# a fallback to the underscore form for standalone Docker usage.

# Helper: read an INPUT_ variable by the exact action.yml input name.
# Tries the hyphen form first (GitHub Actions), then the underscore form
# (standalone Docker / legacy), then returns empty string.
_get_input() {
  _upper="$(echo "$1" | tr '[:lower:]-' '[:upper:]_')"
  _val="$(printenv "INPUT_$1" 2>/dev/null || true)"
  if [ -z "$_val" ]; then
    _val="$(printenv "INPUT_${_upper}" 2>/dev/null || true)"
  fi
  printf '%s' "$_val"
}

# GitHub credentials
_v="$(_get_input 'GITHUB-OWNER')" ; export GITHUB_OWNER="${_v:-$GITHUB_OWNER}"
_v="$(_get_input 'GITHUB-TOKEN')" ; export GITHUB_TOKEN="${_v:-$GITHUB_TOKEN}"

# Issue #15: Warn if GITHUB_TOKEN appears to be the built-in Actions token (ghs_ prefix).
# The built-in token only has access to the current repository and will cause permission
# errors when trying to sync other repos. Users should provide a PAT (ghp_) instead.
case "$GITHUB_TOKEN" in
  ghs_*)
    echo "[WARNING] GITHUB_TOKEN appears to be the GitHub Actions built-in token (ghs_ prefix)."
    echo "  This token only has permissions for the current repository."
    echo "  Please provide a Personal Access Token (PAT) with 'repo' scope for cross-repo sync."
    echo "  For organization repos, also add 'read:org' scope."
    ;;
esac

# Gitee credentials
_v="$(_get_input 'GITEE-OWNER')" ; export GITEE_OWNER="${_v:-$GITEE_OWNER}"
_v="$(_get_input 'GITEE-TOKEN')" ; export GITEE_TOKEN="${_v:-$GITEE_TOKEN}"

# Optional parameters (with defaults)
_v="$(_get_input 'ACCOUNT-TYPE')"       ; export ACCOUNT_TYPE="${_v:-${ACCOUNT_TYPE:-user}}"
_v="$(_get_input 'INCLUDE-PRIVATE')"    ; export INCLUDE_PRIVATE="${_v:-${INCLUDE_PRIVATE:-true}}"
_v="$(_get_input 'INCLUDE-REPOS')"      ; export INCLUDE_REPOS="${_v:-$INCLUDE_REPOS}"
_v="$(_get_input 'EXCLUDE-REPOS')"      ; export EXCLUDE_REPOS="${_v:-$EXCLUDE_REPOS}"
_v="$(_get_input 'DIRECTION')"          ; export SYNC_DIRECTION="${_v:-${SYNC_DIRECTION:-github2gitee}}"
_v="$(_get_input 'CREATE-MISSING-REPOS')"; export CREATE_MISSING_REPOS="${_v:-${CREATE_MISSING_REPOS:-true}}"
_v="$(_get_input 'SYNC-EXTRA')"         ; export SYNC_EXTRA="${_v:-$SYNC_EXTRA}"
_v="$(_get_input 'DRY-RUN')"            ; export DRY_RUN="${_v:-${DRY_RUN:-false}}"

# Execute sync script (exec replaces shell as PID 1 for proper signal handling)
exec python /app/sync.py
