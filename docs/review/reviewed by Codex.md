# GitHub-Gitee-Sync 代码审查（Codex）

## 总体印象
- 模块划分清晰（入口 `sync.py`、API 封装、单仓库同步模块），日志与需求文档互相引用，便于理解。
- 单元测试覆盖度高（144 个用例跑通），核心分支和错误场景都被验证。
- 凭据与重试做得较稳健：统一 `api_request` 封装、GIT_ASKPASS 避免 token 泄露、rate limit 处理防御性强。

## 改进建议（按优先级）
1) **[High] Token 校验未复用统一请求封装**（`lib/github_api.py:35-78`, `lib/gitee_api.py`）  
   目前直接用 `requests.get`，没有重试/超时/Accept 头，也没有日志脱敏。建议改为复用 `api_request` 与 `github_headers`/`access_token`，并统一错误文案，避免网络抖动或 API 版本差异导致误判。

2) **[High] 全量 mirror 每次重新 clone，成本高**（`lib/sync_repo.py:65-153`）  
   对多仓库或大仓库，每次新建临时目录 clone → push 会重复拉取同样的历史。建议提供可选的本地裸仓缓存（如 `~/.cache/github-gitee-sync/<repo>.git`），按 `git fetch --all --prune` 再 `git push --mirror`，显著降低带宽与时延。

3) **[Medium] 缺少并发/限速调度**（`sync.py:94-195` 顺序逐仓同步）  
   当前完全串行，仓库多时执行时间线性增长。可以增加可配置的并发度（线程/进程池），并在 `api_request` 暴露速率信号后做“令牌桶”或排队，避免同时命中 GitHub/Gitee 限流。

4) **[Medium] Release Asset 只按名称去重，不校验内容**（`lib/sync_repo.py:434-532`）  
   同名但尺寸/内容变更的资产不会被更新，也不会清理目标端已删除的资产。建议比较 size/etag（或哈希），发现差异时重新上传并可选删除孤立资产，保证 Release 完整一致。

5) **[Medium] 可观测性不足，失败定位粗粒度**（`sync.py` 汇总仅写 counts）  
   Action 输出只有 `synced/failed/skipped` 总数，无法直接知道哪些仓库失败/原因。建议追加每仓库结果的 JSON（如 `repo_results=[{name,status,reason}]`）到 `GITHUB_OUTPUT` 或上传 artifact，便于重试与告警。

6) **[Low] 配置值未校验，易被静默忽略**（`sync.py:41-91`）  
   `sync_extra`/`exclude_repos` 的非法值直接被丢弃。可在解析后校验白名单（releases/wiki/labels/milestones/issues）并对未知项给出显式警告或错误，降低误配置风险。

7) **[Low] 预热/健康检查缺少环境依赖提示**  
   `check_git_installed` 有覆盖，但缺少对网络连通性（github.com/gitee.com DNS/端口）和磁盘空间的早期检查。可以在开始阶段增加轻量自检，提前失败而不是在首个仓库 git/push 时才暴露问题。

## 增量审查（数据安全，2026-03）
- **日志脱敏覆盖不足**：`TokenMaskingFilter` 未包含 GitHub Actions 内置 token (`ghs_`) 与通用 `Bearer <token>` 头部，极端情况下（异常堆栈或第三方库日志带 headers）可能泄露凭据。已补充脱敏模式。（lib/utils.py）
- **CLI 传参泄露风险提醒**：直接通过命令行传递 Token 会出现在进程列表，易被旁观进程/日志采集到。现解析到 CLI Token 时发出警告，引导使用环境变量 `GITHUB_TOKEN` / `GITEE_TOKEN`。（sync.py）
