# Caffeine V3 & Claude Fable 5.1 协同实战手册

> **核心定位**：`tencent-vps`（`https://news.shuashuabao.xyz`）以 `--role consumer` 运行作为全天候数据与账本底座，负责只读/读写分权鉴权、SQLite 组合工作区、V2 REST API 与 MCP `2025-11-25`（8 工具）服务；**Caffeine V3（内置 Claude Fable 5.1）** 专注于基于 `DESIGN.md` 的高审美视觉审查、CSS 增量精修与只读界面原型开发。

---

## 一、 架构分工与安全边界

| 层级 | 承载方 | 职责与边界 |
| :--- | :--- | :--- |
| **采集与发布端** | 本机 Windows 定时任务（20 分钟） | 抓取 FRED 宏观（含净流动性与 CPI 同比）、美股盘面与 20 只核心投研画像、SEC EDGAR 官方披露、实时外汇与加密报价（`fx_snapshot`、`crypto_quotes`）、AIHOT/EQN/国内外财经电报（含 A 股·港股看盘）与 X/KOL 观点，完成反标题党过滤、五板块均衡、子主题标注后，凭 `SYNC_PUBLISH` 令牌推送 `POST /api/v2/publish`。 |
| **消费托管与鉴权层** | `tencent-vps` (`--role consumer`, `127.0.0.1:18280`) | 提供 Cookie + `X-Macro-Request: 1` CSRF 保护的 4 入口 Web 驾驶舱、`PORTFOLIO_RW` 组合/规则/导入 V2 接口、`READ_ONLY` 白名单只读 API，以及 `POST /mcp`（8 正交工具）。绝不触发爬虫抓取，离线打包 `build_static_bundle()` 严格只裁剪公开数据，绝不注入私人 `journal`。 |
| **纯 UI 审查与 CSS 精修** | Caffeine V3 (`caffeine.ai/chat`) | 基于 `DESIGN.md` 与现成 `web/index.html`、`web/styles.css` 进行报刊纸感、排版节奏、移动端溢出与交互可达性审查，产出**增量 CSS 精修补丁**或可选只读伴侣视图，**不改动后端 V2/MCP 契约，不接触数据库写入或推送密钥**。 |

---

## 二、 推荐协同落地玩法

### 玩法 ①：纯 UI 审查与 CSS 增量精修（直接针对 `web/` 现有 DOM）
1. 在 `caffeine.ai/chat` 中上传 `DESIGN.md`、`web/index.html` 与 `web/styles.css`（无需暴露真实持仓或令牌）。
2. 复制第四节「纯 UI 审查与 CSS 增量精修 Prompt」，让 Claude Fable 5.1 对照浅纸色日间/深曜石夜间 Token、板块导览条（`.sector-ribbon`）、算法精选/实时时间线双视图（`#newsModeSwitch`）、粘性首列宽表（`.table-scroll`）与 `390×844` 移动端抽屉逐项审查。
3. 仅合入经过审阅的 CSS 增量精修并运行 `pytest` 回归，再执行 `python -m backend.server --build-static` 更新公开离线包 `dist/macro_cockpit_offline.html`。

### 玩法 ②：Caffeine MCP Server + 仓库契约联动
1. 如需让外部设计或投研 Agent 实时预览脱敏公开字段，可挂载仓库内置 MCP（`POST /mcp` 配 `READ_ONLY` 令牌，或本地 `python -m backend.mcp_stdio --offline-public` 只读快照）。
2. 所有只读工具均采用 `additionalProperties: false` 严格校验：
   - `market_snapshot`、`signal_search`、`research_get`、`portfolio_risk(view="summary")`（10% 脱敏区间）、`calendar_list`。
3. 所有私人配置与提交工具（`preferences_get`、`import_preview`、`workspace_commit`）严格锁定为 `PORTFOLIO_RW` 角色。

### 玩法 ③：构建 React 只读伴侣页消费 V2 白名单接口
1. 仅使用 `READ_ONLY` 令牌调用公开只读端点（支持 `Authorization: Bearer <token>`）：
   - `GET /api/state`（全站公开快照，绝对不含 `journal`）
   - `GET /api/v2/signals?category=all|equities|ashare|ai|crypto|macro&subtopic=...&limit=40`（支持五大板块与子主题分页筛选；`READ_ONLY` 自动脱敏 `holding_radar` 且禁止探测持仓）
   - `GET /api/stock?symbol=NVDA`（单只标的估值、健康评分、`MA20 / MA50` 乖离与开仓纪律检查、1 年走势与 `sec_filings`）
   - `GET /api/article?file=<filename.md>`、`GET /api/digest.md?topic=all`、`GET /feed.xml`、`GET /feeds.opml`

---

## 三、 V2 核心数据契约速查（给 Fable 5.1 的真实结构）

```json
{
  "schema_version": 2,
  "generation": 12,
  "generated_at_bjt": "2026-11-22 23:57:05+08:00",
  "risk_gate": {
    "light": "YELLOW",
    "label": "黄灯 · 头部宏观偏空控险",
    "long_risk_cap": 0.5,
    "summary_exposure": "方向性多头 0% · 黄金避险 0% · 熊市配对 +35% BTC / -30% 山寨",
    "reasons": ["..."]
  },
  "positions": [
    {
      "id": "btc_trend | gold_hedge | bear_basket | crash_bounce",
      "name": "核心趋势仓 (BTC) | 黄金避险仓 | 熊市配对对冲仓 | 超跌反弹短打仓",
      "state": "空仓观望 | 持仓运行",
      "baseline_pct": 35.0,
      "recommended_pct": 35.0,
      "est_notional_usd": 35000.0,
      "note": "..."
    }
  ],
  "fx_snapshot": {
    "pair": "USD/CNY",
    "rate": "6.6777",
    "status": "live",
    "label": "人民币参考汇率",
    "market_at": "2026-11-22 23:55+08:00"
  },
  "signals": [
    {
      "id": "...",
      "source": "财联社电报 | 华尔街见闻 | SEC EDGAR | AIHOT精选 | 方程式快讯 | 美股盘面",
      "category": "equities | ashare | ai | crypto | macro",
      "topics": ["DeepSeek", "算力芯片"],
      "direction": "bullish | bearish | neutral",
      "importance": "高 | 中",
      "attention_score": 82,
      "selected": true,
      "merged_count": 3,
      "merged_sources": ["财联社电报", "华尔街见闻"],
      "heat_48h": 45.2,
      "tickers": ["NVDA"],
      "why_it_matters": "...",
      "url": "https://..."
    }
  ],
  "calendar_events": [
    {
      "id": "fomc-2026-12-09",
      "type": "fomc | cpi | nfp | earnings",
      "title": "美联储 FOMC 利率决议与新闻发布会",
      "date": "2026-12-09",
      "precision": "exact | date",
      "event_utc": "2026-12-10T03:00:00Z",
      "hours_until": 411.0
    }
  ],
  "stock_profiles": {
    "NVDA": {
      "symbol": "NVDA",
      "name_cn": "英伟达",
      "price": 125.53,
      "change_pct": -14.01,
      "ma20": 181.46,
      "ma50": 207.0,
      "bias20_pct": -30.82,
      "bias50_pct": -39.36,
      "range_52w_pos": 0.9,
      "pre_trade_checklist": {"verdict": "...", "checks": []},
      "health": {"score": 8, "label": "财务稳健"},
      "sec_filings": [{"symbol": "NVDA", "form": "10-Q", "filing_date": "2026-11-18", "url": "https://www.sec.gov/..."}]
    }
  }
}
```

---

## 四、 可直接复制给 `caffeine.ai/chat`（Claude Fable 5.1）的 Prompt

```text
请以《DESIGN.md》为唯一视觉与交互契约，对我提供的 MacroCockpit（宏观决策情报站）现有 `web/index.html` 与 `web/styles.css` 执行一次【纯 UI 审查与 CSS 增量精修】。

【范围限制：只做视觉审查与样式精修，严禁重写 JS 或改数据结构】
1. 严禁修改任何 HTML DOM id / data-* 钩子名称，严禁改动 `web/app.js` 的逻辑与 API 字段约定（`/api/state`、`/api/v2/workspace`、`/api/v2/signals`、`/api/v2/changes`、`/api/v2/import/*`、`/api/stock`、`/api/push`、`/feeds.opml`）。
2. 仅输出两项内容：
   - **UI 审查清单**：按优先级列出排版呼吸感、信息密度层级、对比度、表格固定列、抽屉交互、长文阅读舒适度与 `390×844` 移动端适配的真实细节问题（不要编造未见元素）；
   - **增量 CSS 精修补丁（unified diff 或精确作用域 CSS 片段）**：只修发现的具体样式瑕疵，不整体推翻重来。

【必须逐条核验的硬性标准】
1. **报刊纸感 × 精密交易台双主题**：浅纸底 (`#FAFAF9 / #FFFFFF / #F4F3F0 / #FBFAF7 / #16140F / #0E6E6E`) 与深曜石夜间 (`#100F09 / #1A1915 / #22211B / #171611 / #F5F4EF / #12CCD8`) 切换后边框、分割线、角标与表格对比度全部清晰。
2. **四入口与五板块导览条层次**：
   - 顶栏含 `$ USD / ¥ RMB` 切换与 `隐藏金额` 开关，紧凑不换行溢出；
   - `.sector-ribbon`（全部 · 美股 · A股·港股 · AI · 加密 · 宏观）、`#newsModeSwitch`（算法精选 ↔ 实时时间线双视图切换与 `HH:MM` 时间轴）及板块下钻后的 `.subtopic-chips` 视觉主次分明；
   - `持仓`（现货/合约/策略对照）、`研究`（个股投研/宏观热力图/KOL 树/长文抽屉）、`记录`（复盘/随笔）四入口及子页选项卡触控热区均 `>= 44px`。
3. **数值与表格铁律**：所有价格、涨跌幅、百分比、杠杆与 SEC 表单号必须应用 `font-variant-numeric: tabular-nums` 且右对齐；金额输入框隐藏原生上下箭头（`appearance: textfield` + `::-webkit-inner-spin-button { display: none }`）；宽表支持 `.table-scroll` 内局部横滑且首列 `position: sticky; left: 0` 背景不透明遮挡底字。
4. **移动端零溢出与去噪**：`390×844` 视口下整页严格零横向滚动（`scrollWidth <= innerWidth`），底部 4 栏 `.tabbar` 与上滑抽屉安全边距（`env(safe-area-inset-bottom)`）自然协调；严禁建议或添加任何“功能教程横幅”“原理说明条”或空白占位行。
```
