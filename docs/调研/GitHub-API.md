# GitHub REST API 调研

## 概述

GitHub REST API 文档：<https://docs.github.com/en/rest>

本项目主要使用以下 API 端点来实现仓库同步功能。

---

## 1. 列出用户/组织的全部仓库

### 1.1 列出已认证用户的仓库（含私有）

```
GET https://api.github.com/user/repos
```

- **认证**：必须，需要 `repo` scope 的 Personal Access Token
- **参数**：
  | 参数 | 类型 | 说明 |
  |------|------|------|
  | `type` | string | `all`, `owner`, `public`, `private`, `member`（默认 `all`） |
  | `per_page` | integer | 每页数量，最大 100（默认 30） |
  | `page` | integer | 页码（默认 1） |
  | `sort` | string | `created`, `updated`, `pushed`, `full_name`（默认 `full_name`） |

### 1.2 列出指定用户的公开仓库

```
GET https://api.github.com/users/{username}/repos
```

- **认证**：非必须（仅公开仓库）
- **参数**：同上

### 1.3 列出组织的仓库

```
GET https://api.github.com/orgs/{org}/repos
```

- **认证**：需要认证才能看到私有仓库
- **参数**：
  | 参数 | 类型 | 说明 |
  |------|------|------|
  | `type` | string | `all`, `public`, `private`, `forks`, `sources`, `member`（默认 `all`） |
  | `per_page` | integer | 每页数量，最大 100（默认 30） |
  | `page` | integer | 页码（默认 1） |

---

## 2. 获取仓库详情

```
GET https://api.github.com/repos/{owner}/{repo}
```

- **返回字段**（关键）：
  - `name` — 仓库名
  - `full_name` — 完整名称（owner/repo）
  - `private` — 是否私有
  - `description` — 描述
  - `clone_url` — HTTPS 克隆地址
  - `ssh_url` — SSH 克隆地址
  - `default_branch` — 默认分支

---

## 3. 创建仓库

### 3.1 在个人账号下创建

```
POST https://api.github.com/user/repos
```

### 3.2 在组织下创建

```
POST https://api.github.com/orgs/{org}/repos
```

- **请求体**：
  ```json
  {
    "name": "repo-name",
    "description": "描述",
    "private": true,
    "auto_init": false
  }
  ```

---

## 4. 认证方式

### Personal Access Token (PAT)

在请求头中携带：

```
Authorization: token <YOUR_PERSONAL_ACCESS_TOKEN>
Accept: application/vnd.github.v3+json
```

### 所需 Scope

| Scope | 说明 |
|-------|------|
| `repo` | 完整仓库访问（含私有仓库读写） |
| `public_repo` | 仅公开仓库访问 |
| `read:org` | 读取组织信息 |

---

## 5. 分页处理

GitHub API 默认每页 30 条，最大 100 条。需要遍历所有页面获取全部数据。

```python
page = 1
all_repos = []
while True:
    resp = requests.get(url, params={"per_page": 100, "page": page}, headers=headers)
    data = resp.json()
    if not data:
        break
    all_repos.extend(data)
    page += 1
```

---

## 6. 速率限制

- 已认证用户：5000 次/小时
- 未认证：60 次/小时
- 响应头中的 `X-RateLimit-Remaining` 可以查看剩余次数

---

## 7. 仓库关键字段映射（同步用）

| 字段 | 说明 | 同步用途 |
|------|------|---------|
| `name` | 仓库名 | 在 Gitee 创建同名仓库 |
| `private` | 是否私有 | 决定在 Gitee 是否创建私有仓库 |
| `description` | 仓库描述 | 同步描述信息 |
| `clone_url` | HTTPS 克隆地址 | 用于 `git clone --mirror` |
| `default_branch` | 默认分支 | 同步后保持一致 |

---

## 参考链接

- [GitHub REST API 官方文档](https://docs.github.com/en/rest)
- [Repositories API](https://docs.github.com/en/rest/repos/repos)
- [Releases API](https://docs.github.com/en/rest/releases/releases)
- [Release Assets API](https://docs.github.com/en/rest/releases/assets)
- [Issues API](https://docs.github.com/en/rest/issues/issues)
- [Labels API](https://docs.github.com/en/rest/issues/labels)
- [Milestones API](https://docs.github.com/en/rest/issues/milestones)
- [Authentication](https://docs.github.com/en/rest/authentication)
- [Rate Limiting](https://docs.github.com/en/rest/rate-limit)

---

## 8. Releases API

### 8.1 列出 Releases

```
GET /repos/{owner}/{repo}/releases
```

- 返回仓库的全部 releases（不包含未关联 release 的纯 tag）
- 关键字段：`id`, `tag_name`, `name`, `body`, `draft`, `prerelease`, `assets`

### 8.2 创建 Release

```
POST /repos/{owner}/{repo}/releases
```

- 请求体：
  ```json
  {
    "tag_name": "v1.0.0",
    "name": "Release Title",
    "body": "Release notes...",
    "draft": false,
    "prerelease": false
  }
  ```
- 响应中包含 `upload_url` 用于上传附件

### 8.3 上传 Release Asset

```
POST https://uploads.github.com/repos/{owner}/{repo}/releases/{release_id}/assets?name=filename
```

- 注意：上传 URL 域名为 `uploads.github.com`（不是 `api.github.com`）
- 使用 `Content-Type` 头指定文件 MIME 类型
- 请求体为文件二进制内容

### 8.4 列出 Release Assets

```
GET /repos/{owner}/{repo}/releases/{release_id}/assets
```

---

## 9. Labels API

```
GET    /repos/{owner}/{repo}/labels            # 列出全部标签
POST   /repos/{owner}/{repo}/labels            # 创建标签
PATCH  /repos/{owner}/{repo}/labels/{name}     # 更新标签
DELETE /repos/{owner}/{repo}/labels/{name}      # 删除标签
```

- 关键字段：`name`, `color`, `description`

---

## 10. Milestones API

```
GET    /repos/{owner}/{repo}/milestones            # 列出里程碑
POST   /repos/{owner}/{repo}/milestones            # 创建里程碑
PATCH  /repos/{owner}/{repo}/milestones/{number}   # 更新里程碑
DELETE /repos/{owner}/{repo}/milestones/{number}    # 删除里程碑
```

- 关键字段：`title`, `state`, `description`, `due_on`

---

## 11. Issues API

```
GET    /repos/{owner}/{repo}/issues                         # 列出 issues
POST   /repos/{owner}/{repo}/issues                         # 创建 issue
PATCH  /repos/{owner}/{repo}/issues/{issue_number}          # 更新 issue
GET    /repos/{owner}/{repo}/issues/{issue_number}/comments # 列出评论
POST   /repos/{owner}/{repo}/issues/{issue_number}/comments # 创建评论
```

- 关键字段：`title`, `body`, `state`, `labels`, `milestone`, `assignees`
- 注意：Issues API 也会返回 Pull Requests（需通过 `pull_request` 字段区分）

---

## 12. 更新仓库信息

```
PATCH /repos/{owner}/{repo}
```

- 可更新字段：`description`, `homepage`, `default_branch`, `private`, `topics` 等
- 请求体示例：
  ```json
  {
    "description": "New description",
    "homepage": "https://example.com"
  }
  ```

---

## 13. Wiki

GitHub **没有** Wiki REST API。Wiki 是独立的 Git 仓库，通过 `{repo}.wiki.git` URL 访问，可使用 `git clone --mirror` / `git push --mirror` 同步。
