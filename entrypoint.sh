#!/bin/sh
set -e

# Bridge script: maps GitHub Action's INPUT_* environment variables
# to the standard environment variables expected by sync.py.
#
# GitHub Actions automatically converts input names:
#   - to UPPERCASE
#   - hyphens (-) to underscores (_)
#   - adds INPUT_ prefix
# Example: input "github-owner" → INPUT_GITHUB_OWNER

# GitHub credentials
export GITHUB_OWNER="${INPUT_GITHUB_OWNER:-$GITHUB_OWNER}"
export GITHUB_TOKEN="${INPUT_GITHUB_TOKEN:-$GITHUB_TOKEN}"

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
export GITEE_OWNER="${INPUT_GITEE_OWNER:-$GITEE_OWNER}"
export GITEE_TOKEN="${INPUT_GITEE_TOKEN:-$GITEE_TOKEN}"

# Optional parameters (with defaults)
export ACCOUNT_TYPE="${INPUT_ACCOUNT_TYPE:-${ACCOUNT_TYPE:-user}}"
export INCLUDE_PRIVATE="${INPUT_INCLUDE_PRIVATE:-${INCLUDE_PRIVATE:-true}}"
export INCLUDE_REPOS="${INPUT_INCLUDE_REPOS:-$INCLUDE_REPOS}"
export EXCLUDE_REPOS="${INPUT_EXCLUDE_REPOS:-$EXCLUDE_REPOS}"
export SYNC_DIRECTION="${INPUT_DIRECTION:-${SYNC_DIRECTION:-github2gitee}}"
export CREATE_MISSING_REPOS="${INPUT_CREATE_MISSING_REPOS:-${CREATE_MISSING_REPOS:-true}}"
export SYNC_EXTRA="${INPUT_SYNC_EXTRA:-$SYNC_EXTRA}"
export DRY_RUN="${INPUT_DRY_RUN:-${DRY_RUN:-false}}"

# Execute sync script (exec replaces shell as PID 1 for proper signal handling)
exec python /app/sync.py
