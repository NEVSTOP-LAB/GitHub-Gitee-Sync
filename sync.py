#!/usr/bin/env python3
"""
GitHub-Gitee Sync Tool

Sync all repositories (public/private) between GitHub and Gitee.
Supports one-way (GitHub→Gitee, Gitee→GitHub) and bidirectional sync.
"""

import argparse
import logging
import os
import re
import requests
import shutil
import subprocess
import sys
import tempfile
import time


def parse_args():
    """Parse command line arguments and environment variables.

    Priority: CLI arguments > environment variables > default values.
    """
    parser = argparse.ArgumentParser(
        description="Sync repositories between GitHub and Gitee"
    )

    parser.add_argument(
        "--github-owner",
        default=os.environ.get("GITHUB_OWNER"),
        help="GitHub username or organization name",
    )
    parser.add_argument(
        "--github-token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub Personal Access Token",
    )
    parser.add_argument(
        "--gitee-owner",
        default=os.environ.get("GITEE_OWNER"),
        help="Gitee username or organization name",
    )
    parser.add_argument(
        "--gitee-token",
        default=os.environ.get("GITEE_TOKEN"),
        help="Gitee Personal Access Token",
    )
    parser.add_argument(
        "--account-type",
        default=os.environ.get("ACCOUNT_TYPE", "user"),
        choices=["user", "org"],
        help="Account type: user or org (default: user)",
    )
    parser.add_argument(
        "--include-private",
        default=os.environ.get("INCLUDE_PRIVATE", "true"),
        help="Whether to include private repositories (default: true)",
    )
    parser.add_argument(
        "--exclude-repos",
        default=os.environ.get("EXCLUDE_REPOS", ""),
        help="Comma-separated list of repository names to exclude",
    )
    parser.add_argument(
        "--direction",
        default=os.environ.get("SYNC_DIRECTION", "github2gitee"),
        choices=["github2gitee", "gitee2github", "both"],
        help="Sync direction (default: github2gitee)",
    )
    parser.add_argument(
        "--create-missing-repos",
        default=os.environ.get("CREATE_MISSING_REPOS", "true"),
        help="Create repos on target if they don't exist (default: true)",
    )
    parser.add_argument(
        "--sync-extra",
        default=os.environ.get("SYNC_EXTRA", ""),
        help="Comma-separated extra items to sync: releases,wiki,labels,milestones,issues",
    )

    args = parser.parse_args()

    # Convert string booleans to actual booleans
    args.include_private = str(args.include_private).lower() in ("true", "1", "yes")
    args.create_missing_repos = str(args.create_missing_repos).lower() in (
        "true",
        "1",
        "yes",
    )

    # Parse exclude_repos into a set
    args.exclude_repos = set(
        r.strip() for r in args.exclude_repos.split(",") if r.strip()
    )

    # Parse sync_extra into a set
    args.sync_extra = set(
        s.strip() for s in args.sync_extra.split(",") if s.strip()
    )

    # Validate required parameters
    missing = []
    if not args.github_owner:
        missing.append("github-owner (or GITHUB_OWNER env)")
    if not args.github_token:
        missing.append("github-token (or GITHUB_TOKEN env)")
    if not args.gitee_owner:
        missing.append("gitee-owner (or GITEE_OWNER env)")
    if not args.gitee_token:
        missing.append("gitee-token (or GITEE_TOKEN env)")

    if missing:
        parser.error(f"Missing required parameters: {', '.join(missing)}")

    return args


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def mask_token(text):
    """Mask tokens in text to prevent leaking credentials in logs."""
    return re.sub(r'https://[^@]+@', 'https://***@', str(text))


def api_request(method, url, max_retries=3, backoff_base=2, **kwargs):
    """Make an HTTP request with retry logic and rate-limit handling.

    Returns the Response object on success, raises on final failure.
    """
    kwargs.setdefault("timeout", 30)
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)

            # Handle rate limiting
            remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
            if remaining < 100 and remaining > 0:
                time.sleep(1)
            if resp.status_code in (403, 429) and remaining == 0:
                reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
                wait = max(0, reset_time - time.time())
                if wait > 900:
                    raise Exception("API rate limit exceeded, reset time too long (>15min)")
                logging.warning(f"API rate limit reached, waiting {wait:.0f}s ...")
                time.sleep(wait + 1)
                continue

            return resp

        except requests.RequestException as e:
            last_error = e
            if attempt < max_retries:
                wait = backoff_base ** attempt
                logging.warning(
                    f"Request to {url} failed (attempt {attempt+1}), "
                    f"retrying in {wait}s: {e}"
                )
                time.sleep(wait)

    raise last_error


# ---------------------------------------------------------------------------
# GitHub API module
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"


def github_headers(token):
    """Return standard GitHub API headers."""
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def get_github_repos(owner, token, account_type, include_private):
    """Fetch all repositories from GitHub via REST API.

    Args:
        owner: GitHub username or org name.
        token: GitHub personal access token.
        account_type: 'user' or 'org'.
        include_private: whether to include private repos.

    Returns:
        List of dicts with keys: name, private, description, clone_url.
    """
    if account_type == "org":
        url = f"{GITHUB_API}/orgs/{owner}/repos"
    else:
        url = f"{GITHUB_API}/user/repos"

    headers = github_headers(token)
    page = 1
    all_repos = []

    while True:
        params = {"per_page": 100, "page": page}
        if account_type == "user":
            params["type"] = "owner"

        resp = api_request("GET", url, headers=headers, params=params)
        if resp.status_code != 200:
            raise Exception(
                f"Failed to fetch GitHub repos: {resp.status_code} {resp.text}"
            )

        data = resp.json()
        if not data:
            break

        for repo in data:
            name = repo.get("name")
            if not name:
                continue
            is_private = repo.get("private", False)
            if not include_private and is_private:
                continue
            all_repos.append({
                "name": name,
                "private": is_private,
                "description": repo.get("description") or "",
                "clone_url": repo.get("clone_url", ""),
            })

        page += 1

    return all_repos


# ---------------------------------------------------------------------------
# Gitee API module
# ---------------------------------------------------------------------------

GITEE_API = "https://gitee.com/api/v5"


def get_gitee_repos(owner, token, account_type):
    """Fetch all repositories from Gitee via REST API.

    Args:
        owner: Gitee username or org name.
        token: Gitee personal access token.
        account_type: 'user' or 'org'.

    Returns:
        List of dicts with keys: name, private, description.
    """
    if account_type == "org":
        url = f"{GITEE_API}/orgs/{owner}/repos"
    else:
        url = f"{GITEE_API}/user/repos"

    page = 1
    all_repos = []

    while True:
        params = {
            "access_token": token,
            "per_page": 100,
            "page": page,
        }
        if account_type == "user":
            params["type"] = "owner"

        resp = api_request("GET", url, params=params)
        if resp.status_code != 200:
            raise Exception(
                f"Failed to fetch Gitee repos: {resp.status_code} {resp.text}"
            )

        data = resp.json()
        if not data:
            break

        for repo in data:
            name = repo.get("name")
            if not name:
                continue
            all_repos.append({
                "name": name,
                "private": repo.get("private", False),
                "description": repo.get("description") or "",
            })

        page += 1

    return all_repos


def create_gitee_repo(owner, token, repo_name, private, description, account_type):
    """Create a repository on Gitee.

    Args:
        owner: Gitee username or org name.
        token: Gitee personal access token.
        repo_name: Name of the repository to create.
        private: Whether the repo should be private.
        description: Repository description.
        account_type: 'user' or 'org'.

    Returns:
        True if creation was successful or repo already exists, False otherwise.
    """
    if account_type == "org":
        url = f"{GITEE_API}/orgs/{owner}/repos"
    else:
        url = f"{GITEE_API}/user/repos"

    payload = {
        "access_token": token,
        "name": repo_name,
        "description": description[:200] if description else "",
        "private": private,
        "auto_init": False,
    }

    resp = api_request("POST", url, json=payload, max_retries=1)

    if resp.status_code in (200, 201):
        logging.info(f"  Created Gitee repo: {repo_name}")
        return True
    if resp.status_code == 422:
        # Repo may already exist
        logging.info(f"  Gitee repo {repo_name} already exists, skip creation")
        return True

    logging.error(
        f"  Failed to create Gitee repo {repo_name}: {resp.status_code} {resp.text}"
    )
    return False


def create_github_repo(owner, token, repo_name, private, description, account_type):
    """Create a repository on GitHub (used for reverse / bidirectional sync).

    Args:
        owner: GitHub username or org name.
        token: GitHub personal access token.
        repo_name: Name of the repository to create.
        private: Whether the repo should be private.
        description: Repository description.
        account_type: 'user' or 'org'.

    Returns:
        True if creation was successful or repo already exists, False otherwise.
    """
    if account_type == "org":
        url = f"{GITHUB_API}/orgs/{owner}/repos"
    else:
        url = f"{GITHUB_API}/user/repos"

    headers = github_headers(token)
    payload = {
        "name": repo_name,
        "description": description[:350] if description else "",
        "private": private,
        "auto_init": False,
    }

    resp = api_request("POST", url, headers=headers, json=payload, max_retries=1)

    if resp.status_code in (200, 201):
        logging.info(f"  Created GitHub repo: {repo_name}")
        return True
    if resp.status_code == 422:
        logging.info(f"  GitHub repo {repo_name} already exists, skip creation")
        return True

    logging.error(
        f"  Failed to create GitHub repo {repo_name}: {resp.status_code} {resp.text}"
    )
    return False


# ---------------------------------------------------------------------------
# Git Mirror sync module
# ---------------------------------------------------------------------------

GIT_TIMEOUT = 600  # seconds


def mirror_sync(source_url, target_url, repo_name):
    """Perform git clone --mirror + git push --mirror.

    Args:
        source_url: Source repository URL (with token embedded).
        target_url: Target repository URL (with token embedded).
        repo_name: Repository name (for logging).

    Returns:
        'success', 'empty', or 'failed'.
    """
    temp_dir = tempfile.mkdtemp(prefix=f"sync_{repo_name}_")
    try:
        # Step 1: git clone --mirror
        logging.info(f"  Cloning from source ...")
        result = subprocess.run(
            ["git", "clone", "--mirror", source_url, temp_dir],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )

        if result.returncode != 0:
            stderr = result.stderr
            if "empty repository" in stderr.lower():
                logging.warning(f"  {repo_name} is an empty repository, skipping push")
                return "empty"
            logging.error(f"  git clone --mirror failed: {mask_token(stderr)}")
            return "failed"

        # Check for empty repo warning in stderr
        if "empty repository" in (result.stderr or "").lower():
            logging.warning(f"  {repo_name} is an empty repository, skipping push")
            return "empty"

        # Step 2: git push --mirror
        logging.info(f"  Pushing to target ...")
        result = subprocess.run(
            ["git", "push", "--mirror", target_url],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )

        if result.returncode != 0:
            logging.error(
                f"  git push --mirror failed: {mask_token(result.stderr)}"
            )
            return "failed"

        logging.info(f"  Mirror sync completed ✓")
        return "success"

    except subprocess.TimeoutExpired:
        logging.error(f"  git operation timed out ({GIT_TIMEOUT}s)")
        return "failed"
    except Exception as e:
        logging.error(f"  Mirror sync error: {mask_token(str(e))}")
        return "failed"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main sync flow
# ---------------------------------------------------------------------------

def build_source_url(platform, owner, token, repo_name):
    """Build a git clone URL with token embedded for authentication."""
    if platform == "github":
        return f"https://{token}@github.com/{owner}/{repo_name}.git"
    else:
        return f"https://{token}@gitee.com/{owner}/{repo_name}.git"


def sync_one_direction(source_platform, target_platform, source_owner, target_owner,
                       source_token, target_token, account_type, include_private,
                       exclude_repos, create_missing_repos, sync_extra):
    """Sync repos from source platform to target platform.

    Returns:
        Tuple of (synced_count, failed_count, skipped_count, failed_repos).
    """
    synced = 0
    failed = 0
    skipped = 0
    failed_repos = []

    # 1. Get source repos
    logging.info(f"Fetching {source_platform} repos for {source_owner} ...")
    if source_platform == "github":
        source_repos = get_github_repos(source_owner, source_token, account_type, include_private)
    else:
        source_repos = get_gitee_repos(source_owner, source_token, account_type)

    logging.info(f"Found {len(source_repos)} repos on {source_platform}")

    # 2. Filter excluded repos
    if exclude_repos:
        before = len(source_repos)
        source_repos = [r for r in source_repos if r["name"] not in exclude_repos]
        excluded_count = before - len(source_repos)
        if excluded_count > 0:
            logging.info(f"Excluded {excluded_count} repos: {', '.join(sorted(exclude_repos))}")

    logging.info(f"Repos to sync: {len(source_repos)}")

    if not source_repos:
        logging.info("No repos to sync.")
        return synced, failed, skipped, failed_repos

    # 3. Get target repos (for existence check)
    logging.info(f"Fetching {target_platform} repos for {target_owner} ...")
    if target_platform == "github":
        target_repos_list = get_github_repos(target_owner, target_token, account_type, True)
    else:
        target_repos_list = get_gitee_repos(target_owner, target_token, account_type)

    target_repo_names = {r["name"] for r in target_repos_list}
    logging.info(f"Found {len(target_repo_names)} existing repos on {target_platform}")

    # 4. Sync each repo
    total = len(source_repos)
    for idx, repo in enumerate(source_repos, 1):
        repo_name = repo["name"]
        logging.info(f"[{idx}/{total}] Syncing {repo_name} ...")

        # 4a. Check/create target repo
        if repo_name not in target_repo_names:
            if not create_missing_repos:
                logging.info(f"  Target repo not found and create_missing_repos=false, skipping")
                skipped += 1
                continue

            # Create repo on target platform
            if target_platform == "github":
                ok = create_github_repo(
                    target_owner, target_token, repo_name,
                    repo.get("private", False), repo.get("description", ""),
                    account_type,
                )
            else:
                ok = create_gitee_repo(
                    target_owner, target_token, repo_name,
                    repo.get("private", False), repo.get("description", ""),
                    account_type,
                )

            if not ok:
                logging.error(f"  Failed to create target repo, skipping {repo_name}")
                failed += 1
                failed_repos.append((repo_name, "Failed to create target repo"))
                continue

        # 4b. Mirror sync
        source_url = build_source_url(source_platform, source_owner, source_token, repo_name)
        target_url = build_source_url(target_platform, target_owner, target_token, repo_name)
        result = mirror_sync(source_url, target_url, repo_name)

        if result == "failed":
            failed += 1
            failed_repos.append((repo_name, "git mirror sync failed"))
            continue
        elif result == "empty":
            skipped += 1
            continue

        synced += 1

    return synced, failed, skipped, failed_repos


def write_action_outputs(synced, failed, skipped):
    """Write sync results to GitHub Action outputs if running in Actions."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"synced-count={synced}\n")
            f.write(f"failed-count={failed}\n")
            f.write(f"skipped-count={skipped}\n")


def sync_all(args):
    """Main sync orchestration.

    Determines direction, performs sync, outputs summary.

    Returns:
        Exit code: 0=all success, 1=partial failure, 2=all failed, 3=fatal error.
    """
    direction = args.direction
    total_synced = 0
    total_failed = 0
    total_skipped = 0
    all_failed_repos = []

    if direction in ("github2gitee", "both"):
        logging.info("=" * 50)
        logging.info(f"Syncing GitHub({args.github_owner}) → Gitee({args.gitee_owner})")
        logging.info("=" * 50)
        s, f, sk, fr = sync_one_direction(
            "github", "gitee",
            args.github_owner, args.gitee_owner,
            args.github_token, args.gitee_token,
            args.account_type, args.include_private,
            args.exclude_repos, args.create_missing_repos,
            args.sync_extra,
        )
        total_synced += s
        total_failed += f
        total_skipped += sk
        all_failed_repos.extend(fr)

    if direction in ("gitee2github", "both"):
        logging.info("=" * 50)
        logging.info(f"Syncing Gitee({args.gitee_owner}) → GitHub({args.github_owner})")
        logging.info("=" * 50)
        s, f, sk, fr = sync_one_direction(
            "gitee", "github",
            args.gitee_owner, args.github_owner,
            args.gitee_token, args.github_token,
            args.account_type, args.include_private,
            args.exclude_repos, args.create_missing_repos,
            args.sync_extra,
        )
        total_synced += s
        total_failed += f
        total_skipped += sk
        all_failed_repos.extend(fr)

    # Summary
    logging.info("=" * 50)
    logging.info("===== Sync Summary =====")
    logging.info(f"  ✅ Synced:  {total_synced}")
    logging.info(f"  ❌ Failed:  {total_failed}")
    logging.info(f"  ⏭️  Skipped: {total_skipped}")

    if all_failed_repos:
        logging.info("")
        logging.info("Failed repos:")
        for name, reason in all_failed_repos:
            logging.info(f"  - {name}: {reason}")

    logging.info("=" * 50)

    # Write GitHub Action outputs
    write_action_outputs(total_synced, total_failed, total_skipped)

    # Determine exit code
    total = total_synced + total_failed + total_skipped
    if total_failed == 0:
        return 0  # All success
    elif total_synced > 0:
        return 1  # Partial failure
    else:
        return 2  # All failed


def main():
    """Main entry point."""
    args = parse_args()

    exit_code = sync_all(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
