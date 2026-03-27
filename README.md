# GitHub-Gitee-Sync

Sync All the Repos(public/private) between GitHub and Gitee.

同步 GitHub 和 Gitee 账号下的全部仓库（支持公开和私有仓库）。

---

## 功能

- 🔄 自动同步 GitHub 账号下的全部仓库到 Gitee
- 🏢 支持个人账号和组织账号
- 🔒 支持私有仓库同步
- 🚫 支持排除指定仓库
- 🐳 提供 Docker 镜像，开箱即用
- 📋 自动在 Gitee 创建不存在的仓库

## 快速开始

### 前置条件

- Python 3.8+
- Git
- [GitHub Personal Access Token](https://github.com/settings/tokens)（需要 `repo` 权限）
- [Gitee Personal Access Token](https://gitee.com/profile/personal_access_tokens)（需要 `projects` 权限）

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

## 使用示例

```bash
# 同步个人账号全部仓库
python sync.py \
  --github-owner myuser \
  --gitee-owner myuser

# 同步组织仓库，排除部分仓库
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

## 技术方案

- 通过 GitHub REST API 获取仓库列表
- 通过 Gitee API v5 管理目标仓库
- 使用 `git clone --mirror` + `git push --mirror` 实现完整同步
- 自动处理分页、仓库创建、错误重试

## 文档

详细的调研和开发计划请参见 `docs/` 目录：

- **调研文档**
  - [GitHub API 调研](docs/调研/GitHub-API.md)
  - [Gitee API 调研](docs/调研/Gitee-API.md)
  - [Git Mirror 同步机制](docs/调研/Git-Mirror-同步机制.md)

- **开发计划**
  - [Python 脚本设计](docs/计划/Python-脚本设计.md)
  - [Docker 镜像设计](docs/计划/Docker-镜像设计.md)
  - [开发步骤](docs/计划/开发步骤.md)

## License

MIT
