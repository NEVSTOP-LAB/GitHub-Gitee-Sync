# Python 同步脚本设计

## 1. 脚本概述

创建 `sync.py` 脚本，用于将 GitHub 账号（个人或组织）下的全部仓库同步到 Gitee 对应账号下。

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

def get_github_repos(owner, token, account_type, include_private) -> list[dict]:
    """通过 GitHub API 获取全部仓库列表"""

def get_gitee_repos(owner, token, account_type) -> list[dict]:
    """通过 Gitee API 获取全部仓库列表"""

def create_gitee_repo(owner, token, repo_name, private, description, account_type) -> bool:
    """在 Gitee 上创建仓库（如果不存在）"""

def mirror_sync(source_url, target_url, repo_name) -> bool:
    """执行 git clone --mirror + git push --mirror"""

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
  ├─ 2. 获取 GitHub 全部仓库列表
  │     ├─ 分页获取
  │     └─ 根据 include_private 过滤
  │
  ├─ 3. 根据 exclude_repos 过滤不需要同步的仓库
  │
  ├─ 4. 获取 Gitee 已有仓库列表（用于判断是否需要创建）
  │
  ├─ 5. 遍历每个需要同步的仓库：
  │     ├─ 5a. 检查 Gitee 是否已有同名仓库
  │     │     └─ 如果没有 → 调用 Gitee API 创建仓库
  │     │
  │     ├─ 5b. git clone --mirror（从 GitHub 克隆）
  │     │
  │     ├─ 5c. git push --mirror（推送到 Gitee）
  │     │
  │     └─ 5d. 清理临时目录
  │
  ├─ 6. 输出同步结果摘要
  │
  └─ 结束
```

---

## 5. 错误处理

| 场景 | 处理方式 |
|------|---------|
| API 请求失败 | 重试 3 次，间隔 2 秒 |
| 仓库创建失败 | 记录错误，跳过该仓库，继续处理其他仓库 |
| git clone 失败 | 记录错误，跳过该仓库 |
| git push 失败 | 记录错误，跳过该仓库 |
| Token 无效 | 立即退出，明确报错 |
| 网络超时 | 重试 3 次，间隔递增 |

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
# 基本用法
python sync.py \
  --github-owner myuser \
  --github-token ghp_xxxx \
  --gitee-owner myuser \
  --gitee-token xxxxx

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
```

### 环境变量方式

```bash
export GITHUB_OWNER=myuser
export GITHUB_TOKEN=ghp_xxxx
export GITEE_OWNER=myuser
export GITEE_TOKEN=xxxxx
export EXCLUDE_REPOS="old-repo,deprecated-repo"

python sync.py
```
