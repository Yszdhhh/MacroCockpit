# 架构治理进度与优化任务池

- **隔离工作区**：`G:/Quant/MacroCockpit/.worktrees/arch-governance`（分支 `refactor/arch-governance`）
- **基线状态**：
  - `pytest`：112/112 通过（补充复制只读公开缓存 `stock_profiles_cache.json` 与 `fred_cache.json` 至 worktree 后 `test_real_local_kol_root_smoke` 亦通过）
  - `node --check web/app.js && node --check desktop/ui/app.js`：通过
  - `python -m backend.server --build-static`：通过（生成 1,583,974 字节离线包）

## 分阶段批次台账

| 批次 | 主题 | 状态 | 独立 Commit | 关键交付与验证 |
| :--- | :--- | :--- | :--- | :--- |
| **Stage 1** | 隔离 Worktree、12 维深度审计与三大治理台账 | `已完成` | `a777027` | `ARCHITECTURE_MAP.md`、`ADR.md`、`PROGRESS_AND_BACKLOG.md`（合并 Swarm + Claude `plan-split` 双轨实测） |
| **Stage 2** | 权威基础设施归一（`runtime.py` & `guardrails.py`） | `已完成` | `4dd1f2d` | 消除 28 处 `BJT`、7 处 `_safe_home`、10 处违禁词表，补齐 `guru_sources` SSRF 漏洞与 `resolve_data_dir` 隔离，116/116 通过 |
| **Stage 3** | 清零循环依赖与 `backend -> desktop` 反向依赖 | `已完成` | `6e38e93` | 46 模块 0 SCC 环路、0 `backend->desktop` 静态导入、消费端导入闭包 31->20、116/116 通过 |
| **Stage 4** | HTTP 安全归一、跨模块私有耦合收口与死代码删除 | `已完成` | 即将提交 | 抽出 L0 `http_security.py`（0 内部导入）、42 处跨模块 `_foo` 导入清零、删除 7 处零调用死函数、116/116 通过 + Kiro Opus 复审 0 BLOCKER |
| **Stage 5** | UI 设计系统归一、脱敏漏洞修复与前端去重 | `进行中` | — | 修复 `r.qty`/`d.qty` 与 `capitalInput` 静态值漏遮罩、抽出 `web/tokens.css`、收敛 `@media` 断点、双主题/移动端实测 |
| **Stage 6** | AST 架构防回归门禁、CI 接入与终局物理验收 | `待开始` | — | `tests/test_architecture_governance.py` + `.github/workflows/ci_governance.yml` + 全量回归 |

## 优化任务池（按影响面与复杂度排序）

1. **[P0 - 安全] 修复 `guru_sources.py` RSS 抓取缺少禁重定向与 IP 钉扎适配器的 SSRF 缝隙**（已合入 Stage 2 `guardrails.py`）
2. **[P0 - 隐私] 修复 `web/app.js:2423,2426,2465` 隐藏金额状态下 `r.qty`/`weight_pct`/`d.qty` 明文显示及 `web/index.html:60` 静态 `value="100000"`**（Stage 5 执行中）
3. **[P0 - 架构] 断开 3 个循环依赖 SCC 与 7 处 `backend -> desktop` 反向导入**（已合入 Stage 2–3）
4. **[P1 - SSOT] 归一 `runtime.py`、`guardrails.py`、`signal_text.py`、`article_store.py`、`http_security.py`**（已合入 Stage 2–4）
5. **[P1 - 清理] 删除 7 处已核实的零调用死代码与 42 处跨模块私有耦合**（已合入 Stage 3–4）
6. **[P1 - 一致性] 统一 `desktop/pro_server.py` `/mcp` 与 `/api/desktop/*` 鉴权与 JSON 500 异常处理，清理消费端 `server.py` 重型顶层导入，补充服务器结构化告警日志**（已合入 Stage 3–4）
7. **[P2 - 样式与体验] 对齐 `web/` 与 `desktop/ui/` 色彩 Token、隐藏箭头、右对齐数字与 canonical `@media` 断点**（Stage 5 执行中）
8. **[P2 - 门禁与复审建议] 固化 AST 架构检查单测、接入 GitHub Actions CI，并补充 `http_security`（含 `dec.eof`/`unused_data` 严格检查、desktop `/mcp` 版本与 500 兜底）专项测试**（纳入 Stage 6）
