# Python 同步脚本设计

## 1. 脚本概述

创建 `sync.py` 脚本，用于在 GitHub 和 Gitee 之间同步账号（个人或组织）下的全部仓库，支持单向和双向同步。

---

## 2. 输入参数

| 参数 | 环境变量 | CLI 参数 | 必填 | 默认值 | 说明 |
|------|---------|---------|------|--------|------|
| GitHub 账号 | `GITHUB_OWNER` | `--github-owner` | ✅ | - | GitHub 用户名或组织名 |
| GitHub Token | `GITHUB_TOKEN` | `--github-token` | ✅ | - | GitHub Personal Access Token |
| Gitee 账号 | `GITEE_OWNER` | `--gitee-owner` | ✅ | - | Gitee 用户名或组织名 |
| Gitee Token | `GITEE_TOKEN` | `--gitee-token` | ✅ | - | Gitee Personal Access Token |
| 账号类型 | `ACCOUNT_TYPE` | `--account-type` | ❌ | `user` | `user` 或 `org`（个人/组织） |
| 包含私有仓库 | `INCLUDE_PRIVATE` | `--include-private` | ❌ | `true` | 是否同步私有仓库 |
| 排除仓库列表 | `EXCLUDE_REPOS` | `--exclude-repos` | ❌ | 空 | 逗号分隔的仓库名列表 |
| 同步方向 | `SYNC_DIRECTION` | `--direction` | ❌ | `github→gitee` | 同步方向（见下表） |
| 创建不存在的仓库 | `CREATE_MISSING_REPOS` | `--create-missing-repos` | ❌ | `true` | 目标仓库不存在时是否自动创建 |
| 附属信息同步 | `SYNC_EXTRA` | `--sync-extra` | ❌ | 空 | 逗号分隔：`releases,wiki,labels,milestones,issues` |

### 同步方向参数

| 参数值 | 含义 | 说明 |
|-------|------|------|
| `github→gitee` | GitHub → Gitee | 从 GitHub 同步到 Gitee（默认） |
| `gitee→github` | Gitee → GitHub | 从 Gitee 同步到 GitHub |
| `github↔gitee` | GitHub ↔ Gitee | 双向同步（以 GitHub 为主） |

> **说明**：CLI 中可以使用 `github2gitee`、`gitee2github`、`both` 作为简写形式。

### 参数优先级

1. CLI 参数 > 环境变量 > 默认值
2. Token 仅支持环境变量传递（安全性考虑），CLI 参数作为备选

---

## 3. 核心模块设计

### 3.1 模块结构

```
sync.py                  # 主入口脚本（单文件即可）
```

### 3.2 主要函数

```python
def parse_args() -> argparse.Namespace:
    """解析命令行参数和环境变量"""

def validate_tokens(github_token, gitee_token) -> None:
    """验证 GitHub 和 Gitee Token 的有效性（在同步前调用）"""

def get_github_repos(owner, token, account_type, include_private) -> list[dict]:
    """通过 GitHub API 获取全部仓库列表"""

def get_gitee_repos(owner, token, account_type) -> list[dict]:
    """通过 Gitee API 获取全部仓库列表"""

def create_repo(platform, owner, token, repo_name, private, description, account_type) -> bool:
    """在目标平台上创建仓库（如果 create_missing_repos=true 且仓库不存在）"""

def mirror_sync(source_url, target_url, repo_name) -> bool:
    """执行 git clone --mirror + git push --mirror"""

def sync_wiki(source_owner, source_repo, target_owner, target_repo, ...) -> bool:
    """同步 Wiki（git clone --mirror .wiki.git + git push --mirror）"""

def sync_releases(source_platform, target_platform, owner, repo, ...) -> bool:
    """同步 Releases 和 Release Assets"""

def sync_labels(source_platform, target_platform, owner, repo, ...) -> bool:
    """同步 Labels"""

def sync_milestones(source_platform, target_platform, owner, repo, ...) -> bool:
    """同步 Milestones"""

def sync_issues(source_platform, target_platform, owner, repo, ...) -> bool:
    """同步 Issues 和 Comments（可选，复杂度高）"""

def sync_repo_metadata(source_platform, target_platform, owner, repo, ...) -> bool:
    """同步仓库元信息（描述、主页等）"""

def sync_single_repo(repo, args) -> str:
    """同步单个仓库（代码 + 根据配置同步附属信息），返回 'success'/'failed'/'skipped'"""

def sync_all(args) -> None:
    """主同步流程"""
```

---

## 4. 同步流程

```
开始
  │
  ├─ 1. 解析参数（CLI + 环境变量）
  │
  ├─ 2. 验证 Token 有效性
  │
  ├─ 3. 确定同步方向（源平台/目标平台）
  │
  ├─ 4. 获取源平台全部仓库列表
  │     ├─ 分页获取
  │     └─ 根据 include_private 过滤
  │
  ├─ 5. 根据 exclude_repos 过滤不需要同步的仓库
  │
  ├─ 6. 获取目标平台已有仓库列表（用于判断是否需要创建）
  │
  ├─ 7. 遍历每个需要同步的仓库：
  │     ├─ 7a. 检查目标平台是否已有同名仓库
  │     │     └─ 如果没有 且 create_missing_repos=true → 创建仓库
  │     │     └─ 如果没有 且 create_missing_repos=false → 跳过该仓库
  │     │
  │     ├─ 7b. git clone --mirror（从源平台克隆）
  │     │
  │     ├─ 7c. git push --mirror（推送到目标平台）
  │     │
  │     ├─ 7d. 同步仓库元信息（描述、主页等）
  │     │
  │     ├─ 7e. 根据 sync_extra 配置同步附属信息：
  │     │     ├─ releases: 同步 Releases + Assets
  │     │     ├─ wiki: 同步 Wiki（.wiki.git mirror）
  │     │     ├─ labels: 同步 Labels
  │     │     ├─ milestones: 同步 Milestones
  │     │     └─ issues: 同步 Issues + Comments
  │     │
  │     └─ 7f. 清理临时目录
  │
  ├─ 8. 如果是双向同步 → 反向执行一轮
  │
  ├─ 9. 输出同步结果摘要
  │
  └─ 结束
```

> 详细的流程图请参见 [流程图](流程图.md)

---

## 5. 错误处理

| 场景 | 处理方式 |
|------|---------|
| API 请求失败 | 重试 3 次，间隔递增 (2s, 4s, 8s) |
| 仓库创建失败 | 记录错误，跳过该仓库，继续处理其他仓库 |
| git clone 失败 | 重试 2 次，最终失败则跳过该仓库 |
| git push 失败 | 重试 2 次，最终失败则跳过该仓库 |
| Token 无效 | 立即退出，明确报错 |
| Token 权限不足 | 降级处理或退出 |
| 网络超时 | 重试 3 次，间隔递增 |
| Rate Limit | 检查剩余配额，必要时等待重置 |
| 空仓库 | 跳过 git push，记录警告 |
| Wiki 不存在 | 静默跳过 |
| 附属信息同步失败 | 记录警告，不影响仓库整体同步状态 |
| 双向冲突 | 以主方为准（默认 GitHub） |

> 详细的错误处理设计请参见 [错误处理设计](错误处理设计.md)

---

## 6. 日志输出

使用 Python `logging` 模块：

```
[INFO]  开始同步 GitHub(owner) -> Gitee(owner)
[INFO]  获取到 GitHub 仓库 25 个
[INFO]  排除仓库: repo-a, repo-b
[INFO]  需要同步仓库 23 个
[INFO]  [1/23] 同步 repo-name ...
[INFO]  [1/23]   Gitee 仓库已存在，跳过创建
[INFO]  [1/23]   镜像克隆完成
[INFO]  [1/23]   推送完成 ✓
[ERROR] [5/23] 同步 repo-x 失败: git push error ...
[INFO]  ===== 同步完成 =====
[INFO]  成功: 22, 失败: 1, 跳过: 0
```

---

## 7. 依赖

| 依赖 | 用途 | 是否为标准库 |
|------|------|------------|
| `requests` | HTTP API 调用 | ❌（需安装） |
| `argparse` | 命令行参数解析 | ✅ |
| `subprocess` | 执行 git 命令 | ✅ |
| `logging` | 日志输出 | ✅ |
| `os` / `shutil` / `tempfile` | 文件系统操作 | ✅ |

唯一的外部依赖是 `requests`。

---

## 8. 使用示例

### 命令行方式

```bash
# 基本用法（GitHub → Gitee，默认）
python sync.py \
  --github-owner myuser \
  --github-token ghp_xxxx \
  --gitee-owner myuser \
  --gitee-token xxxxx

# 反向同步：Gitee → GitHub
python sync.py \
  --github-owner myuser \
  --github-token ghp_xxxx \
  --gitee-owner myuser \
  --gitee-token xxxxx \
  --direction gitee2github

# 双向同步
python sync.py \
  --github-owner myuser \
  --github-token ghp_xxxx \
  --gitee-owner myuser \
  --gitee-token xxxxx \
  --direction both

# 组织账号 + 排除特定仓库
python sync.py \
  --github-owner my-org \
  --gitee-owner my-org \
  --account-type org \
  --exclude-repos "old-repo,deprecated-repo"

# 仅同步公开仓库
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser \
  --include-private false

# 不自动创建目标仓库（仅同步已存在的同名仓库）
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser \
  --create-missing-repos false

# 同步代码 + Releases + Wiki
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser \
  --sync-extra "releases,wiki"

# 同步全部附属信息
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser \
  --sync-extra "releases,wiki,labels,milestones,issues"
```

### 环境变量方式

```bash
export GITHUB_OWNER=myuser
export GITHUB_TOKEN=ghp_xxxx
export GITEE_OWNER=myuser
export GITEE_TOKEN=xxxxx
export EXCLUDE_REPOS="old-repo,deprecated-repo"
export SYNC_DIRECTION=github2gitee
export CREATE_MISSING_REPOS=true
export SYNC_EXTRA=releases,wiki

python sync.py
```
