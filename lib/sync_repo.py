"""
lib/sync_repo.py — 单仓库同步模块

实现单个仓库的完整同步流程，包括：
- Git 增量同步（核心: git clone --mirror + git push --all/--tags --force）
- 仓库元信息同步（description, homepage）
- 附属信息同步（Releases, Wiki, Labels, Milestones, Issues）

对应需求文档:
- docs/计划/流程图.md — "单仓库同步流程" (步骤 A-H)
- docs/调研/Git-Mirror-同步机制.md — clone --mirror + push --all/--tags
- docs/调研/仓库附属信息同步调研.md — 各附属信息同步策略
- docs/计划/错误处理设计.md — 附属信息失败不影响仓库同步状态

同步方式:
  代码同步: git clone --mirror → git push --all --force + git push --tags --force
            （增量同步，不会删除目标仓库独有的分支和标签）
  元信息同步: REST API PATCH（非致命，失败仅警告）
  附属信息同步: REST API CRUD（非致命，失败仅警告）
"""

import logging
import os
import shutil
import subprocess
import tempfile
from urllib.parse import quote

import requests

from .utils import (
    GITHUB_API,
    GITEE_API,
    api_request,
    build_clone_url,
    gitee_headers,
    github_headers,
    make_git_env,
    mask_token,
    paginated_get,
)
from .github_api import get_github_repo_details, update_github_repo_metadata
from .gitee_api import get_gitee_repo_details, update_gitee_repo_metadata


# ===========================================================================
# 常量
# ===========================================================================

# Git 操作超时时间: 10 分钟
# 大型仓库 clone/push 可能需要较长时间
GIT_TIMEOUT = 600

# 释放资产下载的最大文件大小: 500MB
# 超过此大小的资产将被跳过，防止内存溢出
MAX_ASSET_SIZE = 500 * 1024 * 1024

# Issue 同步去重标记，嵌入在 issue body 的 HTML 注释中
# 用于防止重复运行时创建重复 issue
# 对应: docs/调研/仓库附属信息同步调研.md — "通过标识避免重复创建"
SYNC_MARKER = "<!-- synced-from: {url} -->"


# ===========================================================================
# Git 增量同步
# ===========================================================================


def mirror_sync(source_url, target_url, repo_name,
                source_token, target_token, dry_run=False):
    """执行 git clone --mirror + git push --all/--tags --force 完成代码同步。

    这是仓库同步的核心步骤。使用增量方式同步所有分支和标签，
    不会删除目标仓库上独有的分支和标签。

    流程:
    1. git clone --mirror <source_url> <temp_dir>   — 完整镜像克隆
    2. git push --all --force <target_url>           — 推送所有分支到目标
    3. git push --tags --force <target_url>          — 推送所有标签到目标
    4. 清理临时目录和 askpass 脚本

    安全策略:
    - 使用 --all + --tags 代替 --mirror 推送，确保不会删除目标仓库独有的
      分支和标签（增量同步）。
    - 使用 --force 确保源平台的变更能够覆盖到目标平台。
    - 目标仓库上仅存在的分支、标签不受影响。

    认证方式:
    - 使用 GIT_ASKPASS 临时脚本传递 Token（不在 URL 中内联 Token）
    - clone 使用 source_token，push 使用 target_token
    - 对应: PR review — "使用 GIT_ASKPASS 减少 Token 暴露"

    对应需求:
    - docs/调研/Git-Mirror-同步机制.md — "Strategy A: 每次全新 clone + push"
    - docs/计划/流程图.md — Step B "同步 Git 代码"
    - docs/计划/错误处理设计.md — "git clone/push 超时处理, 空仓库检测"

    Args:
        source_url: 源仓库 URL（无凭据的 HTTPS URL）。
        target_url: 目标仓库 URL（无凭据的 HTTPS URL）。
        repo_name: 仓库名（用于日志）。
        source_token: 源平台 Token（用于 clone 认证）。
        target_token: 目标平台 Token（用于 push 认证）。
        dry_run: 如果为 True，跳过实际 git 操作。

    Returns:
        'success': 同步成功。
        'empty': 源仓库为空，跳过推送。
        'failed': 同步失败。
    """
    if dry_run:
        logging.info(f"  [DRY-RUN] Would mirror sync {repo_name}")
        return "success"

    temp_dir = tempfile.mkdtemp(prefix=f"sync_{repo_name}_")
    askpass_paths = []
    try:
        # --- Step 1: git clone --mirror (使用 source_token 认证) ---
        logging.info(f"  Cloning from source ...")
        src_env, src_askpass = make_git_env(source_token)
        askpass_paths.append(src_askpass)
        result = subprocess.run(
            ["git", "clone", "--mirror", source_url, temp_dir],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env=src_env,
        )

        if result.returncode != 0:
            stderr = result.stderr
            # 空仓库检测: clone 会在 stderr 中包含 "empty repository" 警告
            if "empty repository" in stderr.lower():
                logging.warning(
                    f"  {repo_name} is an empty repository, skipping push"
                )
                return "empty"
            logging.error(
                f"  git clone --mirror failed: {mask_token(stderr)}"
            )
            return "failed"

        # 二次检查: clone 成功但 stderr 中有空仓库警告
        if "empty repository" in (result.stderr or "").lower():
            logging.warning(
                f"  {repo_name} is an empty repository, skipping push"
            )
            return "empty"

        # --- Step 2: git push --all --force (推送所有分支，不删除目标独有分支) ---
        logging.info(f"  Pushing branches to target ...")
        tgt_env, tgt_askpass = make_git_env(target_token)
        askpass_paths.append(tgt_askpass)
        result = subprocess.run(
            ["git", "push", "--all", "--force", target_url],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env=tgt_env,
        )

        if result.returncode != 0:
            logging.error(
                f"  git push --all --force failed: "
                f"{mask_token(result.stderr)}"
            )
            return "failed"

        # --- Step 3: git push --tags --force (推送所有标签，不删除目标独有标签) ---
        logging.info(f"  Pushing tags to target ...")
        result = subprocess.run(
            ["git", "push", "--tags", "--force", target_url],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env=tgt_env,
        )

        if result.returncode != 0:
            logging.error(
                f"  git push --tags --force failed: "
                f"{mask_token(result.stderr)}"
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
        # --- Step 4: 清理临时目录和 askpass 脚本 ---
        # 确保临时文件被清理，即使发生异常
        # 对应: 安全评审 — 防止敏感数据残留
        try:
            shutil.rmtree(temp_dir, ignore_errors=False)
        except Exception as e:
            # 清理失败记录警告，但不影响返回结果
            logging.warning(
                f"  Failed to clean up temp directory {temp_dir}: {e}"
            )

        for p in askpass_paths:
            try:
                # 先尝试覆盖文件内容再删除，防止数据恢复
                if os.path.exists(p):
                    # 用零覆盖文件内容
                    file_size = os.path.getsize(p)
                    with open(p, 'wb') as f:
                        f.write(b'\x00' * file_size)
                    os.unlink(p)
            except OSError as e:
                logging.warning(f"  Failed to clean askpass script {p}: {e}")


# ===========================================================================
# 仓库元信息同步
# ===========================================================================


def sync_repo_metadata(source_platform, target_platform, source_owner,
                       target_owner, source_token, target_token, repo_name,
                       dry_run=False):
    """同步仓库元信息（description, homepage）。

    流程:
    1. 获取源仓库详情（description, homepage 等）
    2. 获取目标仓库详情
    3. 对比差异，仅更新有变化的字段
    4. 调用 PATCH API 更新

    此步骤为非致命操作，失败仅记录警告。
    对应: docs/计划/流程图.md — Step C "同步元信息"
    对应: docs/调研/仓库附属信息同步调研.md — "Repo Metadata Sync"

    Args:
        source_platform: 源平台 ("github" 或 "gitee")。
        target_platform: 目标平台。
        source_owner: 源仓库所有者。
        target_owner: 目标仓库所有者。
        source_token: 源平台 Token。
        target_token: 目标平台 Token。
        repo_name: 仓库名。
        dry_run: 如果为 True，跳过实际更新操作。
    """
    try:
        # --- 获取源仓库详情 ---
        if source_platform == "github":
            source_info = get_github_repo_details(
                source_owner, source_token, repo_name
            )
        else:
            source_info = get_gitee_repo_details(
                source_owner, source_token, repo_name
            )

        if not source_info:
            logging.warning(
                f"  Could not fetch source repo details for metadata sync"
            )
            return

        # --- 获取目标仓库详情 ---
        if target_platform == "github":
            target_info = get_github_repo_details(
                target_owner, target_token, repo_name
            )
        else:
            target_info = get_gitee_repo_details(
                target_owner, target_token, repo_name
            )

        if not target_info:
            logging.warning(
                f"  Could not fetch target repo details for metadata sync"
            )
            return

        # --- 对比差异 ---
        updates = {}
        for key in ("description", "homepage"):
            if source_info.get(key, "") != target_info.get(key, ""):
                updates[key] = source_info[key]

        if not updates:
            logging.debug(f"  Metadata already in sync")
            return

        if dry_run:
            logging.info(
                f"  [DRY-RUN] Would update metadata: {', '.join(updates.keys())}"
            )
            return

        # --- 更新目标仓库 ---
        logging.info(f"  Syncing metadata: {', '.join(updates.keys())}")
        if target_platform == "github":
            update_github_repo_metadata(
                target_owner, target_token, repo_name, updates
            )
        else:
            update_gitee_repo_metadata(
                target_owner, target_token, repo_name, updates
            )

    except Exception as e:
        logging.warning(f"  Metadata sync failed: {e}")


# ===========================================================================
# Releases 同步
# ===========================================================================


def sync_releases(source_platform, target_platform, source_owner, target_owner,
                  source_token, target_token, repo_name, dry_run=False):
    """同步 Releases（含 Release Assets）。

    匹配方式: 以 tag_name 为匹配键。
    - 目标不存在的 release: 创建并同步 assets
    - 目标已存在的 release: 更新元信息（name, body, prerelease）并补充缺失的 assets

    对应需求:
    - docs/调研/仓库附属信息同步调研.md — "Releases Sync"
    - docs/调研/GitHub-API.md — GET/POST /repos/{owner}/{repo}/releases
    - docs/调研/Gitee-API.md — GET/POST /api/v5/repos/{owner}/{repo}/releases

    Release Assets 同步:
    - GitHub: 通过 asset.url + Accept: application/octet-stream 下载（支持私有仓库）
    - Gitee: 通过 browser_download_url + Bearer header 下载
    - 上传: GitHub 使用 uploads.github.com; Gitee 使用 attach_files 端点
    - 文件流式下载到临时文件，避免大文件导致内存溢出
    - 跳过超过 MAX_ASSET_SIZE 的文件

    Args:
        dry_run: 如果为 True，仅列出需要同步的 releases 而不实际操作。
    """
    try:
        # --- 获取源/目标 releases ---
        src_releases = paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/releases",
        )
        tgt_releases = paginated_get(
            target_platform, target_token,
            f"/repos/{target_owner}/{repo_name}/releases",
        )

        tgt_by_tag = {
            r["tag_name"]: r for r in tgt_releases if r.get("tag_name")
        }

        created = 0
        updated = 0
        for src_rel in src_releases:
            tag = src_rel.get("tag_name")
            if not tag:
                continue

            if tag in tgt_by_tag:
                # --- 已存在: 更新元信息 + 补充缺失的 assets ---
                tgt_rel = tgt_by_tag[tag]
                _update_existing_release(
                    target_platform, target_owner, target_token, repo_name,
                    src_rel, tgt_rel, dry_run,
                )
                # 同步缺失的 assets
                _sync_release_assets(
                    source_platform, target_platform,
                    source_owner, target_owner,
                    source_token, target_token,
                    repo_name, src_rel, tgt_rel, dry_run,
                )
                updated += 1
                continue

            if dry_run:
                logging.info(f"  [DRY-RUN] Would create release: {tag}")
                created += 1
                continue

            # --- 不存在: 创建 release ---
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
                resp = api_request(
                    "POST", url, headers=gitee_headers(target_token),
                    json=payload, max_retries=1,
                )

            if resp.status_code in (200, 201):
                created += 1
                new_release = resp.json()
                # 同步 release assets
                _sync_release_assets(
                    source_platform, target_platform,
                    source_owner, target_owner,
                    source_token, target_token,
                    repo_name, src_rel, new_release, dry_run,
                )
            else:
                logging.warning(
                    f"  Failed to create release {tag}: {resp.status_code}"
                )

        if created or updated:
            logging.info(
                f"  Releases synced: {created} created, {updated} checked/updated"
            )

    except Exception as e:
        logging.warning(f"  Releases sync failed: {e}")


def _update_existing_release(target_platform, target_owner, target_token,
                             repo_name, src_rel, tgt_rel, dry_run):
    """更新已存在 release 的元信息（name, body, prerelease）。"""
    needs_update = (
        (src_rel.get("name") or "") != (tgt_rel.get("name") or "")
        or (src_rel.get("body") or "") != (tgt_rel.get("body") or "")
        or src_rel.get("prerelease", False) != tgt_rel.get("prerelease", False)
    )
    if not needs_update:
        return

    tag = src_rel.get("tag_name", "")
    if dry_run:
        logging.info(f"  [DRY-RUN] Would update release: {tag}")
        return

    release_id = tgt_rel.get("id")
    if not release_id:
        return

    url = _get_api_url(
        target_platform,
        f"/repos/{target_owner}/{repo_name}/releases/{release_id}",
    )
    payload = {
        "name": src_rel.get("name") or tag,
        "body": src_rel.get("body") or "",
        "prerelease": src_rel.get("prerelease", False),
    }

    if target_platform == "github":
        payload["tag_name"] = tag
        resp = api_request(
            "PATCH", url, headers=github_headers(target_token),
            json=payload, max_retries=1,
        )
    else:
        payload["tag_name"] = tag
        resp = api_request(
            "PATCH", url, headers=gitee_headers(target_token),
            json=payload, max_retries=1,
        )

    if resp.status_code not in (200, 201):
        logging.warning(f"  Failed to update release {tag}: {resp.status_code}")


def _sync_release_assets(source_platform, target_platform, source_owner,
                         target_owner, source_token, target_token, repo_name,
                         src_release, tgt_release, dry_run=False):
    """同步 Release Assets（下载源端 → 上传目标端）。

    流式下载到临时文件，避免内存溢出。
    已存在同名 asset 则跳过。

    对应: PR review — "使用 asset.url + Accept 认证下载, 流式传输"
    """

    assets = src_release.get("assets", [])
    if not assets:
        return

    tgt_release_id = tgt_release.get("id")
    if not tgt_release_id:
        return

    # 获取目标已有的 asset 名称集合
    tgt_assets = tgt_release.get("assets", [])
    tgt_asset_names = {a.get("name", "") for a in tgt_assets}

    for asset in assets:
        asset_name = asset.get("name", "")
        if not asset_name:
            continue

        # 跳过目标已存在的同名 asset
        # 二级评审 Issue #9: 添加 debug 日志记录跳过的 asset
        if asset_name in tgt_asset_names:
            logging.debug(
                f"  Asset {asset_name} already exists on target, skipping"
            )
            continue

        # 检查文件大小
        asset_size = asset.get("size", 0)
        if asset_size > MAX_ASSET_SIZE:
            logging.warning(
                f"  Skipping asset {asset_name} ({asset_size} bytes > "
                f"{MAX_ASSET_SIZE} bytes limit)"
            )
            continue

        if dry_run:
            logging.info(f"  [DRY-RUN] Would upload asset: {asset_name}")
            continue

        try:
            # --- 下载 asset (流式写入临时文件) ---
            # 确定下载 URL 和认证参数
            if source_platform == "github":
                # GitHub: 使用 asset.url + Accept header 认证下载
                # 这样私有仓库的 assets 也能正确下载
                asset_api_url = asset.get("url", "")
                if not asset_api_url:
                    continue
                dl_url = asset_api_url
                dl_kwargs = {
                    "headers": {
                        "Authorization": f"Bearer {source_token}",
                        "Accept": "application/octet-stream",
                    },
                    "timeout": 300,
                    "stream": True,
                }
            else:
                download_url = asset.get("browser_download_url", "")
                if not download_url:
                    continue
                dl_url = download_url
                dl_kwargs = {
                    "headers": gitee_headers(source_token),
                    "timeout": 300,
                    "stream": True,
                }

            # 创建临时文件用于流式写入
            tmp_path = None
            fd, tmp_path = tempfile.mkstemp(prefix="asset_")
            try:
                # 使用 with 确保 response 在所有路径（包括 continue）上正确关闭
                with requests.get(dl_url, **dl_kwargs) as dl_resp:
                    if dl_resp.status_code != 200:
                        logging.warning(
                            f"  Failed to download asset {asset_name}: "
                            f"HTTP {dl_resp.status_code}"
                        )
                        continue

                    # 流式写入临时文件（使用 os.fdopen 确保 fd 被正确关闭）
                    with os.fdopen(fd, "wb") as tmp_file:
                        for chunk in dl_resp.iter_content(chunk_size=8192):
                            if chunk:
                                tmp_file.write(chunk)
                    # fd 已被 os.fdopen 接管并关闭，标记为 None 防止重复关闭
                    fd = None

                # --- 上传 asset ---
                content_type = asset.get(
                    "content_type", "application/octet-stream"
                )
                if target_platform == "github":
                    upload_url = tgt_release.get("upload_url", "")
                    # 移除 URL 模板部分: {?name,label}
                    upload_url = upload_url.split("{")[0]
                    upload_url = f"{upload_url}?name={quote(asset_name)}"
                    with open(tmp_path, "rb") as f:
                        up_resp = api_request(
                            "POST", upload_url,
                            headers={
                                "Authorization": f"Bearer {target_token}",
                                "Content-Type": content_type,
                            },
                            data=f,
                            max_retries=1,
                            timeout=300,
                        )
                else:
                    upload_url = _get_api_url(
                        target_platform,
                        f"/repos/{target_owner}/{repo_name}/releases/"
                        f"{tgt_release_id}/attach_files",
                    )
                    with open(tmp_path, "rb") as f:
                        up_resp = api_request(
                            "POST", upload_url,
                            headers=gitee_headers(target_token),
                            files={"file": (asset_name, f)},
                            max_retries=1,
                            timeout=300,
                        )

                if up_resp.status_code in (200, 201):
                    logging.debug(f"  Uploaded asset: {asset_name}")
                else:
                    logging.warning(
                        f"  Failed to upload asset {asset_name}: "
                        f"{up_resp.status_code}"
                    )
            finally:
                # 确保 fd 被关闭（如果尚未被 os.fdopen 接管）
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                # 清理临时文件
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        except Exception as e:
            logging.warning(f"  Asset sync failed for {asset_name}: {e}")


# ===========================================================================
# Wiki 同步
# ===========================================================================


def sync_wiki(source_platform, target_platform, source_owner, target_owner,
              source_token, target_token, repo_name, dry_run=False):
    """同步 Wiki（使用 git clone --mirror .wiki.git + git push --all/--tags --force）。

    Wiki 没有统一的 REST API（GitHub 完全不支持 Wiki REST API），
    因此使用 Git 方式同步。使用增量推送（--all + --tags），不会删除
    目标仓库上独有的 Wiki 页面和修改。

    前提条件: 目标平台已启用 Wiki（至少有一个页面或已启用 Wiki 功能）。
    源 Wiki 不存在时静默跳过（clone 会失败，但不视为错误）。

    认证方式: 使用 GIT_ASKPASS 临时脚本（不在 URL 中内联 Token）。

    对应需求:
    - docs/调研/仓库附属信息同步调研.md — "Wiki Sync: git clone --mirror .wiki.git"
    - docs/计划/流程图.md — Step D "同步 Wiki"

    Args:
        dry_run: 如果为 True，跳过实际 git 操作。
    """
    if dry_run:
        logging.info(f"  [DRY-RUN] Would sync wiki for {repo_name}")
        return

    askpass_paths = []
    try:
        # 构建 .wiki.git URL（无凭据）
        if source_platform == "github":
            source_url = (
                f"https://github.com/"
                f"{source_owner}/{repo_name}.wiki.git"
            )
        else:
            source_url = (
                f"https://gitee.com/"
                f"{source_owner}/{repo_name}.wiki.git"
            )

        if target_platform == "github":
            target_url = (
                f"https://github.com/"
                f"{target_owner}/{repo_name}.wiki.git"
            )
        else:
            target_url = (
                f"https://gitee.com/"
                f"{target_owner}/{repo_name}.wiki.git"
            )

        temp_dir = tempfile.mkdtemp(prefix=f"wiki_{repo_name}_")
        try:
            # Clone wiki 使用 source_token 认证
            src_env, src_askpass = make_git_env(source_token)
            askpass_paths.append(src_askpass)
            result = subprocess.run(
                ["git", "clone", "--mirror", source_url, temp_dir],
                capture_output=True, text=True, timeout=GIT_TIMEOUT,
                env=src_env,
            )
            if result.returncode != 0:
                # Wiki clone 失败 — 可能源仓库未启用 Wiki
                # 二级评审 Issue #8: 从 debug 改为 warning，让用户知道 Wiki 未被同步
                logging.warning(
                    f"  Wiki not available for {repo_name}, skipping "
                    f"(ensure Wiki is enabled on source repo)"
                )
                return

            # Push wiki 使用 target_token 认证（增量推送，不删除目标独有内容）
            tgt_env, tgt_askpass = make_git_env(target_token)
            askpass_paths.append(tgt_askpass)
            push_result = subprocess.run(
                ["git", "push", "--all", "--force", target_url],
                cwd=temp_dir,
                capture_output=True, text=True, timeout=GIT_TIMEOUT,
                env=tgt_env,
            )
            if push_result.returncode != 0:
                logging.warning(
                    f"  Wiki push failed: "
                    f"{mask_token(push_result.stderr)}"
                )
            else:
                # 推送标签（Wiki 通常无标签，但为完整性保留）
                # Wiki 同步为非致命操作，标签推送失败仅记录警告
                tags_result = subprocess.run(
                    ["git", "push", "--tags", "--force", target_url],
                    cwd=temp_dir,
                    capture_output=True, text=True, timeout=GIT_TIMEOUT,
                    env=tgt_env,
                )
                if tags_result.returncode != 0:
                    logging.warning(
                        f"  Wiki tags push failed: "
                        f"{mask_token(tags_result.stderr)}"
                    )
                else:
                    logging.info(f"  Wiki synced ✓")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    except subprocess.TimeoutExpired:
        logging.warning(f"  Wiki sync timed out")
    except Exception as e:
        logging.warning(f"  Wiki sync failed: {mask_token(str(e))}")
    finally:
        # 清理 askpass 脚本
        for p in askpass_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


# ===========================================================================
# Labels 同步
# ===========================================================================


def sync_labels(source_platform, target_platform, source_owner, target_owner,
                source_token, target_token, repo_name, dry_run=False):
    """同步 Labels（按 name 匹配）。

    策略（对应 docs/调研/仓库附属信息同步调研.md — "Labels Sync"）:
    - 源有 → 目标无: 创建
    - 两端都有: 对比 color/description，有差异则更新
    - 源无 → 目标有: 保留（不删除用户自定义标签）

    同步字段: name, color, description

    REST API:
    - GitHub: GET/POST/PATCH /repos/{owner}/{repo}/labels[/{name}]
    - Gitee:  GET/POST/PATCH /api/v5/repos/{owner}/{repo}/labels[/{name}]

    注意: label name 可能包含空格等特殊字符，URL 中需要编码。

    Args:
        dry_run: 如果为 True，仅列出需要同步的 labels 而不实际操作。
    """
    try:
        # --- 获取源/目标 labels ---
        src_labels = paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/labels",
        )
        tgt_labels = paginated_get(
            target_platform, target_token,
            f"/repos/{target_owner}/{repo_name}/labels",
        )

        tgt_by_name = {
            la["name"]: la for la in tgt_labels if la.get("name")
        }

        created = 0
        updated = 0
        for src_label in src_labels:
            name = src_label.get("name")
            if not name:
                continue

            # 标准化颜色值: 去掉前缀 '#'
            color = src_label.get("color", "")
            if color.startswith("#"):
                color = color[1:]
            description = src_label.get("description") or ""

            if name not in tgt_by_name:
                # --- 创建新 label ---
                if dry_run:
                    logging.info(f"  [DRY-RUN] Would create label: {name}")
                    created += 1
                    continue

                url = _get_api_url(
                    target_platform,
                    f"/repos/{target_owner}/{repo_name}/labels",
                )
                payload = {
                    "name": name,
                    "color": color,
                    "description": description,
                }
                if target_platform == "github":
                    resp = api_request(
                        "POST", url,
                        headers=github_headers(target_token),
                        json=payload, max_retries=1,
                    )
                else:
                    resp = api_request(
                        "POST", url, headers=gitee_headers(target_token),
                        json=payload, max_retries=1,
                    )

                if resp.status_code in (200, 201):
                    created += 1
                else:
                    logging.warning(
                        f"  Failed to create label {name}: "
                        f"{resp.status_code}"
                    )
            else:
                # --- 检查是否需要更新 ---
                tgt = tgt_by_name[name]
                tgt_color = (tgt.get("color") or "").lstrip("#")
                tgt_desc = tgt.get("description") or ""

                if tgt_color != color or tgt_desc != description:
                    if dry_run:
                        logging.info(
                            f"  [DRY-RUN] Would update label: {name}"
                        )
                        updated += 1
                        continue

                    # Label name 可能包含空格等特殊字符，需要 URL 编码
                    encoded_name = quote(name, safe="")
                    url = _get_api_url(
                        target_platform,
                        f"/repos/{target_owner}/{repo_name}/labels/"
                        f"{encoded_name}",
                    )
                    # 始终发送 description，即使为空（确保能清除目标端的描述）
                    payload = {
                        "color": color,
                        "description": description,
                    }
                    if target_platform == "github":
                        payload["new_name"] = name
                        resp = api_request(
                            "PATCH", url,
                            headers=github_headers(target_token),
                            json=payload, max_retries=1,
                        )
                    else:
                        resp = api_request(
                            "PATCH", url, headers=gitee_headers(target_token),
                            json=payload, max_retries=1,
                        )

                    if resp.status_code in (200, 201):
                        updated += 1

        if created or updated:
            logging.info(
                f"  Labels synced: {created} created, {updated} updated"
            )

    except Exception as e:
        logging.warning(f"  Labels sync failed: {e}")


# ===========================================================================
# Milestones 同步
# ===========================================================================


def sync_milestones(source_platform, target_platform, source_owner,
                    target_owner, source_token, target_token, repo_name,
                    dry_run=False):
    """同步 Milestones（按 title 匹配）。

    策略（对应 docs/调研/仓库附属信息同步调研.md — "Milestones Sync"）:
    - 源有 → 目标无: 创建
    - 两端都有: 对比 state/description/due_on，有差异则更新
    - 源无 → 目标有: 保留

    同步字段: title, state, description, due_on

    REST API:
    - GitHub: GET/POST /repos/{owner}/{repo}/milestones
              PATCH /repos/{owner}/{repo}/milestones/{number}
    - Gitee:  GET/POST /api/v5/repos/{owner}/{repo}/milestones
              PATCH /api/v5/repos/{owner}/{repo}/milestones/{number}

    Args:
        dry_run: 如果为 True，仅列出需要同步的 milestones 而不实际操作。
    """
    try:
        # --- 获取源/目标 milestones (包括已关闭的) ---
        src_milestones = paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/milestones",
            extra_params={"state": "all"},
        )
        tgt_milestones = paginated_get(
            target_platform, target_token,
            f"/repos/{target_owner}/{repo_name}/milestones",
            extra_params={"state": "all"},
        )

        tgt_by_title = {
            m["title"]: m for m in tgt_milestones if m.get("title")
        }

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
                # --- 创建新 milestone ---
                if dry_run:
                    logging.info(
                        f"  [DRY-RUN] Would create milestone: {title}"
                    )
                    created += 1
                    continue

                url = _get_api_url(
                    target_platform,
                    f"/repos/{target_owner}/{repo_name}/milestones",
                )
                if target_platform == "github":
                    resp = api_request(
                        "POST", url,
                        headers=github_headers(target_token),
                        json=payload, max_retries=1,
                    )
                else:
                    resp = api_request(
                        "POST", url, headers=gitee_headers(target_token),
                        json=payload, max_retries=1,
                    )

                if resp.status_code in (200, 201):
                    created += 1
                else:
                    logging.warning(
                        f"  Failed to create milestone {title}: "
                        f"{resp.status_code}"
                    )
            else:
                # --- 检查是否需要更新 ---
                tgt_ms = tgt_by_title[title]
                needs_update = (
                    tgt_ms.get("state") != payload["state"]
                    or (tgt_ms.get("description") or "")
                    != payload["description"]
                    or tgt_ms.get("due_on") != payload.get("due_on")
                )
                if needs_update:
                    if dry_run:
                        logging.info(
                            f"  [DRY-RUN] Would update milestone: {title}"
                        )
                        updated += 1
                        continue

                    number = tgt_ms.get("number")
                    url = _get_api_url(
                        target_platform,
                        f"/repos/{target_owner}/{repo_name}/"
                        f"milestones/{number}",
                    )
                    if target_platform == "github":
                        resp = api_request(
                            "PATCH", url,
                            headers=github_headers(target_token),
                            json=payload, max_retries=1,
                        )
                    else:
                        resp = api_request(
                            "PATCH", url, headers=gitee_headers(target_token),
                            json=payload, max_retries=1,
                        )

                    if resp.status_code in (200, 201):
                        updated += 1

        if created or updated:
            logging.info(
                f"  Milestones synced: {created} created, {updated} updated"
            )

    except Exception as e:
        logging.warning(f"  Milestones sync failed: {e}")


# ===========================================================================
# Issues 同步
# ===========================================================================


def sync_issues(source_platform, target_platform, source_owner, target_owner,
                source_token, target_token, repo_name, dry_run=False):
    """同步 Issues（仅同步 open 状态的 issues）。

    复杂性说明（对应 docs/调研/仓库附属信息同步调研.md — "Issues Sync"）:
    - Issue 编号自动分配，无法控制
    - 作者信息丢失（使用 Token 所有者身份创建）
    - 跨引用 (#123) 会失效（编号不同）
    - Assignees 无法映射（不同平台的用户系统不同）

    去重策略:
    - 在 issue body 中嵌入 HTML 注释标记: <!-- synced-from: {source_url} -->
    - 重复运行时通过标记检测已同步的 issue，避免重复创建

    REST API:
    - GitHub: GET/POST /repos/{owner}/{repo}/issues
              GET/POST /repos/{owner}/{repo}/issues/{number}/comments
    - Gitee:  GET/POST /api/v5/repos/{owner}/{repo}/issues
              GET/POST /api/v5/repos/{owner}/{repo}/issues/{number}/comments

    Args:
        dry_run: 如果为 True，仅列出需要同步的 issues 而不实际操作。
    """
    try:
        # --- 获取源端 open issues ---
        src_issues = paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/issues",
            extra_params={"state": "open"},
        )
        # --- 获取目标端所有 issues（用于去重检查）---
        tgt_issues = paginated_get(
            target_platform, target_token,
            f"/repos/{target_owner}/{repo_name}/issues",
            extra_params={"state": "all"},
        )

        # 过滤掉 Pull Requests（GitHub 的 issues API 会返回 PR）
        src_issues = [i for i in src_issues if not i.get("pull_request")]
        tgt_issues = [i for i in tgt_issues if not i.get("pull_request")]

        # 构建已同步 issue 标记集合
        synced_markers = set()
        for issue in tgt_issues:
            body = issue.get("body") or ""
            if "<!-- synced-from:" in body:
                marker = (
                    body.split("<!-- synced-from:")[1].split("-->")[0].strip()
                )
                synced_markers.add(marker)

        created = 0
        for src_issue in src_issues:
            title = src_issue.get("title")
            if not title:
                continue

            # 构建源 issue URL 作为去重标记
            issue_number = src_issue.get("number")
            if source_platform == "github":
                src_url = (
                    f"https://github.com/{source_owner}/{repo_name}"
                    f"/issues/{issue_number}"
                )
            else:
                src_url = (
                    f"https://gitee.com/{source_owner}/{repo_name}"
                    f"/issues/{issue_number}"
                )

            # 跳过已同步的 issue
            if src_url in synced_markers:
                continue

            if dry_run:
                logging.info(
                    f"  [DRY-RUN] Would create issue: '{title}'"
                )
                created += 1
                continue

            # --- 创建 issue ---
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
                    "POST", url,
                    headers=github_headers(target_token),
                    json=payload, max_retries=1,
                )
            else:
                resp = api_request(
                    "POST", url, headers=gitee_headers(target_token),
                    json=payload, max_retries=1,
                )

            if resp.status_code in (200, 201):
                created += 1
                new_issue = resp.json()
                # 同步 issue comments
                _sync_issue_comments(
                    source_platform, target_platform,
                    source_owner, target_owner,
                    source_token, target_token,
                    repo_name, issue_number, new_issue.get("number"),
                    dry_run,
                )
            else:
                logging.warning(
                    f"  Failed to create issue '{title}': {resp.status_code}"
                )

        if created:
            logging.info(f"  Issues synced: {created} created")

    except Exception as e:
        logging.warning(f"  Issues sync failed: {e}")


def _sync_issue_comments(source_platform, target_platform, source_owner,
                         target_owner, source_token, target_token,
                         repo_name, src_issue_number, tgt_issue_number,
                         dry_run=False):
    """同步单个 issue 的评论。"""
    if not tgt_issue_number:
        return
    try:
        comments = paginated_get(
            source_platform, source_token,
            f"/repos/{source_owner}/{repo_name}/issues/"
            f"{src_issue_number}/comments",
        )

        if dry_run:
            if comments:
                logging.info(
                    f"  [DRY-RUN] Would sync {len(comments)} comments"
                )
            return

        for comment in comments:
            body = comment.get("body")
            if not body:
                continue
            url = _get_api_url(
                target_platform,
                f"/repos/{target_owner}/{repo_name}/issues/"
                f"{tgt_issue_number}/comments",
            )
            payload = {"body": body}
            if target_platform == "github":
                api_request(
                    "POST", url,
                    headers=github_headers(target_token),
                    json=payload, max_retries=1,
                )
            else:
                api_request(
                    "POST", url, headers=gitee_headers(target_token),
                    json=payload, max_retries=1,
                )
    except Exception as e:
        logging.warning(f"  Issue comments sync failed: {e}")


# ===========================================================================
# 附属信息同步分发
# ===========================================================================


def sync_extras(source_platform, target_platform, source_owner, target_owner,
                source_token, target_token, repo_name, sync_extra,
                dry_run=False):
    """根据 sync_extra 参数调用对应的附属信息同步函数。

    对应需求: docs/计划/Python-脚本设计.md — sync_extra 参数
    支持的值: releases, wiki, labels, milestones, issues（逗号分隔）

    Args:
        sync_extra: 需要同步的附属信息集合（如 {"releases", "wiki", "labels"}）。
        dry_run: 如果为 True，所有子功能均以 dry-run 模式运行。
    """
    common_args = (
        source_platform, target_platform,
        source_owner, target_owner,
        source_token, target_token,
        repo_name,
    )

    if "releases" in sync_extra:
        logging.info(f"  Syncing releases ...")
        sync_releases(*common_args, dry_run=dry_run)

    if "wiki" in sync_extra:
        logging.info(f"  Syncing wiki ...")
        sync_wiki(*common_args, dry_run=dry_run)

    if "labels" in sync_extra:
        logging.info(f"  Syncing labels ...")
        sync_labels(*common_args, dry_run=dry_run)

    if "milestones" in sync_extra:
        logging.info(f"  Syncing milestones ...")
        sync_milestones(*common_args, dry_run=dry_run)

    if "issues" in sync_extra:
        logging.info(f"  Syncing issues ...")
        sync_issues(*common_args, dry_run=dry_run)


# ===========================================================================
# 内部辅助函数
# ===========================================================================


def _get_api_url(platform, path):
    """构建平台 API 的完整 URL。"""
    if platform == "github":
        return f"{GITHUB_API}{path}"
    return f"{GITEE_API}{path}"
