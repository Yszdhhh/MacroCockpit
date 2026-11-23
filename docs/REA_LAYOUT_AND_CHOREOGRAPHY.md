# MacroCockpit 界面架构与视觉编排蓝图（"Rea" 专项研报 · 布局篇）

> **定位**：本文档沉淀对四个参考信息终端（TheMarketBrew、FiNews、NewsLiquid Watcher、AIHOT）的实时 DOM / CSS 实测解剖，以及对本地既有看板（`<WORKSPACE>/reports/dashboard/cockpit.html`、`hermes/.../daily_report_template.html`）的连续性审计，直接指导 `web/` 的信息层级、主题质感、手机端单手交互与反臃肿编排落地。

---

## 一、四站实测横向对比矩阵

以下结论均基于桌面端 `1440×900` 与移动端 `390×844` 实测渲染树、样式表源码及 GitHub 源码提取。

| 维度 | **TheMarketBrew** (`/twitter-digest`) | **FiNews** (`finews.fyi`) | **NewsLiquid Watcher** (`watcher.newsliquid.com`) | **AIHOT** (`KKKKhazix/AIHOT`) |
| :--- | :--- | :--- | :--- | :--- |
| **技术形态** | Next.js + Tailwind v4（暗色暖炭纸） | 纯静态 SSR HTML + 内联 SVG Treemap（报刊风） | 单文件静态 HTML + Vanilla JS（可完全离线） | React Router SSR + Tailwind v4 |
| **核心隐喻** | **FinTwit 多空擂台叙事流**：把数千条推文压缩成“多空打架”卡片 | **华尔街日报盘后头版**：主栏定调事件新闻 + 右栏行情/温度计 | **投研档案索引库**：左栏多维分面筛选 + 右栏编年索引卡片 | **精选信息流 + 移动阅读器**：答案先行摘要 + 推荐理由 + 同源折叠 |
| **色彩体系** | 暖黑炭底 `#100F09`、`--card: #201F1D`、`--foreground: #F5F4EF`、`--muted-foreground: #A8A59B`；涨绿 `#34D399`、跌红 `#F87171`、多空冲突框 `amber-700/35`、标签 `sky-400`；`.editorial-paper:before` 叠加 2.5% 噪点纹理 | 米黄报纸桌底 `#EDE8DC`、纸面 `#FBF7EC`、卡片 `#FFFDF6`、主墨 `#111111`、`--muted: #5C5A55`；`4px double #171717` 双报线；涨 `#176B43`、跌 `#9F1D20` | **纯白/暖纸标杆**：`--bg: #FFFFFF`、`--bg-2: #FAFAF9`、`--bg-3: #F4F3F0`、主墨 `#16140F`、`--sub: #6E675B`、纸纹细线 `--rule: #E9E5DC`；点缀 `--brand: #8C76FB` / `--teal: #0E6E6E`；多 `#1F9D55` / 空 `#D6453D` | **日间暖纸 + 夜间深青**：日间 `--bg: #FAF9F6`、`--surface: #FFFFFF`、`--well: #F3F3EF`、`--ink: #202A30`、主色青绿 `--accent: #176B75`、`--note: #526F72`；夜间 `--bg: #13191C`、`--surface: #1A2226`、`--accent: #12CCD8` |
| **字体与数字** | 展示标题 `LXGW WenKai` + `Instrument Serif`（桌面 48px / 移动 30px）；数据 `.font-data` 用 `JetBrains Mono` + `tnum` | `@font-face "Lining Digits"` 把数字单独映射到 Times New Roman 等高数字；正文 Georgia + `Noto Serif SC`；行情右栏 `ui-sans-serif` + `tabular-nums` | `--serif`: `Source Serif 4` / `Noto Serif SC`（标题 23px）；`--sans`: `Inter` / `Noto Sans SC`（正文 16px）；`--mono`: `JetBrains Mono`（`.num` 强制 `tabular-nums`） | 系统无衬线 + `PingFang SC` / `Microsoft YaHei`；全文启用 `text-autospace: normal` 优化中英混排，输入框与代码块强制 `no-autospace`；`.num` / `.mono` 严格等宽 |
| **桌面网格** | 单中心阅读柱（内容宽 848px）从上到下线性展开 | `1180px` 容器内 `minmax(0, 1fr) 326px`（左主栏 788px + 右行情栏 326px，`3px double` 竖线分隔） | `1320px` 容器内 `262px minmax(0, 1fr)`（左粘性筛选栏 + 右索引列表；单条研报 `96px 1fr` 左日期/Ticker 右内容） | `180px` 左侧固定导航栏（窄屏缩为 `64px` 图标轨）+ 居中时间轴 `64px 22px minmax(0, 1fr)`；长文正文字宽锁定 `max-w-[760px]` |
| **信息密度与亮点** | ① 顶栏一句话大盘定调+样本量（`383 KOL · 5082 推`）；② TOP 5 关注度榜双栏速览；③ 单标的分“多空冲突总结 → 买入逻辑 → 卖出风险 → 客观观察” | ① 顶部大盘总结 `01~07` 编号（17px 易扫读）；② 右栏高度压缩的“市场温度计 + 指数 + 七巨头 + 美债 + 币价”对照表；③ **计算方法默认 `hidden`，仅点 `i` 图标展开**，首屏零文字噪音 | ① 粘性半透明顶栏 (`60px`) + 结果工具条；② 活跃筛选标签条 (`.activebar`) 可逐一删除；③ 研报长文页 (`#FBFAF7`, `860px`) 极简专注阅读 | ① **卡片四段式**：来源时间评分头 → 标题+答案先行摘要 → `推荐理由`（`--note` 独立色）；② 同事件聚合折叠；③ 深浅色平滑 `ViewTransition` |
| **移动端实测** (`390×844`) | `scrollW=390`，无横向溢出；卡片自动退为单列，但全页长达 `8027px`，缺少底部快捷跳转栏 | `scrollW=390`；`<=860px` 切单列，`news-list` 由 2 列切 `1fr`；**缺点**：右栏指数/个股被挤到 `y=5108px` 底部，首屏看不到行情 | `scrollW=390`；`<=960px` 左侧筛选栏化身为左侧滑抽屉 (`min(87vw, 348px)`)；`.note-side` 日期与 Ticker 横向并排 | 原生 App 级体验：**顶部 `48px` `PhoneBar` + 粘性出血日期条 `DayHeader` + 底部 `50px` 固定导航栏 (`TabBar`) + 手势下拉抽屉 (`Sheet`)** |

---

## 二、本地既有看板审计与继承清单

### 1. `<WORKSPACE>/reports/dashboard/cockpit.html`（由决策引擎生成）
- **值得直接保留的交互资产**：
  1. **防打断资金输入**：`#capitalInput`（`type="number"`，右对齐，已屏蔽微调上下箭头 `-moz-appearance: textfield` + `::-webkit-inner-spin-button { display: none }`），仅监听「应用」按钮 `click` 与回车 `keydown(Enter)` 后才重算金额，绝不绑定 `input`/`change`。
  2. **一键显隐金额列**：`.hide-amt .amt-col, .hide-amt .amt-cell { display: none; }` 搭配按钮文案切换，保护截屏或隐私阅读场景。
  3. **首屏逻辑骨架**：4 个核心 KPI 卡紧跟 4 个仓位建议表，紧接着横向 `.metrics-grid` 市场温度。
- **必须在本项目彻底根除的缺陷**：
  1. **黑话与英文残留**：旧版充斥 `FLAT`、`ACTIVE`、`Tier-S`、`Tier-A`、`Tradfin`、`四袖`、`前向样本 16` 等内部口径术语；新版须 100% 替换为中文大白话（`空仓观望`、`持仓运行`、`辅助仓位建议` 等）。
  2. **占位废词**：旧版 JS 中当权重为空时输出 `'待刷新'`，旧表存在 `'—'`；新版须严格执行“无数据即不渲染空行/占位词”。
  3. **无深浅切换、无长文阅读器**：旧版写死深黑 `#0f1216`，KOL 摘要被强制截断在表格单元格里无法展开，且手机端只能硬挤双列或横向撑宽表格。

### 2. `%HERMES_HOME%/scripts/news/scripts/daily_report_template.html`
- **值得直接借鉴的视觉元素**：
  1. **日间暖纸底色与涨跌对比**：`#F5F5F3` 底 + `#FFFFFF` 卡 + 绿 `#16713B` / 红 `#B91C1C` / 琥珀 `#D97706`，在白昼桌面及手机强光下辨识度远胜纯黑后台风。
  2. **双向居中涨跌柱条**：`.hm-r` 与 `.sec-bar` 中以 `50%` 虚/细中线为轴，正涨幅向右延伸绿色条、负跌幅向左延伸红色条，非常适合移植到 `MacroCockpit` 的「美股行业板块强弱条」。
  3. **风险温度进度条**：`.rw-bar` 的极简 3~4px 横向分位条，直观呈现 VIX / 美债 / 估值温度而不占面积。
- **必须规避的设计错误**：
  1. `<meta name="viewport" content="width=1000">` 硬编码 1000px 视口宽度，导致手机端无法响应式排版；
  2. 基本面表硬塞 `'N/A'` 字符串占位，违反零空占位铁律；
  3. 正文 20px / KPI 40px 是为长图截图服务的“海报字号”，不适合交互式决策工作台。

---

## 三、手机端单手阅读与信息密度设计规范（`<= 768px`）

综合 `AIHOT` 移动端壳层、`Watcher` 抽屉交互与 `FiNews` 紧凑行表，确立 `MacroCockpit` 在 `390×844`（及最小兼容 `320px`）下的单手操作体系：

### 1. 固定底部五栏导航（`TabBar`，仅移动端展示）
- **结构**：`position: fixed; inset-inline: 0; bottom: 0; z-index: 40;`，高度 `52px + env(safe-area-inset-bottom)`，背景采用 `rgba(250, 250, 249, 0.90)`（深色 `rgba(16, 15, 9, 0.90)`）配合 `backdrop-filter: saturate(150%) blur(16px)` 与顶部 `1px` 发丝线阴影。
- **五栏精确分工**（对应主内容五大板块，`grid-template-columns: repeat(5, minmax(0, 1fr))`）：
  1. **仓位**（风险灯号 + 4 个辅助仓位建议 + 资金基数）
  2. **信号**（快讯与 AI 风向统一时间流 + 筛选）
  3. **美股**（盘后一句话定调 + 指数 + 七巨头 & AI 算力 + 板块条）
  4. **观点**（KOL 多空摘要卡片）
  5. **深读**（本地长文与知识库研报列表 + 推送入口）
- **单手交互细节**：每个 Tab 点击热区高度 `>= 48px`（满足 WCAG 触控 `44px` 下限）；主页面底部预留 `padding-bottom: calc(72px + env(safe-area-inset-bottom))`，确保最后一张卡片不被导航栏遮挡；再次点击当前激活 Tab 时平滑滚动回顶部。

### 2. 横向滚动 KPI / 核心温度条（`MacroStrip`）
- 解决 `FiNews` 在移动端把行情指标挤到页面最底部的痛点：**美联储与跨资产核心温度在首屏仓位卡片正下方即刻呈现**。
- **桌面端**：`grid-template-columns: repeat(auto-fit, minmax(156px, 1fr))` 自动铺开，一眼扫完 7~9 项核心温度。
- **移动端 (`<= 768px`)**：切为单行横向滚动轨道：
  ```css
  display: flex;
  gap: 10px;
  overflow-x: auto;
  scroll-snap-type: x proximity;
  scrollbar-width: none;
  margin-inline: -16px;
  padding-inline: 16px;
  ```
  单卡 `flex: 0 0 144px; scroll-snap-align: start;`。单手拇指左右轻滑即可扫完「联邦基金利率 / 美债10Y / 10Y-2Y利差 / 净流动性 / VIX / 标普500 / BTC / 黄金 / 场外U溢价」，不拉长首屏纵向高度，不触发页面整体 `scrollWidth > innerWidth`。

### 3. 抽屉式研报长文阅读（`ReaderDrawer`）与推送抽屉（`PushDrawer`）
- **视觉参照**：融合 `AIHOT` `Sheet.tsx`（移动底部弹出 `max-h-[88dvh]`、`rounded-t-[16px]`、顶部 `36×5px` 抓手条）与 `Watcher` 研报正文（`#FBFAF7` 纸底、`16.5–17px` 正文、`line-height: 1.8`、`max-width: 720px` 阅读列）。
- **双端形态**：
  - **桌面端 (`> 768px`)**：右侧滑入抽屉（`width: min(760px, 92vw)`，左带 `--shadow-drawer` 阴影与遮罩）。
  - **移动端 (`<= 768px`)**：从底部滑起（`height: 88dvh`，顶部留抓手条 + 粘性标题/关闭栏，关闭按钮最小 `44×44px` 放在右上或右下方便拇指触达）。
- **离线优先加载策略**：打开抽屉时优先直接读取 `cockpit_state.json`（或 `window.__INITIAL_COCKPIT_STATE__`）中预置的 `body_md` 渲染 Markdown（保证双击 `dist/macro_cockpit_offline.html` 在 `file://` 下 0 网络请求秒开）；若条目未内嵌全文且当前运行于 HTTP 服务模式，再异步请求 `/api/article?file=<filename>`。打开抽屉时给 `body` 加 `overflow: hidden` 锁定底层滚动，按 `Esc` 或点击遮罩关闭。

---

## 四、落地到 `web/` 的视觉 Token 与编排铁律

### 1. 统一设计 Token（默认亮色暖纸 + 可切换深色终端）

```css
:root {
  color-scheme: light;
  /* 纸感底色梯度（继承 Watcher + AIHOT） */
  --bg: #FAFAF9;
  --surface: #FFFFFF;
  --surface-2: #F4F3F0;
  --paper-read: #FBFAF7;

  /* 墨色梯度 */
  --ink: #16140F;
  --ink-2: #3A3733;
  --sub: #6E675B;
  --faint: #9A9284;

  /* 纸面细线 */
  --rule: #E9E5DC;
  --rule-strong: #D8D2C6;

  /* 主题强调与业务语义（参考 Watcher + AIHOT + FiNews） */
  --accent: #0E6E6E;
  --accent-soft: rgba(14, 110, 110, 0.09);
  --bull: #1F9D55;
  --bull-soft: #E7F4EC;
  --bear: #D6453D;
  --bear-soft: #FBEAE8;
  --warn: #B8873A;
  --warn-soft: #FEF3C7;
  --neutral: #6B6459;
  --neutral-soft: #F0EEE9;

  /* 字体与等宽数字 */
  --serif: "Source Serif 4", "Noto Serif SC", Georgia, "Songti SC", serif;
  --sans: "Inter", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  --mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;

  --maxw: 1320px;
  --topbar-h: 58px;
}

[data-theme="dark"] {
  color-scheme: dark;
  /* 暖炭纸感深色（继承 MarketBrew + AIHOT） */
  --bg: #100F09;
  --surface: #1A1915;
  --surface-2: #22211B;
  --paper-read: #171611;
  --ink: #F5F4EF;
  --ink-2: #DCDAD0;
  --sub: #A8A59B;
  --faint: #757268;
  --rule: rgba(245, 244, 239, 0.11);
  --rule-strong: rgba(245, 244, 239, 0.20);
  --accent: #12CCD8;
  --accent-soft: rgba(18, 204, 216, 0.12);
  --bull: #34D399;
  --bull-soft: rgba(52, 211, 153, 0.14);
  --bear: #F87171;
  --bear-soft: rgba(248, 113, 113, 0.14);
  --warn: #FBBF24;
  --warn-soft: rgba(251, 191, 36, 0.14);
  --neutral: #A8A59B;
  --neutral-soft: rgba(168, 165, 155, 0.14);
}
```

### 2. 数字与输入框 CSS 硬规矩
- **全局等宽右对齐**：所有价格、收益率、仓位百分比、预估金额、时间戳必须挂载 `.num` 类：
  ```css
  .num {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
    text-align: right;
    white-space: nowrap;
  }
  ```
- **彻底隐藏 `input[type="number"]` 微调上下箭头**：
  ```css
  input[type="number"] {
    text-align: right;
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    -moz-appearance: textfield;
    appearance: textfield;
  }
  input[type="number"]::-webkit-inner-spin-button,
  input[type="number"]::-webkit-outer-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }
  ```
- **严防横向溢出**：`html, body { overflow-x: hidden; max-width: 100%; }`，所有 Grid 子项加 `min-width: 0`，保证桌面 `1440px` 与手机 `390px`/`320px` 下 `document.documentElement.scrollWidth <= window.innerWidth`。

### 3. 十大落地编排铁律（直接约束 `web/` 实现与验收）

1. **零教程横幅与零方法论说教**：页面上绝不允许出现“测算口径说明”、“公式推导教程”、“联动机制解释”或引导新手怎么看盘的大段横幅；只呈现当前事实、结果数值与可执行要点。
2. **零空行与零“待补”占位**：严禁渲染任何 `待补`、`N/A`、`null`、`0` 价格空行或空占位卡片。若 `macro_fred` 某指标缺失或 `us_equities` 某股票 `price <= 0`，直接跳过不生成 DOM；若某子分组整体为空，隐藏该子区块或给出紧凑的一句无异常提示，不留空表框。
3. **100% 中文大白话术语**：严禁在界面可见文本中出现 `Sleeve`、`E21`、`Tier-1`、`Tier-S`、`arbitration`、`Regime`、`FLAT`、`ACTIVE`、`Quant Baseline` 等英文黑话或内部代号；统一使用 `辅助仓位建议`、`空仓观望`、`持仓运行`、`量化参考`、`风控系数`、`建议比例`、`参考金额`、`影响研判`、`重点关注`。
4. **首屏直击核心决策三角（`Above-the-Fold`）**：
   - 顶栏下方首屏第一眼必须同时呈现：① **风险灯号卡**（灯号颜色 + 大白话结论 + 触发原因）、② **4 个辅助仓位建议卡**（`核心趋势仓 (BTC)`、`黄金避险仓`、`熊市配对对冲仓`、`超跌反弹短打仓`，严格遵守 `baseline_pct === 0` 则 `recommended_pct === 0`）、③ **美联储与跨资产核心温度横向条**（利率/美债/VIX/BTC/黄金/U溢价）。
5. **不打断输入铁律（防丢焦点 / 防拼音中断）**：
   - 顶栏「资金基数」输入框与「关键词筛选」输入框**永远作为静态 DOM 节点存在**，任何筛选或数据刷新都**严禁通过 `innerHTML` 替换包裹输入框的父容器**。
   - 「资金基数」只在按下 `Enter` 或点击「应用」按钮时更新本金并重算金额展示；
   - 「关键词筛选」只在按下 `Enter` 或点击「筛选」按钮时提交搜索词并刷新结果区（`input`/`change` 事件**绝不**触发列表重建或 DOM 刷新）。
6. **选中具体分类时动态隐藏无关控件与区块**：
   - 顶部分类胶囊（`全部总览` / `宏观与美联储` / `美股与科技` / `加密与仓位` / `AI与科技风向` / `观点与深读`）作为唯一视图总控：
   - 选中非「全部总览 / 加密与仓位」的分类时，**自动隐藏顶栏与「资金基数 / 显示参考金额」强绑定且当前视图用不到的调参控件**（或在非仓位视角下自动折叠 `仓位与资金控件` 组），同时隐藏无关业务区块与无关子来源筛选项，确保屏幕只留当前所选分类的高信噪比信息。
7. **参考金额默认显示且可一键切换**：
   - 页面加载默认开启「显示参考金额」（`showAmounts = true`），在 4 个仓位建议卡/对照表内直接显示依据当前资金基数折算的美元参考金额（如 `$35,000`）；点击顶栏「隐藏参考金额 / 显示参考金额」按钮可一键切显隐，状态持久化到 `localStorage`。
8. **双栏主体编辑台分工明确**：
   - 进入「全部总览」首屏以下为主编辑台（桌面 `grid-template-columns: minmax(0, 1.15fr) minmax(350px, 0.85fr)`，gap `24px`）：
     - **左栏（快讯与 AI 风向 `signals`）**：融合 `AIHOT` 的结构（来源标签 + 时间 + 倾向圆点 + 标题 + 摘要 + **青色强调的 `影响研判`（`why_it_matters`）** + 关联标的）。
     - **右栏（美股定调与指数 + 七巨头/AI算力 + KOL 观点）**：上段复用 `FiNews` 的超紧凑行情行表（`summary` 定调一句 + 指数 + 七巨头/AI算力标签组 + 板块双向涨跌条）；下段复用 `MarketBrew` 风格 KOL 观点卡，按「宏观与风控」「加密与链上」分组，点击关联长文直接滑出阅读抽屉。
9. **单文件离线与在线双模加载（`file://` 零报错）**：
   - `app.js` 启动顺序：优先检查 `window.__INITIAL_COCKPIT_STATE__`（供 `server.py --build-static` 注入打包成 `dist/macro_cockpit_offline.html` 双击直接打开）；若未注入则 `fetch('/api/state')`，HTTP 异常时再降级尝试 `../data/cockpit_state.json`。
   - `index.html` 中对 `styles.css` 与 `app.js` 使用干净的标准引入标签（便于后端静态打包正则/标记精确内联替换）。
10. **移动端推送安全闭环**：
    - 「推送手机」按钮永远先以 `POST /api/push` 携带 `{"dry_run": true}` 弹出预览抽屉，展示当前已配置通道（飞书个人私信 / ntfy / Bark / Telegram / Webhook）与即将发送的精简摘要全文；只有用户二次点击「确认发送」才发起 `{"live": true}` 真实推送，绝不无提示自动外发。

---

## 五、给 `frontend-station` 的组件与布局速查映射表

| `cockpit_state.json` 字段 | 对应前端模块 | 参考原型来源 | 移动端归属 Tab |
| :--- | :--- | :--- | :--- |
| `risk_gate` + `positions` | 首屏风险灯号条 + 4 张辅助仓位建议卡（含资金基数联动与金额开关） | 原 `cockpit.html`（去黑话、补齐日间纸感） | `仓位` |
| `macro_fred` | 首屏下方横向核心温度卡带（利率/美债/VIX/BTC/黄金/U溢价） | `FiNews` `.gauges` + 手机横向 `scroll-snap` 滑轨 | `仓位`（首屏紧随） |
| `signals` | 左主栏统一信号时间流（带来源/分类/关键词回车筛选与「影响研判」栏） | `AIHOT` `FeedItem.tsx` + `Watcher` `.note` | `信号` |
| `us_equities` | 右栏上段盘后定调 + 指数 + 七巨头 & AI 算力表 + 行业双向强弱条 | `FiNews` `.rail` + `daily_report_template.html` `.sec-bar` | `美股` |
| `kol_digest` | 右栏下段 KOL 多空观点卡（含点击打开长文抽屉入口） | `MarketBrew` 叙事观点卡 | `观点` |
| `articles` | 底部/专属「深度长文」索引 + 侧滑/底部抽屉 Markdown 阅读器 | `Watcher` `.wrap (860px)` + `AIHOT` `Sheet.tsx` | `深读` |
| `push_channels` | 顶栏「推送手机」触发预览抽屉（先 `dry_run` 预览后确认 `live` 发送） | `Watcher` `<dialog>` + `AIHOT` `Sheet.tsx` | 顶栏全局入口 |
