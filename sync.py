#!/usr/bin/env python3
"""
GitHub-Gitee Sync Tool

Sync all repositories (public/private) between GitHub and Gitee.
Supports one-way (GitHub→Gitee, Gitee→GitHub) and bidirectional sync.
"""

import argparse
import os
import sys


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


def main():
    """Main entry point."""
    args = parse_args()


if __name__ == "__main__":
    main()
