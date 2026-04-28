"""
lib/local_target.py — 本地目录作为同步 target 的支持模块

提供将 GitHub / Gitee 仓库同步到本地目录（以裸仓库形式存放）所需的辅助函数。
本地仓库以 `<local_path>/<repo_name>.git` 的目录形式存在，使用 git init --bare
初始化，可作为 git push 的目标。

特性:
- 跨平台路径支持: 使用 pathlib.Path，自动适配 Windows (C:\\repos) 与
  Linux/macOS (/var/repos) 的路径分隔符。
- 安全: 路径合法性由 validate_repo_name 保证仓库名不会引入路径遍历。
- 兼容现有 sync 流程: get_local_repos 返回结构与 GitHub/Gitee API 列表一致
  ([{"name": ..., "private": False}, ...])，create_local_repo 返回 bool。

对应需求:
- 计划文档: 添加 `local` target 支持计划 — 第 3 节 "本地 target 仓库管理"
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path


# 本地裸仓库目录后缀，与 git clone --mirror 的输出保持一致
_BARE_SUFFIX = ".git"


def normalize_local_path(local_path):
    """规范化本地路径，兼容 Windows / Linux 路径格式。

    使用 pathlib.Path 处理：
    - 自动展开 ``~`` 为当前用户主目录
    - 不做绝对化处理，保留调用方意图（相对路径合法）
    - 不做存在性校验（由 ensure_local_path_writable 负责）

    Args:
        local_path: 用户传入的路径字符串。

    Returns:
        Path 对象。
    """
    if not local_path:
        raise ValueError("local_path must be a non-empty string")
    return Path(os.path.expanduser(str(local_path)))


def ensure_local_path_writable(local_path):
    """确保本地路径存在且可写。

    若目录不存在则尝试创建；若已存在但不是目录或不可写则抛出异常。

    Args:
        local_path: 字符串或 Path。

    Returns:
        规范化后的 Path 对象。

    Raises:
        ValueError: 路径已存在但不是目录。
        PermissionError: 路径不可写。
        OSError: 创建目录失败。
    """
    p = normalize_local_path(local_path)
    if p.exists():
        if not p.is_dir():
            raise ValueError(
                f"local-path '{p}' exists but is not a directory"
            )
    else:
        logging.info(f"Creating local target directory: {p}")
        p.mkdir(parents=True, exist_ok=True)

    # 对于目录, 同时检查写入(W_OK)与遍历(X_OK)权限。
    # 仅 W_OK 通过但缺少 X_OK 时, 后续 git init/push 仍会失败。
    if not os.access(str(p), os.W_OK | os.X_OK):
        raise PermissionError(
            f"local-path '{p}' is not writable/traversable "
            "(requires write + execute permission for directories)"
        )
    return p


def build_local_clone_url(local_path, repo_name):
    """构建指向本地裸仓库的 git URL（即文件系统路径）。

    git 接受文件系统路径作为 remote URL，因此直接返回 `<local_path>/<repo>.git`
    即可。使用 pathlib.Path 拼接以兼容 Windows / Linux 分隔符。

    Args:
        local_path: 本地根目录（字符串或 Path）。
        repo_name: 仓库名（应已通过 validate_repo_name 校验）。

    Returns:
        本地裸仓库目录路径的字符串形式（与平台原生分隔符一致）。
    """
    p = normalize_local_path(local_path) / f"{repo_name}{_BARE_SUFFIX}"
    return str(p)


def get_local_repos(local_path):
    """扫描本地目录，返回所有裸仓库（`<name>.git` 子目录）的列表。

    返回结构与 GitHub/Gitee API 一致，便于 sync_one_direction 复用：
        [{"name": "repo_a", "private": False}, ...]

    若目录不存在或无任何裸仓库，返回空列表（不抛错）。
    本地仓库无私有/公开概念，统一标记为 private=False。

    Args:
        local_path: 本地根目录（字符串或 Path）。

    Returns:
        list[dict]：每个元素至少包含 ``name`` 与 ``private`` 字段。
    """
    p = normalize_local_path(local_path)
    if not p.exists() or not p.is_dir():
        return []

    repos = []
    try:
        for entry in p.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if not name.endswith(_BARE_SUFFIX):
                continue
            # 去除 .git 后缀作为仓库名
            repo_name = name[: -len(_BARE_SUFFIX)]
            if not repo_name:
                continue
            repos.append({"name": repo_name, "private": False})
    except OSError as e:
        logging.warning(f"Failed to list local target directory {p}: {e}")
        return []

    return repos


def create_local_repo(local_path, repo_name, log_repo_name=None, **_ignored):
    """在本地路径下创建一个裸仓库 (git init --bare)。

    与 create_github_repo / create_gitee_repo 接口对齐：返回 bool 表示成功
    与否；额外参数（private/description 等）对本地 target 无意义，会被忽略。

    Args:
        local_path: 本地根目录。
        repo_name: 仓库名（应已通过 validate_repo_name 校验）。
        log_repo_name: 日志中显示的仓库名（可选，用于私有仓库脱敏）。

    Returns:
        True 表示创建成功（或目录已存在且是裸仓库），False 表示失败。
    """
    if log_repo_name is None:
        log_repo_name = repo_name

    target_dir = Path(build_local_clone_url(local_path, repo_name))

    # 如已存在并已经是 git 目录则视为成功（幂等）
    if target_dir.exists():
        if (target_dir / "HEAD").exists() or (target_dir / "config").exists():
            logging.debug(
                f"  Local repo {log_repo_name} already exists at {target_dir}"
            )
            return True
        logging.error(
            f"  Local repo path {target_dir} exists but is not a git "
            f"repository"
        )
        return False

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "init", "--bare", str(target_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logging.error(
                f"  git init --bare failed for {log_repo_name}: "
                f"{result.stderr.strip()}"
            )
            # 清理半成品
            shutil.rmtree(target_dir, ignore_errors=True)
            return False
        logging.info(f"  Initialized local bare repo at {target_dir}")
        return True
    except OSError as e:
        logging.error(
            f"  Failed to create local repo {log_repo_name} at {target_dir}: {e}"
        )
        # 异常路径同样清理半成品目录，保持幂等
        shutil.rmtree(target_dir, ignore_errors=True)
        return False
