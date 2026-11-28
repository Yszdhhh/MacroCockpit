# MacroCockpit × Meta Muse（Peach）V2 全栈协同、Git 源码对接与双云去本机化交接规范

> **公开仓库**：`https://github.com/Yszdhhh/MacroCockpit`（仅含脱敏源码、公开设计规范、公共信号流水线与无状态云采集器；严禁向任何仓库推送 `.env` 或私人 `user_journal.db`）。
> 本文档说明宏观金融情报站如何通过 **「腾讯国内 VPS（直采 25 路 + 汇总去重发布）+ GitHub Actions / 海外 VPS（拉取国内受阻 12 路并 Gzip 投递至 `/api/v2/ingest-intl`）」** 彻底摆脱本地 PC 依赖，并通过 V2 REST 与 **MCP `2025-11-25`** 对接 Muse 空间改造。

---

## 一、 角色隔离、双云拆采与数据通路（零本机依赖）

于 **2026-11-23** 在腾讯广州机房 VPS（`tencent-vps`）对 **37 个外部端点** 实测直连可达性（`5s` 超时），结果明确切分为两半：

| 归属节点 | `tencent-vps` 实测结论 | 负责抓取的数据源 |
| :--- | :--- | :--- |
| **腾讯国内 VPS（`--role cn-merge-publish`）** | **25 路直连 `200 OK` (`245–2907ms`)** | 财联社电报(3频道)+深度、华尔街见闻(3频道)、腾讯财经美/A/港股与估值、东方财富 `USD/CNH`、Frankfurter `USD/CNY`、Binance Vision `BTCUSDT`、SEC EDGAR、美联储官方 RSS、AIHOT (`latest+hot`)、量子位、OpenAI 官方 (`identity`编码)、Google AI、arXiv (`cs.AI+q-fin`)、HN AI、Ars Technica、CNBC、MarketWatch、Seeking Alpha、Cointelegraph、Last Week in AI、Simon Willison、MIT TR、Lil'Log、BAIR、Ahead of AI |
| **海外节点（GitHub Actions / 海外 VPS `--role intl-push`）** | **12 路超时 / `Errno 101` / `403` / `429`** | `FRED` 13 条宏观序列 + COMEX `GC=F` 黄金、`OKX` 现货与永续合约 `mark_price`/`funding_rate`、Yahoo Finance 20 股季报与 1Y 日线、`DeepMind 官方`、`Anthropic 官方/研究`、`The Batch`、`Hugging Face`、`FT 全球市场`、`CoinDesk`、`Import AI (Substack)`、`Investing.com`（及可迁入海外节点的 X-KOL / EquationNews） |

### 生产双云架构与统一去重闭环

1. **海外节点（默认已由 `.github/workflows/intl_collector.yml` 每 20 分钟自动运行；Muse 可随时接管至海外 VPS）**：
   - 运行 `python -m backend.cloud_collector --role intl-push`（依赖 `MACROCOCKPIT_SYNC_TOKEN`，可配 `FRED_API_KEY`）。
   - Gzip 压缩后带上 `Authorization: Bearer <MACROCOCKPIT_SYNC_TOKEN>` 推送至 **`POST https://news.shuashuabao.xyz/api/v2/ingest-intl`**。
2. **腾讯国内主站（`tencent-vps:/opt/macro-intel`，启用 `macro-collector-cn.timer` + `macro-intel.service --role consumer`）**：
   - 收到 `/api/v2/ingest-intl`（或本地 20 分钟定时器触发）后，由 `backend/cloud_collector.py` 在 `cloud_merge.lock` 文件锁保护下加载最近 `intl_relay_bundle.json`，结合本地直采 25 路信号/长文：
     * **统一跑一遍 `kazike_engine.process_financial_signals()`**：URL 规范化 + 14 天跨源聚类 + **中英跨语种 Ticker/数字锚点同事件合并** + 五轴盲评 + 五板块保底均衡截断；
     * **统一跑一遍长文二次去重**：合并 `feed_*.md` 与 `ta_*.md` 时按规范化 URL + 标题相似度去重；
     * 纯 NumPy 计算 `7×7` 跨资产相关性矩阵（不依赖 `pandas`）；
     * 调用 `publish_generation()` 原子切换新一代 `gen_N` 快照。
   - **个人账本绝对隔离**：`/opt/macro-intel/data/user_journal.db` 只留在腾讯 VPS，海外节点与公开 Git 仓库零接触。

### Muse 接管海外采集至自有 VPS 的方法
如果 Muse 需要把海外抓取从 GitHub Actions 迁入你们管理的海外 VPS（`deploy/systemd/macro-collector-intl.{service,timer}`）：
1. `git clone https://github.com/Yszdhhh/MacroCockpit.git /opt/macro-intel-intl && cd /opt/macro-intel-intl && pip install -r requirements.txt`
2. 配置 `.env`：填入 `MACROCOCKPIT_SYNC_TOKEN` 与 `MACROCOCKPIT_INGEST_URL=https://news.shuashuabao.xyz/api/v2/ingest-intl`（以及可选 `FRED_API_KEY`、`X/EQN` 上游）。
3. 安装并启用 `deploy/systemd/macro-collector-intl.{service,timer}`，然后停用 GitHub Actions 的 `schedule` 即可无缝交接。

---

## 二、 卡兹克（Kazike）信号去噪、五板块均衡与子主题标签

所有进入 `/api/state` 与 `/api/v2/signals` 的快讯已经过 `backend/kazike_engine.py` 与 `backend/rss_sources.py` 流水线处理：

1. **反标题党与硬规则预过滤（`dropped_prefilter`）**：拦截情绪诱导标题党（`震惊|揭秘|速看|紧急通知|太可怕了`）、纯转发水贴与无实质事件喊单，写入 `prefilter_label`。
2. **信源分层与入选阈值**：
   - `T1`（核心一手，如美联储/SEC/官方与核心研报）：最低入选分 **`60`**
   - `T1_5`（重点一线媒体/行业聚合，含 `Investing.com`、`Seeking Alpha` 等）：最低入选分 **`65`**
   - `T2`（常规财经/科技流媒体，含 `Cointelegraph` 等）：最低入选分 **`76`**
3. **五大一级板块均衡覆盖（`equities / ashare / ai / crypto / macro`）**：
   - **A股·港股 (`ashare`)** 独立升为一级板块（融合华尔街见闻 A 股频道与财联社看盘电报）。
   - 在 `max_per_source` 限流与 `max_visible=85` 截断阶段执行按板块轮转并设最低保底席位，防止单一快讯源刷屏挤出其他板块。
4. **同事件聚类去重与 `topics[]` 子主题**：
   - 同事件合并入 `merged_sources[]` 并记录 `merged_count`、`heat_48h`（48 小时半衰期热度）与 `topics[]` 子标签（如 `DeepSeek`、`国产大模型`、`算力芯片`、`具身智能`、`科创·半导体`、`华为·汽车`、`美联储`、`CPI·通胀`、`黄金·大宗`、`ETF·监管` 等）。
   - 全站 OPML 2.0 订阅源（`GET /feeds.opml`）覆盖 **29 路**精选快讯与深度长文源。

---

## 三、 四入口信息架构与五板块契约（供前端与 Muse 对齐）

| 一级入口 | 子页与核心内容 | 访问与隐私边界 |
| --- | --- | --- |
| **1. 今日 (`today`)** | 顶栏 `$ USD / ¥ RMB` 实时汇率切换与五格总览；板块导览条（`全部 · 美股 · A股·港股 · AI · 加密 · 宏观`）；快讯区支持**「算法精选 ↔ 实时时间线」双视图**（`#newsModeSwitch`）与单板块专属子主题标签正交过滤；右侧财经/财报日历（严格区分精确倒计时与仅日期）、最高风险持仓卡、美股盘面与 48h 热度雷达 | 公共快讯、宏观与日历可由 `READ_ONLY` 访问；持仓关联顺风/逆风气泡与自选/规则筛选仅限浏览器 Cookie 或 `PORTFOLIO_RW` |
| **2. 持仓 (`portfolio`)** | 汇总 KPI 条 + `+ 录入持仓` + `粘贴持仓`（文本 / CSV 预览 Diff 后确认提交）；三子页：**`现货与现金`** / **`合约风控`** / **`策略对照`**（实时持仓占比 vs 四类辅助策略目标） | **绝对私人**，仅限浏览器 Cookie 或 `PORTFOLIO_RW`；`READ_ONLY` 仅可通过 MCP `portfolio_risk(view="summary")` 获取 10% 区间脱敏桶汇总 |
| **3. 研究 (`research`)** | 四子页：**`个股投研`**（20+ 核心美股 1Y 走势、`MA20 / MA50` 乖离率与追高开仓纪律检查、财务健康、SEC EDGAR 披露）/ **`宏观与关联`**（分组宏观指标 + `7×7` 相关性热力图）/ **`KOL 观点树`** / **`长文待读`**（`AI与金融研报 / KOL深度长文 / 本地知识库 / 同步深度长文` + `/feeds.opml`） | 公开投研数据对 `READ_ONLY` 开放；开仓检查在 `READ_ONLY` 模式下仅执行公共市场检查，不探测私人持仓重叠 |
| **4. 记录 (`records`)** | 双子页：**`操作复盘`**（交易复盘台账，带版本冲突保护）与 **`随笔备忘`**（私人 Markdown 随笔库） | **仅本人浏览器 Cookie 或 `PORTFOLIO_RW` 可读写**，禁止 `READ_ONLY` 与离线包读取 |

---

## 四、 鉴权角色矩阵、REST 接口与 MCP `2025-11-25`（8 工具）

### 4.1 四类访问凭据（严格职责分离）

| 凭据 / 角色 | 传递方式 | 允许范围 | 显式禁止 |
| --- | --- | --- | --- |
| **浏览器会话 (`Cookie`)** | 密码登录后下发 `macro_auth`（写操作须带 Same-Origin + `X-Macro-Request: 1`） | 全站 4 入口读/写、私人复盘/随笔、组合变更、导入预览提交、推送触发 | `/api/sync`、`/api/v2/publish` |
| **`PORTFOLIO_RW`** (`MACROCOCKPIT_RW_TOKEN`) | `Authorization: Bearer <token>` | 所有 V2 组合读写接口 + MCP 全部 8 个工具 | `/api/sync`、`/api/v2/publish`、URL `?token=` |
| **`READ_ONLY`** (`MACROCOCKPIT_READ_TOKEN`) | `Authorization: Bearer <token>`（仅 V1 GET 兼容 `?token=`） | `/api/state`（无 `journal`）、`/api/v2/signals`（脱敏且禁探持仓）、`/api/stock`、`/api/article`、`/api/digest.md`、`/feed.xml`、`/feeds.opml` + MCP 5 个只读工具 | `/api/sync`（必回 `403`）、`/api/v2/workspace`（`403`）、`portfolio_risk(detail)`（`403`）、`signal_search(watch)` 或 `held+instrument_id`（`403`） |
| **`SYNC_PUBLISH`** (`MACROCOCKPIT_SYNC_TOKEN`) | `Authorization: Bearer <token>` | **仅限** `POST /api/sync` 与 `POST /api/v2/publish` | 一切 `/mcp`（`403`）、一切读接口与私人工作区 |

### 4.2 REST 接口清单

1. **公开 / 只读白名单 GET**：
   - `GET /health`、`GET /api/health`（仅读清单元数据，无快照返 `503`）
   - `GET /api/state`（强制 `project_public_state` 裁剪；仅 Cookie 会话附加 `journal`）
   - `GET /api/v2/signals?scope=all&category=all|equities|ashare|ai|crypto|macro&subtopic=...&direction=...&sort=importance|time&from=...&to=...&limit=85&cursor=...`（支持分页及按重要度/发布时间严格倒序；`READ_ONLY` 自动剥离 `holding_radar` 与私人打分原因，并拒绝 `watch`/`rules`/`held+instrument_id`）
   - `GET /api/stock?symbol=AAPL`（`consumer` 模式下仅读已发布 `stock_profiles`）
   - `GET /api/digest.md?topic=all|finance|ai|crypto`、`GET /api/article?file=<name.md>`、`GET /feed.xml`、`GET /feeds.opml`、`GET /api/muse-spec.md`、`GET /api/caffeine-brief.md`
2. **组合读写 V2 接口（仅 Cookie + `X-Macro-Request: 1` 或 `PORTFOLIO_RW` Bearer）**：
   - `GET /api/v2/workspace?currency=USD|CNY`：完整私有资产估值、合约强平安全距离、策略偏离对照与个性化信号排序。
   - `GET /api/v2/preferences`：个人偏好与信源筛选规则。
   - `POST /api/v2/changes`：`{request_id, expected_revision, ops}` 原子批提交（版本冲突或幂等载荷冲突均返回 `409`）。
   - `POST /api/v2/import/preview` 与 `POST /api/v2/import/commit`：文本/CSV 持仓解析预览（10 分钟有效）与原子提交。
   - `POST /api/push`：支持 `mode`（`daily`/`headwind`/`drift`）、`include_amounts`（默认 `false` 脱敏）、`channels`，联合真实 `workspace` 构建移动推送卡片。
3. **发布同步接口（严格仅限 `SYNC_PUBLISH` Bearer）**：
   - `POST /api/sync`、`POST /api/v2/publish`：接受 JSON 或 `gzip` 压缩载荷（`<= 16 MiB`，超限回 `413`，非 JSON 回 `415`），原子发布世代并校验 `generation`/`sha256` 冲突（`409`）。

### 4.3 MCP `2025-11-25` 端点与 8 个正交工具

- **传输端点**：`POST /mcp`（`GET /mcp` 返回 `405`；仅接受 `Authorization: Bearer`，**禁止 URL `?token=`**；校验 `Origin` 白名单与 `MCP-Protocol-Version`；`notifications/initialized` 返回 `202` 空体）。
- **本地 stdio 桥接（`backend/mcp_stdio.py`）**：
  - 默认远端代理模式：读取 `MACROCOCKPIT_MCP_URL` + `MACROCOCKPIT_MCP_TOKEN` 转发至 `/mcp`，日志仅写 `stderr`，每行 UTF-8 JSON-RPC 仅写 `stdout`；
  - `--offline-public`：纯只读加载本地已发布快照，**绝不创建或写入本地可写账本**。
- **8 工具权限与参数规范（所有 schema 均开启 `additionalProperties: false`）**：

| 工具名 | 允许角色 | 核心能力与安全约束 |
| --- | --- | --- |
| `market_snapshot` | `RO`, `RW` | 读取已发布的风控灯号、4 项基准仓位、宏观指标、美股盘面、汇率、加密报价与相关性 |
| `signal_search` | `RO`, `RW` | 支持 `scope` / `category`（含 `ashare`）/ `subtopic` / `instrument_id` / `event_id` / `direction` / `sort`（`importance\|time`）/ `from` / `to` / `limit` / `cursor`；`RO` 拒绝 `watch`/`rules` 与 `held+instrument_id` 反推 |
| `research_get` | `RO`, `RW` | 按 `symbol` 分段返回 `profile` / `trend` / `filings` / `checklist`；`RO` 仅返回公共纪律检查 |
| `portfolio_risk` | `RO`, `RW` | `RO` 请求 `view="summary"` 仅返回 **10% 区间脱敏桶**，请求 `detail` 拒绝 `403`；`RW` 支持完整 `summary` 与 `detail` |
| `calendar_list` | `RO`, `RW` | 按 `type`（`all/macro/earnings/fomc/cpi/nfp`）/ `symbol` / 时间窗口查询日历，保留精确时刻与仅日期精度边界 |
| `preferences_get` | `RW` | 读取个人展示偏好与信源筛选规则 |
| `import_preview` | `RW` | 校验并解析自然文本或 CSV 持仓，生成预览差异与 `preview_id` |
| `workspace_commit` | `RW` | 基于 `expected_revision` + `request_id` 幂等提交 `ops` 或 `preview_id`（互斥） |

---

## 五、 给 Muse 改造时的最小改动清单

1. **严禁在国内机直连爬海外站点**，优先调用 `http://127.0.0.1:18280/api/v2/signals`（或 `/api/state`）配合 `Authorization: Bearer <MACROCOCKPIT_READ_TOKEN>`。
2. **按五大板块与子主题消费快讯**：
   - `A股·港股`：`GET /api/v2/signals?category=ashare`
   - `美股 / 宏观 / 加密`：`GET /api/v2/signals?category=equities|macro|crypto`（或 `GET /api/digest.md?topic=finance`）
   - `AI 专刊`：`GET /api/v2/signals?category=ai`（可叠加 `subtopic=DeepSeek` / `算力芯片` / `具身智能`）
3. **Agent 工作流直连 MCP**：若 Muse / Claude / Codex 支持 MCP Streamable HTTP，直接配置 `POST https://news.shuashuabao.xyz/mcp`（仅配 `READ_ONLY` 令牌即可使用 `market_snapshot`、`signal_search`、`research_get`、`portfolio_risk(summary)`、`calendar_list`）。
4. **安全守则**：只读客户端绝不携带 `SYNC_PUBLISH` 或 `PORTFOLIO_RW` 令牌，不在前端页面或公开消息暴露令牌与个人金额。


### 舆论趋势（国内采集节点）

- 当期舆论和持续趋势是两个信号。雪球、东财股吧的行情人气榜不进入社区倾向分；KOL 观点和涨跌幅也不进入讨论趋势。
- 国内生产任务在北京时间 10–12 点从项目已有的免登公共热榜上游抽样：微博、百度、头条、抖音、知乎、B 站各最多 20 条有效标题中的标的命中占比。每平台至少 12 条不同标题并需有可核验的近 30 分钟榜单时间。完全相同（忽略空格和标点）的跨板标题不能充作独立命中。该采样反映看板中可见度，并不保证不同标题对应不同事件或作者，更不是全平台发帖、存量仓位或真实资金流。
- 每天仅保存一组可比较的观察；如果部分来源缺失，至少间隔半小时重试、合计不超过三轮。网络失败、无日期、跨日缓存或样本不足不填零；成功可比榜单确实零命中才记录零。上游可用性不作保证，微信、雪球、X 不是此指标输入，亦无历史回填。
- 在 7/30/90 天窗口内，各来源前后两段均须有至少六成覆盖并有近两日观察。至少两个可比平台出现明确抬升、后段多数采样日及最近一次仍达阈值，且跨源中位变化同向，才提示「持续升温」。持续高位但不增加另列「持续高关注」，不提示见顶或触发交易。
- 仅在国内采集端写入本地滚动 120 天的聚合缓存 `data/discussion_temperature_cache.json`（已被 Git 排除），没有持仓、凭据、原帖或正文。发布只包含限长的 `forum_sentiment.discussion_trends` 摘要；`as_of` 为北京评估日期而非最新帖发布时间，超过一天在公开读端和浏览器均不再显示。Windows 或海外发布不写该历史；不同发布方交替的瞬间可能看不到趋势，下一次国内快照恢复。
