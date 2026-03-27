# GitHub Actions 自定义 Action 调研

## 概述

GitHub Actions 允许开发者创建可复用的自定义 Action，发布到 GitHub Marketplace 供其他用户使用。本项目需要将同步工具封装为一个 GitHub Action，使用户可以通过 `uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1` 在自己的 Workflow 中直接调用。

官方文档：<https://docs.github.com/en/actions/creating-actions>

---

## 1. Action 类型对比

GitHub 支持三种自定义 Action 类型：

| 类型 | 运行环境 | 语言限制 | 平台支持 | 启动速度 | 适用场景 |
|------|---------|---------|---------|---------|---------|
| **Docker Container Action** | Docker 容器内 | 无限制（任意语言） | 仅 Linux | 较慢（需构建/拉取镜像） | 需要完整环境控制、非 JS 语言 |
| **JavaScript Action** | Runner 直接运行 | 仅 JavaScript/TypeScript | Linux/macOS/Windows | 快 | 轻量 API 交互、跨平台 |
| **Composite Action** | Runner 直接运行 | Shell + 组合其他 Action | Linux/macOS/Windows | 快 | 编排多个已有步骤 |

### 推荐：Docker Container Action

本项目选择 **Docker Container Action**，原因：

1. **已有 Docker 方案**：项目已经设计了 Dockerfile，可直接复用
2. **Python + Git 依赖**：需要 Python 运行时和 git 命令行工具，Docker 容器可以完整打包
3. **环境隔离**：不依赖 Runner 上的工具版本，保证行为一致
4. **仅需 Linux**：同步任务不需要跨平台运行

---

## 2. Docker Container Action 工作机制

### 2.1 执行流程

```
用户 Workflow 引用 Action
  │
  ├─ 1. GitHub 拉取 Action 仓库代码
  │
  ├─ 2. 根据 action.yml 中的 image 字段构建 Docker 镜像
  │     └─ image: 'Dockerfile' → 在 Runner 上执行 docker build
  │     └─ image: 'docker://xxx' → 直接拉取预构建镜像
  │
  ├─ 3. 以 action.yml 中定义的 inputs 注入环境变量
  │     └─ 每个 input 转为 INPUT_<NAME> 环境变量（大写、连字符转下划线）
  │
  ├─ 4. 启动容器，执行 entrypoint
  │
  └─ 5. 容器退出后，读取 outputs
```

### 2.2 环境变量注入规则

Action 的 inputs 在容器内以环境变量形式存在，命名规则：

- input 名转为**大写**
- **连字符（`-`）转为下划线（`_`）**
- 添加 `INPUT_` 前缀

| action.yml input 名 | 容器内环境变量 |
|---------------------|--------------|
| `github-owner` | `INPUT_GITHUB_OWNER` |
| `github-token` | `INPUT_GITHUB_TOKEN` |
| `include-private` | `INPUT_INCLUDE_PRIVATE` |

### 2.3 GitHub 提供的特殊环境变量

容器运行时 GitHub 会自动注入以下环境变量：

| 变量 | 说明 |
|------|------|
| `GITHUB_OUTPUT` | 写入 output 的文件路径 |
| `GITHUB_ENV` | 写入环境变量的文件路径 |
| `GITHUB_WORKSPACE` | 工作目录（挂载了仓库代码） |
| `GITHUB_ACTION` | Action 名称 |
| `GITHUB_REPOSITORY` | 当前仓库（owner/repo） |

---

## 3. action.yml 元数据文件

### 3.1 基本结构

```yaml
name: 'Action 名称'
description: 'Action 描述'
author: 'NEVSTOP-LAB'

inputs:
  input-name:
    description: '输入描述'
    required: true/false
    default: '默认值'

outputs:
  output-name:
    description: '输出描述'

runs:
  using: 'docker'
  image: 'Dockerfile'           # 或 'docker://image:tag'
  entrypoint: '/entrypoint.sh'  # 可选，覆盖 Dockerfile 的 ENTRYPOINT
  args:                         # 可选，传递给 entrypoint 的参数
    - ${{ inputs.input-name }}
  env:                          # 可选，额外环境变量
    KEY: 'value'

branding:
  icon: 'refresh-cw'            # Feather Icons 图标名
  color: 'blue'                 # blue/green/yellow/orange/red/purple/gray
```

### 3.2 关键字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | Action 显示名称，需全局唯一 |
| `description` | ✅ | Action 描述 |
| `inputs` | ❌ | 输入参数定义 |
| `outputs` | ❌ | 输出参数定义 |
| `runs.using` | ✅ | 执行方式，Docker 类型填 `docker` |
| `runs.image` | ✅ | Dockerfile 路径或预构建镜像地址 |
| `runs.entrypoint` | ❌ | 覆盖默认 entrypoint |
| `runs.args` | ❌ | 传递给 entrypoint 的参数 |
| `branding` | ❌ | Marketplace 图标和颜色 |

---

## 4. Outputs 输出机制

### 4.1 当前方式（GITHUB_OUTPUT）

在容器中通过写入 `$GITHUB_OUTPUT` 文件来设置输出：

```bash
# Bash
echo "synced-count=23" >> "$GITHUB_OUTPUT"
echo "failed-count=1" >> "$GITHUB_OUTPUT"
```

```python
# Python
import os
with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
    print("synced-count=23", file=f)
    print("failed-count=1", file=f)
```

### 4.2 在 Workflow 中引用输出

```yaml
- name: Sync repos
  id: sync
  uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  with:
    github-owner: myuser
    gitee-owner: myuser

- name: Check results
  run: echo "Synced ${{ steps.sync.outputs['synced-count'] }} repos"
```

### 4.3 已废弃方式（不要使用）

```bash
# ❌ 已废弃
echo "::set-output name=key::value"
```

---

## 5. 安全考虑

### 5.1 Token 传递

- 用户在 Workflow 中通过 `secrets` 传递 Token
- Token 作为 `INPUT_*` 环境变量进入容器
- 不要在日志中打印 Token 值
- GitHub 会自动屏蔽已知 secret 值在日志中的显示

```yaml
# 用户 Workflow 中的使用方式
- uses: NEVSTOP-LAB/GitHub-Gitee-Sync@v1
  with:
    github-token: ${{ secrets.GH_TOKEN }}
    gitee-token: ${{ secrets.GITEE_TOKEN }}
```

### 5.2 最小权限原则

- Action 只请求必要的 inputs
- 文档中明确说明所需 Token 的最小权限范围
- 不在镜像中硬编码任何凭据

---

## 6. 版本发布与 Marketplace

### 6.1 版本管理

- 使用 Git Tag 进行版本管理：`v1.0.0`
- 维护主版本标签：`v1` 指向最新的 `v1.x.x`
- 用户可以引用：`uses: owner/action@v1`

### 6.2 发布到 Marketplace

1. 仓库必须为 **公开**
2. 根目录必须有 `action.yml`
3. 在 GitHub Release 页面勾选 "Publish this Action to the GitHub Marketplace"
4. 需要启用账号的 2FA

### 6.3 Branding

使用 [Feather Icons](https://feathericons.com/) 图标集：

```yaml
branding:
  icon: 'refresh-cw'    # 同步/刷新图标
  color: 'blue'
```

可用颜色：`blue`, `green`, `yellow`, `orange`, `red`, `purple`, `gray`

图标速查表：<https://haya14busa.github.io/github-action-brandings/>

---

## 7. 与现有方案的兼容

### Docker 镜像复用

项目已设计的 Dockerfile 可以同时用于：

1. **独立 Docker 运行**：`docker run --rm -e ... github-gitee-sync`
2. **GitHub Action**：通过 `action.yml` 引用同一个 Dockerfile

关键是 `sync.py` 需要同时支持：

- 环境变量方式（Docker 独立运行、GitHub Action 的 `INPUT_*` 变量）
- CLI 参数方式（命令行直接运行）

### entrypoint.sh 桥接层

为兼容 GitHub Action 的 `INPUT_*` 环境变量命名规则，增加一个 `entrypoint.sh` 脚本，将 `INPUT_*` 变量映射为 `sync.py` 期望的环境变量：

```bash
#!/bin/sh
# 将 GitHub Action 的 INPUT_* 变量映射为标准环境变量
# GitHub Actions 将 input 名中的连字符转为下划线并大写
export GITHUB_OWNER="${INPUT_GITHUB_OWNER:-$GITHUB_OWNER}"
export GITHUB_TOKEN="${INPUT_GITHUB_TOKEN:-$GITHUB_TOKEN}"
export GITEE_OWNER="${INPUT_GITEE_OWNER:-$GITEE_OWNER}"
export GITEE_TOKEN="${INPUT_GITEE_TOKEN:-$GITEE_TOKEN}"
# ...

python /app/sync.py
```

---

## 参考链接

- [About custom actions](https://docs.github.com/en/actions/creating-actions/about-custom-actions)
- [Creating a Docker container action](https://docs.github.com/en/actions/creating-actions/creating-a-docker-container-action)
- [Metadata syntax for GitHub Actions](https://docs.github.com/en/actions/creating-actions/metadata-syntax-for-github-actions)
- [Publishing actions in GitHub Marketplace](https://docs.github.com/en/actions/creating-actions/publishing-actions-in-github-marketplace)
- [GitHub Action Branding Cheat Sheet](https://haya14busa.github.io/github-action-brandings/)
- [actions/container-action 模板](https://github.com/actions/container-action)
