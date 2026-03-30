# GitHub-Gitee-Sync

Sync All the Repos(public/private) between [GitHub](https://github.com/) and [Gitee](https://gitee.com/).

同步 [GitHub](https://github.com/) 和 [Gitee](https://gitee.com/) 账号下的全部仓库（支持公开和私有仓库）。

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

| 参数 & 环境变量 | CLI 参数 | 必填 | 默认值 | 说明 |
|---------------|---------|------|--------|------|
| GitHub 账号 <br/> `GITHUB_OWNER` | `--github-owner` | ✅ | - | GitHub 用户名或组织名 |
| GitHub Token <br/> `GITHUB_TOKEN` | `--github-token` | ✅ | - | GitHub Personal Access Token |
| Gitee 账号 <br/> `GITEE_OWNER` | `--gitee-owner` | ✅ | - | Gitee 用户名或组织名 |
| Gitee Token <br/> `GITEE_TOKEN` | `--gitee-token` | ✅ | - | Gitee Personal Access Token |
| 账号类型 <br/> `ACCOUNT_TYPE` | `--account-type` | ❌ | `user` | `user`（个人）或 `org`（组织），同时应用于 GitHub 和 Gitee 两侧 |
| 包含私有仓库 <br/> `INCLUDE_PRIVATE` | `--include-private` | ❌ | `true` | 是否同步私有仓库 |
| 指定仓库（允许列表） <br/> `INCLUDE_REPOS` | `--include-repos` | ❌ | 空 | 逗号分隔的仓库名列表；设置后**仅同步**这些仓库，优先于排除列表 |
| 排除仓库 <br/> `EXCLUDE_REPOS` | `--exclude-repos` | ❌ | 空 | 逗号分隔的仓库名列表；当 `include-repos` 已设置时此参数被忽略 |
| 同步方向 <br/> `SYNC_DIRECTION` | `--direction` | ❌ | `github2gitee` | `github2gitee` / `gitee2github` / `both` |
| 创建不存在的仓库 <br/> `CREATE_MISSING_REPOS` | `--create-missing-repos` | ❌ | `true` | 目标仓库不存在时是否自动创建 |
| 附属信息同步 <br/> `SYNC_EXTRA` | `--sync-extra` | ❌ | 空 | 逗号分隔：`releases,wiki,labels,milestones,issues` |
| 干运行模式 <br/> `DRY_RUN` | `--dry-run` | ❌ | `false` | 运行全部逻辑但不实际同步，用于调试和测试 |
| 可见性过滤 <br/> `VISIBILITY` | `--visibility` | ❌ | `all` | `all` / `public` / `private`，在 include/exclude 过滤之后再次按可见性过滤仓库 |
| 显示私有仓库名 <br/> `SHOW_PRIVATE_REPO_NAMES` | `--show-private-repo-names` | ❌ | `false` | 日志中私有仓库名的显示方式：`false` 隐藏为 `[private]`；`true` 显示完整名称；正整数 N 显示前 N 个字符（如 `3` → `[CSM****]`） |
| Git 超时时间 <br/> `GIT_TIMEOUT` | `--git-timeout` | ❌ | `900` | 单次 git 操作的超时秒数（默认 15 分钟）；超时后自动重试一次 |

---

## 数据安全与同步策略

本工具采用**增量同步**策略，确保同步操作不会删除目标仓库上的独有内容。以下是各项同步的安全保证：

### 代码同步（分支与标签）

| 场景 | 行为 | 说明 |
|------|------|------|
| 源平台有新分支/标签 | ✅ 推送到目标 | 目标平台自动获得新分支/标签 |
| 目标平台有独有分支 | ✅ 保留不删除 | 同步不会影响目标平台独有的分支 |
| 目标平台有独有标签 | ✅ 保留不删除 | 同步不会影响目标平台独有的标签 |
| 两端都有同名分支 | ⚡ 以源平台为准 | 源平台的变更会强制覆盖到目标平台 |

**技术实现：** 使用 `git push --all --force` + `git push --tags --force` 代替 `git push --mirror`，确保只推送分支和标签，不会删除目标端独有的引用。

### Wiki 同步

| 场景 | 行为 | 说明 |
|------|------|------|
| 源平台有 Wiki 内容 | ✅ 推送到目标 | 目标平台自动获得 Wiki 内容 |
| 目标平台有独有 Wiki 页面 | ✅ 保留不删除 | 同步不会影响目标平台独有的页面 |
| 源平台无 Wiki | ⏭️ 静默跳过 | 不会影响目标平台已有的 Wiki |

### 附属信息同步

| 类型 | 同步策略 | 目标独有内容 | 说明 |
|------|---------|-------------|------|
| Releases | 增量同步 | ✅ 保留 | 按 tag_name 匹配，创建新的、更新已有的，不删除目标独有 release |
| Labels | 增量同步 | ✅ 保留 | 按名称匹配，创建新的、更新颜色和描述，不删除目标独有标签 |
| Milestones | 增量同步 | ✅ 保留 | 按标题匹配，创建新的、更新状态和描述，不删除目标独有里程碑 |
| Issues | 增量同步 | ✅ 保留 | 仅同步源平台 Open 状态的 issue，通过内嵌标记避免重复创建 |

### Token 与凭据安全

- **GIT_ASKPASS 认证**：Git 操作使用临时 askpass 脚本传递 Token，不会将 Token 内联到 URL 中，降低凭据泄露风险。
- **Token 自动脱敏**：日志输出自动过滤 `ghp_`、`gho_`、`github_pat_` 等格式的 Token，以及 URL 中嵌入的凭据信息。
- **临时文件清理**：同步完成后自动清理临时克隆目录和 askpass 脚本。
- **最小权限原则**：GitHub Token 只需 `repo` 权限，Gitee Token 只需 `projects` 权限。

### Dry-Run 模式

建议首次使用时启用 `--dry-run` 模式，运行全部逻辑但不实际执行同步操作，可以预览将要同步的仓库列表和操作，确认无误后再正式运行。

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

# 仅同步指定的仓库（允许列表）
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  with:
    github-owner: myuser
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-owner: myuser
    gitee-token: ${{ secrets.GITEE_TOKEN }}
    include-repos: 'repo-a,repo-b'

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

[MIT](LICENSE)
