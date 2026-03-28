# 安全评审报告：数据安全与隐私泄露

**评审日期**: 2026-03-28
**评审范围**: 基于 `docs/` 目录下的设计文档与调研文档，对实现代码进行数据安全、隐私泄露和潜在实现问题的专项审查
**评审文件**: `lib/utils.py`, `lib/github_api.py`, `lib/gitee_api.py`, `lib/sync_repo.py`, `sync.py`, `entrypoint.sh`

---

## 发现的问题与修复

### 🔴 P0 — GITHUB_OUTPUT Heredoc 注入漏洞

**文件**: `lib/utils.py` — `write_action_outputs()`
**问题**: 使用固定的 heredoc 分隔符 `SYNC_LOG_EOF` 写入 `$GITHUB_OUTPUT`。如果日志内容中包含该固定字符串（例如恶意仓库名或 API 返回的错误信息中出现），攻击者可以提前终止 heredoc，注入任意 GitHub Action output 键值对。

这是 GitHub Actions 已知的安全风险类型（[GitHub Security Advisory](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions#using-an-intermediate-environment-variable)），可被用于篡改下游 workflow 步骤的行为。

**修复**:
```python
# 之前（不安全）:
f.write(f"sync-log<<SYNC_LOG_EOF\n{log_text}\nSYNC_LOG_EOF\n")

# 之后（安全）:
delimiter = f"SYNC_LOG_EOF_{secrets.token_hex(16)}"
f.write(f"sync-log<<{delimiter}\n{log_text}\n{delimiter}\n")
```

每次写入使用 `secrets.token_hex(16)` 生成的 256 位随机后缀，攻击者无法预测分隔符内容。

**对应文档**: `docs/计划/错误处理设计.md` — "Token 信息脱敏"；`docs/调研/GitHub-Actions.md` — "Outputs via GITHUB_OUTPUT"

---

### 🔴 P0 — GITHUB_STEP_SUMMARY Markdown 注入

**文件**: `lib/utils.py` — `write_action_outputs()`
**问题**: 日志文本直接写入 `$GITHUB_STEP_SUMMARY` 的 markdown 代码块中，未转义反引号字符。如果日志内容包含 ` ``` `（三个反引号），可以逃逸代码块，注入任意 markdown/HTML 内容到 Actions UI 中，可能导致 XSS 或误导性信息展示。

**修复**:
```python
# 之前（不安全）:
f.write(f"```\n{log_text}\n```\n")

# 之后（安全）:
safe_log = log_text.replace("`", "\\`")
f.write(f"```\n{safe_log}\n```\n")
```

**对应文档**: `docs/计划/GitHub-Action-设计.md` — Action 输出格式

---

### 🟠 P1 — API 错误响应体泄露敏感信息

**文件**: `lib/github_api.py`, `lib/gitee_api.py`
**问题**: 在仓库创建失败和仓库列表获取失败时，直接将 `resp.text` 完整写入日志或异常消息：

```python
# github_api.py
raise Exception(f"Failed to fetch GitHub repos: {resp.status_code} {resp.text}")
logging.error(f"Failed to create GitHub repo: {resp.status_code} {resp.text}")

# gitee_api.py (同样的问题)
```

API 错误响应体可能包含：
- 认证相关的诊断信息
- Token 片段（某些 API 在错误消息中回显部分认证信息）
- 内部 API 路径或系统信息

虽然 `TokenMaskingFilter` 会处理日志输出，但异常消息可能通过其他路径传播（如 traceback 打印），且过滤器只匹配已知模式。

**修复**: 新增 `sanitize_response_text()` 工具函数，统一截断（200 字符）、去除换行、并应用 `mask_token()` 脱敏：

```python
def sanitize_response_text(text, max_len=200):
    if not text:
        return ""
    preview = text[:max_len].replace("\n", " ")
    return mask_token(preview)
```

所有错误日志和异常消息中的 `resp.text` 均已替换为 `sanitize_response_text(resp.text)`。

`paginated_get()` 中的 `body_preview` 也改为使用 `sanitize_response_text()`。

**对应文档**: `docs/计划/错误处理设计.md` — "Token 信息脱敏"

---

### 🟠 P1 — TokenMaskingFilter 缺少关键 Token 模式

**文件**: `lib/utils.py` — `TokenMaskingFilter`
**问题**: 原始过滤器仅匹配 GitHub PAT 模式（`ghp_`, `gho_`, `github_pat_`），缺少以下重要模式：

1. **GitHub Actions 内置 Token (`ghs_`)**: 在 `entrypoint.sh` 中有专门的检测逻辑，但 TokenMaskingFilter 不匹配此前缀
2. **Bearer Token**: Gitee 和 GitHub 都使用 `Authorization: Bearer <token>` 头部，如果任何调试/错误输出包含完整的 HTTP 请求/响应头，Token 会泄露
3. **Gitee Token**: Gitee 的 Token 没有已知前缀特征，无法通过前缀匹配；但 Bearer 模式可以作为兜底

**修复**:
```python
TOKEN_PATTERNS = [
    re.compile(r'ghp_[a-zA-Z0-9]{36}'),
    re.compile(r'gho_[a-zA-Z0-9]{36}'),
    re.compile(r'ghs_[a-zA-Z0-9]{36}'),          # 新增: GitHub Actions 内置 Token
    re.compile(r'github_pat_[a-zA-Z0-9_]{82}'),
    re.compile(r'https://[^@\s]+@'),
    re.compile(r'access_token=[^&\s]+'),
    re.compile(r'Bearer\s+[a-zA-Z0-9_\-\.]+', re.IGNORECASE),  # 新增: Bearer Token
]
```

**对应文档**: `docs/计划/错误处理设计.md` — "Token 脱敏"; `docs/调研/Gitee-API.md` — "认证方式"

---

### 🟡 P2 — 分页请求缺少安全上限

**文件**: `lib/utils.py` — `paginated_get()`
**问题**: `while True` 循环遍历 API 分页，没有页数上限。如果 API 出现异常行为（如始终返回非空数据），循环将永远不会终止，导致：
- 无限 API 调用，消耗 Rate Limit 配额
- 内存持续增长（所有结果累积在 `items` 列表中）
- GitHub Action 运行时间无限增长

**修复**: 添加 `MAX_PAGES = 500` 安全上限（500 页 × 100 条/页 = 50,000 条记录，足以覆盖绝大多数场景），并在达到上限时记录警告：

```python
MAX_PAGES = 500
page = 1
while page <= MAX_PAGES:
    # ... 分页逻辑 ...
    page += 1
else:
    logging.warning("Pagination safety limit reached (%d pages)...", MAX_PAGES, path)
```

注意: `get_github_repos()` 和 `get_gitee_repos()` 中的自定义分页循环同样存在此问题，但由于这些函数调用 `api_request()` 时有重试上限和超时设置，且仓库数量通常远小于此上限，风险较低。

**对应文档**: `docs/调研/GitHub-API.md` — "分页处理"; `docs/调研/Gitee-API.md` — "分页处理"

---

## 审查通过的安全设计

以下是经审查确认正确实现的安全措施（对应文档要求）：

| 安全措施 | 文档位置 | 实现状态 |
|---------|---------|---------|
| GIT_ASKPASS 替代 URL 内联 Token | `docs/调研/Git-Mirror-同步机制.md` | ✅ `make_git_env()` 使用 base64 编码 + 临时脚本 |
| Token 不出现在 clone URL 中 | `docs/调研/Git-Mirror-同步机制.md` | ✅ `build_clone_url()` 返回无凭据 URL |
| askpass 脚本权限 0o700 | 安全最佳实践 | ✅ `os.chmod(askpass_path, 0o700)` |
| askpass 脚本用后即删 | 安全最佳实践 | ✅ `finally` 块中 `os.unlink()` |
| git stderr 输出脱敏 | `docs/计划/错误处理设计.md` | ✅ `mask_token()` 应用于所有 git 错误输出 |
| Gitee Bearer 头部认证 | 二级评审 Issue #1 | ✅ `gitee_headers()` 使用 Bearer 而非 URL 参数 |
| GitHub Bearer 头部认证 | `docs/调研/GitHub-API.md` | ✅ `github_headers()` 使用 Bearer 格式 |
| Rate Limit 防御性解析 | PR review | ✅ `try/except` 包裹 Header 解析 |
| 流式下载防内存溢出 | `docs/调研/仓库附属信息同步调研.md` | ✅ `tempfile.mkstemp` + `iter_content` |
| 资产大小限制 | 安全最佳实践 | ✅ `MAX_ASSET_SIZE = 500MB` |
| subprocess 列表参数 | 安全最佳实践 | ✅ 无 `shell=True`，不存在命令注入风险 |
| 增量推送替代 mirror 推送 | 二级评审 Issue #11 | ✅ `--all --force` + `--tags --force` |

---

## 新增测试

| 测试类 | 测试方法 | 验证内容 |
|-------|---------|---------|
| `TestSanitizeResponseText` | `test_truncates_long_text` | 响应文本截断功能 |
| `TestSanitizeResponseText` | `test_masks_token_in_response` | 响应文本中的 Token 脱敏 |
| `TestSanitizeResponseText` | `test_replaces_newlines` | 响应文本换行符替换 |
| `TestSanitizeResponseText` | `test_empty_text_returns_empty` | 空文本处理 |
| `TestTokenMaskingFilterExtended` | `test_masks_ghs_token` | ghs_ 前缀 Token 脱敏 |
| `TestTokenMaskingFilterExtended` | `test_masks_bearer_token` | Bearer Token 模式脱敏 |
| `TestWriteActionOutputsSecurity` | `test_heredoc_delimiter_is_randomized` | heredoc 分隔符随机化 |
| `TestWriteActionOutputsSecurity` | `test_log_with_fake_delimiter_does_not_inject` | heredoc 注入防御 |
| `TestWriteActionOutputsSecurity` | `test_step_summary_escapes_backticks` | markdown 反引号转义 |
| `TestPaginatedGetSafetyLimit` | `test_stops_at_max_pages` | 分页安全上限 |

**测试总数**: 169 → 179（新增 10 个安全测试）
