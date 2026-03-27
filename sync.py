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


def main():
    """Main entry point."""
    args = parse_args()


if __name__ == "__main__":
    main()
