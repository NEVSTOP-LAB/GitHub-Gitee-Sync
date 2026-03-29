"""Entrypoint for GitHub-Gitee-Sync: bridges GitHub Actions INPUT_* env vars
to the standard env var names expected by sync.py.

GitHub Actions Docker container actions set inputs as ``INPUT_{NAME}`` where
``{NAME}`` is the uppercased action input name **with hyphens preserved** – e.g.
the ``github-owner`` input becomes the env var ``INPUT_GITHUB-OWNER``.

POSIX shell silently drops environment variables whose names contain characters
that are not valid in shell identifiers (such as hyphens).  Python reads
``os.environ`` directly from the OS and therefore supports any env-var name,
making it the right tool for this adapter layer.
"""

import os
import sys


def _get_input(action_name: str, default: str = "") -> str:
    """Return the value for a GitHub Actions input.

    Resolution order (first *present and non-empty* value wins):

    1. ``INPUT_{NAME}`` with hyphens preserved – GitHub Actions Docker behavior
       (e.g. ``INPUT_GITHUB-OWNER``).
    2. ``INPUT_{NAME}`` with hyphens replaced by underscores – alternative / legacy
       form (e.g. ``INPUT_GITHUB_OWNER``).
    3. The conventional standalone env-var name (e.g. ``GITHUB_OWNER``).
    4. *default*.

    An empty string is treated as "not present" so that GitHub Actions' habit of
    setting every defined input to ``""`` when the caller omits it still falls
    through to the next candidate (e.g. a pre-existing env var from standalone
    Docker usage).

    Parameters
    ----------
    action_name:
        The input name exactly as declared in ``action.yml`` (e.g. ``github-owner``).
    default:
        Value to return when none of the env-var lookups yield a non-empty string.
    """
    upper = action_name.upper()          # GITHUB-OWNER
    upper_under = upper.replace("-", "_")  # GITHUB_OWNER

    candidates = (
        os.environ.get(f"INPUT_{upper}"),    # INPUT_GITHUB-OWNER (hyphens)
        os.environ.get(f"INPUT_{upper_under}"),  # INPUT_GITHUB_OWNER (underscores)
        os.environ.get(upper_under),         # GITHUB_OWNER (standalone Docker)
    )
    for val in candidates:
        if val:  # non-None and non-empty: this is a real value
            return val
    return default


def main() -> None:
    # ------------------------------------------------------------------ #
    # Map every action.yml input to the env var expected by sync.py.      #
    # The "direction" input is special: sync.py reads SYNC_DIRECTION.     #
    # ------------------------------------------------------------------ #
    mappings: dict[str, str] = {
        "GITHUB_OWNER":         _get_input("github-owner"),
        "GITHUB_TOKEN":         _get_input("github-token"),
        "GITEE_OWNER":          _get_input("gitee-owner"),
        "GITEE_TOKEN":          _get_input("gitee-token"),
        "ACCOUNT_TYPE":         _get_input("account-type",         "user"),
        "INCLUDE_PRIVATE":      _get_input("include-private",      "true"),
        "INCLUDE_REPOS":        _get_input("include-repos",        ""),
        "EXCLUDE_REPOS":        _get_input("exclude-repos",        ""),
        "SYNC_DIRECTION":       _get_input("direction",            "github2gitee"),
        "CREATE_MISSING_REPOS": _get_input("create-missing-repos", "true"),
        "SYNC_EXTRA":           _get_input("sync-extra",           ""),
        "DRY_RUN":              _get_input("dry-run",              "false"),
    }

    for key, value in mappings.items():
        # Set the env var when we have a non-empty value.  An empty string means
        # "not provided" and should not override a value already in os.environ.
        if value:
            os.environ[key] = value
        elif key not in os.environ:
            os.environ[key] = ""

    # Issue #15: warn when the caller accidentally passes the ephemeral
    # GitHub Actions built-in token (ghs_ prefix) instead of a PAT.
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if github_token.startswith("ghs_"):
        print(
            "[WARNING] GITHUB_TOKEN appears to be the GitHub Actions built-in"
            " token (ghs_ prefix).\n"
            "  This token only has permissions for the current repository.\n"
            "  Please provide a Personal Access Token (PAT) with 'repo' scope"
            " for cross-repo sync.\n"
            "  For organization repos, also add 'read:org' scope.",
            flush=True,
        )

    # Replace this process with sync.py so that it becomes PID 1 and receives
    # signals (SIGTERM, SIGINT) directly – important for Docker container
    # lifecycle management.
    sync_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync.py")
    os.execv(sys.executable, [sys.executable, sync_py])


if __name__ == "__main__":
    main()
