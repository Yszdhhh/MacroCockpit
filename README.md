# MacroCockpit 2.0 — 宏观与金融市场实时情报工作台

MacroCockpit 2.0 是一个面向跨市场（美股、A股·港股、AI 前沿、数字资产与全球宏观）的轻量实时情报终端与个人风控工作台，支持「**中国国内 VPS 主站直采 + 海外 Runner（GitHub Actions / 海外 VPS）无状态中继**」双云采集架构，同时内置 **MCP `2025-11-25`（Streamable HTTP + stdio）**、桌面端 **OpenBB Workspace Bridge** 与多智能体多空深研能力。

---

## 最新版本更新说明（2026-11）

1. **全网舆论风向聚合与资金宽度对照（[`backend/forum_sentiment.py`](backend/forum_sentiment.py)）**
   - 汇聚雪球、东财股吧人气榜、全网主流热榜（微博/百度/头条/抖音/知乎/华尔街见闻/财联社）、Reddit/HN、小红书 5 路社区讨论，结合沪深两市成交额、两融余额、港股通南向资金与大众热榜占榜率进行温差与分歧对照。
   - 首页「全网舆论风向」卡片精简为综合情绪指数、宏观资金宽度与四大核心赛道专业/散户聚合指数（移动端单列对齐）；舆论详情页将当期观点对照与多日讨论趋势分开。榜单股当日涨跌幅不再计入社区多空分，仅作为「人气榜」热度展示。
   - 国内及本地聚合端全天分 4 个 6 小时时段滚动记录微博、百度、头条、抖音、知乎、B站公开热榜前 30 条，追踪「A股与港股 / 美股AI与科技 / 黄金与贵金属 / 比特币与加密 / 美联储与宏观」5 大赛道的 7/30/90 天关注趋势；并支持跨端发布自动承接云端历史趋势，无历史回填且不将缺口填零。

2. **英文快讯与海外热帖 Memo 投资备忘（[`backend/zh_brief.py`](backend/zh_brief.py)）**
   - 自动识别英文财经快讯与海外社区热帖（如 CNBC、MarketWatch、CoinDesk、Cointelegraph、Reddit），在标题下方以简洁 `Memo` 投资备忘标签展示中文要点摘要并将较长英文原文默认折叠至「展开全文」内按需查看。
   - 仅缓存有效网络译文并跳过兜底草稿，且在请求外部翻译前先经本地隐私文本过滤器拦截，杜绝敏感或疑似持仓文本外发。

3. **快讯评分护栏升级与加密降噪（[`backend/kazike_engine.py`](backend/kazike_engine.py)、[`backend/curation.py`](backend/curation.py)）**
   - 引入高分对数软压缩（`75+`）、个人家庭理财水文过滤（保留美联储等正式宏观债务报告）、`Live updates` / `Stock market today` 等盘中综述降权、以及中性/综述条目的持仓加权折减。
   - 严禁加密与美股快讯跨板块合并事件簇，调降媒体级加密源分级并取消加密新闻对 `QQQ`/`SPY` 等美股指数别名的误匹配；在核心精选、个人排序与离线快照中将默认精选前 15 条的加密货币上限严格收敛在 2 条以内。

4. **KOL 零前视胜率核验、信源导航与周期深研（[`backend/kol_intel_engine.py`](backend/kol_intel_engine.py)、[`backend/market_synthesis.py`](backend/market_synthesis.py)、[`backend/source_matrix.py`](backend/source_matrix.py)、[`backend/periodic_report.py`](backend/periodic_report.py)）**
   - 新增高胜率 KOL 零前视历史观点胜率核验、海外大佬长文专栏全文抓取、市场四维定调与跨源共振/背离分析、6 大资产类别信源导航与中美产业链映射，以及周/月度深研报告自动生成。

5. **一事一卡事件流与离线回放（[`backend/event_feed.py`](backend/event_feed.py)、[`docs/EVENT_FEED_DESIGN.md`](docs/EVENT_FEED_DESIGN.md)）**
   - 新增持久事件台账 `data/event_ledger.json` 与 `GET /api/v2/events`，把跨源转载、中英译文与同源采访归并至统一出处家族，严格分离观点与综述；仅在出现实质增量事实时递增时间线版本，首页支持「事件卡 / 逐条快讯」一键切换，默认不启用新推送并支持 `python -m backend.event_feed --replay` 离线回放。

6. **私密持仓防倒退云同步、隐私加固与双端适配（[`backend/vps_sync.py`](backend/vps_sync.py)、[`backend/public_snapshot.py`](backend/public_snapshot.py)、[`backend/mcp_server.py`](backend/mcp_server.py)）**
   - 新增经 `PORTFOLIO_RW` 令牌防倒退同步本机账户/现货/合约/筛选规则至云端登录态账本，同时在公开快照投影与只读 MCP 输出中加强信号、中文简介及信源矩阵的隐私文本和结构校验；优化桌面与手机端排版及资源缓存控制。

7. **多智能体并行深研工作台（接入 `google-gemini/demolab` 方法论：[`backend/deep_research_runner.py`](backend/deep_research_runner.py)、[`.agents/skills/deep-research/SKILL.md`](.agents/skills/deep-research/SKILL.md)）**
   - 将课题自动拆解为「一手官方披露 / 产业传导 / 反方证伪」3 路并发检索（自带防长尾超时熔断与缺口标注），主研究员交叉合成后自动清洗违禁词并写入 `articles/`；支持周/月报提问词旁一键点击「直接深研」、CLI `--run-demolab-dr`、MCP `deep_research_run(mode="demolab")`，同时保留 Markdown 粘贴导入作为兜底，消费端与离线包安全隔离。

8. **日线信号建议与短中期自动复盘（[`backend/signal_advisor.py`](backend/signal_advisor.py)）**
   - 基于已完成日线收盘序列零前视检测四类日线级结构（「日线超跌反弹 / 日线底部反转 / 主升顺风布局 / 过热背离防守」），给出明确参考价位、目标参考与失效保护，彻底屏蔽盘中噪声与超短线信号。
   - `记录` 模块升级为「信号建议与复盘 + 投资备忘录」，即使用户未采用系统信号也自动跟踪 T+5 个交易日（短期）与 T+20 个交易日（中期）真实走势并区分「冲高回落 vs 演化反转」，支持关联个人实操备忘。

---

## 核心架构

1. **双云拆分采集、单一全局去重（零本机依赖）**
   - **国内主节点（`--role cn-merge-publish`）**：直接采集国内可达的 **25 路高实时源**（财联社电报/深度、华尔街见闻 3 频道、腾讯财经美/A/港股与估值、东方财富 USD/CNH、Frankfurter USD/CNY、Binance Vision BTC、SEC EDGAR、美联储官方、AIHOT、量子位、OpenAI、Google AI、arXiv、HN AI、CNBC、MarketWatch、Seeking Alpha、Cointelegraph 等）。
   - **海外协作节点（`--role intl-push`，可运行于 GitHub Actions 或海外 VPS）**：仅采集国内机房受阻/限流的 **12 路海外源**（FRED 13 条宏观序列 + COMEX 黄金、OKX 现货与永续标记价/资金费率、Yahoo Finance 20 股季报与 1Y 日线、DeepMind、Anthropic、Hugging Face、FT、CoinDesk、Import AI），Gzip 压缩推送至 `POST /api/v2/ingest-intl`。
   - **统一去重与原子发布**：主节点合并海内外分片后统一运行 `backend/kazike_engine.py`（URL 规范化 + 14 天跨源事件聚类 + 中英跨语种数值锚点去重 + 五轴盲评 + 五板块保底截断），通过 `publish_generation()` 原子切换快照世代。

2. **四角色隔离与隐私边界**
   - `SYNC_PUBLISH`（`MACROCOCKPIT_SYNC_TOKEN`）：仅允许 `POST /api/sync`、`POST /api/v2/publish`、`POST /api/v2/ingest-intl`。
   - `READ_ONLY`（`MACROCOCKPIT_READ_TOKEN`）：只读公共快讯、宏观、个股投研与脱敏风控汇总（`portfolio_risk(summary)` 仅返回 10% 区间，禁用私人持仓明细与写入）。
   - `PORTFOLIO_RW` / 登录 Cookie（`MACROCOCKPIT_RW_TOKEN` / `MACROCOCKPIT_ACCESS_PASSWORD`）：读写本地 `SQLite (WAL)` 现货/合约账本与复盘随笔，永远不随公共仓库或离线快照外泄。

3. **四入口 UI 与设计契约**
   - 详见 [DESIGN.md](DESIGN.md) 与 [docs/MUSE_HANDOFF.md](docs/MUSE_HANDOFF.md)：包含 `今日`（六栏板块导览 +「算法精选 ↔ 实时时间线」双模式）、`持仓`（纯 Decimal 多币种估值与强平安全垫）、`研究`（多空速判卡、1Y 走势、SEC EDGAR、7×7 纯 NumPy 相关性矩阵、长文书房抽屉 + `/feeds.opml`）与 `记录`。

---

## 快速部署与运行

```bash
pip install -r requirements.txt
cp .env.example .env  # 填入凭据，严禁提交 .env
```

- **启动本地免密驾驶舱**：
  `powershell -ExecutionPolicy Bypass -File .\start_local_cockpit.ps1`
- **启动消费托管服务**：
  `python -m backend.server --host 127.0.0.1 --port 18280 --role consumer`
- **国内节点单次合并且发布**：
  `python -m backend.cloud_collector --role cn-merge-publish`
- **海外节点采集并回传（GitHub Actions 与海外 VPS 通用）**：
  `python -m backend.cloud_collector --role intl-push --target-url https://news.shuashuabao.xyz/api/v2/ingest-intl`
- **systemd 定时配置模板**：见 [`deploy/systemd/`](deploy/systemd/) 与 [`.github/workflows/intl_collector.yml`](.github/workflows/intl_collector.yml)。
- **运行全量回归测试**：
  `python -m pytest tests/ -v`
