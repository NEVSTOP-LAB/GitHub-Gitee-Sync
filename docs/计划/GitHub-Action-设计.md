# GitHub Action 设计

## 1. 概述

将本项目封装为一个 GitHub Action（Docker Container 类型），使用户可以在自己的 Workflow 中通过 `uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1` 直接调用，实现定时或手动触发 GitHub ↔ Gitee 的仓库同步（支持单向和双向）。

---

## 2. action.yml 设计

```yaml
name: 'GitHub Gitee Sync'
description: 'Sync all repositories (public/private) between GitHub and Gitee'
author: 'NEVSTOP-LAB'

inputs:
  github-owner:
    description: 'GitHub username or organization name'
    required: true
  github-token:
    description: 'GitHub Personal Access Token (needs repo scope)'
    required: true
  gitee-owner:
    description: 'Gitee username or organization name'
    required: true
  gitee-token:
    description: 'Gitee Personal Access Token (needs projects scope)'
    required: true
  account-type:
    description: 'Account type: user or org'
    required: false
    default: 'user'
  include-private:
    description: 'Whether to include private repositories'
    required: false
    default: 'true'
  exclude-repos:
    description: 'Comma-separated list of repository names to exclude'
    required: false
    default: ''
  direction:
    description: 'Sync direction: github2gitee, gitee2github, or both'
    required: false
    default: 'github2gitee'
  create-missing-repos:
    description: 'Whether to create repos on target platform if they do not exist'
    required: false
    default: 'true'
  sync-extra:
    description: 'Comma-separated list of extra items to sync: releases,wiki,labels,milestones,issues'
    required: false
    default: ''

outputs:
  synced-count:
    description: 'Number of repositories successfully synced'
  failed-count:
    description: 'Number of repositories that failed to sync'
  skipped-count:
    description: 'Number of repositories skipped'

runs:
  using: 'docker'
  image: 'Dockerfile'

branding:
  icon: 'refresh-cw'
  color: 'blue'
```

---

## 3. 文件结构（更新）

```
项目根目录/
├── action.yml               # GitHub Action 元数据（新增）
├── entrypoint.sh            # Action 入口脚本（新增）
├── sync.py                  # 同步脚本
├── requirements.txt         # Python 依赖
├── Dockerfile               # Docker 镜像（需适配）
├── .dockerignore
├── .gitignore
├── README.md
└── docs/
    ├── 调研/
    └── 计划/
```

---

## 4. entrypoint.sh 设计

GitHub Action 将 inputs 注入为 `INPUT_*` 环境变量。需要一个桥接脚本将其映射为 `sync.py` 识别的标准环境变量。

```bash
#!/bin/sh
set -e

# 映射 GitHub Action 的 INPUT_* 变量到 sync.py 期望的环境变量
# GitHub Actions 自动将 input 名中的连字符转为下划线并大写，加 INPUT_ 前缀
# 例如 input "github-owner" → 环境变量 INPUT_GITHUB_OWNER

# GitHub 相关
export GITHUB_OWNER="${INPUT_GITHUB_OWNER:-$GITHUB_OWNER}"
export GITHUB_TOKEN="${INPUT_GITHUB_TOKEN:-$GITHUB_TOKEN}"

# Gitee 相关
export GITEE_OWNER="${INPUT_GITEE_OWNER:-$GITEE_OWNER}"
export GITEE_TOKEN="${INPUT_GITEE_TOKEN:-$GITEE_TOKEN}"

# 可选参数
export ACCOUNT_TYPE="${INPUT_ACCOUNT_TYPE:-${ACCOUNT_TYPE:-user}}"
export INCLUDE_PRIVATE="${INPUT_INCLUDE_PRIVATE:-${INCLUDE_PRIVATE:-true}}"
export EXCLUDE_REPOS="${INPUT_EXCLUDE_REPOS:-$EXCLUDE_REPOS}"
export SYNC_DIRECTION="${INPUT_DIRECTION:-${SYNC_DIRECTION:-github2gitee}}"
export CREATE_MISSING_REPOS="${INPUT_CREATE_MISSING_REPOS:-${CREATE_MISSING_REPOS:-true}}"
export SYNC_EXTRA="${INPUT_SYNC_EXTRA:-$SYNC_EXTRA}"

# 执行同步脚本
python /app/sync.py
```

---

## 5. Dockerfile 适配

需要对现有 Dockerfile 进行微调以兼容 GitHub Action 使用：

```dockerfile
FROM python:3.11-slim

# 安装 git
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制脚本
COPY sync.py .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 使用 entrypoint.sh 作为入口
# - GitHub Action 模式：通过 INPUT_* 环境变量接收参数
# - Docker 独立模式：可通过 --entrypoint python 覆盖，直接运行 sync.py
ENTRYPOINT ["/entrypoint.sh"]
```

### 两种使用模式兼容

| 模式 | 入口 | 参数传递 |
|------|------|---------|
| GitHub Action | `entrypoint.sh` → `sync.py` | `INPUT_*` 环境变量 → 标准环境变量 |
| Docker 独立运行 | `entrypoint.sh` → `sync.py` | 标准环境变量直接传入 |
| Python 直接运行 | `python sync.py` | CLI 参数 或 环境变量 |

---

## 6. sync.py 适配

`sync.py` 的 `parse_args()` 函数已支持环境变量，无需大幅修改。只需确保：

1. 环境变量名与 `entrypoint.sh` 导出的一致
2. 支持通过 `GITHUB_OUTPUT` 文件写入 Action outputs

### 输出 Action Outputs

在 `sync_all()` 函数结尾添加：

```python
import os

def write_action_outputs(synced, failed, skipped):
    """如果在 GitHub Action 环境中运行，写入 outputs"""
    output_file = os.environ.get('GITHUB_OUTPUT')
    if output_file:
        with open(output_file, 'a') as f:
            f.write(f"synced-count={synced}\n")
            f.write(f"failed-count={failed}\n")
            f.write(f"skipped-count={skipped}\n")
```

---

## 7. 用户使用示例

### 7.1 基本用法

```yaml
name: Sync to Gitee
on:
  schedule:
    - cron: '0 2 * * *'   # 每天 UTC 2:00 自动同步
  workflow_dispatch:        # 支持手动触发

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Sync GitHub to Gitee
        uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
        with:
          github-owner: ${{ github.repository_owner }}
          github-token: ${{ secrets.GH_TOKEN }}
          gitee-owner: ${{ secrets.GITEE_OWNER }}
          gitee-token: ${{ secrets.GITEE_TOKEN }}
```

### 7.2 组织账号 + 排除仓库

```yaml
      - name: Sync org repos
        uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
        with:
          github-owner: my-org
          github-token: ${{ secrets.GH_TOKEN }}
          gitee-owner: my-org
          gitee-token: ${{ secrets.GITEE_TOKEN }}
          account-type: org
          exclude-repos: 'old-repo,deprecated-repo'
```

### 7.3 反向同步（Gitee → GitHub）

```yaml
      - name: Sync Gitee to GitHub
        uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
        with:
          github-owner: myuser
          github-token: ${{ secrets.GH_TOKEN }}
          gitee-owner: myuser
          gitee-token: ${{ secrets.GITEE_TOKEN }}
          direction: gitee2github
```

### 7.4 双向同步

```yaml
      - name: Bidirectional sync
        uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
        with:
          github-owner: myuser
          github-token: ${{ secrets.GH_TOKEN }}
          gitee-owner: myuser
          gitee-token: ${{ secrets.GITEE_TOKEN }}
          direction: both
```

### 7.5 不自动创建仓库（仅同步已存在的同名仓库）

```yaml
      - name: Sync existing repos only
        uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
        with:
          github-owner: myuser
          github-token: ${{ secrets.GH_TOKEN }}
          gitee-owner: myuser
          gitee-token: ${{ secrets.GITEE_TOKEN }}
          create-missing-repos: 'false'
```

### 7.6 同步附属信息（Releases + Wiki）

```yaml
      - name: Sync with releases and wiki
        uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
        with:
          github-owner: myuser
          github-token: ${{ secrets.GH_TOKEN }}
          gitee-owner: myuser
          gitee-token: ${{ secrets.GITEE_TOKEN }}
          sync-extra: 'releases,wiki'
```

### 7.7 仅公开仓库 + 使用输出

```yaml
      - name: Sync public repos
        id: sync
        uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
        with:
          github-owner: myuser
          github-token: ${{ secrets.GH_TOKEN }}
          gitee-owner: myuser
          gitee-token: ${{ secrets.GITEE_TOKEN }}
          include-private: 'false'

      - name: Show results
        run: |
          echo "✅ Synced: ${{ steps.sync.outputs.synced-count }}"
          echo "❌ Failed: ${{ steps.sync.outputs.failed-count }}"
          echo "⏭️ Skipped: ${{ steps.sync.outputs.skipped-count }}"
```

---

## 8. 版本管理策略

### Tag 命名

```
v1.0.0    # 首个正式版本
v1.0.1    # Bug 修复
v1.1.0    # 新增功能
v2.0.0    # 破坏性变更
```

### 主版本 Tag

维护 `v1` tag 指向最新的 `v1.x.x`，方便用户使用：

```bash
git tag -fa v1 -m "Update v1 tag"
git push origin v1 --force
```

### 发布流程

1. 更新代码并测试
2. 创建版本 Tag：`git tag v1.0.0`
3. 更新主版本 Tag：`git tag -fa v1`
4. 推送 Tags：`git push origin --tags`
5. 在 GitHub Releases 页面创建 Release，勾选 "Publish this Action to the GitHub Marketplace"

---

## 9. 用户需要配置的 Secrets

在使用此 Action 的仓库中，需要配置以下 Secrets：

| Secret 名 | 说明 | 获取方式 |
|-----------|------|---------|
| `GH_TOKEN` | GitHub Personal Access Token | [GitHub Settings > Tokens](https://github.com/settings/tokens)，需要 `repo` scope |
| `GITEE_OWNER` | Gitee 用户名或组织名 | Gitee 个人主页 URL 中的用户名 |
| `GITEE_TOKEN` | Gitee Personal Access Token | [Gitee Settings > Tokens](https://gitee.com/profile/personal_access_tokens)，需要 `projects` 权限 |

> **注意**：不要使用仓库自带的 `GITHUB_TOKEN`，它仅有当前仓库的权限，无法读取其他仓库。需要创建具有 `repo` scope 的 PAT。
