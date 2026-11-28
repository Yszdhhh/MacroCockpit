# AGENTS.md — MacroCockpit 工程与运维实操规范

> 本文档仅记录经代码、配置与本机运行环境核实的工程事实与操作边界，严禁写入任何密钥、Token、密码或私人/团队非工程信息。

---

## 1. 服务入口与运行模式（已核实）

- **本地无密驾驶舱**（[`start_local_cockpit.ps1`](start_local_cockpit.ps1)）：
  - 默认监听 `http://127.0.0.1:8788/`，执行 `python -m backend.server --host 127.0.0.1 --port 8788 --role standalone --no-auth`（设置 `PYTHONUTF8=1`）。
  - 优先使用 `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`。
- **桌面专业终端**（[`start_desktop_pro.ps1`](start_desktop_pro.ps1)）：
  - 默认监听 `http://127.0.0.1:18790/`，执行 `python -m desktop.pro_server --host 127.0.0.1 --port 18790`。
- **消费端与鉴权门禁**（[`backend/server.py`](backend/server.py)）：
  - `--role consumer` 启动时强制要求同时提供 `MACROCOCKPIT_ACCESS_PASSWORD` 与 `MACROCOCKPIT_SYNC_TOKEN`，且拒绝 `--refresh-once`。
  - 非 loopback 地址绑定（非 `127.0.0.1` / `localhost` / `::1`）强制要求 `MACROCOCKPIT_ACCESS_PASSWORD`。

---

## 2. 双云采集与 VPS 同步架构（已核实）

- **国内主站合并与发布**（[`backend/cloud_collector.py`](backend/cloud_collector.py)、[`deploy/systemd/macro-collector-cn.service`](deploy/systemd/macro-collector-cn.service)）：
  - 部署目录 `/opt/macro-intel`，执行 `python3 -m backend.cloud_collector --role cn-merge-publish`。
  - 采集国内可达的 25 路财经/AI/官方实时源，合并海外节点推送分片后，经 [`backend/kazike_engine.py`](backend/kazike_engine.py)（URL 规范化 + 14 天事件聚类 + 中英数值锚点去重 + 五轴盲评）并通过 [`backend/public_snapshot.py`](backend/public_snapshot.py) 的 `publish_generation()` 原子切换发布世代（保留最近 3 代）。
- **海外分片采集与回传**（[`.github/workflows/intl_collector.yml`](.github/workflows/intl_collector.yml)、[`deploy/systemd/macro-collector-intl.service`](deploy/systemd/macro-collector-intl.service)）：
  - GitHub Actions 每 20 分钟运行一次（或海外 VPS `/opt/macro-intel-intl`），执行 `python -m backend.cloud_collector --role intl-push`，采集海外受限的 12 路源并 Gzip 推送至 `POST /api/v2/ingest-intl`（默认目标 `https://news.shuashuabao.xyz/api/v2/ingest-intl`）。
- **Windows 主机向 VPS 同步**（[`backend/vps_sync.py`](backend/vps_sync.py)、`AppData\Local\hermes\scripts\vps_intel_sync_wrapper.py`）：
  - 定时包装器调用 `python -m backend.vps_sync --refresh-state --sync-portfolio`，加载环境时永远跳过 `MACROCOCKPIT_ACCESS_PASSWORD` 与 `MACROCOCKPIT_READ_TOKEN`。
  - 公开部分优先经 HTTP `POST /api/v2/publish` 推送经 `project_public_state(include_candidates=True)` 脱敏后的公共快照及最多 `--max-articles 40` 篇文章（仅当返回 HTTP 404 时兼容回退 `/api/sync`）；仅在 HTTP 连接失败（排除 `SSLError` / `ProxyError`）且未传入 `--no-ssh-fallback` 时才回退 `ssh tencent-vps` 远端执行 `publish_generation()`。
  - 私密持仓部分（仅当显式开启 `--sync-portfolio` 时）经专用 `PORTFOLIO_RW` 令牌（`MACROCOCKPIT_RW_TOKEN`）调用 `GET /api/v2/workspace`、`GET /api/v2/preferences` 与 `POST /api/v2/changes`，将本机 `data/user_journal.db` 的账户、现货、合约、盯盘规则与自选防倒退同步至线上受登录密码保护的 `user_journal.db`；公开快照、`/api/v2/publish`、`READ_ONLY` 令牌与离线快照仍严禁包含任何私人持仓。

---

## 3. 四角色隔离、公共快照边界与文案红线

- **私有数据隔离**（[`backend/public_snapshot.py`](backend/public_snapshot.py)）：
  - `_PRIVATE_TOP_LEVEL_KEYS = {"journal", "portfolio", "preferences", "workspace", "credentials"}` 永远不进入 `project_public_state()`、Git 公共仓库与离线快照 `dist/macro_cockpit_offline.html`。
  - 含持仓净值占比、私人持仓明细或密钥正则的文章由 `has_private_article_content()` 拦截，不随公开快照同步。
  - `portfolio_risk` 对只读/公共端仅暴露汇总区间，禁止返回真实持仓明细与金额；前端开启隐藏金额时同步清空 DOM 明文数值。
- **MCP (`2025-11-25`) 权限分级**（[`backend/mcp_server.py`](backend/mcp_server.py)）：
  - 只读工具：`market_snapshot`、`signal_search`、`research_get`、`portfolio_risk`、`calendar_list`。
  - 读写工具：`preferences_get`、`import_preview`、`workspace_commit`（需对应读写权限）。
- **公共展示违禁词门禁**（[`backend/kol_intel_engine.py`](backend/kol_intel_engine.py)、[`backend/periodic_report.py`](backend/periodic_report.py)、[`DESIGN.md`](DESIGN.md)）：
  - 生成物与前端页面严禁出现 `FORBIDDEN_TERMS`（`Sleeve`、`E21`、`E32`、`E33`、`Tier-1`、`Regime`、`arbitration`、`测算口径`、`公式解释`、`公式说明`、`逻辑解释`、`联动说明`、`方法论说明`、`待补`、`待刷新`、`N/A`），须通过 `scrub_forbidden()` 清洗为直白中文业务用语。

---

## 4. KOL 追踪、舆情分歧与周期深研链路

- **KOL 原始库与调度治理**（`AppData\Local\hermes\scripts\news\x-kol-notes\`、`run_kol_round_robin_wrapper.py`）：
  - `DEFAULT_KOL_DIR` 默认指向 `AppData\Local\hermes\scripts\news\x-kol-notes`（支持 `MACROCOCKPIT_KOL_DIR` 覆盖）。
  - 当前 `config.json` 收录 **129** 个 X 账号，分为 5 个板块（`us-stocks`: 67、`macro`: 30、`crypto`: 18、`quant`: 8、`investing`: 6）与 6 类角色（`ai_semi`: 35、`quant_trader`: 25、`macro_guru`: 25、`news_wire`: 17、`crypto_alpha`: 16、`hedge_fund`: 11），通过 `daily_batches.json` 分 4 批次轮转抓取。
  - `run_kol_round_robin_wrapper.py` 强制以 `--fetch-only` 静默落盘，不单独发飞书卡片，统一由决策简报与驾驶舱消费。
- **KOL 回测与胜率评估**（[`backend/kol_intel_engine.py`](backend/kol_intel_engine.py)）：
  - 严格执行零前视门禁（`mtime <= as_of_ts`）、排除 `MEDIA_WIRE_HANDLES` 纯快讯号、同作者同标的 5 个自然日内同向观点合并去刷屏，且胜率徽章受 `MIN_VERIFIED_SAMPLE = 5` 样本门槛约束。
- **社区情绪、信源矩阵与周月度深研**：
  - [`backend/forum_sentiment.py`](backend/forum_sentiment.py)：5 路社区情绪（雪球、东财股吧人气榜、百度/微博/头条/抖音/知乎/华尔街见闻/财联社全网热榜、Reddit/HN、小红书）+ A股/港股真金白银资金大盘（两市成交、两融余额、港股通南向资金、格隆汇）聚合与分歧对照，单次输出体积上限 `MAX_OUTPUT_BYTES = 40 KB`。
  - [`backend/source_matrix.py`](backend/source_matrix.py)：6 大资产类别信源导航与中美产业链映射（联动 `data/industry_chain_map.json`）。
  - [`backend/periodic_report.py`](backend/periodic_report.py)：生成周/月报并内嵌 Deep Research 提问词；默认禁用自动付费 API 调用（`--status` 可查本机能力，无 `GEMINI_API_KEY` 时推荐 `manual_prompt_copy`）。

---

## 5. 常用构建与验证命令（Windows）

- **全量刷新状态与文章聚合**：`python -m backend.signal_aggregator`
- **构建公共离线快照 HTML**：`python -m backend.server --build-static`
- **运行单元测试**（避开系统临时目录权限锁）：`python -m pytest tests/ --basetemp=.pytest_tmp -v`
- **检查前端 JS 语法**：`node --check web/app.js`
- **查询归档历史记忆**（严格只读离线库）：`python "%USERPROFILE%\.gemini\config\plugins\linus-ops-kit\skills\hermes-history-search\scripts\search_history.py" "<关键词>"`

---

## 6. 核心基座单一真相源与架构门禁（已核实）

- **L0 零内部依赖底座**：
  - [`backend/runtime.py`](backend/runtime.py)：统一提供 `UTC`/`BJT` 时区、路径解析（`resolve_cockpit_root`、`resolve_data_dir`、`resolve_user_home`、`resolve_kol_dir`）、可重入文件锁 `file_lock` 与原子写盘 `atomic_write_json`/`atomic_write_text`。
  - [`backend/guardrails.py`](backend/guardrails.py)：`FORBIDDEN_TERMS` 清洗（`scrub_forbidden`、`scrub_quant_thesis`）、隐私文章/正则拦截及 SSRF 安全抓取 `safe_fetch_public_url` 的唯一权威来源。
  - [`backend/http_security.py`](backend/http_security.py)：统一 `const_eq`、五角色/Bearer 解析 `resolve_bearer_auth`、回环/Origin/CORS 校验及有界 gzip 解压 `decompress_gzip_bounded`。
- **双端共用设计令牌**：[`web/tokens.css`](web/tokens.css) 为 Web 与 Desktop Pro 唯一的亮/暗主题令牌定义（离线静态包由 `backend.server.build_static_bundle()` 自动内联，桌面端由 `desktop/pro_server.py` `/tokens.css` 路由直出）。
- **自动化防回归门禁**：[`tests/test_architecture_governance.py`](tests/test_architecture_governance.py) 与 [`.github/workflows/ci_governance.yml`](.github/workflows/ci_governance.yml) 强制校验：全仓 0 循环依赖、0 `backend -> desktop` 静态导入、0 生产跨模块私有 `_foo` 导入、SSOT 唯一归属、UI 零违禁词、Ruff `F821,F401,F811,F541` 清零及全部 10 个 CLI `--help` 烟雾通过。
- **架构治理命令**：`python -m pytest tests/test_architecture_governance.py --basetemp=.pytest_tmp -v`
