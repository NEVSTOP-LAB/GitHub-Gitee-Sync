# GitHub-Gitee-Sync

Sync All the Repos(public/private) between GitHub and Gitee.

同步 GitHub 和 Gitee 账号下的全部仓库（支持公开和私有仓库）。

---

## 功能

- 🔄 自动同步 GitHub 和 Gitee 账号下的全部仓库
- ↔️ 支持多种同步方向：GitHub→Gitee / Gitee→GitHub / 双向同步
- 🏢 支持个人账号和组织账号
- 🔒 支持私有仓库同步
- 🚫 支持排除指定仓库
- 📦 支持同步 Releases、Wiki、Labels、Milestones 等附属信息
- 🐳 提供 Docker 镜像，开箱即用
- 🎬 提供 GitHub Action，一键集成到 Workflow
- 📋 自动在目标平台创建不存在的仓库（可配置关闭）

## 快速开始

### 前置条件

- Python 3.8+
- Git
- [GitHub Personal Access Token](https://github.com/settings/tokens)（需要 `repo` 权限）
- [Gitee Personal Access Token](https://gitee.com/profile/personal_access_tokens)（需要 `projects` 权限）

### 使用 GitHub Action

在你的仓库中创建 `.github/workflows/sync.yml`：

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

> **注意**：需要在仓库 Settings > Secrets 中配置 `GH_TOKEN`、`GITEE_OWNER`、`GITEE_TOKEN`。

### 使用 Python

```bash
pip install requests

python sync.py \
  --github-owner <GitHub用户名或组织名> \
  --github-token <GitHub Token> \
  --gitee-owner <Gitee用户名或组织名> \
  --gitee-token <Gitee Token>
```

### 使用 Docker

```bash
docker build -t github-gitee-sync .

docker run --rm \
  -e GITHUB_OWNER=<GitHub用户名或组织名> \
  -e GITHUB_TOKEN=<GitHub Token> \
  -e GITEE_OWNER=<Gitee用户名或组织名> \
  -e GITEE_TOKEN=<Gitee Token> \
  github-gitee-sync
```

## 参数说明

| 参数 | 环境变量 | CLI 参数 | 必填 | 默认值 | 说明 |
|------|---------|---------|------|--------|------|
| GitHub 账号 | `GITHUB_OWNER` | `--github-owner` | ✅ | - | GitHub 用户名或组织名 |
| GitHub Token | `GITHUB_TOKEN` | `--github-token` | ✅ | - | GitHub Personal Access Token |
| Gitee 账号 | `GITEE_OWNER` | `--gitee-owner` | ✅ | - | Gitee 用户名或组织名 |
| Gitee Token | `GITEE_TOKEN` | `--gitee-token` | ✅ | - | Gitee Personal Access Token |
| 账号类型 | `ACCOUNT_TYPE` | `--account-type` | ❌ | `user` | `user`（个人）或 `org`（组织） |
| 包含私有仓库 | `INCLUDE_PRIVATE` | `--include-private` | ❌ | `true` | 是否同步私有仓库 |
| 排除仓库 | `EXCLUDE_REPOS` | `--exclude-repos` | ❌ | 空 | 逗号分隔的仓库名列表 |
| 同步方向 | `SYNC_DIRECTION` | `--direction` | ❌ | `github2gitee` | `github2gitee` / `gitee2github` / `both` |
| 创建不存在的仓库 | `CREATE_MISSING_REPOS` | `--create-missing-repos` | ❌ | `true` | 目标仓库不存在时是否自动创建 |
| 附属信息同步 | `SYNC_EXTRA` | `--sync-extra` | ❌ | 空 | 逗号分隔：`releases,wiki,labels,milestones,issues` |

## 使用示例

### GitHub Action

```yaml
# 同步个人账号全部仓库
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  with:
    github-owner: myuser
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-owner: myuser
    gitee-token: ${{ secrets.GITEE_TOKEN }}

# 反向同步：Gitee → GitHub
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  with:
    github-owner: myuser
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-owner: myuser
    gitee-token: ${{ secrets.GITEE_TOKEN }}
    direction: gitee2github

# 双向同步
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  with:
    github-owner: myuser
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-owner: myuser
    gitee-token: ${{ secrets.GITEE_TOKEN }}
    direction: both

# 同步组织仓库，排除部分仓库
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  with:
    github-owner: my-org
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-owner: my-org
    gitee-token: ${{ secrets.GITEE_TOKEN }}
    account-type: org
    exclude-repos: 'old-repo,deprecated-repo'

# 不自动创建仓库 + 同步 Releases 和 Wiki
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  with:
    github-owner: myuser
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-owner: myuser
    gitee-token: ${{ secrets.GITEE_TOKEN }}
    create-missing-repos: 'false'
    sync-extra: 'releases,wiki'

# 仅同步公开仓库 + 获取结果
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  id: sync
  with:
    github-owner: myuser
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-owner: myuser
    gitee-token: ${{ secrets.GITEE_TOKEN }}
    include-private: 'false'
- run: echo "Synced ${{ steps.sync.outputs.synced-count }} repos"
```

### Python CLI

```bash
# 同步个人账号全部仓库（默认 GitHub → Gitee）
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser

# 反向同步：Gitee → GitHub
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser \
  --direction gitee2github

# 双向同步
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser \
  --direction both

# 同步组织仓库，排除部分仓库
python sync.py \
  --github-owner my-org \
  --gitee-owner my-org \
  --account-type org \
  --exclude-repos "old-repo,deprecated-repo"

# 仅同步公开仓库，不自动创建目标仓库
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser \
  --include-private false \
  --create-missing-repos false

# 同步代码 + Releases + Wiki + Labels
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser \
  --sync-extra "releases,wiki,labels"
```

## 技术方案

- 通过 GitHub REST API 获取仓库列表
- 通过 Gitee API v5 管理目标仓库
- 使用 `git clone --mirror` + `git push --mirror` 实现完整同步
- 通过 REST API 同步 Releases、Labels、Milestones 等附属信息
- 通过 Git mirror 方式同步 Wiki
- 自动处理分页、仓库创建、错误重试

## 文档

详细的调研和开发计划请参见 `docs/` 目录：

- **调研文档**
  - [GitHub API 调研](docs/调研/GitHub-API.md)
  - [Gitee API 调研](docs/调研/Gitee-API.md)
  - [Git Mirror 同步机制](docs/调研/Git-Mirror-同步机制.md)
  - [GitHub Actions 自定义 Action](docs/调研/GitHub-Actions.md)
  - [仓库附属信息同步调研](docs/调研/仓库附属信息同步调研.md)

- **开发计划**
  - [Python 脚本设计](docs/计划/Python-脚本设计.md)
  - [Docker 镜像设计](docs/计划/Docker-镜像设计.md)
  - [GitHub Action 设计](docs/计划/GitHub-Action-设计.md)
  - [流程图](docs/计划/流程图.md)
  - [错误处理设计](docs/计划/错误处理设计.md)
  - [开发步骤](docs/计划/开发步骤.md)

## License

MIT
