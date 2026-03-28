"""
lib/utils.py — 通用工具模块

提供全局共享的辅助功能：
- 日志配置 (setup_logging)
- Token 脱敏 (mask_token)
- HTTP 请求封装与重试 (api_request)
- Git 环境检测 (check_git_installed)
- GitHub Action 输出写入 (write_action_outputs)

对应需求文档:
- docs/计划/错误处理设计.md — 重试策略、Rate Limit 处理、Token 脱敏
- docs/计划/Python-脚本设计.md — api_request, mask_token, setup_logging
"""

import logging
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote

import requests

# ===========================================================================
# API 常量
# ===========================================================================

GITHUB_API = "https://api.github.com"
GITEE_API = "https://gitee.com/api/v5"

# ===========================================================================
# 日志配置
# ===========================================================================


def setup_logging():
    """配置日志格式与级别。

    使用 [LEVEL] message 格式输出到 stdout，方便在 GitHub Action 日志中查看。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
        stream=sys.stdout,
    )


# ===========================================================================
# Token 与 URL 工具
# ===========================================================================


def mask_token(text):
    """在日志文本中隐藏 Token 信息，防止凭据泄漏。

    将 https://<token>@... 形式的 URL 中的 token 部分替换为 ***。
    对应需求: docs/计划/错误处理设计.md — "Token 信息脱敏"
    """
    return re.sub(r'https://[^@]+@', 'https://***@', str(text))


def build_clone_url(platform, owner, token, repo_name):
    """构建带 Token 认证的 Git clone URL。

    Token 会经过 URL 编码，确保包含特殊字符（如 +, /, @）时不会破坏 URL。

    Args:
        platform: 平台标识 ("github" 或 "gitee")。
        owner: 仓库所有者。
        token: 个人访问令牌。
        repo_name: 仓库名。

    Returns:
        形如 https://<encoded_token>@github.com/<owner>/<repo>.git 的 URL。

    对应需求: docs/调研/Git-Mirror-同步机制.md — "HTTPS + Token in URL"
    """
    encoded_token = quote(token, safe="")
    if platform == "github":
        return f"https://{encoded_token}@github.com/{owner}/{repo_name}.git"
    else:
        return f"https://{encoded_token}@gitee.com/{owner}/{repo_name}.git"


# ===========================================================================
# HTTP 请求封装
# ===========================================================================


def api_request(method, url, max_retries=3, backoff_base=2, **kwargs):
    """通用 HTTP 请求函数，内置重试逻辑与 Rate Limit 处理。

    重试策略（对应 docs/计划/错误处理设计.md）：
    - 网络异常 (ConnectionError, Timeout): 最多重试 max_retries 次，间隔指数递增
    - 服务端错误 (HTTP 502/503/504): 视为瞬态错误，同样重试
    - Rate Limit (HTTP 403/429 且 X-RateLimit-Remaining=0): 等待至 reset 时间后重试
    - 其他 HTTP 错误: 直接返回 Response 让调用方处理

    Args:
        method: HTTP 方法 ("GET", "POST", "PATCH" 等)。
        url: 请求 URL。
        max_retries: 最大重试次数（默认 3）。
        backoff_base: 指数退避基数（默认 2，即 2s, 4s, 8s）。
        **kwargs: 传递给 requests.request 的额外参数。

    Returns:
        requests.Response 对象。

    Raises:
        最后一次重试仍失败时抛出原始异常。
    """
    kwargs.setdefault("timeout", 30)
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)

            # --- Rate Limit 检测 ---
            # 对应: docs/计划/错误处理设计.md — "Rate Limit 处理"
            # GitHub: X-RateLimit-Remaining, X-RateLimit-Reset
            # Gitee: 类似机制，但文档不完善，仍检测相同 Header
            remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
            if 0 < remaining < 100:
                # 接近限额时主动降速
                time.sleep(1)
            if resp.status_code in (403, 429) and remaining == 0:
                reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
                wait = max(0, reset_time - time.time())
                if wait > 900:
                    raise Exception(
                        "API rate limit exceeded, reset time too long (>15min)"
                    )
                logging.warning(
                    f"API rate limit reached, waiting {wait:.0f}s ..."
                )
                time.sleep(wait + 1)
                continue

            # --- 服务端瞬态错误重试 ---
            # 对应: PR review — "Transient HTTP errors (502/503/504)"
            if resp.status_code in (502, 503, 504) and attempt < max_retries:
                wait = backoff_base ** attempt
                logging.warning(
                    f"Server error {resp.status_code} for {url}, "
                    f"retrying in {wait}s (attempt {attempt+1})"
                )
                time.sleep(wait)
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


# ===========================================================================
# 分页请求辅助
# ===========================================================================


def github_headers(token):
    """构建 GitHub API 标准请求头。

    对应: docs/调研/GitHub-API.md — "认证方式"
    - Authorization: token <TOKEN>
    - Accept: application/vnd.github.v3+json
    """
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def paginated_get(platform, token, path, extra_params=None):
    """通用分页 GET 请求，兼容 GitHub 和 Gitee 平台。

    遍历所有分页直到返回空列表。每页最多 100 条记录。
    对应: docs/调研/GitHub-API.md — "分页处理（per_page=100, page 递增）"
    对应: docs/调研/Gitee-API.md — "分页处理（per_page=100, page 递增）"

    Args:
        platform: "github" 或 "gitee"。
        token: 个人访问令牌。
        path: API 路径（如 "/repos/{owner}/{repo}/labels"）。
        extra_params: 额外查询参数（如 {"state": "all"}）。

    Returns:
        所有分页结果合并后的列表。
    """
    items = []
    page = 1
    while True:
        p = {"per_page": 100, "page": page}
        if extra_params:
            p.update(extra_params)

        if platform == "github":
            url = f"{GITHUB_API}{path}"
            kwargs = {"headers": github_headers(token), "params": p}
        else:
            url = f"{GITEE_API}{path}"
            p["access_token"] = token
            kwargs = {"params": p}

        resp = api_request("GET", url, max_retries=2, **kwargs)

        # 非 200 响应时记录警告并停止分页（而非静默忽略）
        if resp.status_code != 200:
            body_preview = (resp.text or "")[:200].replace("\n", " ")
            logging.warning(
                "Paginated GET failed for %s %s: status=%s, body=%r",
                platform, url, resp.status_code, body_preview,
            )
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


# ===========================================================================
# 环境检测
# ===========================================================================


def check_git_installed():
    """检查 git 是否已安装并可用。

    在同步开始前调用，确保运行环境满足前提条件。
    对应需求: docs/计划/错误处理设计.md — "环境异常 → 立即退出"
    """
    try:
        result = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=True
        )
        logging.info(f"Git version: {result.stdout.strip()}")
    except FileNotFoundError:
        raise Exception("Git is not installed or not in PATH")


# ===========================================================================
# GitHub Action 输出
# ===========================================================================


def write_action_outputs(synced, failed, skipped):
    """将同步结果写入 GitHub Action outputs。

    检测 $GITHUB_OUTPUT 环境变量是否存在，如存在则追加写入。
    对应需求: docs/计划/开发步骤.md — Step 13 "适配 sync.py 输出"
    """
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"synced-count={synced}\n")
            f.write(f"failed-count={failed}\n")
            f.write(f"skipped-count={skipped}\n")
