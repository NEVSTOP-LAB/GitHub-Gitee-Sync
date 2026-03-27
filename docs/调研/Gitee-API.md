# Gitee API v5 调研

## 概述

Gitee API v5 文档（Swagger）：<https://gitee.com/api/v5/swagger>

本项目主要使用以下 API 端点来实现仓库同步功能。

---

## 1. 列出用户/组织的全部仓库

### 1.1 列出已认证用户的仓库（含私有）

```
GET https://gitee.com/api/v5/user/repos
```

- **认证**：必须，需要 `access_token`
- **参数**：
  | 参数 | 类型 | 说明 |
  |------|------|------|
  | `access_token` | string | 用户授权码 |
  | `type` | string | `all`, `owner`, `public`, `private`, `member`（默认 `all`） |
  | `per_page` | integer | 每页数量，最大 100（默认 20） |
  | `page` | integer | 页码（默认 1） |
  | `sort` | string | `created`, `updated`, `pushed`, `full_name`（默认 `full_name`） |

### 1.2 列出指定用户的公开仓库

```
GET https://gitee.com/api/v5/users/{username}/repos
```

- **认证**：非必须（仅公开仓库）

### 1.3 列出组织的仓库

```
GET https://gitee.com/api/v5/orgs/{org}/repos
```

- **认证**：需要认证才能看到私有仓库
- **参数**：
  | 参数 | 类型 | 说明 |
  |------|------|------|
  | `access_token` | string | 用户授权码 |
  | `type` | string | `all`, `public`, `private`（默认 `all`） |
  | `per_page` | integer | 每页数量，最大 100（默认 20） |
  | `page` | integer | 页码（默认 1） |

---

## 2. 获取仓库详情

```
GET https://gitee.com/api/v5/repos/{owner}/{repo}
```

- **返回字段**（关键）：
  - `name` — 仓库名
  - `full_name` — 完整名称（owner/repo）
  - `private` — 是否私有
  - `public` — 开源类型 (0=私有, 1=外部开源, 2=内部开源)
  - `description` — 描述
  - `html_url` — 仓库页面地址
  - `ssh_url` — SSH 克隆地址
  - `default_branch` — 默认分支

---

## 3. 创建仓库

### 3.1 在个人账号下创建

```
POST https://gitee.com/api/v5/user/repos
```

### 3.2 在组织下创建

```
POST https://gitee.com/api/v5/orgs/{org}/repos
```

- **请求体**：
  ```json
  {
    "access_token": "YOUR_TOKEN",
    "name": "repo-name",
    "description": "描述",
    "private": true,
    "auto_init": false
  }
  ```

- **关键参数**：
  | 参数 | 类型 | 说明 |
  |------|------|------|
  | `access_token` | string | **必填**，用户授权码 |
  | `name` | string | **必填**，仓库名 |
  | `description` | string | 仓库描述 |
  | `private` | boolean | 是否私有（默认 false） |
  | `public` | integer | 0=私有, 1=外部开源, 2=内部开源（与 private 互斥，public 优先） |
  | `auto_init` | boolean | 是否初始化 README（默认 false） |
  | `path` | string | 自定义仓库路径 |
  | `default_branch` | string | 默认分支名 |

---

## 4. 认证方式

### Personal Access Token

可通过以下两种方式传递：

1. **请求参数**：`?access_token=YOUR_TOKEN`
2. **请求头**：`Authorization: Bearer YOUR_TOKEN`（推荐）

### Token 创建

在 Gitee 设置页面创建：<https://gitee.com/profile/personal_access_tokens>

### 所需权限

| 权限 | 说明 |
|------|------|
| `projects` | 仓库管理（创建、读写等） |
| `user_info` | 用户基本信息 |

---

## 5. 分页处理

Gitee API 默认每页 20 条，最大 100 条。响应头中包含分页信息：

- `total_count` — 总条数
- `total_page` — 总页数

```python
page = 1
all_repos = []
while True:
    resp = requests.get(url, params={"access_token": token, "per_page": 100, "page": page})
    data = resp.json()
    if not data:
        break
    all_repos.extend(data)
    page += 1
```

---

## 6. 速率限制

- Gitee API v5 对速率限制的文档相对不透明
- 建议在请求之间适当添加延时（如 0.5~1 秒）
- 检查响应状态码，遇到 429 时进行退避重试

---

## 7. 仓库关键字段映射（同步用）

| 字段 | 说明 | 同步用途 |
|------|------|---------|
| `name` | 仓库名 | 匹配 GitHub 同名仓库 |
| `private` | 是否私有 | 保持与 GitHub 仓库一致 |
| `description` | 仓库描述 | 同步描述信息 |
| `html_url` | 仓库地址 | 用于 `git push --mirror` |
| `default_branch` | 默认分支 | 同步后保持一致 |

---

## 8. 注意事项

1. **仓库名限制**：Gitee 仓库名可能对特殊字符有额外限制，需注意与 GitHub 仓库名的兼容性
2. **私有仓库创建**：`private` 和 `public` 参数互斥，`public` 优先级更高
3. **auto_init 与镜像同步冲突**：创建目标仓库时 **不要** 设置 `auto_init=true`，否则会导致 `git push --mirror` 冲突
4. **API 版本**：始终使用 v5 版本

---

## 参考链接

- [Gitee API v5 Swagger 文档](https://gitee.com/api/v5/swagger)
- [Gitee 帮助文档](https://gitee.com/help)
