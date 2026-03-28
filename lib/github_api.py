"""
lib/github_api.py — GitHub REST API 封装模块

封装与 GitHub 平台交互的所有 API 调用：
- Token 验证
- 仓库列表获取
- 仓库创建
- 仓库详情获取与元信息更新

对应需求文档:
- docs/调研/GitHub-API.md — API 端点、认证方式、分页机制
- docs/计划/Python-脚本设计.md — get_github_repos, create_repo, validate_tokens
- docs/计划/错误处理设计.md — Token 认证失败处理

REST API 端点汇总:
┌──────────────────────────────────────────────────────────────┐
│ 方法   │ 端点                            │ 用途            │
├──────────────────────────────────────────────────────────────┤
│ GET    │ /user                           │ Token 验证      │
│ GET    │ /user/repos                     │ 列出个人仓库     │
│ GET    │ /orgs/{org}/repos               │ 列出组织仓库     │
│ GET    │ /repos/{owner}/{repo}           │ 仓库详情        │
│ POST   │ /user/repos                     │ 创建个人仓库     │
│ POST   │ /orgs/{org}/repos               │ 创建组织仓库     │
│ PATCH  │ /repos/{owner}/{repo}           │ 更新仓库信息     │
└──────────────────────────────────────────────────────────────┘

认证方式:
  Header: Authorization: token <TOKEN>
  Header: Accept: application/vnd.github.v3+json
  所需权限: repo (完整仓库访问), public_repo (仅公开), read:org (组织)

分页:
  默认 30 条/页, 最大 100 条/页
  参数: per_page, page
  遍历至返回空数组为止

Rate Limit:
  已认证: 5000 次/小时
  Header: X-RateLimit-Remaining, X-RateLimit-Reset
"""

import logging

import requests

from .utils import GITHUB_API, api_request, github_headers


# ===========================================================================
# Token 验证
# ===========================================================================


def validate_github_token(token):
    """验证 GitHub Token 是否有效。

    调用 GET /user 接口检测 Token 认证状态。
    复用 api_request 统一请求封装，获得重试、超时、日志脱敏能力。
    对应需求: docs/计划/错误处理设计.md — "认证错误 → 立即退出，提供清晰指引"

    Args:
        token: GitHub Personal Access Token。

    Returns:
        认证用户的 login 名称。

    Raises:
        Exception: Token 无效(401)、请求失败、网络异常。
    """
    logging.info("Validating GitHub token ...")
    try:
        resp = api_request(
            "GET", f"{GITHUB_API}/user",
            headers=github_headers(token),
            max_retries=2,
        )
        if resp.status_code == 401:
            raise Exception(
                "GitHub Token authentication failed (HTTP 401).\n"
                "  Please check your token: https://github.com/settings/tokens\n"
                "  Required scope: repo (full repository access)\n"
                "  For organization repos, also requires: read:org"
            )
        if resp.status_code != 200:
            raise Exception(
                f"GitHub Token validation failed: HTTP {resp.status_code}"
            )
        github_user = resp.json().get("login", "unknown")
        logging.info(f"  GitHub authenticated as: {github_user}")
        return github_user
    except requests.RequestException as e:
        raise Exception(f"GitHub Token validation network error: {e}")


# ===========================================================================
# 仓库列表
# ===========================================================================


def get_github_repos(owner, token, account_type, include_private):
    """获取 GitHub 账号下的所有仓库。

    根据 account_type 选择不同的 API 端点：
    - user: GET /user/repos (已认证用户的仓库，type=owner 过滤仅自己拥有的)
    - org:  GET /orgs/{org}/repos (组织仓库)

    获取后根据 owner 做二次过滤，确保只返回指定 owner 的仓库。
    这是因为 /user/repos 会返回 Token 所有者的仓库，可能与 --github-owner 不一致。
    对应: PR review — "验证 owner 与认证用户一致或过滤结果"

    Args:
        owner: GitHub 用户名或组织名。
        token: GitHub Personal Access Token。
        account_type: 'user' 或 'org'。
        include_private: 是否包含私有仓库。

    Returns:
        仓库字典列表，每项包含: name, private, description, clone_url。

    对应需求:
    - docs/调研/GitHub-API.md — GET /user/repos, GET /orgs/{org}/repos
    - docs/计划/Python-脚本设计.md — get_github_repos()
    """
    # --- 选择 API 端点 ---
    if account_type == "org":
        # GET /orgs/{org}/repos — 组织仓库，按 org 名过滤
        url = f"{GITHUB_API}/orgs/{owner}/repos"
    else:
        # GET /user/repos — 已认证用户的仓库
        url = f"{GITHUB_API}/user/repos"

    headers = github_headers(token)
    page = 1
    all_repos = []

    # --- 分页遍历 ---
    while True:
        params = {"per_page": 100, "page": page}
        if account_type == "user":
            # type=owner: 仅返回自己拥有的仓库（排除协作仓库）
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

            # --- Owner 二次验证 ---
            # /user/repos 返回 Token 所有者的仓库，需确认 repo.owner.login 匹配
            repo_owner = (repo.get("owner") or {}).get("login", "")
            if repo_owner and repo_owner.lower() != owner.lower():
                continue

            # --- 私有仓库过滤 ---
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


# ===========================================================================
# 仓库创建
# ===========================================================================


def create_github_repo(owner, token, repo_name, private, description, account_type):
    """在 GitHub 上创建仓库（用于反向同步 Gitee→GitHub 或双向同步）。

    根据 account_type 选择端点：
    - user: POST /user/repos
    - org:  POST /orgs/{org}/repos

    Body 参数:
      name (string, required) — 仓库名
      description (string) — 描述，GitHub 限制约 350 字符
      private (boolean) — 是否私有
      auto_init (boolean) — 不自动初始化（避免与 mirror push 冲突）

    对应需求:
    - docs/调研/GitHub-API.md — POST /user/repos, POST /orgs/{org}/repos
    - docs/计划/流程图.md — Step A "检查/创建目标仓库"

    Args:
        owner: GitHub 用户名或组织名。
        token: GitHub Personal Access Token。
        repo_name: 要创建的仓库名。
        private: 是否私有。
        description: 仓库描述。
        account_type: 'user' 或 'org'。

    Returns:
        True 如果创建成功或仓库已存在，False 如果失败。
    """
    if account_type == "org":
        url = f"{GITHUB_API}/orgs/{owner}/repos"
    else:
        url = f"{GITHUB_API}/user/repos"

    headers = github_headers(token)
    payload = {
        "name": repo_name,
        # GitHub 仓库描述限制约 350 字符
        "description": description[:350] if description else "",
        "private": private,
        # 不自动初始化 — 避免与 mirror push 产生冲突
        "auto_init": False,
    }

    resp = api_request("POST", url, headers=headers, json=payload, max_retries=1)

    if resp.status_code in (200, 201):
        logging.info(f"  Created GitHub repo: {repo_name}")
        return True
    if resp.status_code == 422:
        # 422 通常表示仓库已存在
        logging.info(f"  GitHub repo {repo_name} already exists, skip creation")
        return True

    logging.error(
        f"  Failed to create GitHub repo {repo_name}: "
        f"{resp.status_code} {resp.text}"
    )
    return False


# ===========================================================================
# 仓库详情与元信息更新
# ===========================================================================


def get_github_repo_details(owner, token, repo_name):
    """获取 GitHub 仓库详情。

    API: GET /repos/{owner}/{repo}
    对应: docs/调研/GitHub-API.md — "仓库详情"

    Args:
        owner: 仓库所有者。
        token: GitHub Personal Access Token。
        repo_name: 仓库名。

    Returns:
        包含 description, homepage 的字典，失败返回 None。
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}"
    resp = api_request("GET", url, headers=github_headers(token), max_retries=2)

    if resp.status_code != 200:
        return None

    data = resp.json()
    return {
        "description": data.get("description") or "",
        "homepage": data.get("homepage") or "",
    }


def update_github_repo_metadata(owner, token, repo_name, metadata):
    """更新 GitHub 仓库元信息。

    API: PATCH /repos/{owner}/{repo}
    Body: description, homepage, default_branch, private, topics 等
    对应: docs/调研/GitHub-API.md — "更新仓库信息"
    对应: docs/调研/仓库附属信息同步调研.md — "Repo Metadata Sync"

    Args:
        owner: 仓库所有者。
        token: GitHub Personal Access Token。
        repo_name: 仓库名。
        metadata: 要更新的字段字典（如 {"description": "...", "homepage": "..."}）。

    Returns:
        True 成功, False 失败。
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}"
    resp = api_request(
        "PATCH", url, headers=github_headers(token),
        json=metadata, max_retries=1,
    )

    if resp.status_code in (200, 201):
        return True
    logging.warning(f"  Failed to update GitHub metadata: {resp.status_code}")
    return False
