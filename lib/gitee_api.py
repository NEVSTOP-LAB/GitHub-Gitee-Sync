"""
lib/gitee_api.py — Gitee REST API 封装模块

封装与 Gitee 平台交互的所有 API 调用：
- Token 验证
- 仓库列表获取
- 仓库创建
- 仓库详情获取与元信息更新

对应需求文档:
- docs/调研/Gitee-API.md — API 端点、认证方式、分页机制
- docs/计划/Python-脚本设计.md — get_gitee_repos, create_repo, validate_tokens
- docs/计划/错误处理设计.md — Token 认证失败处理

REST API 端点汇总:
┌──────────────────────────────────────────────────────────────┐
│ 方法   │ 端点                                 │ 用途        │
├──────────────────────────────────────────────────────────────┤
│ GET    │ /api/v5/user                         │ Token 验证  │
│ GET    │ /api/v5/user/repos                   │ 列出个人仓库 │
│ GET    │ /api/v5/orgs/{org}/repos             │ 列出组织仓库 │
│ GET    │ /api/v5/repos/{owner}/{repo}         │ 仓库详情    │
│ POST   │ /api/v5/user/repos                   │ 创建个人仓库 │
│ POST   │ /api/v5/orgs/{org}/repos             │ 创建组织仓库 │
│ PATCH  │ /api/v5/repos/{owner}/{repo}         │ 更新仓库信息 │
└──────────────────────────────────────────────────────────────┘

认证方式:
  Query: ?access_token=TOKEN
  Header: Authorization: Bearer TOKEN (推荐)
  所需权限: projects (仓库管理), user_info (基本信息)

分页:
  默认 20 条/页, 最大 100 条/页
  参数: per_page, page
  Header: total_count, total_page
  遍历至返回空数组为止

Rate Limit:
  Gitee 文档对 Rate Limit 描述不完善
  建议请求间隔 0.5-1 秒，检测 429 状态码并重试
"""

import logging

import requests

from .utils import GITEE_API, api_request


# ===========================================================================
# Token 验证
# ===========================================================================


def validate_gitee_token(token):
    """验证 Gitee Token 是否有效。

    调用 GET /api/v5/user 接口检测 Token 认证状态。
    对应需求: docs/计划/错误处理设计.md — "认证错误 → 立即退出，提供清晰指引"

    Args:
        token: Gitee Personal Access Token。

    Returns:
        认证用户的 login 名称。

    Raises:
        Exception: Token 无效(401)、请求失败、网络异常。
    """
    logging.info("Validating Gitee token ...")
    try:
        resp = requests.get(
            f"{GITEE_API}/user",
            params={"access_token": token},
            timeout=30,
        )
        if resp.status_code == 401:
            raise Exception(
                "Gitee Token authentication failed (HTTP 401).\n"
                "  Please check your token: "
                "https://gitee.com/profile/personal_access_tokens\n"
                "  Required permission: projects"
            )
        if resp.status_code != 200:
            raise Exception(
                f"Gitee Token validation failed: HTTP {resp.status_code}"
            )
        gitee_user = resp.json().get("login", "unknown")
        logging.info(f"  Gitee authenticated as: {gitee_user}")
        return gitee_user
    except requests.RequestException as e:
        raise Exception(f"Gitee Token validation network error: {e}")


# ===========================================================================
# 仓库列表
# ===========================================================================


def get_gitee_repos(owner, token, account_type, include_private=True):
    """获取 Gitee 账号下的所有仓库。

    根据 account_type 选择不同的 API 端点：
    - user: GET /api/v5/user/repos (已认证用户的仓库，type=owner 过滤仅自己拥有的)
    - org:  GET /api/v5/orgs/{org}/repos (组织仓库)

    获取后根据 owner 做二次过滤，确保只返回指定 owner 的仓库。
    对应: PR review — "验证 owner 与认证用户一致或过滤结果"

    Args:
        owner: Gitee 用户名或组织名。
        token: Gitee Personal Access Token。
        account_type: 'user' 或 'org'。
        include_private: 是否包含私有仓库（默认 True）。

    Returns:
        仓库字典列表，每项包含: name, private, description。

    对应需求:
    - docs/调研/Gitee-API.md — GET /api/v5/user/repos, GET /api/v5/orgs/{org}/repos
    - docs/计划/Python-脚本设计.md — get_gitee_repos()
    """
    # --- 选择 API 端点 ---
    if account_type == "org":
        # GET /api/v5/orgs/{org}/repos — 组织仓库
        url = f"{GITEE_API}/orgs/{owner}/repos"
    else:
        # GET /api/v5/user/repos — 已认证用户的仓库
        url = f"{GITEE_API}/user/repos"

    page = 1
    all_repos = []

    # --- 分页遍历 ---
    while True:
        params = {
            "access_token": token,
            "per_page": 100,
            "page": page,
        }
        if account_type == "user":
            # type=owner: 仅返回自己拥有的仓库
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

            # --- Owner 二次验证 ---
            # /user/repos 可能返回非目标 owner 的仓库
            namespace = repo.get("namespace", {})
            repo_owner = namespace.get("path", "") if namespace else ""
            if repo_owner and repo_owner.lower() != owner.lower():
                continue

            # --- 私有仓库过滤 ---
            # 对应: PR review — "include_private 应同时应用于 Gitee"
            is_private = repo.get("private", False)
            if not include_private and is_private:
                continue

            all_repos.append({
                "name": name,
                "private": is_private,
                "description": repo.get("description") or "",
            })

        page += 1

    return all_repos


# ===========================================================================
# 仓库创建
# ===========================================================================


def create_gitee_repo(owner, token, repo_name, private, description, account_type):
    """在 Gitee 上创建仓库。

    根据 account_type 选择端点：
    - user: POST /api/v5/user/repos
    - org:  POST /api/v5/orgs/{org}/repos

    Body 参数:
      access_token (string, required)
      name (string, required) — 仓库名
      description (string) — 描述，Gitee 限制 200 字符
      private (boolean) — 是否私有
      auto_init (boolean) — 不自动初始化（避免与 mirror push 冲突）

    对应需求:
    - docs/调研/Gitee-API.md — POST /api/v5/user/repos, POST /api/v5/orgs/{org}/repos
    - docs/计划/流程图.md — Step A "检查/创建目标仓库"

    Args:
        owner: Gitee 用户名或组织名。
        token: Gitee Personal Access Token。
        repo_name: 要创建的仓库名。
        private: 是否私有。
        description: 仓库描述。
        account_type: 'user' 或 'org'。

    Returns:
        True 如果创建成功或仓库已存在，False 如果失败。
    """
    if account_type == "org":
        url = f"{GITEE_API}/orgs/{owner}/repos"
    else:
        url = f"{GITEE_API}/user/repos"

    payload = {
        "access_token": token,
        "name": repo_name,
        # Gitee API 仓库描述限制 200 字符
        "description": description[:200] if description else "",
        "private": private,
        # 不自动初始化 — 避免与 mirror push 产生冲突
        "auto_init": False,
    }

    resp = api_request("POST", url, json=payload, max_retries=1)

    if resp.status_code in (200, 201):
        logging.info(f"  Created Gitee repo: {repo_name}")
        return True
    if resp.status_code == 422:
        # 422 通常表示仓库已存在
        logging.info(f"  Gitee repo {repo_name} already exists, skip creation")
        return True

    logging.error(
        f"  Failed to create Gitee repo {repo_name}: "
        f"{resp.status_code} {resp.text}"
    )
    return False


# ===========================================================================
# 仓库详情与元信息更新
# ===========================================================================


def get_gitee_repo_details(owner, token, repo_name):
    """获取 Gitee 仓库详情。

    API: GET /api/v5/repos/{owner}/{repo}
    对应: docs/调研/Gitee-API.md — "仓库详情"

    Args:
        owner: 仓库所有者。
        token: Gitee Personal Access Token。
        repo_name: 仓库名。

    Returns:
        包含 description, homepage 的字典，失败返回 None。
    """
    url = f"{GITEE_API}/repos/{owner}/{repo_name}"
    resp = api_request(
        "GET", url, params={"access_token": token}, max_retries=2
    )

    if resp.status_code != 200:
        return None

    data = resp.json()
    return {
        "description": data.get("description") or "",
        "homepage": data.get("homepage") or "",
    }


def update_gitee_repo_metadata(owner, token, repo_name, metadata):
    """更新 Gitee 仓库元信息。

    API: PATCH /api/v5/repos/{owner}/{repo}
    Body: access_token, description, homepage, default_branch, private
    对应: docs/调研/Gitee-API.md — "更新仓库信息"
    对应: docs/调研/仓库附属信息同步调研.md — "Repo Metadata Sync"

    Args:
        owner: 仓库所有者。
        token: Gitee Personal Access Token。
        repo_name: 仓库名。
        metadata: 要更新的字段字典。

    Returns:
        True 成功, False 失败。
    """
    url = f"{GITEE_API}/repos/{owner}/{repo_name}"
    payload = {"access_token": token}
    payload.update(metadata)
    resp = api_request("PATCH", url, json=payload, max_retries=1)

    if resp.status_code in (200, 201):
        return True
    logging.warning(f"  Failed to update Gitee metadata: {resp.status_code}")
    return False
