# MacroCockpit 架构地图与治理诊断基线（2026-11-28）

> 本文件在隔离 worktree `G:/Quant/MacroCockpit/.worktrees/arch-governance`（分支 `refactor/arch-governance`）中维护，所有结论均基于源码、AST 依赖扫描、Swarm 双路审计与基线实测。

---

## 1. 业务全景与运行时拓扑

MacroCockpit 2.0 是一个跨市场（美股、A股·港股、AI 前沿、加密资产、全球宏观）情报工作台与个人持仓风控驾驶舱，生产拓扑包含三条主链路：

1. **双云实时情报采集与原子世代发布**：
   - **海外节点**（GitHub Actions `.github/workflows/intl_collector.yml` 每 20 分钟 / 海外 VPS `macro-collector-intl.service`）：执行 `python -m backend.cloud_collector --role intl-push`，采集海外受限源（FRED/Yahoo/Curated/SEC/Guru）并以 Gzip 推送至 `POST /api/v2/ingest-intl`。
   - **国内主站合并与发布**（`macro-collector-cn.service`）：执行 `python -m backend.cloud_collector --role cn-merge-publish`（以及快速轮询 `--role eqn-fast-sync`），合并国内 25 路财经/AI/官方实时源、社区情绪与海外分片，经 `kazike_engine` + `event_feed` + `curation` 去重与评分后，由 `public_snapshot.publish_generation()` 原子切换 `data/generations/`（保留最近 3 代）。
   - **消费端服务**（`backend/server.py --role consumer`）：零聚合冷启，严格读取已发布世代或兼容快照，受 `MACROCOCKPIT_ACCESS_PASSWORD`、Bearer RBAC 令牌（`READ_ONLY` / `PORTFOLIO_RW` / `SYNC_PUBLISH`）与 `X-Macro-Request: 1` CSRF 防护门禁保护。
2. **本机全量聚合、同步与定时推流**（`%LOCALAPPDATA%/hermes/scripts/`）：
   - `vps_intel_sync_wrapper.py` -> `python -m backend.vps_sync --refresh-state --sync-portfolio`：刷新全量 `signal_aggregator.generate_cockpit_state()`，通过 `POST /api/v2/publish` 推送脱敏公共快照与最多 40 篇非私密文章；并通过 `PORTFOLIO_RW` 令牌双向防倒退同步 `user_journal.db`。
   - `forum_pulse_wrapper.py` -> `python -m backend.forum_sentiment`：采集 5 路社区讨论与 A/港股资金面并输出不超过 `40 KB` 契约块。
   - `notion_intel_sync_wrapper.py` -> `backend/notion_sync.py`：双向同步 Notion 投研看板与文章库。
   - `periodic_report_wrapper.py` -> `python -m backend.periodic_report`：生成周/月度深研报告与提问词；按需触发 `deep_research_runner`（DemoLab 3 路并发检索）。
   - `backend/mobile_push.py` & `backend/backup_journal.py`：仅私聊目标推送与 SQLite WAL 在线热备。
3. **双端用户界面与 MCP 接口**：
   - **Web 驾驶舱**（`web/index.html`, `web/styles.css`, `web/app.js`）：无构建工具直出，支持 `backend.server --build-static` 打包为单文件只读离线快照 `dist/macro_cockpit_offline.html`。
   - **桌面专业终端**（`desktop/pro_server.py` + `desktop/ui/`）：本地回环端口 `18790`，集成 `openbb_bridge`、`acp_bridge`（本地 AGY CLI/ACP）、`multi_agent_debate` 五角色多空辩论，以及含桌面扩展的 12 工具 MCP。
   - **MCP `2025-11-25`**（`backend/mcp_server.py` + `backend/mcp_stdio.py`）：8 个标准后端工具 + 4 个桌面扩展工具（`--include-desktop-tools`）。

---

## 2. 目标分层架构与当前依赖违规

### 2.1 目标严格单向分层（DAG）

| 层级 | 定位 | 目标模块 | 依赖约束 |
| :--- | :--- | :--- | :--- |
| **L0 基座** | 时钟、路径、环境配置、锁、安全文件 IO、违禁词与隐私/URL 红线 | `backend/runtime.py`, `backend/guardrails.py`, `backend/http_security.py` | **零内部模块依赖** |
| **L1 存储与文本原语** | SQLite 台账、文章仓解析、公共快照世代、通用行情/SEC/RSS 文本原语 | `backend/journal_store.py`, `backend/portfolio_store.py`, `backend/valuation.py`, `backend/article_store.py`, `backend/signal_text.py`, `backend/public_snapshot.py`, `backend/agy_runtime.py` | 仅依赖 L0 与同层更低阶模块（无环） |
| **L2 采集、分析与业务引擎** | FRED/行情/RSS/Guru/KOL/SEC/舆情/事件流/打分/精选/深研/日线建议 | `fred_and_market`, `market_quotes`, `sec_edgar`, `rss_sources`, `guru_sources`, `kazike_engine`, `zh_brief`, `event_feed`, `curation`, `kol_intel_engine`, `forum_sentiment`, `discussion_temperature`, `correlation_engine`, `source_matrix`, `stock_research`, `market_synthesis`, `signal_advisor`, `periodic_report`, `deep_research_runner`, `calendar_events`, `import_positions` | 仅向下依赖 L0/L1/L2 前序模块（严格 DAG） |
| **L3 编排、同步与服务入口** | 聚合编排、双云合并、同步推送、HTTP/MCP 服务 | `signal_aggregator`, `cloud_collector`, `vps_sync`, `mobile_push`, `notion_sync`, `backup_journal`, `mcp_server`, `mcp_stdio`, `server` | 严禁导入 `desktop/*` |
| **L4 桌面与表现层** | 桌面专业终端、多智能体辩论、OpenBB 桥接与静态前端 | `desktop/openbb_bridge.py`, `desktop/acp_bridge.py`, `desktop/multi_agent_debate.py`, `desktop/mcp_extensions.py`, `desktop/pro_server.py`, `web/`, `desktop/ui/` | 仅单向依赖 `backend` 公开接口 |

### 2.2 基线实测循环依赖与跨层反向依赖（共 3 组强连通环 + 7 处跨包反向导入）

1. **6 模块环路 SCC-1**：`event_feed` -> `{kazike_engine, public_snapshot, zh_brief}`；`kazike_engine` -> `zh_brief`；`zh_brief` -> `public_snapshot`；`public_snapshot` -> `{event_feed, signal_advisor}`；`signal_advisor` -> `{kol_intel_engine, public_snapshot}`；`kol_intel_engine` -> `public_snapshot`。
   - **根因**：14 个模块为复用 `file_lock` 与 `has_private_article_*` 导入了高层的 `public_snapshot`，而 `public_snapshot` 反向懒加载 `event_feed` 与 `signal_advisor`。
2. **2 模块环路 SCC-2**：`guru_sources <-> rss_sources`。
   - **根因**：`guru_sources` 导入 `rss_sources` 的文本/XML/SSRF 原语，而 `rss_sources.build_opml_export()` 反向导入 `guru_sources.GURU_ARTICLE_FEEDS`。
3. **2 模块环路 SCC-3**：`deep_research_runner <-> periodic_report`。
   - **根因**：`periodic_report` 调用 `deep_research_runner.run_demolab_deep_research`，而 `deep_research_runner` 反向导入 `periodic_report.ingest_deep_research_markdown`、`_scrub_forbidden` 与路径常量。
4. **`backend -> desktop` 跨包反向导入（7 处，均为函数内懒导入）**：
   - `backend/deep_research_runner.py`（L107, L201, L223）-> `desktop.acp_bridge.{resolve_agy_executable, ask_agy}`
   - `backend/mcp_server.py`（L1200, L1208, L1220, L1271）-> `desktop.{openbb_bridge, acp_bridge, multi_agent_debate}`

---

## 3. 状态所有权地图（State Ownership）

| 状态载体 | 权威写者 | 消费者 | 隔离与并发约束 | 当前规范偏离 |
| :--- | :--- | :--- | :--- | :--- |
| `data/user_journal.db` | `JournalStore`、`PortfolioStore` | `server`, `pro_server`, `mcp_server`, `vps_sync`, `backup_journal`, `desktop/*` | 私密唯一真相源；WAL 模式 + `_PATH_LOCKS_GUARD` + `expected_rev` 乐观锁；严禁进公共快照 | 部分 `desktop/*` 与 `pro_server.py` 忽略 `MACROCOCKPIT_DATA_DIR` 硬编码 `root/data` |
| `data/generations/` + `manifest.json` | `public_snapshot.publish_generation()` | `server`（consumer 优先读世代）、`cloud_collector`、`mcp_server` | `_PUBLISH_LOCK` + `file_lock` 跨进程串行，保留最近 3 代 | `server.py` 直接导入 `public_snapshot` 私有符号 `_PUBLISH_LOCK`、`_current_manifest`、`_generation_timestamp` |
| `data/cockpit_state.json` | 兼容快照（`signal_aggregator` / `public_snapshot`） | `server`（standalone 回退）、`vps_sync`、`mobile_push`、`notion_sync` | 公共脱敏边界由 `project_public_state()` 保证 | **多写者分裂**：`desktop/multi_agent_debate.py:L613-634` 与 `server.py:L493,1517` 直接读写该文件 |
| `articles/*.md` | `rss_sources`、`guru_sources`、`periodic_report`、`multi_agent_debate` | `signal_aggregator`、`cloud_collector`、`server`、`vps_sync` | 文件名白名单 `safe_article_filename()` + 隐私拦截 `has_private_article_content()` | 解析、目录白名单、安全写盘与去重逻辑分散在 5+ 个模块 |
| `data/*_cache.json` 与账本（15+ 文件） | 各自领域模块 | 采集与引擎层 | `zh_brief`、`event_feed`、`public_snapshot` 使用 `file_lock` | 多数 cache 使用无锁或仅线程锁读改写；11 处散落裸 `os.replace` |

---

## 4. 重复实现、私有耦合与规范偏离清单

1. **基础运行时重复**：
   - `BJT` / `_BJT` 在 **28** 个文件中重复定义；`_safe_home` 在 **7** 个文件中重复定义；`COCKPIT_ROOT` / `DATA_DIR` 在 **16+** 个文件各自解析（`mcp_stdio.py` 跳过 `MACROCOCKPIT_ROOT`；`notion_sync.py`、`desktop/multi_agent_debate.py`、`desktop/pro_server.py` 忽略 `MACROCOCKPIT_DATA_DIR`）。
   - `atomic_write_json` / `_atomic_write_json` / `_atomic_write_text` 4 套并存 + 11 处裸 `os.replace`；`_read_json` 6 处重复；`_finite_float` / `_safe_float` / `_clamp` 多处重复；16 个模块仅为了拿 `DATA_DIR` 与 `atomic_write_json` 反向依赖业务模块 `fred_and_market`。
2. **违禁词与安全红线重复/漂移**：
   - `FORBIDDEN_TERMS` 与清洗函数在 **10+** 个文件中各自维护且替换词已出现漂移（`市场阶段` vs `市场周期`、`参考摘要` vs `规则摘要` vs `""`、`—` vs `样本积累`）。
   - **SSRF 防护漏洞偏离**：`rss_sources.py` 实现了完整的多层 SSRF 防护（`_is_forbidden_ip`、`_is_safe_public_url`、`_make_pinned_adapter`、`_http_get_no_redirect`），但 `guru_sources.py:L575-586` 使用裸 `requests.get` 且未禁用重定向和固定 IP 适配器，存在跳转绕过与 DNS 重绑定缝隙。
3. **HTTP 安全与 RBAC 重复/不一致**：
   - `_const_eq` 3 处重复（`server.py`、`mcp_server.py`、`pro_server.py`）；Bearer 解析两套（`server._resolve_bearer_token` vs `mcp_server._resolve_bearer_auth`）；`_read_json_body`、`_is_loopback_origin` / `_origin_allowed` 各自实现；`_bucket_leverage`（`mcp_server:147`）与 `_band_leverage`（`openbb_bridge:454`）重复。
   - `server.py` 多处 GET 路由手写角色判断而未复用 `_require_role`；`desktop/pro_server.py` `/mcp` 绕过了 `mcp_server.handle_mcp_http_request()` 的 `Content-Type` 与 `MCP-Protocol-Version` 检查，且 `/api/desktop/openbb`、`/api/desktop/debate` 缺少 JSON 500 兜底；3 个 HTTP 服务均静默吞掉 `log_message()`（全仓 `logging` 导入数为 0，存在 275 处宽泛 `except` 与 94 处裸 `pass`）。
4. **跨模块私有符号耦合、消费端导入膨胀与重复业务逻辑**：
   - 存在 **53 处**（生产代码 48 处）跨模块私有 `_foo` 导入（其中 `cloud_collector.py` 占 23 处）。
   - **消费端导入过重与伪兼容层**：`server.py` 顶层导入 `signal_aggregator` 导致 `import backend.server` 直接加载 28 个后端模块（含全部采集器）；全仓存在 22 处内部模块 `try/except ImportError` 兜底（其中 `server.py:56-80` 甚至另起了一套 `parse_markdown_article` 平行实现）；`safe_article_filename` 在 `server._lookup_article_strict` 与 `portfolio_store` 各有一份手写校验副本，另有 `vps_sync._safe_article_name` 与 `PortfolioStore._connect` 纯透传包装。
   - `cloud_collector.run_cn_merge_and_publish` 与 `signal_aggregator.generate_cockpit_state` 并行编排且 `coverage` 字段与世代计数已有轻微行为分叉；`guru_sources` 重复 `rss_sources` 的 XML 解析、编码解码与 Markdown 落盘；`sec_edgar.SEC_CN_NAMES` 与 `stock_research.CORE_RESEARCH_STOCKS` 重复维护 20 只美股中文名；`extract_tickers` 存在 3 份实现。
5. **经确认的零调用死代码与待复核候选**：
   - 已复核（工作区 + Hermes 均 0 调用）：`cloud_collector._bump_producer_generation`、`event_feed._load_ledger`、`notion_sync._build_home_callout_texts`、`rss_sources.collect_equation_news_signals`、`web/app.js:formatMoney`。
   - 待进一步复核外部调用后清理：`event_feed.load_public_event_cards_from_ledger:1576`、`JournalStore._seed:236`、`NotionSyncClient.sync_home_callout:870`、`PortfolioStore.create_credential:391`。
6. **UI 设计系统与脱敏偏离**：
   - `desktop/ui/styles.css` 复制 `web/styles.css` Token 且出现数值漂移（`--mast-bg` `0.90` vs `0.92`，缺失 `--tabbar-bg`、`:focus-visible` 等），两处 CSS 仍残留硬编码 `#fff` / `#16140F` 与 `rgba(...)` 阴影。
   - `web/styles.css` 存在 22 个 `@media` 块分散在 **12** 种宽度断点；`desktop/ui/styles.css` 额外引入第 13 个 `1040px` 断点且 `.kpi-val` 缺少右对齐、`input[type="number"]` 未纳入统一样式。
   - **真实脱敏漏点**：`web/app.js:L2423, L2426, L2465` 在 `renderPortfolioView()` 中当 `!appState.showAmounts` 时未遮罩 `r.qty`、`weight_pct` 占比与 `d.qty`；`web/index.html:L60` 静态硬编码 `value="100000"`；`web/app.js` 使用 `c["sl" + "eeve"]` 绕过静态词扫描。
7. **自动化防回归与 Ruff 基线（已实测）**：
   - `ruff 0.15.16` 基线：25 条 `F` 类告警、176 条 `C901` 复杂度 > 10 函数；`tests/` 下存在 5 份重复的 `_assert_no_forbidden_recursive`；4 处生产代码检查 `PYTEST_CURRENT_TEST`。
   - 当前 CI（`.github/workflows/intl_collector.yml`）仅跑 14 个测试文件中的 2 个（Python 3.12），未覆盖全量 pytest、`node --check` 与 `--build-static`。

---

## 5. 待确认事项（非阻塞，保留契约待业务拍板）

1. **历史兼容字段与路由退役窗口**：
   - `vps_sync.py:L1020-1023` 对 `/api/sync`（HTTP 404 回退旧路由）仍在 `tests/test_macro_cockpit.py` 中测试覆盖；建议确认线上 VPS 全面切换 `/api/v2/publish` 后再行下线。
   - `web/app.js` 内部数据结构仍保留 `c["sl" + "eeve"]` 属性键以读取历史状态 JSON；待全链路统一迁移为 `strategy_bucket` 后可彻底清除。
2. **`server.py` `GET /api/state` vs `GET /api/journal` 对 `PORTFOLIO_RW` Bearer 令牌的边界**：
   - 当前 `GET /api/journal` 允许 `PORTFOLIO_RW` Bearer 令牌访问，而 `GET /api/state` 仅对 `cookie`/`local_open` 附加 `journal` 字段（对 `PORTFOLIO_RW` Bearer 剥离）。本次治理保持现有契约不变，需业务确认是否统一。
3. **`test_real_local_kol_root_smoke` 对本机非托管缓存的隐式依赖**：
   - 该烟雾测试在隔离 git worktree 中依赖未入库的 `data/stock_profiles_cache.json` 与 `data/fred_cache.json` 才能通过 `verified_kols >= 1`；建议后续为烟雾测试增加显式夹具或环境跳过说明。
4. **违禁词统一译法与 `producer_generation.json` 退役窗口**：
   - `Regime`、`待补` 与方法论类提示词在旧代码中有多种近义译法（如 `市场周期`/`市场阶段`、`—`/`样本积累`）；本次在统一为直白中文的同时保持现有测试断言全绿，最终标准译法以产品审定为准。
   - 废弃 `producer_generation.json` 与 `cockpit_state.json` 兼容副本涉及云端 CAS 与外部 Hermes/Notion 读取方，列为后续单独演进项，不在本轮破坏现有外部契约。
