#!/usr/bin/env python3
"""
sync.py — GitHub-Gitee 全仓库同步工具 (主入口)

同步 GitHub 和 Gitee 账号下的全部仓库（支持公开和私有仓库）。
支持单向同步 (GitHub→Gitee, Gitee→GitHub) 和双向同步。

使用方式:
  python sync.py --github-owner <owner> --github-token <token> \
                 --gitee-owner <owner> --gitee-token <token>

  或通过环境变量:
  GITHUB_OWNER=<owner> GITHUB_TOKEN=<token> \
  GITEE_OWNER=<owner> GITEE_TOKEN=<token> python sync.py

模块结构:
  sync.py          — 主入口: 参数解析、主同步流程编排
  lib/utils.py     — 通用工具: 日志、HTTP 请求、Token 脱敏
  lib/github_api.py — GitHub REST API 封装
  lib/gitee_api.py  — Gitee REST API 封装
  lib/sync_repo.py  — 单仓库同步: mirror, metadata, extras

对应需求文档:
  docs/计划/Python-脚本设计.md — 整体架构与参数设计
  docs/计划/开发步骤.md — 开发步骤与交付检查项
  docs/计划/流程图.md — 同步流程（主流程 + 单仓库流程）
  docs/计划/错误处理设计.md — 退出码设计、错误分类
"""

import argparse
import logging
import os
import sys

from lib.utils import (
    build_clone_url,
    check_git_installed,
    get_log_collector,
    setup_logging,
    validate_repo_name,
    write_action_outputs,
)
from lib.github_api import (
    create_github_repo,
    get_github_repos,
    validate_github_token,
)
from lib.gitee_api import (
    create_gitee_repo,
    get_gitee_repos,
    validate_gitee_token,
)
from lib.sync_repo import (
    mirror_sync,
    sync_extras,
    sync_repo_metadata,
)


# ===========================================================================
# 参数解析
# ===========================================================================


def parse_args():
    """解析命令行参数和环境变量。

    优先级: CLI 参数 > 环境变量 > 默认值
    对应需求: docs/计划/Python-脚本设计.md — parse_args() 模块

    支持的参数:
    ┌───────────────────────┬──────────────────────┬──────┬──────────────┐
    │ CLI 参数              │ 环境变量              │ 必填 │ 默认值       │
    ├───────────────────────┼──────────────────────┼──────┼──────────────┤
    │ --github-owner        │ GITHUB_OWNER         │ ✅   │ -            │
    │ --github-token        │ GITHUB_TOKEN         │ ✅   │ -            │
    │ --gitee-owner         │ GITEE_OWNER          │ ✅   │ -            │
    │ --gitee-token         │ GITEE_TOKEN          │ ✅   │ -            │
    │ --account-type        │ ACCOUNT_TYPE         │ ❌   │ user         │
    │ --include-private     │ INCLUDE_PRIVATE      │ ❌   │ true         │
    │ --include-repos       │ INCLUDE_REPOS        │ ❌   │ (空)         │
    │ --exclude-repos       │ EXCLUDE_REPOS        │ ❌   │ (空)         │
    │ --direction           │ SYNC_DIRECTION       │ ❌   │ github2gitee │
    │ --create-missing-repos│ CREATE_MISSING_REPOS │ ❌   │ true         │
    │ --sync-extra          │ SYNC_EXTRA           │ ❌   │ (空)         │
    │ --dry-run             │ DRY_RUN              │ ❌   │ false        │
    └───────────────────────┴──────────────────────┴──────┴──────────────┘
    """
    parser = argparse.ArgumentParser(
        description="Sync repositories between GitHub and Gitee"
    )

    # --- 必填参数 ---
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

    # --- 可选参数 ---
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
        "--include-repos",
        default=os.environ.get("INCLUDE_REPOS", ""),
        help=(
            "Comma-separated list of repository names to include (allow list). "
            "When set, ONLY these repos are synced. "
            "Takes precedence over exclude-repos."
        ),
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
        help=(
            "Comma-separated extra items to sync: "
            "releases,wiki,labels,milestones,issues"
        ),
    )
    # --- dry-run 选项 ---
    # 对应需求: 用户反馈 — "增加 dry-run 选项，运行全部的功能，但不实际同步"
    parser.add_argument(
        "--dry-run",
        default=os.environ.get("DRY_RUN", "false"),
        help=(
            "Run all logic without performing actual sync operations "
            "(default: false). Useful for debugging and testing."
        ),
    )

    args = parser.parse_args()

    # --- 字符串布尔值转换 ---
    args.include_private = str(args.include_private).lower() in (
        "true", "1", "yes",
    )
    args.create_missing_repos = str(args.create_missing_repos).lower() in (
        "true", "1", "yes",
    )
    args.dry_run = str(args.dry_run).lower() in ("true", "1", "yes")

    # --- 逗号分隔列表解析 ---
    args.include_repos = set(
        r.strip() for r in args.include_repos.split(",") if r.strip()
    )
    args.exclude_repos = set(
        r.strip() for r in args.exclude_repos.split(",") if r.strip()
    )
    args.sync_extra = set(
        s.strip() for s in args.sync_extra.split(",") if s.strip()
    )

    # --- sync_extra 有效值校验 ---
    # 对应: 二级评审 Issue #6 — "sync_extra 参数对无效值静默忽略"
    VALID_EXTRA = {"releases", "wiki", "labels", "milestones", "issues"}
    invalid = args.sync_extra - VALID_EXTRA
    if invalid:
        logging.warning(f"Unknown sync-extra values ignored: {invalid}")
        args.sync_extra = args.sync_extra & VALID_EXTRA

    # --- include_repos 与 exclude_repos 冲突校验 ---
    if args.include_repos and args.exclude_repos:
        logging.warning(
            "Both include-repos and exclude-repos are set. "
            "include-repos takes precedence; exclude-repos will be ignored."
        )

    # --- 必填参数校验 ---
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


# ===========================================================================
# 主同步流程
# ===========================================================================


def sync_one_direction(source_platform, target_platform, source_owner,
                       target_owner, source_token, target_token,
                       account_type, include_private, include_repos,
                       exclude_repos, create_missing_repos, sync_extra,
                       dry_run=False,
                       source_username="git", target_username="git"):
    """执行单方向同步: 从 source 平台到 target 平台。

    对应需求: docs/计划/流程图.md — "主同步流程" 步骤 4-8

    完整流程:
    1. 获取源平台仓库列表（分页、私有过滤）
    2. 过滤排除的仓库
    3. 获取目标平台仓库列表（用于存在性检查）
    4. 逐个仓库同步:
       a. 检查/创建目标仓库 (docs/计划/流程图.md — Step A)
       b. git mirror 同步      (docs/计划/流程图.md — Step B)
       c. 元信息同步            (docs/计划/流程图.md — Step C)
       d. 附属信息同步          (docs/计划/流程图.md — Step D-H)

    错误处理原则（对应 docs/计划/错误处理设计.md）:
    - Git 代码同步失败 → 整个仓库标记失败
    - 附属信息同步失败 → 仅警告，不影响仓库同步状态
    - 任何错误 → 继续下一个仓库（不中断全局流程）

    Returns:
        (synced_count, failed_count, skipped_count, failed_repos) 元组。
    """
    synced = 0
    failed = 0
    skipped = 0
    failed_repos = []

    # === 步骤 1: 获取源平台仓库列表 ===
    logging.info(f"Fetching {source_platform} repos for {source_owner} ...")
    if source_platform == "github":
        source_repos = get_github_repos(
            source_owner, source_token, account_type, include_private
        )
    else:
        # include_private 参数传递给 Gitee（修复 PR review 反馈）
        source_repos = get_gitee_repos(
            source_owner, source_token, account_type, include_private
        )

    logging.info(f"Found {len(source_repos)} repos on {source_platform}")

    # === 步骤 2: 过滤仓库（include_repos 优先于 exclude_repos）===
    if include_repos:
        before = len(source_repos)
        source_repos = [
            r for r in source_repos if r["name"] in include_repos
        ]
        included_count = len(source_repos)
        logging.info(
            f"Include-repos filter applied: {included_count}/{before} repos "
            f"matched allow list: {', '.join(sorted(include_repos))}"
        )
    elif exclude_repos:
        before = len(source_repos)
        source_repos = [
            r for r in source_repos if r["name"] not in exclude_repos
        ]
        excluded_count = before - len(source_repos)
        if excluded_count > 0:
            logging.info(
                f"Excluded {excluded_count} repos: "
                f"{', '.join(sorted(exclude_repos))}"
            )

    logging.info(f"Repos to sync: {len(source_repos)}")

    if not source_repos:
        logging.info("No repos to sync.")
        return synced, failed, skipped, failed_repos

    # === 步骤 3: 获取目标平台仓库列表（用于存在性检查）===
    logging.info(
        f"Fetching {target_platform} repos for {target_owner} ..."
    )
    if target_platform == "github":
        target_repos_list = get_github_repos(
            target_owner, target_token, account_type, True
        )
    else:
        target_repos_list = get_gitee_repos(
            target_owner, target_token, account_type
        )

    target_repo_names = {r["name"] for r in target_repos_list}
    logging.info(
        f"Found {len(target_repo_names)} existing repos on {target_platform}"
    )

    # === 步骤 4: 逐个仓库同步 ===
    total = len(source_repos)
    for idx, repo in enumerate(source_repos, 1):
        repo_name = repo["name"]

        # --- 安全: 验证仓库名 ---
        # 对应: 安全评审 — 防止路径遍历和命令注入
        if not validate_repo_name(repo_name):
            logging.warning(
                f"[{idx}/{total}] Skipping {repo_name}: "
                f"invalid repository name (security check)"
            )
            skipped += 1
            continue

        logging.info(f"[{idx}/{total}] Syncing {repo_name} ...")

        # --- Step A: 检查/创建目标仓库 ---
        # 对应: docs/计划/流程图.md — "检查目标仓库是否存在"
        if repo_name not in target_repo_names:
            if not create_missing_repos:
                logging.info(
                    f"  Target repo not found and "
                    f"create_missing_repos=false, skipping"
                )
                skipped += 1
                continue

            if dry_run:
                logging.info(
                    f"  [DRY-RUN] Would create {target_platform} "
                    f"repo: {repo_name}"
                )
            else:
                # 在目标平台创建仓库
                if target_platform == "github":
                    ok = create_github_repo(
                        target_owner, target_token, repo_name,
                        repo.get("private", False),
                        repo.get("description", ""),
                        account_type,
                    )
                else:
                    ok = create_gitee_repo(
                        target_owner, target_token, repo_name,
                        repo.get("private", False),
                        repo.get("description", ""),
                        account_type,
                    )

                if not ok:
                    logging.error(
                        f"  Failed to create target repo, "
                        f"skipping {repo_name}"
                    )
                    failed += 1
                    failed_repos.append(
                        (repo_name, "Failed to create target repo")
                    )
                    continue

        # --- Step B: Git Mirror 同步（核心步骤）---
        # 对应: docs/计划/流程图.md — "git clone --mirror → git push --mirror"
        source_url = build_clone_url(
            source_platform, source_owner, repo_name
        )
        target_url = build_clone_url(
            target_platform, target_owner, repo_name
        )
        result = mirror_sync(
            source_url, target_url, repo_name,
            source_token, target_token, dry_run,
            source_username=source_username,
            target_username=target_username,
        )

        if result == "failed":
            failed += 1
            failed_repos.append((repo_name, "git mirror sync failed"))
            continue
        elif result == "empty":
            skipped += 1
            continue

        # --- Step C: 同步仓库元信息 (description, homepage) ---
        # 对应: docs/计划/流程图.md — "同步元信息"
        sync_repo_metadata(
            source_platform, target_platform,
            source_owner, target_owner,
            source_token, target_token,
            repo_name, dry_run,
        )

        # --- Step D-H: 同步附属信息 (releases, wiki, labels, ...) ---
        # 对应: docs/计划/流程图.md — "同步附属信息"
        if sync_extra:
            sync_extras(
                source_platform, target_platform,
                source_owner, target_owner,
                source_token, target_token,
                repo_name, sync_extra, dry_run,
                source_username=source_username,
                target_username=target_username,
            )

        synced += 1

    return synced, failed, skipped, failed_repos


def sync_all(args):
    """主同步编排函数。

    根据 direction 参数确定同步方向，执行同步，输出摘要。
    对应需求: docs/计划/流程图.md — "主同步流程" 步骤 1-11

    退出码设计（对应 docs/计划/错误处理设计.md）:
      0 = 全部成功
      1 = 部分失败（有成功也有失败）
      2 = 全部失败
      3 = 致命错误（认证失败、环境异常）
    """
    direction = args.direction
    dry_run = args.dry_run
    # Token 所有者用户名由 main() 中的 validate_*_token() 设置到 args 上。
    # 使用 getattr 防御性回退，确保测试等场景下不会因缺少属性而崩溃。
    github_username = getattr(args, "github_username", "git")
    gitee_username = getattr(args, "gitee_username", "git")
    total_synced = 0
    total_failed = 0
    total_skipped = 0
    all_failed_repos = []

    if dry_run:
        logging.info("🔍 DRY-RUN MODE — no actual changes will be made")

    # --- 正向同步: GitHub → Gitee ---
    if direction in ("github2gitee", "both"):
        logging.info("=" * 50)
        logging.info(
            f"Syncing GitHub({args.github_owner}) → "
            f"Gitee({args.gitee_owner})"
        )
        logging.info("=" * 50)
        s, f, sk, fr = sync_one_direction(
            "github", "gitee",
            args.github_owner, args.gitee_owner,
            args.github_token, args.gitee_token,
            args.account_type, args.include_private,
            args.include_repos, args.exclude_repos,
            args.create_missing_repos,
            args.sync_extra, dry_run,
            source_username=github_username,
            target_username=gitee_username,
        )
        total_synced += s
        total_failed += f
        total_skipped += sk
        all_failed_repos.extend(fr)

    # --- 反向同步: Gitee → GitHub ---
    if direction in ("gitee2github", "both"):
        logging.info("=" * 50)
        logging.info(
            f"Syncing Gitee({args.gitee_owner}) → "
            f"GitHub({args.github_owner})"
        )
        logging.info("=" * 50)
        s, f, sk, fr = sync_one_direction(
            "gitee", "github",
            args.gitee_owner, args.github_owner,
            args.gitee_token, args.github_token,
            args.account_type, args.include_private,
            args.include_repos, args.exclude_repos,
            args.create_missing_repos,
            args.sync_extra, dry_run,
            source_username=gitee_username,
            target_username=github_username,
        )
        total_synced += s
        total_failed += f
        total_skipped += sk
        all_failed_repos.extend(fr)

    # === 同步摘要 ===
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

    # === 写入 GitHub Action outputs ===
    write_action_outputs(total_synced, total_failed, total_skipped)

    # === 确定退出码 ===
    # 对应: docs/计划/错误处理设计.md — 退出码设计
    #   0 = 全部成功（有仓库被同步）
    #   1 = 部分失败（有成功也有失败）
    #   2 = 全部失败
    #   3 = 致命错误（认证失败、环境异常）
    # 二级评审 Issue #3: 全部跳过时给出明确警告
    if total_failed == 0 and total_synced == 0 and total_skipped > 0:
        logging.warning(
            "All repositories were skipped — no repos were actually synced. "
            "Check create_missing_repos setting and target repo availability."
        )
        return 0
    if total_failed == 0:
        return 0  # 全部成功
    elif total_synced > 0:
        return 1  # 部分失败
    else:
        return 2  # 全部失败


# ===========================================================================
# 主入口
# ===========================================================================


def main():
    """程序主入口。

    流程:
    1. 配置日志
    2. 解析参数
    3. 前置检查 (git 安装, Token 验证)
    4. 执行同步
    5. 返回退出码
    """
    setup_logging()
    args = parse_args()

    try:
        # --- 前置检查 ---
        # 对应: docs/计划/错误处理设计.md — "环境检查失败 → 立即退出(code=3)"
        check_git_installed()
        args.github_username = validate_github_token(args.github_token)
        args.gitee_username = validate_gitee_token(args.gitee_token)
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
