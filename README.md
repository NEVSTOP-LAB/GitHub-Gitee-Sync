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

---

## 快速开始

### 前置条件

- [GitHub Personal Access Token](https://github.com/settings/tokens)（需要 `repo` 权限；同步组织仓库还需要 `read:org` 权限）
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

# 也可以使用 .env 文件
docker run --rm --env-file .env github-gitee-sync
```

---

## 参数说明

| 参数 | 环境变量 | CLI 参数 | 必填 | 默认值 | 说明 |
|------|---------|---------|------|--------|------|
| GitHub 账号 | `GITHUB_OWNER` | `--github-owner` | ✅ | - | GitHub 用户名或组织名 |
| GitHub Token | `GITHUB_TOKEN` | `--github-token` | ✅ | - | GitHub Personal Access Token |
| Gitee 账号 | `GITEE_OWNER` | `--gitee-owner` | ✅ | - | Gitee 用户名或组织名 |
| Gitee Token | `GITEE_TOKEN` | `--gitee-token` | ✅ | - | Gitee Personal Access Token |
| 账号类型 | `ACCOUNT_TYPE` | `--account-type` | ❌ | `user` | `user`（个人）或 `org`（组织），同时应用于 GitHub 和 Gitee 两侧 |
| 包含私有仓库 | `INCLUDE_PRIVATE` | `--include-private` | ❌ | `true` | 是否同步私有仓库 |
| 排除仓库 | `EXCLUDE_REPOS` | `--exclude-repos` | ❌ | 空 | 逗号分隔的仓库名列表 |
| 同步方向 | `SYNC_DIRECTION` | `--direction` | ❌ | `github2gitee` | `github2gitee` / `gitee2github` / `both` |
| 创建不存在的仓库 | `CREATE_MISSING_REPOS` | `--create-missing-repos` | ❌ | `true` | 目标仓库不存在时是否自动创建 |
| 附属信息同步 | `SYNC_EXTRA` | `--sync-extra` | ❌ | 空 | 逗号分隔：`releases,wiki,labels,milestones,issues` |
| 干运行模式 | `DRY_RUN` | `--dry-run` | ❌ | `false` | 运行全部逻辑但不实际同步，用于调试和测试 |

---

## 使用示例

```yaml
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
```

> **注意**：`account-type` 参数同时应用于 GitHub 和 Gitee 两侧，不支持非对称配置（例如 GitHub 为个人账号而 Gitee 为组织账号）。如需同步组织仓库，两侧均须为组织账号。
> 同步组织仓库时，GitHub Token 需要额外的 `read:org` 权限。

```yaml
# 同步 Releases 和 Wiki，读取 Action 输出
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  id: sync
  with:
    github-owner: myuser
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-owner: myuser
    gitee-token: ${{ secrets.GITEE_TOKEN }}
    sync-extra: 'releases,wiki'
- run: echo "Synced ${{ steps.sync.outputs['synced-count'] }} repos"
```

---

## Action Outputs

| Output | 说明 |
|--------|------|
| `synced-count` | 成功同步的仓库数量 |
| `failed-count` | 同步失败的仓库数量 |
| `skipped-count` | 跳过的仓库数量 |

## 退出码

| 退出码 | 含义 |
|-------|------|
| 0 | 全部成功 |
| 1 | 部分仓库失败 |
| 2 | 全部失败 |
| 3 | 致命错误（认证失败、环境异常） |

---

## 文档

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

- **实施记录**
  - [实施记录](docs/实施记录.md)（模块结构、技术选择、代码审查反馈实施）

---

## License

MIT
