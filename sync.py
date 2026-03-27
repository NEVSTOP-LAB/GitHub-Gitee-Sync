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
# API constants
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"
GITEE_API = "https://gitee.com/api/v5"

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def setup_logging():
    """Configure logging format and level."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
        stream=sys.stdout,
    )


def mask_token(text):
    """Mask tokens in text to prevent leaking credentials in logs."""
    return re.sub(r'https://[^@]+@', 'https://***@', str(text))


def validate_tokens(github_token, gitee_token):
    """Validate GitHub and Gitee tokens before starting sync.

    Raises Exception with clear message if any token is invalid.
    """
    # Validate GitHub Token
    logging.info("Validating GitHub token ...")
    try:
        resp = requests.get(
            f"{GITHUB_API}/user",
            headers={"Authorization": f"token {github_token}"},
            timeout=30,
        )
        if resp.status_code == 401:
            raise Exception(
                "GitHub Token authentication failed (HTTP 401).\n"
                "  Please check your token: https://github.com/settings/tokens\n"
                "  Required scope: repo (full repository access)"
            )
        if resp.status_code != 200:
            raise Exception(
                f"GitHub Token validation failed: HTTP {resp.status_code}"
            )
        github_user = resp.json().get("login", "unknown")
        logging.info(f"  GitHub authenticated as: {github_user}")
    except requests.RequestException as e:
        raise Exception(f"GitHub Token validation network error: {e}")

    # Validate Gitee Token
    logging.info("Validating Gitee token ...")
    try:
        resp = requests.get(
            f"{GITEE_API}/user",
            params={"access_token": gitee_token},
            timeout=30,
        )
        if resp.status_code == 401:
            raise Exception(
                "Gitee Token authentication failed (HTTP 401).\n"
                "  Please check your token: https://gitee.com/profile/personal_access_tokens\n"
                "  Required permission: projects"
            )
        if resp.status_code != 200:
            raise Exception(
                f"Gitee Token validation failed: HTTP {resp.status_code}"
            )
        gitee_user = resp.json().get("login", "unknown")
        logging.info(f"  Gitee authenticated as: {gitee_user}")
    except requests.RequestException as e:
        raise Exception(f"Gitee Token validation network error: {e}")


def check_git_installed():
    """Check if git is available in PATH."""
    try:
        result = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=True
        )
        logging.info(f"Git version: {result.stdout.strip()}")
    except FileNotFoundError:
        raise Exception("Git is not installed or not in PATH")


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
# Repository metadata sync module
# ---------------------------------------------------------------------------

def get_repo_details(platform, owner, token, repo_name):
    """Get repository details from platform API.

    Returns dict with description, homepage, etc., or None on failure.
    """
    if platform == "github":
        url = f"{GITHUB_API}/repos/{owner}/{repo_name}"
        resp = api_request("GET", url, headers=github_headers(token), max_retries=2)
    else:
        url = f"{GITEE_API}/repos/{owner}/{repo_name}"
        resp = api_request("GET", url, params={"access_token": token}, max_retries=2)

    if resp.status_code != 200:
        return None

    data = resp.json()
    return {
        "description": data.get("description") or "",
        "homepage": data.get("homepage") or "",
    }


def update_repo_metadata(platform, owner, token, repo_name, metadata):
    """Update repository metadata on target platform via PATCH API."""
    if platform == "github":
        url = f"{GITHUB_API}/repos/{owner}/{repo_name}"
        resp = api_request(
            "PATCH", url, headers=github_headers(token),
            json=metadata, max_retries=1,
        )
    else:
        url = f"{GITEE_API}/repos/{owner}/{repo_name}"
        payload = {"access_token": token}
        payload.update(metadata)
        resp = api_request("PATCH", url, json=payload, max_retries=1)

    if resp.status_code in (200, 201):
        return True
    logging.warning(f"  Failed to update metadata: {resp.status_code}")
    return False


def sync_repo_metadata(source_platform, target_platform, source_owner, target_owner,
                       source_token, target_token, repo_name):
    """Sync repository metadata (description, homepage) from source to target.

    Only updates if there are differences. Non-fatal on failure.
    """
    try:
        source_info = get_repo_details(source_platform, source_owner, source_token, repo_name)
        if not source_info:
            logging.warning(f"  Could not fetch source repo details for metadata sync")
            return

        target_info = get_repo_details(target_platform, target_owner, target_token, repo_name)
        if not target_info:
            logging.warning(f"  Could not fetch target repo details for metadata sync")
            return

        # Compare and update only changed fields
        updates = {}
        for key in ("description", "homepage"):
            if source_info.get(key, "") != target_info.get(key, ""):
                updates[key] = source_info[key]

        if updates:
            logging.info(f"  Syncing metadata: {', '.join(updates.keys())}")
            update_repo_metadata(target_platform, target_owner, target_token, repo_name, updates)
        else:
            logging.debug(f"  Metadata already in sync")

    except Exception as e:
        logging.warning(f"  Metadata sync failed: {e}")


# ---------------------------------------------------------------------------
# Extra sync modules (Releases, Wiki, Labels, Milestones, Issues)
# ---------------------------------------------------------------------------

def _get_api_url(platform, path):
    """Build a full API URL for the given platform."""
    if platform == "github":
        return f"{GITHUB_API}{path}"
    return f"{GITEE_API}{path}"


def _api_auth(platform, token):
    """Return auth kwargs for API requests."""
    if platform == "github":
        return {"headers": github_headers(token)}
    return {"params": {"access_token": token}}


def _api_auth_with_params(platform, token, extra_params=None):
    """Return kwargs for API requests (headers for GitHub, params for Gitee)."""
    kwargs = {}
    if platform == "github":
        kwargs["headers"] = github_headers(token)
        if extra_params:
            kwargs["params"] = extra_params
    else:
        params = {"access_token": token}
        if extra_params:
            params.update(extra_params)
        kwargs["params"] = params
    return kwargs


def _paginated_get(platform, token, path, extra_params=None):
    """Paginated GET for both platforms, returns all items."""
    items = []
    page = 1
    while True:
        p = {"per_page": 100, "page": page}
        if extra_params:
            p.update(extra_params)
        url = _get_api_url(platform, path)
        kwargs = _api_auth_with_params(platform, token, p)
        resp = api_request("GET", url, max_retries=2, **kwargs)
        if resp.status_code != 200:
            break
        data = resp.json()
        if not data:
            break
        if isinstance(data, list):
            items.extend(data)
        else:
            break
        page += 1
    return items


# ---- Releases sync ----

def sync_releases(source_platform, target_platform, source_owner, target_owner,
                  source_token, target_token, repo_name):
    """Sync releases from source to target, matched by tag_name."""
    try:
        src_releases = _paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/releases",
        )
        tgt_releases = _paginated_get(
            target_platform, target_token,
            f"/repos/{target_owner}/{repo_name}/releases",
        )

        tgt_by_tag = {r["tag_name"]: r for r in tgt_releases if r.get("tag_name")}

        created = 0
        for src_rel in src_releases:
            tag = src_rel.get("tag_name")
            if not tag:
                continue
            if tag in tgt_by_tag:
                continue  # Already exists on target

            # Create release on target
            url = _get_api_url(
                target_platform,
                f"/repos/{target_owner}/{repo_name}/releases",
            )
            payload = {
                "tag_name": tag,
                "name": src_rel.get("name") or tag,
                "body": src_rel.get("body") or "",
                "prerelease": src_rel.get("prerelease", False),
            }
            if target_platform == "github":
                payload["draft"] = src_rel.get("draft", False)
                resp = api_request(
                    "POST", url, headers=github_headers(target_token),
                    json=payload, max_retries=1,
                )
            else:
                payload["access_token"] = target_token
                resp = api_request("POST", url, json=payload, max_retries=1)

            if resp.status_code in (200, 201):
                created += 1
                # Sync release assets
                new_release = resp.json()
                _sync_release_assets(
                    source_platform, target_platform,
                    source_owner, target_owner,
                    source_token, target_token,
                    repo_name, src_rel, new_release,
                )
            else:
                logging.warning(
                    f"  Failed to create release {tag}: {resp.status_code}"
                )

        if created:
            logging.info(f"  Releases synced: {created} created")

    except Exception as e:
        logging.warning(f"  Releases sync failed: {e}")


def _sync_release_assets(source_platform, target_platform,
                         source_owner, target_owner,
                         source_token, target_token,
                         repo_name, src_release, tgt_release):
    """Sync release assets (download from source, upload to target)."""
    assets = src_release.get("assets", [])
    if not assets:
        return

    tgt_release_id = tgt_release.get("id")
    if not tgt_release_id:
        return

    for asset in assets:
        asset_name = asset.get("name", "")
        try:
            # Download from source
            if source_platform == "github":
                download_url = asset.get("browser_download_url", "")
                if not download_url:
                    continue
                dl_resp = requests.get(download_url, timeout=300, stream=True)
            else:
                download_url = asset.get("browser_download_url", "")
                if not download_url:
                    continue
                dl_resp = requests.get(
                    download_url,
                    params={"access_token": source_token},
                    timeout=300,
                    stream=True,
                )

            if dl_resp.status_code != 200:
                logging.warning(f"  Failed to download asset {asset_name}")
                continue

            content = dl_resp.content

            # Upload to target
            if target_platform == "github":
                upload_url = tgt_release.get("upload_url", "")
                upload_url = upload_url.split("{")[0]  # Remove template part
                upload_url = f"{upload_url}?name={asset_name}"
                content_type = asset.get("content_type", "application/octet-stream")
                up_resp = api_request(
                    "POST", upload_url,
                    headers={
                        "Authorization": f"token {target_token}",
                        "Content-Type": content_type,
                    },
                    data=content,
                    max_retries=1,
                    timeout=300,
                )
            else:
                upload_url = _get_api_url(
                    target_platform,
                    f"/repos/{target_owner}/{repo_name}/releases/{tgt_release_id}/attach_files",
                )
                up_resp = api_request(
                    "POST", upload_url,
                    params={"access_token": target_token},
                    files={"file": (asset_name, content)},
                    max_retries=1,
                    timeout=300,
                )

            if up_resp.status_code in (200, 201):
                logging.debug(f"  Uploaded asset: {asset_name}")
            else:
                logging.warning(
                    f"  Failed to upload asset {asset_name}: {up_resp.status_code}"
                )
        except Exception as e:
            logging.warning(f"  Asset sync failed for {asset_name}: {e}")


# ---- Wiki sync ----

def sync_wiki(source_platform, target_platform, source_owner, target_owner,
              source_token, target_token, repo_name):
    """Sync wiki using git clone --mirror .wiki.git + git push --mirror.

    Silently skips if source wiki does not exist.
    """
    try:
        if source_platform == "github":
            source_url = f"https://{source_token}@github.com/{source_owner}/{repo_name}.wiki.git"
        else:
            source_url = f"https://{source_token}@gitee.com/{source_owner}/{repo_name}.wiki.git"

        if target_platform == "github":
            target_url = f"https://{target_token}@github.com/{target_owner}/{repo_name}.wiki.git"
        else:
            target_url = f"https://{target_token}@gitee.com/{target_owner}/{repo_name}.wiki.git"

        temp_dir = tempfile.mkdtemp(prefix=f"wiki_{repo_name}_")
        try:
            result = subprocess.run(
                ["git", "clone", "--mirror", source_url, temp_dir],
                capture_output=True, text=True, timeout=GIT_TIMEOUT,
            )
            if result.returncode != 0:
                # Wiki likely does not exist — silently skip
                logging.debug(f"  Wiki not available for {repo_name}, skipping")
                return

            result = subprocess.run(
                ["git", "push", "--mirror", target_url],
                cwd=temp_dir, capture_output=True, text=True, timeout=GIT_TIMEOUT,
            )
            if result.returncode != 0:
                logging.warning(f"  Wiki push failed: {mask_token(result.stderr)}")
            else:
                logging.info(f"  Wiki synced ✓")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    except subprocess.TimeoutExpired:
        logging.warning(f"  Wiki sync timed out")
    except Exception as e:
        logging.warning(f"  Wiki sync failed: {mask_token(str(e))}")


# ---- Labels sync ----

def sync_labels(source_platform, target_platform, source_owner, target_owner,
                source_token, target_token, repo_name):
    """Sync labels from source to target. Matched by name."""
    try:
        src_labels = _paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/labels",
        )
        tgt_labels = _paginated_get(
            target_platform, target_token,
            f"/repos/{target_owner}/{repo_name}/labels",
        )

        tgt_by_name = {l["name"]: l for l in tgt_labels if l.get("name")}

        created = 0
        updated = 0
        for src_label in src_labels:
            name = src_label.get("name")
            if not name:
                continue

            color = src_label.get("color", "")
            # Normalize color: strip leading '#' if present
            if color.startswith("#"):
                color = color[1:]
            description = src_label.get("description") or ""

            if name not in tgt_by_name:
                # Create label
                url = _get_api_url(
                    target_platform,
                    f"/repos/{target_owner}/{repo_name}/labels",
                )
                payload = {"name": name, "color": color}
                if description:
                    payload["description"] = description
                if target_platform == "github":
                    resp = api_request(
                        "POST", url, headers=github_headers(target_token),
                        json=payload, max_retries=1,
                    )
                else:
                    payload["access_token"] = target_token
                    resp = api_request("POST", url, json=payload, max_retries=1)

                if resp.status_code in (200, 201):
                    created += 1
                else:
                    logging.warning(f"  Failed to create label {name}: {resp.status_code}")
            else:
                # Check if update needed
                tgt = tgt_by_name[name]
                tgt_color = (tgt.get("color") or "").lstrip("#")
                tgt_desc = tgt.get("description") or ""
                if tgt_color != color or tgt_desc != description:
                    url = _get_api_url(
                        target_platform,
                        f"/repos/{target_owner}/{repo_name}/labels/{name}",
                    )
                    payload = {"color": color}
                    if description:
                        payload["description"] = description
                    if target_platform == "github":
                        payload["new_name"] = name
                        resp = api_request(
                            "PATCH", url, headers=github_headers(target_token),
                            json=payload, max_retries=1,
                        )
                    else:
                        payload["access_token"] = target_token
                        resp = api_request("PATCH", url, json=payload, max_retries=1)

                    if resp.status_code in (200, 201):
                        updated += 1

        if created or updated:
            logging.info(f"  Labels synced: {created} created, {updated} updated")

    except Exception as e:
        logging.warning(f"  Labels sync failed: {e}")


# ---- Milestones sync ----

def sync_milestones(source_platform, target_platform, source_owner, target_owner,
                    source_token, target_token, repo_name):
    """Sync milestones from source to target. Matched by title."""
    try:
        src_milestones = _paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/milestones",
            extra_params={"state": "all"},
        )
        tgt_milestones = _paginated_get(
            target_platform, target_token,
            f"/repos/{target_owner}/{repo_name}/milestones",
            extra_params={"state": "all"},
        )

        tgt_by_title = {m["title"]: m for m in tgt_milestones if m.get("title")}

        created = 0
        updated = 0
        for src_ms in src_milestones:
            title = src_ms.get("title")
            if not title:
                continue

            payload = {
                "title": title,
                "state": src_ms.get("state", "open"),
                "description": src_ms.get("description") or "",
            }
            due_on = src_ms.get("due_on")
            if due_on:
                payload["due_on"] = due_on

            if title not in tgt_by_title:
                url = _get_api_url(
                    target_platform,
                    f"/repos/{target_owner}/{repo_name}/milestones",
                )
                if target_platform == "github":
                    resp = api_request(
                        "POST", url, headers=github_headers(target_token),
                        json=payload, max_retries=1,
                    )
                else:
                    payload["access_token"] = target_token
                    resp = api_request("POST", url, json=payload, max_retries=1)

                if resp.status_code in (200, 201):
                    created += 1
                else:
                    logging.warning(f"  Failed to create milestone {title}: {resp.status_code}")
            else:
                # Check if update needed
                tgt_ms = tgt_by_title[title]
                needs_update = (
                    tgt_ms.get("state") != payload["state"]
                    or (tgt_ms.get("description") or "") != payload["description"]
                    or tgt_ms.get("due_on") != payload.get("due_on")
                )
                if needs_update:
                    number = tgt_ms.get("number")
                    url = _get_api_url(
                        target_platform,
                        f"/repos/{target_owner}/{repo_name}/milestones/{number}",
                    )
                    if target_platform == "github":
                        resp = api_request(
                            "PATCH", url, headers=github_headers(target_token),
                            json=payload, max_retries=1,
                        )
                    else:
                        payload["access_token"] = target_token
                        resp = api_request("PATCH", url, json=payload, max_retries=1)

                    if resp.status_code in (200, 201):
                        updated += 1

        if created or updated:
            logging.info(f"  Milestones synced: {created} created, {updated} updated")

    except Exception as e:
        logging.warning(f"  Milestones sync failed: {e}")


# ---- Issues sync ----

SYNC_MARKER = "<!-- synced-from: {url} -->"


def sync_issues(source_platform, target_platform, source_owner, target_owner,
                source_token, target_token, repo_name):
    """Sync open issues from source to target.

    Uses a marker in the body to avoid duplicate creation.
    Only syncs open issues. Also syncs comments.
    """
    try:
        src_issues = _paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/issues",
            extra_params={"state": "open"},
        )
        tgt_issues = _paginated_get(
            target_platform, target_token,
            f"/repos/{target_owner}/{repo_name}/issues",
            extra_params={"state": "all"},
        )

        # Filter out pull requests (GitHub returns PRs in issues endpoint)
        src_issues = [i for i in src_issues if not i.get("pull_request")]
        tgt_issues = [i for i in tgt_issues if not i.get("pull_request")]

        # Build set of already-synced issue markers from target
        synced_markers = set()
        for issue in tgt_issues:
            body = issue.get("body") or ""
            if "<!-- synced-from:" in body:
                synced_markers.add(body.split("<!-- synced-from:")[1].split("-->")[0].strip())

        created = 0
        for src_issue in src_issues:
            title = src_issue.get("title")
            if not title:
                continue

            # Build source issue URL for marker
            issue_number = src_issue.get("number")
            if source_platform == "github":
                src_url = f"https://github.com/{source_owner}/{repo_name}/issues/{issue_number}"
            else:
                src_url = f"https://gitee.com/{source_owner}/{repo_name}/issues/{issue_number}"

            if src_url in synced_markers:
                continue  # Already synced

            # Create issue on target
            body = src_issue.get("body") or ""
            marker = SYNC_MARKER.format(url=src_url)
            body = f"{body}\n\n---\n{marker}"

            url = _get_api_url(
                target_platform,
                f"/repos/{target_owner}/{repo_name}/issues",
            )
            payload = {"title": title, "body": body}

            if target_platform == "github":
                resp = api_request(
                    "POST", url, headers=github_headers(target_token),
                    json=payload, max_retries=1,
                )
            else:
                payload["access_token"] = target_token
                resp = api_request("POST", url, json=payload, max_retries=1)

            if resp.status_code in (200, 201):
                created += 1
                new_issue = resp.json()
                # Sync comments
                _sync_issue_comments(
                    source_platform, target_platform,
                    source_owner, target_owner,
                    source_token, target_token,
                    repo_name, issue_number, new_issue.get("number"),
                )
            else:
                logging.warning(f"  Failed to create issue '{title}': {resp.status_code}")

        if created:
            logging.info(f"  Issues synced: {created} created")

    except Exception as e:
        logging.warning(f"  Issues sync failed: {e}")


def _sync_issue_comments(source_platform, target_platform,
                         source_owner, target_owner,
                         source_token, target_token,
                         repo_name, src_issue_number, tgt_issue_number):
    """Sync comments from a source issue to a target issue."""
    if not tgt_issue_number:
        return
    try:
        comments = _paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/issues/{src_issue_number}/comments",
        )
        for comment in comments:
            body = comment.get("body")
            if not body:
                continue
            url = _get_api_url(
                target_platform,
                f"/repos/{target_owner}/{repo_name}/issues/{tgt_issue_number}/comments",
            )
            payload = {"body": body}
            if target_platform == "github":
                api_request(
                    "POST", url, headers=github_headers(target_token),
                    json=payload, max_retries=1,
                )
            else:
                payload["access_token"] = target_token
                api_request("POST", url, json=payload, max_retries=1)
    except Exception as e:
        logging.warning(f"  Issue comments sync failed: {e}")


# ---- Dispatch extra sync calls per repo ----

def sync_extras(source_platform, target_platform, source_owner, target_owner,
                source_token, target_token, repo_name, sync_extra):
    """Call the appropriate extra sync functions based on sync_extra set."""
    if "releases" in sync_extra:
        logging.info(f"  Syncing releases ...")
        sync_releases(
            source_platform, target_platform,
            source_owner, target_owner,
            source_token, target_token,
            repo_name,
        )

    if "wiki" in sync_extra:
        logging.info(f"  Syncing wiki ...")
        sync_wiki(
            source_platform, target_platform,
            source_owner, target_owner,
            source_token, target_token,
            repo_name,
        )

    if "labels" in sync_extra:
        logging.info(f"  Syncing labels ...")
        sync_labels(
            source_platform, target_platform,
            source_owner, target_owner,
            source_token, target_token,
            repo_name,
        )

    if "milestones" in sync_extra:
        logging.info(f"  Syncing milestones ...")
        sync_milestones(
            source_platform, target_platform,
            source_owner, target_owner,
            source_token, target_token,
            repo_name,
        )

    if "issues" in sync_extra:
        logging.info(f"  Syncing issues ...")
        sync_issues(
            source_platform, target_platform,
            source_owner, target_owner,
            source_token, target_token,
            repo_name,
        )


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

        # 4c. Sync repository metadata (description, homepage)
        sync_repo_metadata(
            source_platform, target_platform,
            source_owner, target_owner,
            source_token, target_token,
            repo_name,
        )

        # 4d. Sync extra items (releases, wiki, labels, milestones, issues)
        if sync_extra:
            sync_extras(
                source_platform, target_platform,
                source_owner, target_owner,
                source_token, target_token,
                repo_name, sync_extra,
            )

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
    setup_logging()
    args = parse_args()

    try:
        # Pre-flight checks
        check_git_installed()
        validate_tokens(args.github_token, args.gitee_token)
    except Exception as e:
        logging.error(f"[FATAL] {e}")
        sys.exit(3)

    try:
        exit_code = sync_all(args)
    except Exception as e:
        logging.error(f"[FATAL] Unexpected error: {e}")
        exit_code = 3

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
