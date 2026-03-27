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
- [Authentication](https://docs.github.com/en/rest/authentication)
- [Rate Limiting](https://docs.github.com/en/rest/rate-limit)
