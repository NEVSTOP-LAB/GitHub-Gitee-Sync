# Docker 镜像设计

## 1. 概述

将同步脚本封装为 Docker 镜像，便于在任何环境中运行，也可用于定时任务（如 cron、Kubernetes CronJob、GitHub Actions 等）。

---

## 2. Dockerfile 设计

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

# 复制同步脚本
COPY sync.py .

# 默认执行同步脚本
ENTRYPOINT ["python", "sync.py"]
```

---

## 3. 文件结构

```
项目根目录/
├── sync.py              # 同步脚本
├── requirements.txt     # Python 依赖（requests）
├── Dockerfile           # Docker 镜像构建文件
├── .dockerignore        # Docker 构建忽略文件
└── docs/                # 文档目录
```

---

## 4. requirements.txt

```
requests>=2.28.0,<3.0.0
```

---

## 5. .dockerignore

```
.git
docs
README.md
*.md
__pycache__
*.pyc
.env
```

---

## 6. 构建与运行

### 6.1 构建镜像

```bash
docker build -t github-gitee-sync .
```

### 6.2 运行容器

```bash
# 使用环境变量传递参数
docker run --rm \
  -e GITHUB_OWNER=myuser \
  -e GITHUB_TOKEN=ghp_xxxx \
  -e GITEE_OWNER=myuser \
  -e GITEE_TOKEN=xxxxx \
  github-gitee-sync

# 使用 CLI 参数
docker run --rm \
  github-gitee-sync \
  --github-owner myuser \
  --github-token ghp_xxxx \
  --gitee-owner myuser \
  --gitee-token xxxxx \
  --exclude-repos "old-repo,deprecated-repo"

# 使用 .env 文件
docker run --rm --env-file .env github-gitee-sync
```

### 6.3 使用 .env 文件

```env
GITHUB_OWNER=myuser
GITHUB_TOKEN=ghp_xxxx
GITEE_OWNER=myuser
GITEE_TOKEN=xxxxx
ACCOUNT_TYPE=user
INCLUDE_PRIVATE=true
EXCLUDE_REPOS=old-repo,deprecated-repo
```

---

## 7. 定时运行方案

### 7.1 Linux Cron

```cron
# 每天凌晨 2 点同步
0 2 * * * docker run --rm --env-file /path/to/.env github-gitee-sync >> /var/log/sync.log 2>&1
```

### 7.2 Docker Compose + Cron（可选扩展）

```yaml
version: '3'
services:
  sync:
    build: .
    env_file: .env
    restart: "no"
```

### 7.3 GitHub Actions（可选扩展）

```yaml
name: Sync to Gitee
on:
  schedule:
    - cron: '0 2 * * *'  # 每天 UTC 2:00
  workflow_dispatch:       # 支持手动触发

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run sync
        run: |
          pip install requests
          python sync.py
        env:
          GITHUB_OWNER: ${{ github.repository_owner }}
          GITHUB_TOKEN: ${{ secrets.GH_TOKEN }}
          GITEE_OWNER: ${{ secrets.GITEE_OWNER }}
          GITEE_TOKEN: ${{ secrets.GITEE_TOKEN }}
```

---

## 8. 安全注意事项

1. **不要将 Token 写入 Dockerfile 或提交到版本控制**
2. 使用环境变量或 Docker Secrets 传递 Token
3. 使用 `.env` 文件时确保在 `.gitignore` 中排除
4. 构建的镜像中不包含 Token 信息

---

## 9. 基础镜像选择

| 镜像 | 大小 | 说明 |
|------|------|------|
| `python:3.11-slim` | ~150MB | **推荐**，包含 pip，体积适中 |
| `python:3.11-alpine` | ~50MB | 更小，但可能有兼容性问题 |
| `python:3.11` | ~900MB | 完整版，体积大，不推荐 |

推荐使用 `python:3.11-slim`，平衡体积和兼容性。
