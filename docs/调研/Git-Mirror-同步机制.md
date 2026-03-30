# Git Mirror 同步机制调研

## 概述

本项目需要在 GitHub 和 Gitee 之间同步全部仓库的所有分支和标签。最佳实践是使用 Git 的 **mirror** 模式进行单向同步（GitHub → Gitee）。

---

## 1. 核心命令

### 1.1 镜像克隆（Mirror Clone）

```bash
git clone --mirror <source-repo-url> <local-dir>
```

- `--mirror` 会克隆所有 refs（分支、标签、远程引用等）
- 创建的是一个 **bare** 仓库（没有工作目录）
- 适合用于完整镜像同步

### 1.2 镜像推送（Mirror Push）

```bash
cd <local-dir>
git push --mirror <target-repo-url>
```

- `--mirror` 会推送所有 refs，并删除目标端不存在于源端的 refs
- **注意**：这是破坏性操作，会覆盖目标仓库的所有内容

### 1.3 增量更新

对于已经镜像过的仓库，后续同步只需：

```bash
cd <local-dir>
git fetch --prune origin        # 从源端获取更新，并清理已删除的分支
git push --mirror <target-url>  # 推送到目标端
```

---

## 2. 同步方案选择

### 方案 A：每次全新镜像克隆 + 推送（推荐）

```bash
# 1. 镜像克隆 GitHub 仓库
git clone --mirror https://github.com/{owner}/{repo}.git /tmp/{repo}.git

# 2. 推送到 Gitee
cd /tmp/{repo}.git
git push --mirror https://gitee.com/{owner}/{repo}.git

# 3. 清理临时目录
rm -rf /tmp/{repo}.git
```

**优点**：
- 逻辑简单，每次都是全量同步
- 无需维护本地状态
- 适合定期运行的脚本/容器

**缺点**：
- 大仓库每次都要完整克隆，耗时较长

### 方案 B：维护本地镜像 + 增量更新

```bash
# 首次：镜像克隆
git clone --mirror https://github.com/{owner}/{repo}.git /data/{repo}.git

# 后续：增量更新
cd /data/{repo}.git
git remote update --prune
git push --mirror https://gitee.com/{owner}/{repo}.git
```

**优点**：
- 增量更新，速度更快
- 减少网络传输

**缺点**：
- 需要持久化存储本地镜像
- Docker 容器中需要挂载数据卷

---

## 3. 认证方式

### HTTPS + Token（推荐）

在 URL 中嵌入 Token 进行认证：

```bash
# GitHub
git clone --mirror https://<github_token>@github.com/{owner}/{repo}.git

# Gitee
git push --mirror https://<gitee_token>@gitee.com/{owner}/{repo}.git
```

### SSH Key

需要在容器中配置 SSH 密钥，复杂度较高，不推荐在 Docker 场景下使用。

---

## 4. 在 Python 中执行 Git 命令

### 4.1 使用 subprocess（推荐）

```python
import subprocess

def run_git(args, cwd=None):
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout

# 镜像克隆
run_git(["clone", "--mirror", source_url, local_path])

# 镜像推送
run_git(["push", "--mirror", target_url], cwd=local_path)
```

**优点**：
- 无额外依赖
- 直接调用系统 git，兼容性好

### 4.2 使用 GitPython

```python
from git import Repo

repo = Repo.clone_from(source_url, local_path, mirror=True)
repo.git.push("--mirror", target_url)
```

**优点**：
- API 更 Pythonic

**缺点**：
- 额外依赖
- 某些场景下 mirror 参数支持不完善

### 推荐：使用 subprocess

对于纯镜像同步场景，subprocess 更可靠、无额外依赖。

---

## 5. 处理异常情况

### 5.1 目标仓库不存在

需要先通过 Gitee API 创建仓库，然后再执行 `git push --mirror`。

### 5.2 空仓库推送

空仓库（无任何 commit）不能 `push --mirror`，需要跳过或做特殊处理。

### 5.3 大文件/LFS

如果源仓库使用了 Git LFS，镜像克隆不会自动处理 LFS 对象，需要额外处理（本项目暂不考虑）。

### 5.4 Token 泄露风险

在 URL 中嵌入 Token 时，需要注意：
- 不要在日志中打印完整 URL
- 使用环境变量传递 Token
- Git 的 `credential.helper` 可作为替代方案

---

## 6. 推送前 refs 比较优化

每次运行都执行完整 `git push --mirror` 会产生不必要的网络流量和目标端写操作。为此在推送前先对比两端 refs，若已完全一致则直接跳过推送。

### 实现方式

```python
# 获取本地镜像的所有 refs
local_refs = subprocess.run(["git", "show-ref"], ...)

# 获取目标端的所有 refs
remote_refs = subprocess.run(["git", "ls-remote", "--heads", "--tags", target_url], ...)

# 解析为 {ref_name: sha} 字典后比较
# 本地 refs ⊆ 远端 refs 且所有 SHA 相同 → 跳过推送
```

### 规则

- 本地所有 refs 都已存在于目标端且 SHA 相同 → 跳过推送，输出 `"Both sides already in sync, skipping push ⏭️"`
- 目标端独有的 refs（例如目标端独立提交）不视为差异，保持增量策略
- 剥离型标签（`refs/tags/v1.0^{}`）自动过滤，仅比较轻量/注解标签指针
- 任何 `git show-ref` 或 `git ls-remote` 报错均回退到正常推送，不影响同步正确性

### 适用范围

| 场景 | 说明 |
|------|------|
| 代码仓库同步（`mirror_sync`） | 比较所有 branches + tags |
| Wiki 仓库同步（`sync_wiki`） | 同样比较 wiki refs，一致则跳过 `git push --all/--force` |

---

## 7. 总结

| 维度 | 推荐方案 |
|------|---------|
| 同步方式 | `git clone --mirror` + `git push --mirror` |
| 认证方式 | HTTPS + Token（嵌入 URL） |
| Python 实现 | `subprocess` 调用系统 git |
| 增量 vs 全量 | Docker 场景推荐全量（方案 A）；持久化场景可选增量（方案 B） |

---

## 参考链接

- [Git clone --mirror 文档](https://git-scm.com/docs/git-clone#Documentation/git-clone.txt---mirror)
- [Git push --mirror 文档](https://git-scm.com/docs/git-push#Documentation/git-push.txt---mirror)
- [Synchronizing multiple remote Git Repositories (Microsoft ISE)](https://devblogs.microsoft.com/ise/synchronizing-multiple-remote-git-repositories/)
