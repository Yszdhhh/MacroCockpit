# MacroCockpit 2.0 — 宏观与金融市场实时情报工作台

MacroCockpit 2.0 是一个面向跨市场（美股、A股·港股、AI 前沿、数字资产与全球宏观）的轻量实时情报终端与个人风控工作台，支持「**中国国内 VPS 主站直采 + 海外 Runner（GitHub Actions / 海外 VPS）无状态中继**」双云采集架构，同时内置 **MCP `2025-11-25`（Streamable HTTP + stdio）**、桌面端 **OpenBB Workspace Bridge** 与多智能体多空深研能力。

---

## 核心架构

1. **双云拆分采集、单一全局去重（零本机依赖）**
   - **国内主节点（`--role cn-merge-publish`）**：直接采集国内可达的 **26 路高实时源**（财联社电报/深度、华尔街见闻 3 频道、腾讯财经美/A/港股与估值、东方财富 USD/CNH、Frankfurter USD/CNY、Binance Vision BTC、SEC EDGAR、美联储官方、AIHOT、A.I.日报、量子位、OpenAI、Google AI、arXiv、HN AI、CNBC、MarketWatch、Seeking Alpha、Cointelegraph 等）。
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

- **启动消费托管服务**：
  `python -m backend.server --host 127.0.0.1 --port 18280 --role consumer`
- **国内节点单次合并且发布**：
  `python -m backend.cloud_collector --role cn-merge-publish`
- **海外节点采集并回传（GitHub Actions 与海外 VPS 通用）**：
  `python -m backend.cloud_collector --role intl-push --target-url https://news.shuashuabao.xyz/api/v2/ingest-intl`
- **systemd 定时配置模板**：见 [`deploy/systemd/`](deploy/systemd/) 与 [`.github/workflows/intl_collector.yml`](.github/workflows/intl_collector.yml)。
- **运行全量回归测试**：
  `python -m pytest tests/ -v`
