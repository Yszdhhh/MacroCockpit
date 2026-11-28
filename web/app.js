/* 宏观决策情报站 · Wave 1 四入口前端渲染与交互 */
(function () {
  "use strict";

  const DEFAULT_CAPITAL = 100000;
  const STORAGE_KEYS = {
    theme: "macro_cockpit_theme_v1",
    capital: "macro_cockpit_capital_v1",
    showAmt: "macro_cockpit_show_amt_v1",
    currency: "macro_cockpit_currency_v1",
    readBriefs: "macro_cockpit_read_briefs_v1",
    readEvents: "macro_cockpit_read_events_v1",
    newsMode: "macro_cockpit_news_mode_v1",
    feedView: "macro_cockpit_feed_view_v1"
  };

  const TOP_VIEWS = ["today", "portfolio", "research", "records"];
  const CATEGORY_LABELS = { macro: "宏观", equities: "美股", ashare: "A股·港股", ai: "AI", crypto: "加密" };
  const SECTOR_ORDER = ["equities", "ashare", "ai", "crypto", "macro"];
  const SECTOR_FULL_LABELS = {
    all: "全部",
    equities: "美股",
    ashare: "A股·港股",
    ai: "AI",
    crypto: "加密",
    macro: "宏观"
  };
  const SECTOR_SUBTOPICS = {
    equities: [
      { label: "七巨头·云厂", keys: ["七巨头·云厂", "科技巨头", "七巨头", "云厂", "nvda", "aapl", "msft", "goog", "amzn", "meta", "tsla", "苹果", "微软", "谷歌", "亚马逊", "特斯拉"] },
      { label: "算力芯片", keys: ["算力芯片", "nvidia", "英伟达", "amd", "tsmc", "台积电", "avgo", "broadcom", "hbm", "gpu", "芯片", "半导体"] },
      { label: "财报业绩", keys: ["财报业绩", "财报", "业绩", "指引", "eps", "营收", "earnings", "guidance"] },
      { label: "自动驾驶", keys: ["自动驾驶", "fsd", "robotaxi", "waymo", "智能驾驶"] },
      { label: "指数·盘面", keys: ["指数·盘面", "标普", "纳指", "道指", "spx", "spy", "qqq", "ndx", "盘前", "盘中"] }
    ],
    ashare: [
      { label: "科创·半导体", keys: ["科创·半导体", "科创", "半导体", "中芯", "寒武纪", "光刻", "688"] },
      { label: "华为·汽车", keys: ["华为·汽车", "华为", "鸿蒙", "问界", "比亚迪", "小米", "理想", "小鹏", "蔚来", "新能源车", "汽车"] },
      { label: "港股科技", keys: ["港股科技", "港股", "恒生", "恒科", "腾讯", "阿里", "美团", "网易", "快手", "京东", "百度", "hsi"] },
      { label: "A股政策·两融", keys: ["a股政策·两融", "两融", "融资融券", "证监会", "两会", "北向", "南向", "上证", "深证", "创业板", "央企", "国企"] },
      { label: "高股息·金融", keys: ["高股息·金融", "高股息", "红利", "银行", "保险", "券商", "中特估", "沪深300"] }
    ],
    ai: [
      { label: "DeepSeek", keys: ["deepseek", "深度求索", "r1", "v3"] },
      { label: "OpenAI", keys: ["openai", "chatgpt", "gpt-", "o1", "o3", "sora", "altman"] },
      { label: "Anthropic", keys: ["anthropic", "claude"] },
      { label: "国产大模型", keys: ["国产大模型", "qwen", "千问", "智谱", "kimi", "豆包", "文心", "混元", "glm"] },
      { label: "算力芯片", keys: ["算力芯片", "gpu", "h100", "b200", "blackwell", "nvidia", "英伟达", "tpu", "算力", "数据中心", "推理"] },
      { label: "具身智能", keys: ["具身智能", "机器人", "人形", "optimus", "宇树", "figure", "具身"] },
      { label: "开源·Agent", keys: ["开源·agent", "agent", "智能体", "开源", "llama", "gemini", "mcp"] }
    ],
    crypto: [
      { label: "BTC·链上生态", keys: ["btc·链上生态", "btc", "比特币", "bitcoin", "减半", "矿工", "闪电网络", "链上"] },
      { label: "ETH·生态", keys: ["eth·生态", "eth", "以太坊", "ethereum", "sol", "solana", "layer2", "defi", "质押"] },
      { label: "ETF·监管", keys: ["etf·监管", "etf", "sec", "cftc", "监管", "法案", "牌照", "合规", "贝莱德"] },
      { label: "稳定币·资金", keys: ["稳定币·资金", "稳定币", "usdt", "usdc", "场外", "净流入", "爆仓", "清算", "资金费率", "mstr"] }
    ],
    macro: [
      { label: "美联储", keys: ["美联储", "fed", "fomc", "鲍威尔", "降息", "加息", "缩表", "点阵图", "利率"] },
      { label: "CPI·通胀", keys: ["cpi·通胀", "cpi", "pce", "ppi", "通胀", "非农", "就业", "失业率", "gdp"] },
      { label: "美债·汇率", keys: ["美债·汇率", "美债", "国债", "10y", "2y", "收益率", "利差", "美元", "dxy", "日元", "人民币", "流动性"] },
      { label: "黄金·大宗", keys: ["黄金·大宗", "黄金", "gold", "xau", "白银", "原油", "wti", "brent", "铜", "大宗"] }
    ]
  };
  const DIRECTION_META = {
    bullish: { label: "偏多", cls: "bull" },
    bearish: { label: "偏空", cls: "bear" },
    neutral: { label: "中性", cls: "neutral" }
  };
  const STANCE_CLS = { "偏多": "bull", "偏空": "bear", "中性": "neutral" };
  const ARTICLE_STATUS_LABELS = { unread: "未读", reading: "在读", read: "已读", starred: "星标" };
  const CLOSED_REVIEW_STATUSES = new Set(["已止盈", "已止损", "已取消"]);
  const RISK_LIGHT_TEXT = { GREEN: "绿灯", YELLOW: "黄灯", RED: "红灯" };
  const GLANCE_IDS = [
    "fedfunds", "us_10y", "us_10y_2y_spread", "fed_net_liquidity", "vix",
    "sp500", "btc_spot", "gold", "otc_usdt_premium"
  ];
  const FOCUS_SHORT_LABELS = {
    btc_spot: "BTC",
    us_10y: "美债10Y",
    vix: "VIX",
    sp500: "标普500",
    otc_usdt_premium: "U溢价"
  };
  const DIVERGENCE_LEVEL = { "显著背离": "warn", "极值警戒": "alert" };
  const BLANK_TOKENS = new Set(["", "null", "undefined", "nan", "--", "—"]);
  const BUCKET_NAMES = {
    btc_trend: "核心趋势仓 (BTC)",
    gold_hedge: "黄金避险仓",
    bear_basket: "熊市配对对冲仓",
    crash_bounce: "超跌反弹短打仓",
    other: "其他"
  };
  const CHANNEL_DEFAULT_LABELS = {
    feishu_dm: "飞书个人私信 Card 2.0",
    wecom_bot: "企业微信/微信插件",
    wechat_push: "个人微信直推",
    ntfy: "ntfy 手机锁屏推送",
    bark: "Bark iOS 推送",
    telegram: "Telegram 推送",
    webhook: "外部 Webhook"
  };
  const CASH_SYMBOLS = new Set(["USD", "USDT", "USDC", "CNY", "CNH", "HKD", "CASH"]);
  const INSTRUMENT_ALIASES = {
    NVDA: ["英伟达", "nvidia"],
    AAPL: ["苹果", "apple"],
    MSFT: ["微软", "microsoft"],
    GOOG: ["谷歌", "google", "alphabet", "deepmind"],
    GOOGL: ["谷歌", "google", "alphabet", "deepmind"],
    AMZN: ["亚马逊", "amazon", "aws"],
    META: ["meta", "扎克伯格", "llama"],
    TSLA: ["特斯拉", "tesla", "马斯克"],
    AMD: ["超威", "苏姿丰"],
    TSM: ["台积电", "tsmc"],
    AVGO: ["博通", "broadcom"],
    INTC: ["英特尔", "intel"],
    MU: ["美光", "micron"],
    SMCI: ["超微电脑"],
    MSTR: ["microstrategy"],
    COIN: ["coinbase"],
    PLTR: ["palantir"],
    BABA: ["阿里巴巴", "阿里", "阿里云", "通义"],
    9988: ["阿里巴巴", "阿里", "阿里云", "通义", "baba"],
    TCEHY: ["腾讯", "微信"],
    "0700": ["腾讯", "微信", "tcehy"],
    PDD: ["拼多多", "temu"],
    JD: ["京东"],
    9618: ["京东", "jd"],
    BIDU: ["百度"],
    9888: ["百度", "bidu"],
    BYDDY: ["比亚迪", "byd"],
    1211: ["比亚迪", "byd"],
    "002594": ["比亚迪", "byd"],
    XIACY: ["小米"],
    1810: ["小米"],
    SMICY: ["中芯国际", "中芯"],
    688981: ["中芯国际", "中芯"],
    688256: ["寒武纪"],
    600519: ["贵州茅台", "茅台"],
    300750: ["宁德时代"],
    BTC: ["比特币", "bitcoin"],
    BNB: ["币安", "binance"],
    ETH: ["以太坊", "ethereum"],
    SOL: ["solana"],
    XAU: ["黄金", "现货黄金", "金价"],
    PAXG: ["黄金", "现货黄金", "伦敦金", "金价", "gold", "xau", "gld"],
    GLD: ["黄金", "现货黄金", "金价"],
    IAU: ["黄金", "现货黄金", "金价"],
    GC: ["黄金", "现货黄金", "金价"],
    QQQ: ["纳指", "纳斯达克", "纳斯达克100", "nasdaq", "ndx"],
    ZHIPU: ["智谱", "智谱ai", "2513.hk", "02513", "glm", "chatglm", "autoglm"],
    2513: ["智谱", "智谱ai", "zhipu", "glm", "chatglm", "autoglm"]
  };

  const appState = {
    raw: null,
    workspace: null,
    journal: { trade_reviews: [], essays: [], article_status: {} },
    offline: false,
    serverRole: "",
    lastGeneration: 0,
    lastPollAt: 0,
    pollTimerId: 0,
    capitalBase: DEFAULT_CAPITAL,
    currency: "USD",
    showAmounts: true,
    theme: "light",
    view: "today",
    portfolioSub: "spot",
    researchSub: "stocks",
    recordsSub: "reviews",
    stockSymbol: "AAPL",
    stockMarket: "all",
    stockRange: "1y",
    customStocks: {},
    daily: {
      scope: "all",
      cat: "all",
      subtopic: "all",
      pol: "all",
      highOnly: false,
      keyword: "",
      visibleLimit: 15,
      mode: (() => {
        try {
          return localStorage.getItem(STORAGE_KEYS.newsMode) === "timeline" ? "timeline" : "ranked";
        } catch (_e) {
          return "ranked";
        }
      })(),
      feedView: (() => {
        try {
          return localStorage.getItem(STORAGE_KEYS.feedView) === "wire" ? "wire" : "events";
        } catch (_e) {
          return "events";
        }
      })()
    },
    readBriefs: (() => {
      try {
        const raw = localStorage.getItem(STORAGE_KEYS.readBriefs);
        const parsed = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(parsed) ? parsed.slice(-300) : []);
      } catch (_e) {
        return new Set();
      }
    })(),
    readEvents: (() => {
      try {
        const raw = localStorage.getItem(STORAGE_KEYS.readEvents);
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
      } catch (_e) {
        return {};
      }
    })(),
    expandedEvents: new Set(),
    openSignalMenu: "",
    openScoreId: "",
    lastUndoAction: null,
    kol: { topic: "all", stance: "all", keyword: "", view: "feed", highWinOnly: false },
    articles: { status: "all", source: "all", keyword: "" },
    sources: { category: "all" },
    reviewStatus: "all",
    editingReviewTags: [],
    corrWindow: "30d",
    corrPinned: null,
    corrDefaultText: "",
    drawerArticle: null,
    drawerReturnFocus: null,
    pushChecked: null,
    pushPreviewed: false,
    pushScenario: "daily",
    pushPreviewFormat: "card2",
    lastPushPayload: null,
    pfKind: "spot",
    importPreview: null,
    previewReqSeq: 0,
    positionFormInitial: null,
    maskedPositionValues: null,
    drCapabilities: null,
    drMode: "paste",
    drJobPollTimer: 0,
    drActiveJobId: "",
    drafts: {
      review: null,
      essay: null,
      importText: ""
    },
    busy: {}
  };

  const dom = {};

  function cacheDom() {
    const ids = [
      "metaThemeColor", "generatedAt", "riskPill", "riskPillText",
      "capitalGroup", "capitalInput", "applyCapitalBtn", "currencySeg", "toggleAmtBtn",
      "themeToggleBtn", "refreshBtn", "openPushBtn", "logoutBtn", "viewNav",
      "toast", "undoBar", "undoText", "undoBtn", "undoDismissBtn", "mobileFocusBar",
      "todayOverviewBar", "sectorRibbon", "feedViewSwitch", "newsModeSwitch", "dailyGlance", "execStrip", "execGate", "execPositions",
      "signalsHeading", "signalScopeChips", "polarityChips", "highOnlyBtn", "keywordInput",
      "keywordApplyBtn", "dailyCounter", "curationRulesPanel", "closeRulesPanelBtn",
      "ruleQuickForm", "ruleKindSelect", "ruleActionSelect", "ruleTargetInput",
      "curationRulesList", "sectorDrillBar", "backToOverviewBtn", "subtopicChips",
      "signalList", "todaySentimentCard", "todayCalendarCard", "todayRiskCard",
      "eqSummary", "eqAsOf", "eqIndices", "eqWatch", "eqSectors", "heatRadar",
      "portfolioModeHint", "openAddPosBtn", "openImportBtn", "portfolioSummaryBar",
      "portfolioSubTabs", "spotFxHint", "spotTableWrap", "derivSummaryHint",
      "derivTableWrap", "derivMobileList", "journalCapitalHint", "journalGate",
      "strategyCompareWrap", "journalPositions",
      "researchSubTabs", "stockMarketTabs", "stockChips", "stockSearchInput", "stockSearchBtn", "stockSuggest",
      "stockBanner", "stockChartRet", "stockRangeSeg", "stockChartReadout",
      "stockChartWrap", "stockSummaryPanel", "stockInsightsCol", "stockSectionsCol",
      "macroCount", "macroGroups", "corrWindowSeg", "corrReadout", "corrMatrix",
      "pairsAsOf", "pairsList",
      "marketSynthesisPanel", "marketStanceBadge", "marketAsOf", "marketVerdict",
      "marketDimensions", "marketDivergenceAlerts", "crossSourceHead", "crossSourceCount",
      "crossSourceLinks",
      "forumSentimentPanel", "forumDiscussionTrend", "forumContrarianBadge", "forumOverallBadge", "forumChannelsBar", "forumMacroStrip", "forumDivergenceGrid",
      "forumHotWrap", "forumUpdatedAt", "forumHotList",
      "kolViewSwitch", "kolHighWinBtn", "kolToolbar", "kolTopicChips", "kolStanceChips", "kolSearchInput", "kolSearchBtn",
      "kolCrowdStrip", "kolTugPanel", "kolTug", "kolTopicBars", "kolTimeline",
      "kolScorecardPanel", "kolScorecardCount", "kolScorecardWrap",
      "articleStatusTabs", "articleSourceChips", "articleSearchInput", "articleSearchBtn",
      "exportOpmlBtn", "openIngestDrBtn", "articleList",
      "sourceCategoryChips", "sourcesMeta", "sourcesGrid",
      "recordsSubTabs", "journalModeHint", "signalPlanStatsBar", "newReviewBtn", "reviewStatusChips",
      "reviewForm", "rvId", "rvRev", "rvPlanId", "rvDate", "rvSymbol", "rvDirection", "rvSize",
      "rvPrice", "rvPnl", "rvStatus", "rvThesis", "rvExec", "rvLesson", "rvSaveBtn",
      "rvCancelBtn", "reviewList",
      "newEssayBtn", "essayForm", "esId", "esRev", "esTitle", "esMood", "esTags",
      "esBody", "esPinned", "esSaveBtn", "esCancelBtn", "essayList",
      "articleDrawer", "articleMeta", "articleStatusSeg", "closeArticleBtn",
      "articleScroll", "articleTitle", "articleBody", "articleNote", "noteSavedHint",
      "saveNoteBtn",
      "pushDrawer", "closePushBtn", "pushScenarioSeg", "pushChannels",
      "pushPreviewFormatSeg", "pushPreviewBtn", "pushLiveBtn", "pushHint",
      "pushCard2Preview", "pushPreview", "museDigestUrl", "museFeedUrl", "copyMuseBtn",
      "positionDrawer", "posDrawerTitle", "closePosDrawerBtn", "positionForm",
      "pfId", "pfKindSeg", "pfAccount", "pfSymbol", "pfAssetClassWrap", "pfAssetClass",
      "pfDirWrap", "pfSide", "pfMarginModeWrap", "pfMarginMode", "pfQty", "pfEntry",
      "pfMarginWrap", "pfMargin", "pfLiqWrap", "pfLiq", "pfBucket", "pfNote",
      "pfSaveBtn", "pfCancelBtn",
      "importDrawer", "closeImportDrawerBtn", "importForm", "impFormat", "impMode",
      "impAccount", "impBody", "impPreviewBtn", "impCommitBtn", "importPreviewArea",
      "ingestDrDrawer", "drModeSwitch", "closeIngestDrBtn", "drRunForm", "drRunTitleInput",
      "drRunTickersInput", "drRunPromptInput", "drJobBox", "drJobStatusBadge", "drJobMetaText",
      "drRunBtn", "drRunCancelBtn", "ingestDrForm", "drTitleInput", "drTickersInput",
      "drBodyInput", "drSaveBtn", "drCancelBtn",
      "mobileTabbar"
    ];
    ids.forEach((id) => {
      dom[id] = document.getElementById(id);
    });
    appState.corrDefaultText = dom.corrReadout ? dom.corrReadout.textContent : "";
  }

  /* ---------- 通用工具 ---------- */

  function safeStorageGet(key) {
    try { return window.localStorage.getItem(key); } catch (_) { return null; }
  }

  function safeStorageSet(key, val) {
    try { window.localStorage.setItem(key, String(val)); } catch (_) { /* ignore */ }
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function stripEmojis(str) {
    return String(str ?? "")
      .replace(/[\u{1F1E6}-\u{1F1FF}\u{1F300}-\u{1FAFF}\u2600-\u26FF\u2702-\u2712\u2716-\u27BF\u231A-\u231B\u23E9-\u23FA\u25FD-\u25FE\u2B50\u2B55\uFE0E\uFE0F\u200D\u20E3]+/gu, "")
      .replace(/[ \t]{2,}/g, " ")
      .trim();
  }

  function normDupKey(str) {
    return String(str ?? "")
      .normalize("NFKC")
      .replace(/\u2212/g, "-")
      .toLowerCase()
      .replace(/(?<!\d)\.(?!\d)/g, "")
      .replace(/[^\p{L}\p{N}+\-%‰$¥€£.]+/gu, "");
  }

  function cleanSignalSummary(title, summary) {
    const t = stripEmojis(title);
    let s = stripEmojis(summary);
    if (!s) return "";
    const bracket = s.match(/^【([^】]{2,140})】\s*(.*)$/);
    if (bracket) {
      const nt = normDupKey(t);
      const nb = normDupKey(bracket[1]);
      if (nb && nt && nb === nt) {
        s = bracket[2].trim();
      }
    }
    s = s.replace(/^(?:财联社|华尔街见闻|科创板日报|证券时报|新京报)\s*\d{1,2}月\d{1,2}日(?:电|讯)[，,：:\s]*/, "").trim();
    if (!s) return "";
    const nt = normDupKey(t);
    const ns = normDupKey(s);
    if (nt && (ns === nt || ((s.endsWith("...") || s.endsWith("…")) && ns.length >= 10 && nt.startsWith(ns)))) return "";
    return s;
  }

  function isGenericSourceWhy(text) {
    const s = stripEmojis(text);
    if (!s) return true;
    return /^(?:国内一线中文电报实时快讯|全球宏微观实时编译电讯|主流加密机构与监管核心媒体动态|方程式(?:加密|海外传统金融|全球宏观)一线?突发快讯|中文科技一线报道|来自精选外部信息源|全球财经头条|华尔街核心财报|道琼斯旗下盘前盘后异动|金融时报全球跨资产|美国银行体系|OpenAI 一手产品|Google |DeepMind 前沿|Anthropic |Hugging Face |arXiv |Hacker News )/.test(s);
  }

  function safeHttpUrl(url) {
    if (!url || typeof url !== "string") return "";
    const trimmed = url.trim();
    return /^https?:\/\//i.test(trimmed) ? trimmed : "";
  }

  function isBlank(val) {
    if (val === null || val === undefined) return true;
    return BLANK_TOKENS.has(String(val).trim().toLowerCase());
  }

  function toNum(val) {
    if (val === null || val === undefined || val === "") return NaN;
    const n = Number(val);
    return Number.isFinite(n) ? n : NaN;
  }

  function arr(val) {
    return Array.isArray(val) ? val : [];
  }

  function state() {
    return appState.raw || {};
  }

  function newReqId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return "req-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
  }

  function macroById() {
    const map = {};
    validMacroItems().forEach((m) => { map[m.id] = m; });
    return map;
  }

  function getFxContext() {
    const ws = appState.workspace || {};
    const fx = ws.fx_snapshot || state().fx_snapshot || {};
    const cur = appState.currency === "CNY" ? "CNY" : "USD";
    if (cur === "USD") {
      return { currency: "USD", rate: 1, symbol: "$", available: true, label: "美元基准", as_of: "" };
    }
    if (fx.status === "unavailable") {
      return { currency: "CNY", rate: null, symbol: "¥", available: false, label: "人民币估值不可用", as_of: fx.as_of || "" };
    }
    let rate = toNum(fx.rate);
    let label = fx.label || "人民币估值";
    if (!Number.isFinite(rate) || rate <= 0) {
      const mFx = macroById().usdcnh;
      if (mFx && Number.isFinite(toNum(mFx.raw_value)) && toNum(mFx.raw_value) > 0) {
        rate = toNum(mFx.raw_value);
        label = "离岸人民币估值";
      }
    }
    if (!Number.isFinite(rate) || rate <= 0) {
      return { currency: "CNY", rate: null, symbol: "¥", available: false, label: "人民币估值不可用", as_of: fx.as_of || "" };
    }
    return { currency: "CNY", rate, symbol: "¥", available: true, label, as_of: fx.as_of || "" };
  }

  function formatDisplayMoney(usdVal, opts) {
    const n = toNum(usdVal);
    if (!Number.isFinite(n)) return "";
    const o = opts || {};
    const fx = getFxContext();
    if (fx.currency === "CNY" && !fx.available) return "人民币估值不可用";
    const scaled = n * (fx.rate || 1);
    const sign = scaled < 0 ? "-" : o.signed && scaled > 0 ? "+" : "";
    const digits = o.decimals != null ? o.decimals : 0;
    return sign + fx.symbol + Math.abs(scaled).toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  function formatMoney(num, signed) {
    const n = toNum(num);
    if (!Number.isFinite(n)) return "";
    const sign = n < 0 ? "-" : signed && n > 0 ? "+" : "";
    return sign + "$" + Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
  }

  function formatAssetPrice(num, curSym) {
    const n = toNum(num);
    if (!Number.isFinite(n) || n <= 0) return "";
    const sym = curSym || "$";
    const digits = n >= 1000 ? 2 : n >= 1 ? 2 : n >= 0.05 ? 4 : 6;
    return sym + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: digits });
  }

  function formatPrice(num) {
    return formatAssetPrice(num, "$");
  }

  function formatSignedPct(num) {
    const n = toNum(num);
    if (!Number.isFinite(n)) return "";
    return (n > 0 ? "+" : "") + n.toFixed(2) + "%";
  }

  function formatWeight(num) {
    const n = toNum(num);
    if (!Number.isFinite(n)) return "0%";
    return (Number.isInteger(n) ? String(n) : n.toFixed(1)) + "%";
  }

  function formatCorr(num) {
    const n = toNum(num);
    if (!Number.isFinite(n)) return "";
    return (n > 0 ? "+" : "") + n.toFixed(2);
  }

  function toneClass(num) {
    const n = toNum(num);
    if (!Number.isFinite(n) || Math.abs(n) < 1e-9) return "tone-flat";
    return n > 0 ? "tone-up" : "tone-down";
  }

  function bjtNow() {
    return new Date(Date.now() + 8 * 3600 * 1000);
  }

  function todayBjt() {
    return bjtNow().toISOString().slice(0, 10);
  }

  function nowBjtIso() {
    return bjtNow().toISOString().slice(0, 19) + "+08:00";
  }

  function shortStamp(ts) {
    const s = String(ts || "");
    return s.length >= 16 ? s.slice(0, 16).replace("T", " ") : s;
  }

  function setActive(container, attr, value) {
    if (!container) return;
    container.querySelectorAll("[" + attr + "]").forEach((btn) => {
      const on = btn.getAttribute(attr) === value;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", String(on));
    });
  }

  function isImeEnter(event) {
    return event.isComposing || event.keyCode === 229;
  }

  let toastTimer = null;
  function showToast(msg, tone) {
    if (!dom.toast) return;
    if (toastTimer) clearTimeout(toastTimer);
    dom.toast.textContent = msg;
    dom.toast.className = "toast" + (tone === "error" ? " is-error" : tone === "ok" ? " is-ok" : "");
    dom.toast.hidden = false;
    toastTimer = setTimeout(() => { dom.toast.hidden = true; }, 3600);
  }

  let undoTimer = null;
  function showUndoBar(text, undoFn) {
    if (!dom.undoBar) return;
    if (undoTimer) clearTimeout(undoTimer);
    appState.lastUndoAction = undoFn;
    if (dom.undoText) dom.undoText.textContent = text;
    dom.undoBar.hidden = false;
    undoTimer = setTimeout(() => {
      dom.undoBar.hidden = true;
      appState.lastUndoAction = null;
    }, 8000);
  }

  async function apiJson(url, opts) {
    const options = opts || {};
    const method = (options.method || "GET").toUpperCase();
    const headers = { Accept: "application/json" };
    if (method !== "GET" && method !== "HEAD") {
      headers["X-Macro-Request"] = "1";
    }
    const init = {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers
    };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const resp = await fetch(url, init);
    let data = null;
    try { data = await resp.json(); } catch (_) { data = null; }
    if (resp.status === 401) {
      window.location.href = "/login";
      throw new Error("登录已失效，请重新登录");
    }
    if (resp.status === 409) {
      const conflictErr = new Error((data && data.error) || "数据已被更新（版本冲突），请刷新后重试");
      conflictErr.status = 409;
      throw conflictErr;
    }
    if (!resp.ok) {
      const err = new Error((data && data.error) || "请求失败（" + resp.status + "）");
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) { /* fallback */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    ta.remove();
    return ok;
  }

  /* ---------- Markdown 渲染（先整体转义，再解析结构） ---------- */

  const MD_HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
  const MD_QUOTE = /^\s{0,3}&gt;\s?/;
  const MD_LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const MD_HEADING = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;
  const MD_FENCE = /^\s*(`{3,}|~{3,})/;

  function mdInline(text) {
    const tokens = [];
    const stash = (html) => "\u0000" + (tokens.push(html) - 1) + "\u0000";
    const fmt = (s) => s
      .replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_\n]+?)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*(?!\s)([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/~~([^~\n]+?)~~/g, "<del>$1</del>");
    const link = (href, label) => stash(
      '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + label + "</a>"
    );

    let s = String(text).replace(/\u0000/g, "");
    s = s.replace(/`([^`\n]+)`/g, (_, code) => stash("<code>" + code + "</code>"));
    s = s.replace(/!?\[([^\]\n]*)\]\(([^)\s]+)(?:\s+&quot;[^\n]*?&quot;)?\)/g, (m, label, rawUrl) => {
      const url = String(rawUrl || "").replace(/^&lt;|&gt;$/g, "");
      if (!/^https?:\/\//i.test(url)) return fmt(label);
      return link(url, label ? fmt(label) : url);
    });
    s = s.replace(/https?:\/\/[A-Za-z0-9\-._~:/?#[\]@!$&'()*+,;=%]+/g, (url) => {
      let clean = url;
      let tail = "";
      const trail = clean.match(/(?:[.,;:!?)]|&quot;|&#39;|&gt;)+$/);
      if (trail) {
        tail = trail[0];
        clean = clean.slice(0, -tail.length);
      }
      return link(clean, clean) + tail;
    });
    s = fmt(s);
    let guard = 0;
    while (s.indexOf("\u0000") !== -1 && guard < 5) {
      s = s.replace(/\u0000(\d+)\u0000/g, (_, idx) => tokens[Number(idx)] || "");
      guard += 1;
    }
    return s;
  }

  function mdIsTableStart(lines, i) {
    const head = lines[i];
    const sep = lines[i + 1];
    if (!head || !sep || head.indexOf("|") === -1) return false;
    return /^[\s|:-]+$/.test(sep) && sep.indexOf("-") !== -1 && sep.indexOf("|") !== -1;
  }

  function mdIsBlockStart(lines, i) {
    const line = lines[i];
    return MD_FENCE.test(line) || MD_HEADING.test(line) || MD_HR.test(line) ||
      MD_QUOTE.test(line) || MD_LIST.test(line) || mdIsTableStart(lines, i);
  }

  function mdSplitRow(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|")) s = s.slice(0, -1);
    return s.split("|").map((c) => c.trim());
  }

  function mdTable(lines, start) {
    const header = mdSplitRow(lines[start]);
    const aligns = mdSplitRow(lines[start + 1]).map((c) => {
      const l = c.startsWith(":");
      const r = c.endsWith(":");
      return l && r ? "center" : r ? "right" : "";
    });
    const cell = (tag, txt, idx) => {
      const al = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : "";
      return "<" + tag + al + ">" + mdInline(txt) + "</" + tag + ">";
    };
    let i = start + 2;
    const rows = [];
    while (i < lines.length && lines[i].trim() && lines[i].indexOf("|") !== -1) {
      const cells = mdSplitRow(lines[i]);
      rows.push("<tr>" + header.map((_, idx) => cell("td", cells[idx] || "", idx)).join("") + "</tr>");
      i += 1;
    }
    const html = '<div class="table-wrap"><table><thead><tr>' +
      header.map((h, idx) => cell("th", h, idx)).join("") +
      "</tr></thead><tbody>" + rows.join("") + "</tbody></table></div>";
    return { html, next: i };
  }

  function mdList(lines, start) {
    const items = [];
    let i = start;
    while (i < lines.length) {
      const line = lines[i];
      const m = line.match(MD_LIST);
      if (m && !MD_HR.test(line)) {
        const ordered = /\d/.test(m[2]);
        items.push({
          indent: m[1].replace(/\t/g, "    ").length,
          tag: ordered ? "ol" : "ul",
          start: ordered ? parseInt(m[2], 10) : 1,
          text: [m[3]]
        });
        i += 1;
        continue;
      }
      if (!line.trim()) {
        let j = i + 1;
        while (j < lines.length && !lines[j].trim()) j += 1;
        if (j < lines.length && MD_LIST.test(lines[j]) && !MD_HR.test(lines[j])) {
          i = j;
          continue;
        }
        break;
      }
      if (items.length && !mdIsBlockStart(lines, i)) {
        items[items.length - 1].text.push(line.trim());
        i += 1;
        continue;
      }
      break;
    }

    let html = "";
    const stack = [];
    items.forEach((it) => {
      while (stack.length && it.indent < stack[stack.length - 1].indent) {
        html += "</li></" + stack.pop().tag + ">";
      }
      const top = stack[stack.length - 1];
      const open = (item) => "<" + item.tag + (item.tag === "ol" && item.start !== 1 ? ' start="' + item.start + '"' : "") + ">";
      if (!top || it.indent > top.indent) {
        html += open(it);
        stack.push({ indent: it.indent, tag: it.tag });
      } else {
        html += "</li>";
        if (top.tag !== it.tag) {
          html += "</" + top.tag + ">" + open(it);
          top.tag = it.tag;
        }
      }
      html += "<li>" + it.text.map(mdInline).join("<br>");
    });
    while (stack.length) {
      html += "</li></" + stack.pop().tag + ">";
    }
    return { html, next: i };
  }

  function mdBlocks(lines) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }
      const fence = line.match(MD_FENCE);
      if (fence) {
        const marker = fence[1];
        const buf = [];
        i += 1;
        while (i < lines.length && !lines[i].trim().startsWith(marker)) {
          buf.push(lines[i]);
          i += 1;
        }
        i += 1;
        const canDirectDr = !appState.offline && Boolean(appState.drCapabilities && appState.drCapabilities.can_run_any_dr);
        out.push(
          '<div class="code-copy-wrap"><div class="code-copy-actions">' +
          '<button type="button" class="btn btn-sm code-copy-btn" data-copy-code>复制提问词</button>' +
          (canDirectDr ? '<button type="button" class="btn btn-sm btn-accent code-copy-btn" data-run-dr-code>直接深研</button>' : "") +
          "</div><pre><code>" + buf.join("\n") + "</code></pre></div>"
        );
        continue;
      }
      const heading = line.match(MD_HEADING);
      if (heading) {
        const lvl = Math.min(heading[1].length + 1, 6);
        out.push("<h" + lvl + ">" + mdInline(heading[2]) + "</h" + lvl + ">");
        i += 1;
        continue;
      }
      if (MD_HR.test(line)) { out.push("<hr>"); i += 1; continue; }
      if (MD_QUOTE.test(line)) {
        const buf = [];
        while (i < lines.length && MD_QUOTE.test(lines[i])) {
          buf.push(lines[i].replace(MD_QUOTE, ""));
          i += 1;
        }
        out.push("<blockquote>" + mdBlocks(buf) + "</blockquote>");
        continue;
      }
      if (mdIsTableStart(lines, i)) {
        const t = mdTable(lines, i);
        out.push(t.html);
        i = t.next;
        continue;
      }
      if (MD_LIST.test(line)) {
        const l = mdList(lines, i);
        out.push(l.html);
        i = l.next;
        continue;
      }
      const buf = [line.trim()];
      i += 1;
      while (i < lines.length && lines[i].trim() && !mdIsBlockStart(lines, i)) {
        buf.push(lines[i].trim());
        i += 1;
      }
      out.push("<p>" + buf.map(mdInline).join("<br>") + "</p>");
    }
    return out.join("\n");
  }

  function stripFrontmatter(md) {
    const m = md.match(/^---\n([\s\S]*?)\n---[ \t]*(?:\n|$)/);
    if (!m) return md;
    const looksLikeMeta = m[1].split("\n").every((l) => !l.trim() || /^\s*[\w\u4e00-\u9fff-]+\s*:/.test(l) || /^\s+-\s/.test(l));
    return looksLikeMeta ? md.slice(m[0].length) : md;
  }

  function renderMarkdown(md) {
    const text = stripFrontmatter(String(md ?? "").replace(/\r\n?/g, "\n"));
    return mdBlocks(escapeHtml(text).split("\n"));
  }

  /* ---------- 偏好：主题 / 币种 / 全面脱敏隐藏金额 / 资金基数 ---------- */

  function applyTheme(theme, persist) {
    appState.theme = theme === "dark" ? "dark" : "light";
    const isDark = appState.theme === "dark";
    document.documentElement.setAttribute("data-theme", appState.theme);
    if (dom.metaThemeColor) dom.metaThemeColor.setAttribute("content", isDark ? "#100F09" : "#FAFAF9");
    if (dom.themeToggleBtn) {
      dom.themeToggleBtn.textContent = isDark ? "浅色" : "深色";
      dom.themeToggleBtn.setAttribute("aria-pressed", String(isDark));
    }
    if (persist) safeStorageSet(STORAGE_KEYS.theme, appState.theme);
  }

  function amtHtml(usd, opts) {
    if (!appState.showAmounts) {
      return '<span class="amt num is-masked" aria-label="金额已隐藏">••••</span>';
    }
    const o = opts || {};
    const n = toNum(usd);
    const v = Number.isFinite(n) ? n : 0;
    const cls = "amt num" + (o.cls ? " " + o.cls : "");
    const fx = getFxContext();
    if (fx.currency === "CNY" && !fx.available) {
      return '<span class="amt num tone-flat">人民币估值不可用</span>';
    }
    return '<span class="' + cls + '">' + escapeHtml(formatDisplayMoney(v, o)) + "</span>";
  }

  function applyShowAmounts(show, persist) {
    appState.showAmounts = Boolean(show);
    if (dom.toggleAmtBtn) {
      dom.toggleAmtBtn.textContent = appState.showAmounts ? "隐藏金额" : "显示金额";
      dom.toggleAmtBtn.setAttribute("aria-pressed", String(appState.showAmounts));
    }
    if (persist) safeStorageSet(STORAGE_KEYS.showAmt, appState.showAmounts ? "1" : "0");
    if (dom.capitalInput) {
      dom.capitalInput.value = appState.showAmounts ? String(appState.capitalBase) : "";
      dom.capitalInput.disabled = !appState.showAmounts;
    }
    if (dom.applyCapitalBtn) dom.applyCapitalBtn.disabled = !appState.showAmounts;
    if (appState.positionFormInitial) syncPositionAmountInputs();
    if (appState.raw) {
      renderAll();
      if (dom.pushDrawer && !dom.pushDrawer.hidden && appState.lastPushPayload) {
        renderPushVisual();
      }
    }
  }

  async function applyCurrency(cur, persist) {
    const next = cur === "CNY" ? "CNY" : "USD";
    appState.currency = next;
    setActive(dom.currencySeg, "data-currency", next);
    if (persist) safeStorageSet(STORAGE_KEYS.currency, next);
    if (appState.raw) renderAll();
    if (!appState.offline && appState.raw) {
      await refreshWorkspaceOnly();
    }
  }

  function loadPreferences() {
    applyTheme(document.documentElement.getAttribute("data-theme"), false);
    const savedCur = safeStorageGet(STORAGE_KEYS.currency) === "CNY" ? "CNY" : "USD";
    appState.currency = savedCur;
    setActive(dom.currencySeg, "data-currency", savedCur);
    const rawCap = safeStorageGet(STORAGE_KEYS.capital);
    const savedCap = Number(rawCap);
    if (rawCap !== null && rawCap !== "" && Number.isFinite(savedCap) && savedCap >= 0) {
      appState.capitalBase = Math.round(savedCap);
    }
    applyShowAmounts(safeStorageGet(STORAGE_KEYS.showAmt) !== "0", false);
  }

  function commitCapital() {
    if (!appState.showAmounts) return;
    const parsed = Number(dom.capitalInput.value);
    if (dom.capitalInput.value.trim() === "" || !Number.isFinite(parsed) || parsed < 0) {
      showToast("请输入有效的非负资金基数", "error");
      dom.capitalInput.value = String(appState.capitalBase);
      return;
    }
    appState.capitalBase = Math.round(parsed);
    dom.capitalInput.value = String(appState.capitalBase);
    safeStorageSet(STORAGE_KEYS.capital, appState.capitalBase);
    renderTodayOverviewBar();
    renderPositionsAll();
    showToast("资金基数已更新", "ok");
  }

  function positionNotional(pos) {
    const baseline = toNum(pos.baseline_pct) || 0;
    const rec = baseline === 0 ? 0 : toNum(pos.recommended_pct) || 0;
    if (rec === 0) return 0;
    const est = toNum(pos.est_notional_usd);
    if (Number.isFinite(est) && est > 0) return (est / DEFAULT_CAPITAL) * appState.capitalBase;
    return appState.capitalBase * (rec / 100);
  }

  function positionPct(pos) {
    const baseline = toNum(pos.baseline_pct) || 0;
    return baseline === 0 ? 0 : toNum(pos.recommended_pct) || 0;
  }

  /* ---------- 四入口导航与旧深链兼容 ---------- */

  function setSubTab(group, val) {
    if (group === "portfolio") {
      const next = ["spot", "derivatives", "strategy"].includes(val) ? val : "spot";
      appState.portfolioSub = next;
      setActive(dom.portfolioSubTabs, "data-psub", next);
      document.querySelectorAll("[data-psub-panel]").forEach((p) => {
        p.hidden = p.getAttribute("data-psub-panel") !== next;
      });
    } else if (group === "research") {
      const next = ["stocks", "macro", "sentiment", "kol", "articles", "sources"].includes(val) ? val : "stocks";
      appState.researchSub = next;
      setActive(dom.researchSubTabs, "data-rsub", next);
      document.querySelectorAll("[data-rsub-panel]").forEach((p) => {
        p.hidden = p.getAttribute("data-rsub-panel") !== next;
      });
    } else if (group === "records") {
      const next = ["reviews", "essays"].includes(val) ? val : "reviews";
      appState.recordsSub = next;
      setActive(dom.recordsSubTabs, "data-recsub", next);
      document.querySelectorAll("[data-recsub-panel]").forEach((p) => {
        p.hidden = p.getAttribute("data-recsub-panel") !== next;
      });
    }
  }

  function resolveRoute(target) {
    const raw = String(target || "today").trim();
    if (raw.indexOf("today:") === 0) {
      const sec = raw.slice(6).trim().toLowerCase();
      return { view: "today", sector: (sec === "all" || SECTOR_ORDER.includes(sec)) ? sec : "all" };
    }
    if (raw.indexOf("research:") === 0) {
      return { view: "research", sub: "stocks", stock: raw.slice(9).toUpperCase() };
    }
    if (raw.indexOf("portfolio:") === 0) {
      return { view: "portfolio", sub: raw.slice(10) || "spot" };
    }
    const map = {
      today: { view: "today" },
      daily: { view: "today" },
      portfolio: { view: "portfolio", sub: appState.portfolioSub },
      research: { view: "research", sub: "stocks" },
      stocks: { view: "research", sub: "stocks" },
      macro: { view: "research", sub: "macro" },
      sentiment: { view: "research", sub: "sentiment" },
      kol: { view: "research", sub: "kol" },
      articles: { view: "research", sub: "articles" },
      sources: { view: "research", sub: "sources" },
      records: { view: "records", sub: appState.recordsSub },
      journal: { view: "records", sub: "reviews" },
      reviews: { view: "records", sub: "reviews" },
      essays: { view: "records", sub: "essays" }
    };
    return map[raw] || { view: "today" };
  }

  function currentHash() {
    if (appState.view === "today") {
      return appState.daily.cat && appState.daily.cat !== "all" ? "#today:" + appState.daily.cat : "#today";
    }
    if (appState.view === "portfolio") return "#portfolio";
    if (appState.view === "research") {
      if (appState.researchSub === "stocks") return "#research:" + (appState.stockSymbol || "AAPL");
      return "#" + appState.researchSub;
    }
    return appState.recordsSub === "essays" ? "#essays" : "#journal";
  }

  function syncUrlHash() {
    try {
      window.history.replaceState(null, "", currentHash());
    } catch (_) { /* ignore file:// */ }
  }

  function setView(target, opts) {
    const route = resolveRoute(target);
    const next = TOP_VIEWS.includes(route.view) ? route.view : "today";
    const changed = next !== appState.view;
    appState.view = next;
    if (route.sector) {
      appState.daily.cat = route.sector;
      appState.daily.subtopic = "all";
    }
    if (route.stock) appState.stockSymbol = route.stock;
    if (route.sub) {
      if (next === "portfolio") setSubTab("portfolio", route.sub);
      else if (next === "research") setSubTab("research", route.sub);
      else if (next === "records") setSubTab("records", route.sub);
    } else {
      setSubTab("portfolio", appState.portfolioSub);
      setSubTab("research", appState.researchSub);
      setSubTab("records", appState.recordsSub);
    }

    document.body.setAttribute("data-view", next);
    if (dom.viewNav) {
      dom.viewNav.querySelectorAll(".view-tab[data-view]").forEach((tab) => {
        const on = tab.getAttribute("data-view") === next;
        tab.classList.toggle("is-active", on);
        if (on) tab.setAttribute("aria-current", "page");
        else tab.removeAttribute("aria-current");
      });
    }
    document.querySelectorAll("main > section.view[data-view]").forEach((sec) => {
      sec.hidden = sec.getAttribute("data-view") !== next;
    });
    if (dom.mobileTabbar) {
      dom.mobileTabbar.querySelectorAll(".tab[data-mtab]").forEach((tab) => {
        const on = tab.getAttribute("data-mtab") === next;
        tab.classList.toggle("is-active", on);
        if (on) tab.setAttribute("aria-current", "page");
        else tab.removeAttribute("aria-current");
      });
    }

    try {
      window.history.replaceState(null, "", currentHash());
    } catch (_) { /* ignore file:// */ }

    if (changed && !(opts && opts.keepScroll)) window.scrollTo(0, 0);
  }

  /* ---------- 顶栏与计数 ---------- */

  function renderHeader() {
    const st = state();
    const rg = st.risk_gate || {};
    const stamp = shortStamp(String(st.generated_at_bjt || "").replace(/\+08:00$/, ""));
    if (dom.generatedAt) {
      dom.generatedAt.textContent = (stamp ? stamp + " 更新" : "数据已载入") + (appState.offline ? " · 离线只读" : "");
    }
    if (dom.riskPill) {
      const light = String(rg.light || "").toUpperCase();
      dom.riskPill.setAttribute("data-light", light);
      dom.riskPill.title = [rg.label, rg.summary_exposure].filter((x) => !isBlank(x)).join("\n");
    }
    if (dom.riskPillText) {
      dom.riskPillText.textContent = !isBlank(rg.label) ? rg.label : RISK_LIGHT_TEXT[String(rg.light || "").toUpperCase()] || "风险灯号";
    }
  }

  function unreadCount() {
    return arr(state().articles).filter((a) => articleStatus(a.filename) === "unread").length;
  }

  function renderCounts() {
    const st = state();
    const ws = appState.workspace || {};
    const portCount = arr(ws.spot_balances).length + arr(ws.derivative_positions).length;
    const counts = {
      today: arr(st.signals).length,
      portfolio: portCount || arr(st.positions).length,
      research: Object.keys(st.stock_profiles || {}).length + unreadCount(),
      records: (arr(st.signal_plans).length || arr(appState.journal.trade_reviews).filter((r) => !isLegacyAutoSeedReview(r)).length) + arr(appState.journal.essays).length
    };
    document.querySelectorAll("[data-count-for]").forEach((el) => {
      const n = counts[el.getAttribute("data-count-for")];
      el.textContent = n ? String(n) : "";
    });
  }

  function validMacroItems() {
    return arr(state().macro_fred).filter((m) => m && !isBlank(m.label) && !isBlank(m.value_display) && Number.isFinite(toNum(m.raw_value)));
  }

  function renderFocusStrip() {
    if (!dom.mobileFocusBar) return;
    const rg = state().risk_gate || {};
    const map = macroById();
    const chips = [];
    if (!isBlank(rg.label)) {
      chips.push('<div class="focus-chip"><span>风险灯号</span><b>' + escapeHtml(RISK_LIGHT_TEXT[String(rg.light || "").toUpperCase()] || rg.label) + "</b></div>");
    }
    Object.keys(FOCUS_SHORT_LABELS).forEach((id) => {
      const m = map[id];
      if (!m) return;
      chips.push('<div class="focus-chip"><span>' + escapeHtml(FOCUS_SHORT_LABELS[id]) + '</span><b class="num-plain">' + escapeHtml(m.value_display) + "</b></div>");
    });
    dom.mobileFocusBar.innerHTML = chips.join("");
  }

  /* ---------- 今日：总览条、风险与策略、宏观速览 ---------- */

  function workspaceSummary() {
    const ws = appState.workspace || {};
    if (ws.summary && typeof ws.summary === "object") return ws.summary;
    return null;
  }

  function normalizeHeldBase(raw) {
    let base = String(raw || "").trim().toUpperCase().split(/[-/.]/)[0];
    if (base.length > 4 && (base.endsWith("USDT") || base.endsWith("USDC"))) {
      base = base.slice(0, -4);
    }
    return base;
  }

  function heldSymbolsList() {
    if (appState.offline) return [];
    const ws = appState.workspace || {};
    const list = [];
    const seen = new Set();
    arr(ws.spot_balances).forEach((r) => {
      const isCash = Boolean(r.is_cash) || String(r.asset_class || "").toLowerCase() === "cash";
      if (isCash || !(toNum(r.qty) > 0)) return;
      const base = normalizeHeldBase(r.symbol || r.instrument_id);
      if (base && !CASH_SYMBOLS.has(base) && !seen.has(base)) {
        seen.add(base);
        list.push(base);
      }
    });
    arr(ws.derivative_positions).forEach((r) => {
      if (toNum(r.qty) === 0 || !Number.isFinite(toNum(r.qty))) return;
      const base = normalizeHeldBase(r.symbol || r.instrument_id);
      if (base && !CASH_SYMBOLS.has(base) && !seen.has(base)) {
        seen.add(base);
        list.push(base);
      }
    });
    return list;
  }

  function hasHeldPositions() {
    return heldSymbolsList().length > 0;
  }

  function signalMatchesSymbol(s, sym) {
    const up = normalizeHeldBase(sym);
    if (!up) return false;
    const sigTickers = arr(s && s.tickers).map((t) => String(t).toUpperCase());
    if (sigTickers.includes(up)) return true;
    const rawTxt = [s && s.title, s && s.summary, s && s.why_it_matters].join(" ");
    const txtUp = rawTxt.toUpperCase();
    const esc = up.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (new RegExp("(^|[^A-Z0-9])" + esc + "($|[^A-Z0-9])").test(txtUp)) return true;
    const txtLow = rawTxt.toLowerCase();
    const aliases = INSTRUMENT_ALIASES[up] || [];
    return aliases.some((al) => {
      const low = String(al || "").trim().toLowerCase();
      if (!low) return false;
      if (/^[a-z0-9._-]+$/i.test(low)) {
        const aEsc = low.toUpperCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        return new RegExp("(^|[^A-Z0-9])" + aEsc + "($|[^A-Z0-9])").test(txtUp);
      }
      return txtLow.indexOf(low) !== -1;
    });
  }

  function renderTodayOverviewBar() {
    const heldList = heldSymbolsList();
    const hasHeld = heldList.length > 0;
    document.body.setAttribute("data-has-holdings", hasHeld ? "1" : "0");

    if (dom.execStrip) dom.execStrip.hidden = true;

    const heldScopeBtn = dom.signalScopeChips ? dom.signalScopeChips.querySelector('[data-scope="held"]') : null;
    if (heldScopeBtn) {
      heldScopeBtn.hidden = !hasHeld;
      if (!hasHeld && appState.daily.scope === "held") {
        appState.daily.scope = "all";
        setActive(dom.signalScopeChips, "data-scope", "all");
      }
    }

    if (dom.signalsHeading) {
      dom.signalsHeading.textContent = "核心快讯";
    }

    if (!dom.todayOverviewBar) return;
    if (!hasHeld) {
      dom.todayOverviewBar.hidden = true;
      dom.todayOverviewBar.innerHTML = "";
      return;
    }

    dom.todayOverviewBar.hidden = false;
    const rg = state().risk_gate || {};
    const sum = workspaceSummary();
    const fx = getFxContext();
    const netEq = sum && sum.net_equity_usd != null ? toNum(sum.net_equity_usd) : appState.capitalBase;
    const equitySourceNote = sum && sum.net_equity_usd != null ? "实盘持仓汇总" : "参考资金基数";

    const netDir = sum && sum.net_direction_pct != null ? toNum(sum.net_direction_pct) : NaN;
    const effLev = sum && sum.effective_leverage != null ? toNum(sum.effective_leverage) : NaN;
    const cap = toNum(rg.long_risk_cap);
    const light = String(rg.light || "").toUpperCase();
    const heldChipsHtml = heldList.slice(0, 4).map((sym) => '<span class="ticker">' + escapeHtml(sym) + "</span>").join("") +
      (heldList.length > 4 ? '<span class="ov-sub">+' + (heldList.length - 4) + "</span>" : "");

    dom.todayOverviewBar.innerHTML =
      '<div class="ov-cell"><span class="ov-label">账户净值</span><b class="ov-val">' + amtHtml(netEq) + '</b><span class="ov-sub">' + escapeHtml(equitySourceNote) + "</span></div>" +
      '<div class="ov-cell"><span class="ov-label">净方向性敞口</span><b class="ov-val num-plain ' + (Number.isFinite(netDir) ? toneClass(netDir) : "") + '">' +
      escapeHtml(Number.isFinite(netDir) ? formatSignedPct(netDir) : sum ? "不可计算" : (rg.summary_exposure ? rg.summary_exposure.split("·")[0].trim() : "0.00%")) +
      '</b><span class="ov-sub">多空净比</span></div>' +
      '<div class="ov-cell"><span class="ov-label">有效杠杆</span><b class="ov-val num-plain">' +
      escapeHtml(Number.isFinite(effLev) ? effLev.toFixed(2) + "x" : sum ? "不可计算" : (Number.isFinite(cap) ? cap.toFixed(1) + "x 上限" : "1.0x")) +
      '</b><span class="ov-sub">' + escapeHtml(fx.currency === "CNY" && fx.available ? "CNY " + fx.rate.toFixed(2) : "USD 计价") + "</span></div>" +
      '<div class="ov-cell"><span class="ov-label">策略门控</span><b class="ov-val">' +
      escapeHtml(RISK_LIGHT_TEXT[light] || "提示") + '</b><span class="ov-sub">' + escapeHtml(rg.label || "") + "</span></div>" +
      '<button type="button" class="ov-cell ov-cell-held" data-scope-jump="held" title="点击筛选持仓关联快讯"><span class="ov-label">持仓加权标的</span><span class="ov-held-tags">' +
      heldChipsHtml + '</span><span class="ov-sub">新闻权重已提升 · 点击筛选 →</span></button>';
  }

  function gateReasons(rg) {
    return arr(rg.reasons).filter((r) => !isBlank(r)).map(String);
  }

  function renderExecGate() {
    if (!dom.execGate) return;
    const rg = state().risk_gate || {};
    const light = String(rg.light || "").toUpperCase();
    dom.execGate.setAttribute("data-light", light);
    const cap = toNum(rg.long_risk_cap);
    const reasons = gateReasons(rg).slice(0, 2);
    dom.execGate.innerHTML =
      '<span class="gate-kicker">风险灯号 · ' + escapeHtml(RISK_LIGHT_TEXT[light] || "当前") + "</span>" +
      (!isBlank(rg.label) ? '<span class="gate-label">' + escapeHtml(rg.label) + "</span>" : "") +
      (!isBlank(rg.summary_exposure) ? '<span class="gate-exposure">' + escapeHtml(rg.summary_exposure) + "</span>" : "") +
      (Number.isFinite(cap) ? '<span class="gate-cap num-plain">多头风险上限 ' + escapeHtml(String(cap)) + " 倍</span>" : "") +
      (reasons.length ? '<span class="gate-reasons">' + reasons.map((r) => "<span>· " + escapeHtml(r) + "</span>").join("") + "</span>" : "");
  }

  function renderExecPositions() {
    if (!dom.execPositions) return;
    const ws = appState.workspace || {};
    const cmpMap = {};
    arr(ws.strategy_comparison).forEach((c) => {
      const key = c.bucket || c.strategy_id || c["sl" + "eeve"] || c.id;
      if (key) cmpMap[key] = c;
    });

    dom.execPositions.innerHTML = arr(state().positions).map((pos) => {
      const pct = positionPct(pos);
      const active = pct > 0;
      const cmp = cmpMap[pos.id];
      const actPct = cmp && cmp.actual_pct != null ? toNum(cmp.actual_pct) : NaN;
      return '<article class="pos-card' + (active ? " is-active" : "") + '">' +
        '<div class="pos-name">' + escapeHtml(pos.name) + "</div>" +
        '<div><span class="badge ' + (active ? "badge-accent" : "") + '">' + escapeHtml(pos.state || (active ? "持仓运行" : "空仓观望")) + "</span>" +
        (Number.isFinite(actPct) ? ' <span class="badge badge-outline num-plain">实持 ' + escapeHtml(formatWeight(actPct)) + "</span>" : "") + "</div>" +
        '<div class="pos-figure"><span class="pos-pct num">' + escapeHtml(formatWeight(pct)) + "</span>" +
        '<span class="pos-amt">' + amtHtml(positionNotional(pos)) + "</span></div>" +
        (!isBlank(pos.note) ? '<p class="pos-note">' + escapeHtml(pos.note) + "</p>" : "") +
        "</article>";
    }).join("");
  }

  function renderGlance() {
    if (!dom.dailyGlance) return;
    const map = macroById();
    const glanceOrder = [];
    GLANCE_IDS.forEach((id) => {
      if (map[id]) glanceOrder.push(id);
      if (id === "vix") glanceOrder.push("__forum__");
    });
    const cards = glanceOrder.map((id) => {
      if (id === "__forum__") {
        const fs = state().forum_sentiment;
        const fTemp = fs && typeof fs === "object" ? toNum(fs.overall_crowd_temp) : NaN;
        if (!Number.isFinite(fTemp)) return "";
        const fLabel = String(fs.overall_label || "查看分歧").trim();
        return '<button type="button" class="glance" data-jump="sentiment" title="查看讨论趋势和当期观点对照">' +
          '<span class="glance-label">当期舆论</span>' +
          '<span class="glance-value num-plain">' + escapeHtml(String(Math.round(fTemp))) + "</span>" +
          '<span class="glance-change num-plain">' + escapeHtml(fLabel) + "</span>" +
          "</button>";
      }
      const m = map[id];
      return '<button type="button" class="glance" data-jump="macro" title="' + escapeHtml(m.note || m.label) + '">' +
        '<span class="glance-label">' + escapeHtml(m.label) + "</span>" +
        '<span class="glance-value num-plain">' + escapeHtml(m.value_display) + "</span>" +
        (!isBlank(m.change_display) ? '<span class="glance-change num-plain ' + toneClass(m.change_num) + '">' + escapeHtml(m.change_display) + "</span>" : "") +
        "</button>";
    }).filter(Boolean);
    dom.dailyGlance.innerHTML = cards.join("");
  }

  /* ---------- 今日：信号筛选规则、持仓雷达、分值分解与快讯流 ---------- */

  function activeCurationRules() {
    return arr((appState.workspace || {}).curation_rules);
  }

  function heldSymbolsSet() {
    return new Set(heldSymbolsList());
  }

  function watchedSymbolsAndKeywords() {
    const watched = { syms: new Set(), keywords: [], mutedSources: new Set(), blockedKeywords: [] };
    activeCurationRules().forEach((r) => {
      if (r.enabled === false || r.enabled === 0) return;
      const act = String(r.action || "").toLowerCase();
      const kind = String(r.kind || "").toLowerCase();
      const target = String(r.target || "").trim();
      if (!target) return;
      if (act === "watch") {
        if (kind === "instrument") watched.syms.add(target.toUpperCase());
        else watched.keywords.push(target.toLowerCase());
      } else if (act === "block") {
        watched.blockedKeywords.push(target.toLowerCase());
      } else if (act === "mute" && kind === "source") {
        watched.mutedSources.add(target.toLowerCase());
      }
    });
    return watched;
  }

  function computeHoldingRadar(s, heldSet) {
    if (s.holding_radar && (!isBlank(s.holding_radar.relation) || !isBlank(s.holding_radar.status))) {
      return {
        status: s.holding_radar.relation || s.holding_radar.status,
        symbols: arr(s.holding_radar.symbols)
      };
    }
    if (Object.hasOwn(s, "holding_radar")) return null;
    const matched = Array.from(heldSet || []).filter((sym) => signalMatchesSymbol(s, sym));
    if (!matched.length) return null;
    const isSec = String(s.source || "").toUpperCase().includes("SEC");
    if (isSec || s.direction === "neutral") {
      return { status: "方向未明", symbols: matched };
    }
    const ws = appState.workspace || {};
    const deriv = arr(ws.derivative_positions).find((d) => matched.includes(String(d.symbol || "").toUpperCase().replace(/-USDT.*$/, "")));
    const isShort = deriv && String(deriv.side || "").toLowerCase() === "short";
    if (s.direction === "bullish") {
      return { status: isShort ? "逆风" : "顺风", symbols: matched };
    }
    if (s.direction === "bearish") {
      return { status: isShort ? "顺风" : "逆风", symbols: matched };
    }
    return { status: "方向未明", symbols: matched };
  }

  function renderCurationRulesPanel() {
    if (!dom.curationRulesList) return;
    const rules = activeCurationRules();
    const actionTxt = { watch: "关注", block: "屏蔽", mute: "降权", adjust: "调权" };
    const kindTxt = { instrument: "标的", keyword: "关键词", source: "信源", sector: "板块", topic: "议题" };
    if (!rules.length) {
      dom.curationRulesList.innerHTML = '<span class="panel-meta">暂无自定义规则，可通过下方表单或快讯卡片「⋯」快速添加</span>';
      return;
    }
    dom.curationRulesList.innerHTML = rules.map((r, i) => {
      const id = r.id != null ? String(r.id) : "local-" + i;
      return '<span class="rule-pill">' +
        '<span>' + escapeHtml((kindTxt[r.kind] || r.kind) + " · " + (actionTxt[r.action] || r.action)) + "</span>" +
        '<b>' + escapeHtml(r.target) + "</b>" +
        (!appState.offline ? '<button type="button" class="rule-del-btn" data-del-rule="' + escapeHtml(id) + '" aria-label="删除规则">✕</button>' : "") +
        "</span>";
    }).join("");
  }

  function saveReadBriefs() {
    safeStorageSet(STORAGE_KEYS.readBriefs, JSON.stringify(Array.from(appState.readBriefs).slice(-300)));
  }

  function toggleSignalRead(id, forceRead) {
    if (!id) return;
    const next = forceRead === true ? true : !appState.readBriefs.has(id);
    if (next) appState.readBriefs.add(id);
    else appState.readBriefs.delete(id);
    saveReadBriefs();
    if (dom.signalList) {
      dom.signalList.querySelectorAll(".tl-row[data-sig-id]").forEach((el) => {
        if (el.getAttribute("data-sig-id") === id) {
          el.classList.toggle("is-read", next);
          el.setAttribute("aria-pressed", String(next));
        }
      });
    }
  }

  function signalTopics(s) {
    const cat = String((s && s.category) || "");
    const defs = SECTOR_SUBTOPICS[cat] || [];
    const rawTopics = arr(s && s.topics).map((x) => String(x).trim()).filter(Boolean);
    const hay = [
      s && s.title, s && s.summary, s && s.source, s && s.why_it_matters,
      arr(s && s.tickers).join(" ")
    ].join(" ").toLowerCase();
    const out = [];
    defs.forEach((def) => {
      if (rawTopics.includes(def.label) || def.keys.some((k) => hay.indexOf(k) !== -1)) {
        out.push(def.label);
      }
    });
    return out;
  }

  function collectSectorSubtopics(cat, sectorSignals) {
    const counts = new Map();
    const defs = SECTOR_SUBTOPICS[cat] || [];
    const order = [];
    const bump = (label) => {
      if (!counts.has(label)) order.push(label);
      counts.set(label, (counts.get(label) || 0) + 1);
    };
    sectorSignals.forEach((s) => {
      signalTopics(s).forEach((t) => bump(t));
    });
    const defOrder = defs.map((d) => d.label);
    order.sort((a, b) => {
      const ia = defOrder.indexOf(a);
      const ib = defOrder.indexOf(b);
      if (ia !== -1 && ib !== -1) return ia - ib;
      if (ia !== -1) return -1;
      if (ib !== -1) return 1;
      return (counts.get(b) || 0) - (counts.get(a) || 0);
    });
    return order.map((label) => ({ label, count: counts.get(label) || 0 }));
  }

  function curatedSignalList() {
    const signals = appState.workspace?.curated_signals;
    return !appState.offline && Array.isArray(signals) ? signals : null;
  }

  function curatedEventList() {
    const events = appState.workspace?.curated_events;
    return !appState.offline && Array.isArray(events) ? events : null;
  }

  function hasEventFeedData() {
    return arr(state().event_cards).length > 0 || (curatedEventList()?.length || 0) > 0;
  }

  function activeEventCardsList() {
    const curated = curatedEventList();
    if (curated) return curated;
    return arr(state().event_cards);
  }

  function isEventFeedActive() {
    return appState.daily.feedView === "events" && hasEventFeedData();
  }

  function markEventRead(eventId, version) {
    const eid = String(eventId || "").trim();
    if (!eid) return;
    const ver = Math.max(1, Number(version) || 1);
    const prev = Number(appState.readEvents[eid] || 0);
    if (prev >= ver) return;
    appState.readEvents[eid] = ver;
    const keys = Object.keys(appState.readEvents);
    if (keys.length > 400) {
      keys.slice(0, keys.length - 400).forEach((k) => { delete appState.readEvents[k]; });
    }
    safeStorageSet(STORAGE_KEYS.readEvents, JSON.stringify(appState.readEvents));
  }

  function eventReadState(ev) {
    const eid = String((ev && (ev.event_id || ev.id)) || "");
    const seenVer = Number(appState.readEvents[eid] || 0);
    const curVer = Math.max(1, Number((ev && ev.substantive_version) || 1));
    return {
      seen: seenVer > 0,
      hasNewProgress: seenVer > 0 && curVer > seenVer,
      curVer
    };
  }

  function verificationTone(status) {
    if (status === "confirmed") return "badge-bull";
    if (status === "denied" || status === "corrected") return "badge-bear";
    if (status === "conflicted") return "badge-warn";
    if (status === "multi_source") return "badge-proof";
    return "badge-outline";
  }

  function filteredSignals(opts) {
    const f = appState.daily;
    const kw = f.keyword.toLowerCase();
    const catFilter = opts && opts.overrideCat !== undefined ? opts.overrideCat : f.cat;
    const ignoreSubtopic = Boolean(opts && opts.ignoreSubtopic);
    const heldSet = heldSymbolsSet();
    const rulesInfo = watchedSymbolsAndKeywords();
    const useEvents = !opts?.forceWire && isEventFeedActive();
    const curated = useEvents ? curatedEventList() : curatedSignalList();
    const candidates = arr(state().signal_candidates);
    const rawList = useEvents
      ? activeEventCardsList()
      : (curated ?? (candidates.length && (f.scope === "held" || f.scope === "watch")
        ? candidates
        : arr(state().signals)));

    return rawList.filter((s) => {
      const hay = [s.title, s.summary, s.source, s.why_it_matters, arr(s.tickers).join(" "), arr(s.topics).join(" ")].join(" ").toLowerCase();
      if (!curated && rulesInfo.blockedKeywords.some((bk) => hay.includes(bk))) return false;
      if (catFilter !== "all" && s.category !== catFilter) return false;
      if (!ignoreSubtopic && catFilter !== "all" && f.subtopic && f.subtopic !== "all") {
        if (!signalTopics(s).includes(f.subtopic)) return false;
      }
      if (f.pol !== "all" && s.direction !== f.pol) return false;
      if (f.highOnly && !(s.personal_high ?? (s.importance === "高"))) return false;
      if (f.scope === "held") {
        const radar = computeHoldingRadar(s, heldSet);
        if (!radar && !s.held_match) return false;
      } else if (f.scope === "watch") {
        const tks = arr(s.tickers).map((t) => String(t).toUpperCase());
        const hitSym = tks.some((t) => rulesInfo.syms.has(t));
        const hitKw = rulesInfo.keywords.some((w) => hay.indexOf(w) !== -1);
        const watched = curated && s.is_watched != null
          ? s.is_watched || Boolean(s.holding_radar)
          : s.is_watched || hitSym || hitKw;
        if (!watched) return false;
      }
      if (kw && hay.indexOf(kw) === -1) return false;
      return true;
    });
  }

  function heatBadge(badge) {
    if (isBlank(badge)) return "";
    const cls = badge === "突发" ? "badge-bear" : badge === "升温" ? "badge-warn" : "badge-outline";
    return '<span class="badge ' + cls + '">' + escapeHtml(badge) + "</span>";
  }

  function isMostlyEnglishText(raw) {
    const cleaned = String(raw || "").replace(/https?:\/\/\S+/gi, " ").trim();
    if (!cleaned) return false;
    const cjk = (cleaned.match(/[\u4e00-\u9fff]/g) || []).length;
    const ascii = (cleaned.match(/[A-Za-z]/g) || []).length;
    if (ascii < 12) return false;
    if (cjk === 0) return true;
    if (cjk >= 5) return false;
    return cjk / Math.max(1, cjk + ascii) < 0.12;
  }

  function parseSignalTimeMs(raw) {
    const s = String(raw || "").trim();
    if (!s) return 0;
    const normalized = /^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}$/.test(s)
      ? s.replace(/\s+/, "T") + ":00+08:00"
      : /^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/.test(s)
        ? s.replace(/\s+/, "T") + "+08:00"
        : s;
    const t = Date.parse(normalized);
    return Number.isFinite(t) ? t : 0;
  }

  function extractSignalDateKey(raw) {
    const m = String(raw || "").match(/(\d{4}-\d{2}-\d{2})/);
    return m ? m[1] : "今日快讯";
  }

  function extractSignalClock(raw) {
    const s = String(raw || "").trim();
    const m = s.match(/(\d{2}:\d{2})/);
    return m ? m[1] : (shortStamp(s) || "--:--");
  }

  function impWeight(imp) {
    return imp === "高" ? 3 : imp === "中" ? 2 : 1;
  }

  function signalRankScore(s) {
    if (!isBlank(s && s.final_score) && Number.isFinite(toNum(s.final_score))) return toNum(s.final_score);
    if (!isBlank(s && s.attention_score) && Number.isFinite(toNum(s.attention_score))) return toNum(s.attention_score);
    if (!isBlank(s && s.base_score) && Number.isFinite(toNum(s.base_score))) return toNum(s.base_score);
    return 0;
  }

  function sortSignalsForMode(list, mode) {
    const copy = list.slice();
    if (mode === "timeline") {
      return copy.sort((a, b) => {
        const dt = parseSignalTimeMs(b && b.time_bjt) - parseSignalTimeMs(a && a.time_bjt);
        if (dt !== 0) return dt;
        const sc = String((b && b.time_bjt) || "").localeCompare(String((a && a.time_bjt) || ""));
        if (sc !== 0) return sc;
        return signalRankScore(b) - signalRankScore(a);
      });
    }
    copy.sort((a, b) => {
      const aPri = Boolean(a && (a.selected === true || a.holding_radar || a.held_match));
      const bPri = Boolean(b && (b.selected === true || b.holding_radar || b.held_match));
      const selDiff = (bPri ? 1 : 0) - (aPri ? 1 : 0);
      if (selDiff !== 0) return selDiff;
      const scoreDiff = signalRankScore(b) - signalRankScore(a);
      if (scoreDiff !== 0) return scoreDiff;
      const impDiff = impWeight(b && b.importance) - impWeight(a && a.importance);
      if (impDiff !== 0) return impDiff;
      return parseSignalTimeMs(b && b.time_bjt) - parseSignalTimeMs(a && a.time_bjt);
    });
    const cat = String((appState.daily && appState.daily.cat) || "all").toLowerCase();
    const scope = String((appState.daily && appState.daily.scope) || "all").toLowerCase();
    const inst = String((appState.daily && appState.daily.inst) || "").trim();
    if ((cat === "all" || !cat) && (scope === "all" || !scope) && !inst && copy.length > 2) {
      const headCap = 2;
      const headKept = [];
      const nonCryptoTail = [];
      const deferredCrypto = [];
      let cryptoInHead = 0;
      for (let i = 0; i < copy.length; i += 1) {
        const it = copy[i];
        const isCr = String((it && it.category) || "").toLowerCase() === "crypto";
        if (headKept.length < 15) {
          if (isCr && cryptoInHead >= headCap) {
            deferredCrypto.push(it);
          } else {
            headKept.push(it);
            if (isCr) cryptoInHead += 1;
          }
        } else if (isCr) {
          deferredCrypto.push(it);
        } else {
          nonCryptoTail.push(it);
        }
      }
      return headKept.concat(nonCryptoTail, deferredCrypto);
    }
    return copy;
  }

  function defaultSignalLimit() {
    return appState.daily.mode === "timeline" ? 25 : 15;
  }

  function resetSignalLimit() {
    appState.daily.visibleLimit = defaultSignalLimit();
  }

  function signalTickersHtml(s) {
    const seen = new Set();
    const list = arr(s && s.tickers).map((t) => String(t || "").trim().toUpperCase()).filter((sym) => {
      if (!sym || seen.has(sym)) return false;
      seen.add(sym);
      return true;
    }).slice(0, 2);
    return list.map((sym) => {
      const canOpen = !appState.offline || Boolean(allStockProfiles()[sym]);
      return canOpen
        ? '<button type="button" class="ticker" data-open-stock="' + escapeHtml(sym) + '" title="打开 ' + escapeHtml(sym) + ' 投研">' + escapeHtml(sym) + "</button>"
        : '<span class="ticker">' + escapeHtml(sym) + "</span>";
    }).join("");
  }

  function signalTopicTagsHtml(s) {
    const cat = String((s && s.category) || "");
    if (!cat) return "";
    return signalTopics(s).slice(0, 2).map((t) =>
      '<button type="button" class="topic-chip" data-jump-sector="' + escapeHtml(cat) + '" data-jump-subtopic="' + escapeHtml(t) + '">' + escapeHtml(t) + "</button>"
    ).join("");
  }

  function signalMenuHtml(s, sigId, menuOpen, primaryTicker) {
    if (appState.offline) return "";
    return '<div class="sig-menu-wrap">' +
      '<button type="button" class="sig-menu-btn" data-sig-menu="' + escapeHtml(sigId) + '" aria-label="快捷规则操作" aria-expanded="' + String(menuOpen) + '">⋯</button>' +
      (menuOpen
        ? '<div class="sig-popover" role="menu">' +
          (primaryTicker ? '<button type="button" class="sig-pop-item" data-quick-rule="watch" data-kind="instrument" data-target="' + escapeHtml(primaryTicker) + '">关注标的 · ' + escapeHtml(primaryTicker) + "</button>" : "") +
          '<button type="button" class="sig-pop-item" data-quick-rule="block" data-kind="keyword" data-target="' + escapeHtml(String((s && s.title) || "").slice(0, 16)) + '">屏蔽相关词</button>' +
          '<button type="button" class="sig-pop-item" data-quick-rule="mute" data-kind="source" data-target="' + escapeHtml((s && s.source) || "") + '">降低信源权重</button>' +
          "</div>"
        : "") +
      "</div>";
  }

  function signalScoreHtml(s, sigId, reasons) {
    const hasPersonal = !isBlank(s && s.personal_score) && Number.isFinite(toNum(s.personal_score));
    const hasFinal = !isBlank(s && s.final_score) && Number.isFinite(toNum(s.final_score));
    const hasBase = !isBlank(s && s.base_score) && Number.isFinite(toNum(s.base_score));
    const hasAtt = !isBlank(s && s.attention_score) && Number.isFinite(toNum(s.attention_score));
    const rawVal = hasPersonal ? toNum(s.personal_score)
      : hasFinal ? toNum(s.final_score)
      : hasBase ? toNum(s.base_score)
      : hasAtt ? toNum(s.attention_score)
      : NaN;
    if (!Number.isFinite(rawVal)) return "";
    const label = (s && s.held_match ? "持仓 " : "") + Math.round(rawVal) + "分";
    return '<span class="score-bubble-wrap">' +
      (reasons.length
        ? '<button type="button" class="score-btn num-plain" data-score-toggle="' + escapeHtml(sigId) + '" title="' + escapeHtml(reasons.join(" · ")) + '">' + escapeHtml(label) + "</button>"
        : '<span class="score num-plain">' + escapeHtml(label) + "</span>") +
      "</span>";
  }

  function tickerPillHtml(sym) {
    const up = String(sym || "").trim().toUpperCase();
    if (!up) return "";
    const canOpen = !appState.offline || Boolean(allStockProfiles()[up]);
    return canOpen
      ? '<button type="button" class="ticker" data-open-stock="' + escapeHtml(up) + '" title="打开 ' + escapeHtml(up) + ' 投研">' + escapeHtml(up) + "</button>"
      : '<span class="ticker">' + escapeHtml(up) + "</span>";
  }

  function kolReliabilityTone(badge) {
    const b = String(badge || "").trim();
    if (b === "高胜率") return "badge-bull";
    if (b === "稳健参考") return "badge-accent";
    if (b === "观点敏锐") return "badge-warn";
    if (b === "反向警示") return "badge-bear";
    return "badge-outline";
  }

  function kolWinRateText(k) {
    if (!k) return "定性观察";
    const w20 = toNum(k.win_rate_20d);
    const calls = toNum(k.verified_calls);
    if (k.win_rate_20d != null && Number.isFinite(w20) && (!Number.isFinite(calls) || calls >= 5)) {
      return "20日胜率 " + w20.toFixed(1) + "%" + (Number.isFinite(calls) && calls > 0 ? " · " + calls + "次" : "");
    }
    if (Number.isFinite(calls) && calls > 0) return "样本积累 · " + calls + "次";
    return k.reliability_badge === "样本积累" ? "样本积累" : "定性观察";
  }

  function isHighWinKol(k) {
    if (!k) return false;
    if (k.reliability_badge === "高胜率") return true;
    const w20 = toNum(k.win_rate_20d);
    const calls = toNum(k.verified_calls);
    return k.win_rate_20d != null && Number.isFinite(w20) && w20 >= 60 && (!Number.isFinite(calls) || calls >= 5);
  }

  function signalSourceHtml(src) {
    const s = String(src || "").trim();
    if (!s) return "";
    if (s === "X核心观点" || s === "X深度长文") {
      return '<span class="signal-src is-src-kol">' + escapeHtml(s) + "</span>";
    }
    if (s === "大佬分享") {
      return '<span class="signal-src is-src-guru">' + escapeHtml(s) + "</span>";
    }
    return '<span class="signal-src">' + escapeHtml(s) + "</span>";
  }

  function signalMetaHtml(s, dir, radar, omitTime) {
    const merged = toNum(s && s.merged_count);
    const mergedSources = Array.isArray(s && s.merged_sources)
      ? arr(s.merged_sources).filter((x) => !isBlank(x)).join(" · ")
      : (!isBlank(s && s.merged_sources) ? String(s.merged_sources).trim() : "");
    const radarCls = radar && radar.status === "顺风" ? "badge-bull" : radar && radar.status === "逆风" ? "badge-bear" : "badge-warn";
    return [
      '<span class="dot dot-' + dir.cls + '" aria-hidden="true"></span>',
      signalSourceHtml(s.source),
      !isBlank(s.author_role) ? '<span class="badge badge-outline">' + escapeHtml(s.author_role) + "</span>" : "",
      !isBlank(s.reliability_badge) ? '<span class="badge ' + kolReliabilityTone(s.reliability_badge) + '">' + escapeHtml(s.reliability_badge) + "</span>" : "",
      (!omitTime && !isBlank(s.time_bjt)) ? '<span class="num-plain">' + escapeHtml(s.time_bjt) + "</span>" : "",
      CATEGORY_LABELS[s.category] ? '<span class="badge">' + CATEGORY_LABELS[s.category] + "</span>" : "",
      !isBlank(s.merged_count) && merged > 1
        ? '<span class="badge badge-proof num-plain"' + (mergedSources ? ' title="印证来源：' + escapeHtml(mergedSources) + '"' : "") + ">多源印证 ×" + merged + "</span>"
        : "",
      radar ? '<span class="badge ' + radarCls + '">持仓 · ' + escapeHtml(radar.status) + "</span>" : "",
      (s.personal_high ?? (s.importance === "高")) ? '<span class="badge badge-accent">高优</span>' : "",
      heatBadge(s.heat_badge)
    ].filter(Boolean).join("");
  }

  function signalHtml(s, idx) {
    const dir = DIRECTION_META[s.direction] || DIRECTION_META.neutral;
    const url = safeHttpUrl(s.url);
    const cleanTitle = stripEmojis(s.title);
    const title = escapeHtml(cleanTitle);
    const summary = cleanSignalSummary(cleanTitle, s.summary);
    const zhBrief = stripEmojis(s.zh_brief || "");
    const showZhBrief = Boolean(zhBrief) && isMostlyEnglishText(cleanTitle);
    const hideRawSummary = showZhBrief && Boolean(summary) && isMostlyEnglishText(summary);
    const whyText = stripEmojis(s.why_it_matters);
    const isHigh = Boolean(s.personal_high ?? (s.importance === "高"));
    const showWhyInline = Boolean(whyText) && isHigh && !isGenericSourceWhy(whyText);
    const reasons = arr(s.reasons).filter((r) => !isBlank(r));
    const radar = computeHoldingRadar(s, heldSymbolsSet());
    const sigId = String(s.id || idx);
    const menuOpen = appState.openSignalMenu === sigId;
    const scoreOpen = appState.openScoreId === sigId;
    const primaryTicker = arr(s.tickers).find((t) => !isBlank(t)) || "";

    const meta = signalMetaHtml(s, dir, radar, false);
    const menuHtml = signalMenuHtml(s, sigId, menuOpen, primaryTicker);
    const scoreHtml = signalScoreHtml(s, sigId, reasons);
    const breakdownHtml = scoreOpen && reasons.length
      ? '<div class="score-breakdown">' + reasons.map((r) => "<span>· " + escapeHtml(r) + "</span>").join("") + "</div>"
      : "";
    const zhBriefHtml = showZhBrief
      ? '<p class="signal-zh-brief"><b title="投资备忘录">Memo</b>' + escapeHtml(zhBrief) + "</p>"
      : "";
    const whyHtml = whyText
      ? '<p class="signal-why' + (showWhyInline ? "" : " is-extra-only") + '"><b>影响研判</b>' + escapeHtml(whyText) + "</p>"
      : "";
    const canExpand = hideRawSummary || summary.length > 80 || (whyText && (!showWhyInline || whyText.length > 34));

    return '<li class="signal" data-sig-id="' + escapeHtml(sigId) + '">' +
      '<span class="signal-idx num-plain" aria-hidden="true">' + (idx + 1) + "</span>" +
      '<div class="signal-body">' +
      '<div class="signal-head-row"><div class="signal-meta">' + meta + "</div>" + menuHtml + "</div>" +
      '<h3 class="signal-title">' + (url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + title + "</a>" : title) + "</h3>" +
      zhBriefHtml +
      (summary ? '<p class="signal-summary' + (hideRawSummary ? " is-extra-only" : "") + '">' + escapeHtml(summary) + "</p>" : "") +
      whyHtml +
      '<div class="signal-foot">' + signalTickersHtml(s) + signalTopicTagsHtml(s) +
      '<span class="badge badge-' + dir.cls + '">' + dir.label + "</span>" +
      scoreHtml +
      (canExpand ? '<button type="button" class="btn-link" data-expand>展开全文</button>' : "") +
      "</div>" + breakdownHtml + "</div></li>";
  }

  function timelineSignalHtml(s, idx, isFirst, isLast) {
    const dir = DIRECTION_META[s.direction] || DIRECTION_META.neutral;
    const url = safeHttpUrl(s.url);
    const cleanTitle = stripEmojis(s.title);
    const title = escapeHtml(cleanTitle);
    const summary = cleanSignalSummary(cleanTitle, s.summary);
    const zhBrief = stripEmojis(s.zh_brief || "");
    const showZhBrief = Boolean(zhBrief) && isMostlyEnglishText(cleanTitle);
    const hideRawSummary = showZhBrief && Boolean(summary) && isMostlyEnglishText(summary);
    const radar = computeHoldingRadar(s, heldSymbolsSet());
    const sigId = String(s.id || ("tl-" + idx));
    const read = appState.readBriefs.has(sigId);
    const menuOpen = appState.openSignalMenu === sigId;
    const primaryTicker = arr(s.tickers).find((t) => !isBlank(t)) || "";
    const rowCls = [
      "tl-row",
      isFirst ? "is-group-first" : "",
      isLast ? "is-group-last" : "",
      read ? "is-read" : ""
    ].filter(Boolean).join(" ");

    const meta = signalMetaHtml(s, dir, radar, true);
    const menuHtml = signalMenuHtml(s, sigId, menuOpen, primaryTicker);

    return '<li class="' + rowCls + '" data-sig-id="' + escapeHtml(sigId) + '" data-dir="' + escapeHtml(s.direction || "neutral") + '" tabindex="0">' +
      '<span class="tl-row-time num-plain">' + escapeHtml(extractSignalClock(s.time_bjt)) + "</span>" +
      '<span class="tl-row-rail" aria-hidden="true"></span>' +
      '<div class="tl-row-main">' +
      '<div class="signal-head-row"><div class="signal-meta">' + meta + "</div>" + menuHtml + "</div>" +
      '<h3 class="tl-row-title">' + (url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + title + "</a>" : title) + "</h3>" +
      (showZhBrief ? '<p class="tl-row-zh-brief"><b title="投资备忘录">Memo</b>' + escapeHtml(zhBrief) + "</p>" : "") +
      (summary ? '<p class="tl-row-summary' + (hideRawSummary ? " is-extra-only" : "") + '">' + escapeHtml(summary) + "</p>" : "") +
      '<div class="signal-foot">' + signalTickersHtml(s) + signalTopicTagsHtml(s) +
      '<span class="badge badge-' + dir.cls + '">' + dir.label + "</span>" +
      (hideRawSummary || summary.length > 70 ? '<button type="button" class="btn-link" data-expand>展开全文</button>' : "") +
      "</div></div></li>";
  }

  function eventDetailHtml(ev) {
    const eid = String(ev.event_id || ev.id || "");
    const expanded = appState.expandedEvents.has(eid);
    const conflicts = arr(ev.conflict_claims);
    const priors = arr(ev.prior_claims);
    const timeline = arr(ev.timeline);
    const families = arr(ev.provenance_families);
    if (!expanded) return "";

    const conflictHtml = conflicts.length
      ? '<div class="event-box is-conflict"><div class="event-box-title">说法冲突并列对照</div>' +
        conflicts.map((c) => {
          const cu = safeHttpUrl(c.url);
          return '<div class="event-claim-row"><b>' + escapeHtml(c.source || "多方说法") + "：</b>" +
            escapeHtml(c.claim || "") +
            (cu ? ' <a href="' + escapeHtml(cu) + '" target="_blank" rel="noopener noreferrer">原文</a>' : "") +
            "</div>";
        }).join("") + "</div>"
      : "";

    const priorHtml = priors.length
      ? '<div class="event-box is-prior"><div class="event-box-title">历史说法留痕</div>' +
        priors.map((c) =>
          '<div class="event-claim-row"><span class="badge badge-outline">' + escapeHtml(c.label || "已更正旧说") + "</span> " +
          escapeHtml(c.claim || "") +
          (c.recorded_at ? ' <span class="num-plain">(' + escapeHtml(shortStamp(c.recorded_at)) + ")</span>" : "") +
          "</div>"
        ).join("") + "</div>"
      : "";

    const tlHtml = timeline.length
      ? '<div class="event-subsec"><div class="event-box-title">事件时间线</div><ul class="event-tl-list">' +
        timeline.slice().reverse().map((step) => {
          const su = safeHttpUrl(step.url);
          const stamp = shortStamp(step.published_at || step.first_seen_at || "");
          return '<li class="event-tl-item">' +
            '<div class="event-tl-meta">' +
            (stamp ? '<span class="num-plain">' + escapeHtml(stamp) + "</span>" : "") +
            (step.status_label ? '<span class="badge ' + verificationTone(step.status) + '">' + escapeHtml(step.status_label) + "</span>" : "") +
            (step.effective_at ? '<span class="badge badge-outline">生效 ' + escapeHtml(step.effective_at) + "</span>" : "") +
            (step.source ? '<span>' + escapeHtml(step.source) + "</span>" : "") +
            "</div>" +
            '<div class="event-tl-sum">' + escapeHtml(step.summary || "") +
            (su ? ' · <a href="' + escapeHtml(su) + '" target="_blank" rel="noopener noreferrer" data-event-link="' + escapeHtml(eid) + '" data-event-ver="' + Math.max(1, Number(ev.substantive_version) || 1) + '">出处</a>' : "") +
            "</div></li>";
        }).join("") + "</ul></div>"
      : "";

    const famHtml = families.length
      ? '<div class="event-subsec"><div class="event-box-title">原始出处归并（' + families.length + " 家独立出处）</div>" +
        '<div class="event-fam-list">' +
        families.map((fam) => {
          const linksHtml = arr(fam.links).map((lk) => {
            const lu = safeHttpUrl(lk.url);
            const lName = lk.source || "原文";
            const lTitle = lk.title ? " · " + lk.title : "";
            return lu
              ? '<a class="event-src-link" href="' + escapeHtml(lu) + '" target="_blank" rel="noopener noreferrer" data-event-link="' + escapeHtml(eid) + '" data-event-ver="' + Math.max(1, Number(ev.substantive_version) || 1) + '">' + escapeHtml(lName + lTitle) + "</a>"
              : '<span class="event-src-link">' + escapeHtml(lName + lTitle) + "</span>";
          }).join("");
          return '<div class="event-fam-row"><span class="badge badge-outline">' + escapeHtml(fam.label || "报道") + "</span>" +
            '<div class="event-fam-links">' + linksHtml + "</div></div>";
        }).join("") + "</div></div>"
      : "";

    return '<div class="event-detail">' + conflictHtml + priorHtml + tlHtml + famHtml + "</div>";
  }

  function eventMetaHtml(ev, dir, radar, omitTime) {
    const rState = eventReadState(ev);
    const famCount = Math.max(1, Number(ev.independent_family_count) || 1);
    const totalCount = Math.max(1, Number(ev.total_source_count) || 1);
    const srcCountText = famCount > 1 || totalCount > 1
      ? famCount + "家出处" + (totalCount > famCount ? " · " + totalCount + "篇" : "")
      : "";
    const radarCls = radar && radar.status === "顺风" ? "badge-bull" : radar && radar.status === "逆风" ? "badge-bear" : "badge-warn";
    return [
      '<span class="dot dot-' + dir.cls + '" aria-hidden="true"></span>',
      '<span class="badge ' + verificationTone(ev.verification_status) + '"' + (ev.verification_desc ? ' title="' + escapeHtml(ev.verification_desc) + '"' : "") + ">" +
        escapeHtml(ev.verification_label || "未证实") + "</span>",
      rState.hasNewProgress ? '<span class="badge badge-accent">有新进展</span>' : "",
      ev.pending_review ? '<span class="badge badge-outline">待核对</span>' : "",
      signalSourceHtml(ev.source),
      srcCountText ? '<span class="badge badge-proof num-plain">' + escapeHtml(srcCountText) + "</span>" : "",
      (!omitTime && !isBlank(ev.time_bjt)) ? '<span class="num-plain">' + escapeHtml(shortStamp(ev.time_bjt)) + "</span>" : "",
      CATEGORY_LABELS[ev.category] ? '<span class="badge">' + CATEGORY_LABELS[ev.category] + "</span>" : "",
      radar ? '<span class="badge ' + radarCls + '">持仓 · ' + escapeHtml(radar.status) + "</span>" : "",
      (ev.personal_high ?? (ev.importance === "高")) ? '<span class="badge badge-accent">高优</span>' : ""
    ].filter(Boolean).join("");
  }

  function eventCardHtml(ev, idx) {
    const dir = DIRECTION_META[ev.direction] || DIRECTION_META.neutral;
    const url = safeHttpUrl(ev.url);
    const cleanTitle = stripEmojis(ev.title);
    const title = escapeHtml(cleanTitle);
    const zhBrief = stripEmojis(ev.zh_brief || "");
    const showZhBrief = Boolean(zhBrief) && isMostlyEnglishText(cleanTitle);
    const latestChange = stripEmojis(ev.latest_change || "");
    const showLatest = Boolean(latestChange) && latestChange !== cleanTitle;
    const radar = computeHoldingRadar(ev, heldSymbolsSet());
    const eid = String(ev.event_id || ev.id || ("ev-" + idx));
    const ver = Math.max(1, Number(ev.substantive_version) || 1);
    const expanded = appState.expandedEvents.has(eid);
    const rState = eventReadState(ev);
    const reasons = arr(ev.reasons).filter((r) => !isBlank(r));
    const menuOpen = appState.openSignalMenu === eid;
    const primaryTicker = arr(ev.tickers).find((t) => !isBlank(t)) || "";
    const stepTotal = Math.max(arr(ev.timeline).length, Math.max(1, Number(ev.total_source_count) || 1));

    return '<li class="signal event-card' + (rState.seen && !rState.hasNewProgress ? " is-seen-event" : "") + '" data-sig-id="' + escapeHtml(eid) + '" data-event-id="' + escapeHtml(eid) + '">' +
      '<span class="signal-idx num-plain" aria-hidden="true">' + (idx + 1) + "</span>" +
      '<div class="signal-body">' +
      '<div class="signal-head-row"><div class="signal-meta">' + eventMetaHtml(ev, dir, radar, false) + "</div>" + signalMenuHtml(ev, eid, menuOpen, primaryTicker) + "</div>" +
      '<h3 class="signal-title">' + (url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" data-event-link="' + escapeHtml(eid) + '" data-event-ver="' + ver + '">' + title + "</a>" : title) + "</h3>" +
      (showZhBrief ? '<p class="signal-zh-brief"><b title="投资备忘录">Memo</b>' + escapeHtml(zhBrief) + "</p>" : "") +
      (showLatest ? '<p class="event-latest-change"><b>最新变化</b>' + escapeHtml(latestChange) + "</p>" : "") +
      eventDetailHtml(ev) +
      '<div class="signal-foot">' + signalTickersHtml(ev) + signalTopicTagsHtml(ev) +
      '<span class="badge badge-' + dir.cls + '">' + dir.label + "</span>" +
      signalScoreHtml(ev, eid, reasons) +
      '<button type="button" class="btn-link" data-event-expand="' + escapeHtml(eid) + '" data-event-ver="' + ver + '">' +
        (expanded ? "收起详情" : "时间线与出处 (" + stepTotal + ")") + "</button>" +
      "</div>" +
      (appState.openScoreId === eid && reasons.length
        ? '<div class="score-breakdown">' + reasons.map((r) => "<span>· " + escapeHtml(r) + "</span>").join("") + "</div>"
        : "") +
      "</div></li>";
  }

  function eventTimelineHtml(ev, idx, isFirst, isLast) {
    const dir = DIRECTION_META[ev.direction] || DIRECTION_META.neutral;
    const url = safeHttpUrl(ev.url);
    const cleanTitle = stripEmojis(ev.title);
    const title = escapeHtml(cleanTitle);
    const zhBrief = stripEmojis(ev.zh_brief || "");
    const showZhBrief = Boolean(zhBrief) && isMostlyEnglishText(cleanTitle);
    const latestChange = stripEmojis(ev.latest_change || "");
    const showLatest = Boolean(latestChange) && latestChange !== cleanTitle;
    const radar = computeHoldingRadar(ev, heldSymbolsSet());
    const eid = String(ev.event_id || ev.id || ("ev-tl-" + idx));
    const ver = Math.max(1, Number(ev.substantive_version) || 1);
    const rState = eventReadState(ev);
    const expanded = appState.expandedEvents.has(eid);
    const primaryTicker = arr(ev.tickers).find((t) => !isBlank(t)) || "";
    const stepTotal = Math.max(arr(ev.timeline).length, Math.max(1, Number(ev.total_source_count) || 1));
    const rowCls = [
      "tl-row event-tl-row",
      isFirst ? "is-group-first" : "",
      isLast ? "is-group-last" : "",
      rState.seen && !rState.hasNewProgress ? "is-read" : ""
    ].filter(Boolean).join(" ");

    return '<li class="' + rowCls + '" data-sig-id="' + escapeHtml(eid) + '" data-event-id="' + escapeHtml(eid) + '" data-dir="' + escapeHtml(ev.direction || "neutral") + '">' +
      '<span class="tl-row-time num-plain">' + escapeHtml(extractSignalClock(ev.time_bjt)) + "</span>" +
      '<span class="tl-row-rail" aria-hidden="true"></span>' +
      '<div class="tl-row-main">' +
      '<div class="signal-head-row"><div class="signal-meta">' + eventMetaHtml(ev, dir, radar, true) + "</div>" + signalMenuHtml(ev, eid, appState.openSignalMenu === eid, primaryTicker) + "</div>" +
      '<h3 class="tl-row-title">' + (url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" data-event-link="' + escapeHtml(eid) + '" data-event-ver="' + ver + '">' + title + "</a>" : title) + "</h3>" +
      (showZhBrief ? '<p class="tl-row-zh-brief"><b title="投资备忘录">Memo</b>' + escapeHtml(zhBrief) + "</p>" : "") +
      (showLatest ? '<p class="event-latest-change"><b>最新变化</b>' + escapeHtml(latestChange) + "</p>" : "") +
      eventDetailHtml(ev) +
      '<div class="signal-foot">' + signalTickersHtml(ev) + signalTopicTagsHtml(ev) +
      '<span class="badge badge-' + dir.cls + '">' + dir.label + "</span>" +
      '<button type="button" class="btn-link" data-event-expand="' + escapeHtml(eid) + '" data-event-ver="' + ver + '">' +
        (expanded ? "收起详情" : "时间线与出处 (" + stepTotal + ")") + "</button>" +
      "</div></div></li>";
  }

  function renderFeedViewSwitch() {
    if (!dom.feedViewSwitch) return;
    const hasEvents = hasEventFeedData();
    dom.feedViewSwitch.hidden = !hasEvents;
    if (!hasEvents) return;
    const view = appState.daily.feedView === "wire" ? "wire" : "events";
    dom.feedViewSwitch.querySelectorAll("[data-fview]").forEach((btn) => {
      const on = btn.getAttribute("data-fview") === view;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", String(on));
    });
  }

  function renderNewsModeSwitch() {
    if (!dom.newsModeSwitch) return;
    const mode = appState.daily.mode === "timeline" ? "timeline" : "ranked";
    dom.newsModeSwitch.querySelectorAll("[data-nmode]").forEach((btn) => {
      const on = btn.getAttribute("data-nmode") === mode;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", String(on));
    });
  }

  function syncDailyFilterChipsUi() {
    setActive(dom.signalScopeChips, "data-scope", appState.daily.scope);
    setActive(dom.polarityChips, "data-pol", appState.daily.pol);
    if (dom.highOnlyBtn) {
      dom.highOnlyBtn.classList.toggle("is-active", appState.daily.highOnly);
      dom.highOnlyBtn.setAttribute("aria-pressed", String(appState.daily.highOnly));
    }
    if (dom.keywordInput) dom.keywordInput.value = appState.daily.keyword || "";
  }

  function renderSectorRibbon() {
    if (!dom.sectorRibbon) return;
    const base = filteredSignals({ overrideCat: "all", ignoreSubtopic: true });
    const counts = { all: base.length };
    SECTOR_ORDER.forEach((sec) => { counts[sec] = 0; });
    base.forEach((s) => {
      if (s.category && counts[s.category] !== undefined) counts[s.category] += 1;
    });
    dom.sectorRibbon.querySelectorAll(".ribbon-tab[data-sector]").forEach((btn) => {
      const sec = btn.getAttribute("data-sector");
      const active = sec === appState.daily.cat;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", String(active));
      const cEl = btn.querySelector("[data-scount]");
      if (cEl) cEl.textContent = String(counts[sec] || 0);
    });
  }

  function enterSector(sec, opts) {
    const valid = sec === "all" || SECTOR_ORDER.includes(sec);
    appState.daily.cat = valid ? sec : "all";
    appState.daily.subtopic = opts && opts.subtopic ? opts.subtopic : "all";
    resetSignalLimit();
    renderSectorRibbon();
    renderSignals();
    syncUrlHash();
    if (opts && opts.scrollToTop && dom.sectorRibbon) {
      dom.sectorRibbon.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function renderSectorDrilldown(cat) {
    if (!dom.sectorDrillBar) return;
    const active = Boolean(cat && cat !== "all");
    dom.sectorDrillBar.hidden = !active;
    if (!active || !dom.subtopicChips) return;
    const sectorSignals = filteredSignals({ overrideCat: cat, ignoreSubtopic: true });
    const subs = collectSectorSubtopics(cat, sectorSignals);
    if (!subs.length) {
      dom.subtopicChips.innerHTML = "";
      dom.subtopicChips.hidden = true;
      return;
    }
    dom.subtopicChips.hidden = false;
    const chips = [{ label: "全部", key: "all", count: sectorSignals.length }]
      .concat(subs.map((x) => ({ label: x.label, key: x.label, count: x.count })));
    dom.subtopicChips.innerHTML = chips.map((c) => {
      const on = (appState.daily.subtopic || "all") === c.key;
      return '<button type="button" class="chip' + (on ? " is-active" : "") + '" data-subtopic="' + escapeHtml(c.key) + '" aria-pressed="' + String(on) + '">' +
        escapeHtml(c.label) + ' <span class="num-plain">' + c.count + "</span></button>";
    }).join("");
  }

  function renderTimelineHtml(sorted, useEvents) {
    const groups = [];
    const byDate = new Map();
    sorted.forEach((s) => {
      const dateKey = extractSignalDateKey(s.time_bjt);
      let grp = byDate.get(dateKey);
      if (!grp) {
        grp = { date: dateKey, items: [] };
        byDate.set(dateKey, grp);
        groups.push(grp);
      }
      grp.items.push(s);
    });
    let seqIdx = 0;
    return groups.map((grp) => {
      const head = '<li class="tl-date-head"><span class="tl-date-label">' + escapeHtml(grp.date) + '</span><span class="tl-date-count num-plain">' + grp.items.length + " 条</span></li>";
      const rows = grp.items.map((s, j) => (
        useEvents
          ? eventTimelineHtml(s, seqIdx++, j === 0, j === grp.items.length - 1)
          : timelineSignalHtml(s, seqIdx++, j === 0, j === grp.items.length - 1)
      )).join("");
      return head + rows;
    }).join("");
  }

  function renderSignals() {
    const useEvents = isEventFeedActive();
    const curated = useEvents ? curatedEventList() : curatedSignalList();
    const all = useEvents ? activeEventCardsList() : (curated ?? arr(state().signals));
    const f = appState.daily;
    const mode = f.mode === "timeline" ? "timeline" : "ranked";
    const filtered = filteredSignals();
    const list = curated && mode === "ranked" ? filtered : sortSignalsForMode(filtered, mode);
    const limit = Math.max(1, Number(f.visibleLimit) || defaultSignalLimit());
    const visible = list.slice(0, limit);
    const remaining = Math.max(0, list.length - visible.length);

    renderFeedViewSwitch();
    renderNewsModeSwitch();
    renderSectorDrilldown(f.cat);

    if (dom.signalList) {
      dom.signalList.hidden = false;
      dom.signalList.classList.toggle("is-timeline", mode === "timeline" && visible.length > 0);
      if (!list.length) {
        dom.signalList.innerHTML = '<li class="empty-hint">没有符合条件的' + (useEvents ? "事件" : "快讯") + ' · <button type="button" class="btn-link" data-clear-filters="daily">清除筛选</button></li>';
      } else {
        const bodyHtml = mode === "timeline"
          ? renderTimelineHtml(visible, useEvents)
          : visible.map(useEvents ? eventCardHtml : signalHtml).join("");
        const moreLabel = useEvents ? "事件" : "快讯";
        const moreHtml = remaining > 0
          ? '<li class="signal-more-row">' +
            '<button type="button" class="btn btn-sm" data-signals-more="1">展开更多' + moreLabel + "（剩余 " + remaining + " 条）</button>" +
            '<button type="button" class="btn-link" data-signals-all="1">全部显示 (' + list.length + ")</button>" +
            "</li>"
          : "";
        dom.signalList.innerHTML = bodyHtml + moreHtml;
      }
    }

    if (dom.dailyCounter) {
      dom.dailyCounter.textContent = remaining > 0
        ? "展示 " + visible.length + " / " + list.length + (useEvents ? " 事" : " 条")
        : "显示 " + list.length + " / " + all.length + (useEvents ? " 事" : " 条");
    }
  }

  function formatEventTiming(ev) {
    const utcStr = ev.event_utc || ev.timestamp_utc;
    if (ev.precision === "exact" && !isBlank(utcStr)) {
      const target = Date.parse(utcStr);
      if (Number.isFinite(target)) {
        const diffMin = Math.round((target - Date.now()) / 60000);
        if (diffMin <= 0) return "已触发 / 进行中";
        const d = Math.floor(diffMin / 1440);
        const h = Math.floor((diffMin % 1440) / 60);
        const m = diffMin % 60;
        if (d > 0) return d + "天 " + h + "小时后";
        if (h > 0) return h + "小时 " + m + "分后";
        return m + "分钟后";
      }
    }
    return String(ev.date_local || ev.date || "") + (ev.precision === "date" ? "（当日）" : "");
  }

  function renderTodaySideCards() {
    if (dom.todayCalendarCard) {
      const events = arr(state().calendar_events).slice(0, 4);
      if (events.length) {
        dom.todayCalendarCard.hidden = false;
        dom.todayCalendarCard.innerHTML =
          '<div class="side-head"><h3>近期财经与财报日历</h3><span class="side-meta">' + events.length + ' 项</span></div>' +
          '<div class="cal-list">' +
          events.map((ev) => '<div class="cal-row"><span class="cal-title">' + escapeHtml(ev.title || ev.name) + '</span><span class="cal-when num-plain">' + escapeHtml(formatEventTiming(ev)) + "</span></div>").join("") +
          "</div>";
      } else {
        dom.todayCalendarCard.hidden = true;
      }
    }

    if (dom.todayRiskCard) {
      if (!hasHeldPositions()) {
        dom.todayRiskCard.hidden = true;
        dom.todayRiskCard.innerHTML = "";
      } else {
        const ws = appState.workspace || {};
        const derivs = arr(ws.derivative_positions).slice().sort((a, b) => {
          const ba = toNum(a.liq_buffer_pct);
          const bb = toNum(b.liq_buffer_pct);
          if (Number.isFinite(ba) && Number.isFinite(bb)) return ba - bb;
          return Number.isFinite(ba) ? -1 : 1;
        });
        if (derivs.length) {
          const top = derivs[0];
          const buf = toNum(top.liq_buffer_pct);
          const bufStr = top.liq_status === "无法估算" || !Number.isFinite(buf) ? "无法估算" : buf.toFixed(1) + "%";
          dom.todayRiskCard.hidden = false;
          dom.todayRiskCard.innerHTML =
            '<div class="side-head"><h3>最高风险持仓关注</h3><button type="button" class="btn-link" data-jump="portfolio:derivatives">合约风控 →</button></div>' +
            '<div class="risk-hi-row"><b>' + escapeHtml(top.symbol) + " · " + (top.side === "short" ? "空" : "多") + '</b>' +
            '<span class="badge ' + (Number.isFinite(buf) && buf < 15 ? "badge-bear" : "badge-warn") + ' num-plain">强平距离 ' + escapeHtml(bufStr) + "</span></div>" +
            '<div class="risk-hi-row" style="color:var(--sub)"><span>未实现盈亏</span>' + amtHtml(top.unrealized_pnl_usd, { signed: true, cls: toneClass(top.unrealized_pnl_usd) }) + "</div>";
        } else {
          dom.todayRiskCard.hidden = true;
          dom.todayRiskCard.innerHTML = "";
        }
      }
    }
  }

  function equityTile(row, showTag) {
    const sym = String(row.symbol || "").toUpperCase();
    const isClickable = /^[A-Z0-9.]{1,12}$/.test(sym) && (!appState.offline || Boolean(allStockProfiles()[sym]));
    const attr = isClickable ? ' data-open-stock="' + escapeHtml(sym) + '" role="button" tabindex="0" title="查看 ' + escapeHtml(sym) + ' 投研"' : "";
    return '<div class="tile"' + attr + ">" +
      '<div class="tile-name"><span>' + escapeHtml(row.name || row.symbol) + '</span><span class="tile-sym">' + escapeHtml(row.symbol) + "</span></div>" +
      '<div class="tile-price num-plain">' + escapeHtml(formatPrice(row.price)) + "</div>" +
      '<div class="tile-chg num-plain ' + toneClass(row.change_pct) + '">' + escapeHtml(formatSignedPct(row.change_pct)) +
      (showTag && !isBlank(row.tag) ? ' <span class="tile-sym">' + escapeHtml(row.tag) + "</span>" : "") + "</div>" +
      "</div>";
  }

  function validEquityRows(key) {
    const eq = state().us_equities || {};
    return arr(eq[key]).filter((r) => r && toNum(r.price) > 0 && Number.isFinite(toNum(r.change_pct)));
  }

  function renderEquities() {
    const eq = state().us_equities || {};
    if (dom.eqSummary) {
      dom.eqSummary.textContent = isBlank(eq.summary) ? "" : eq.summary;
      dom.eqSummary.hidden = isBlank(eq.summary);
    }
    if (dom.eqAsOf) dom.eqAsOf.textContent = isBlank(eq.as_of) ? "" : eq.as_of;
    if (dom.eqIndices) dom.eqIndices.innerHTML = validEquityRows("indices").map((r) => equityTile(r, false)).join("");
    if (dom.eqWatch) dom.eqWatch.innerHTML = validEquityRows("mag7_and_ai").map((r) => equityTile(r, true)).join("");
    if (dom.eqSectors) {
      const sectors = validEquityRows("sectors");
      const maxAbs = Math.max(0.01, ...sectors.map((r) => Math.abs(toNum(r.change_pct))));
      dom.eqSectors.innerHTML = sectors.map((r) => {
        const v = toNum(r.change_pct);
        const w = Math.min(50, (Math.abs(v) / maxAbs) * 50);
        return '<div class="sector-row">' +
          '<span class="sector-name" title="' + escapeHtml(r.symbol) + '">' + escapeHtml(r.name || r.symbol) + "</span>" +
          '<span class="sector-track"><span class="sector-fill ' + (v >= 0 ? "up" : "down") + '" style="width:' + w.toFixed(1) + '%"></span></span>' +
          '<span class="sector-val num-plain ' + toneClass(v) + '">' + escapeHtml(formatSignedPct(v)) + "</span>" +
          "</div>";
      }).join("");
    }
  }

  function renderRadar() {
    if (!dom.heatRadar) return;
    const top = arr(state().signals)
      .filter((s) => toNum(s.heat_48h) > 0)
      .sort((a, b) => toNum(b.heat_48h) - toNum(a.heat_48h))
      .slice(0, 8);
    dom.heatRadar.innerHTML = top.map((s, i) => '<li class="radar-item"><button type="button" class="radar-btn" data-radar="' + escapeHtml(s.id) + '">' +
      '<span class="radar-rank num-plain">' + (i + 1) + "</span>" +
      '<span class="radar-title">' + escapeHtml(stripEmojis(s.title)) + "</span>" +
      '<span class="radar-heat"><span class="num-plain">' + escapeHtml(toNum(s.heat_48h).toFixed(1)) + "</span>" + heatBadge(s.heat_badge) + "</span>" +
      "</button></li>").join("");
  }

  function resetDailyFilters() {
    appState.daily = {
      scope: "all",
      cat: "all",
      subtopic: "all",
      pol: "all",
      highOnly: false,
      keyword: "",
      visibleLimit: defaultSignalLimit(),
      mode: appState.daily.mode || "ranked",
      feedView: appState.daily.feedView || "events"
    };
    syncDailyFilterChipsUi();
    if (dom.curationRulesPanel) dom.curationRulesPanel.hidden = true;
    renderSectorRibbon();
    renderSignals();
    syncUrlHash();
  }

  function jumpToSignal(id) {
    const findInList = () => dom.signalList
      ? Array.from(dom.signalList.querySelectorAll("[data-sig-id]")).find((el) => el.getAttribute("data-sig-id") === String(id))
      : null;
    let el = findInList();
    if (!el) {
      appState.daily.visibleLimit = 500;
      renderSignals();
      el = findInList();
    }
    if (!el) {
      resetDailyFilters();
      appState.daily.visibleLimit = 500;
      renderSignals();
      el = findInList();
    }
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("is-flash");
    void el.offsetWidth;
    el.classList.add("is-flash");
  }

  /* ---------- 持仓：现货、合约风控、策略对照、录入与粘贴导入 ---------- */

  function renderPortfolioView() {
    const ws = appState.workspace || {};
    const sum = ws.summary || {};
    const fx = getFxContext();

    const unpricedNames = arr(ws.unpriced_items)
      .map((u) => (u && typeof u === "object") ? (u.symbol || u.instrument_id || u.id || "") : String(u || ""))
      .filter((x) => !isBlank(x));

    if (dom.portfolioModeHint) {
      const baseHint = appState.offline
        ? "共享离线只读 · 请在登录后服务中录入或同步真实持仓"
        : (ws.summary ? "已接入个人持仓账本 · 保证金只计入现金净值一次" : "暂未录入个人持仓，点击右上角「+ 录入持仓」或「粘贴持仓」");
      dom.portfolioModeHint.textContent = unpricedNames.length
        ? baseHint + " ｜ 部分标的暂无实时报价（暂未计入总净值）：" + unpricedNames.join("、")
        : baseHint;
    }
    if (dom.spotFxHint) {
      dom.spotFxHint.textContent = fx.currency === "CNY" ? fx.label : "单价按原始币种，总估值按 USD";
    }

    if (dom.portfolioSummaryBar) {
      const netEq = sum.net_equity_usd != null ? sum.net_equity_usd : appState.capitalBase;
      const spotTotal = sum.spot_and_cash_usd != null ? sum.spot_and_cash_usd : 0;
      const derivUpl = sum.derivative_upl_usd != null ? sum.derivative_upl_usd : 0;
      const netDir = toNum(sum.net_direction_pct);
      const effLev = toNum(sum.effective_leverage);
      dom.portfolioSummaryBar.innerHTML =
        '<div class="pkpi"><div class="pkpi-lbl">总净值</div><div class="pkpi-val">' + amtHtml(netEq) + "</div></div>" +
        '<div class="pkpi"><div class="pkpi-lbl">现货与现金</div><div class="pkpi-val">' + amtHtml(spotTotal) + "</div></div>" +
        '<div class="pkpi"><div class="pkpi-lbl">合约未实现盈亏</div><div class="pkpi-val">' + amtHtml(derivUpl, { signed: true, cls: toneClass(derivUpl) }) + "</div></div>" +
        '<div class="pkpi"><div class="pkpi-lbl">净方向性敞口</div><div class="pkpi-val num-plain ' + toneClass(netDir) + '">' + escapeHtml(Number.isFinite(netDir) ? formatSignedPct(netDir) : ws.summary ? "不可计算" : "0.00%") + "</div></div>" +
        '<div class="pkpi"><div class="pkpi-lbl">有效杠杆</div><div class="pkpi-val num-plain">' + escapeHtml(Number.isFinite(effLev) ? effLev.toFixed(2) + "x" : ws.summary ? "不可计算" : "0.00x") + "</div></div>";
    }

    // 1. 现货与现金表
    if (dom.spotTableWrap) {
      const spots = arr(ws.spot_balances);
      if (!spots.length) {
        dom.spotTableWrap.innerHTML = '<div class="empty-hint">暂无现货或现金持仓记录</div>';
      } else {
        dom.spotTableWrap.innerHTML =
          '<table class="fin-table sticky-table"><thead><tr>' +
          '<th>资产</th><th>类别</th><th>账户</th><th class="num">数量</th><th class="num">最新价 (原币)</th>' +
          '<th class="num">持仓市值</th><th class="num">未实现盈亏</th><th class="num">占净值</th><th>操作</th>' +
          "</tr></thead><tbody>" +
          spots.map((r) => {
            const id = escapeHtml(r.id);
            const mv = r.market_value_usd;
            const upl = r.unrealized_pnl_usd;
            const wt = toNum(r.weight_pct);
            const isCash = Boolean(r.is_cash) || String(r.asset_class || "").toLowerCase() === "cash";
            const pxNum = toNum(r.price_usd);
            const pxText = (!isBlank(r.price_usd) && Number.isFinite(pxNum) && pxNum > 0)
              ? formatPrice(pxNum)
              : (isCash ? "$1.00" : "未取得报价");
            return "<tr>" +
              "<td>" + escapeHtml(r.symbol) + "</td>" +
              "<td>" + escapeHtml(r.asset_class_cn || r.asset_class || "现货") + "</td>" +
              "<td>" + escapeHtml(r.account_name || r.account_id || "主账户") + "</td>" +
              '<td class="num">' + escapeHtml(String(r.qty ?? "0")) + "</td>" +
              '<td class="num">' + escapeHtml(pxText) + "</td>" +
              '<td class="num">' + (mv != null ? amtHtml(mv) : "—") + "</td>" +
              '<td class="num">' + (upl != null ? amtHtml(upl, { signed: true, cls: toneClass(upl) }) : "—") + "</td>" +
              '<td class="num">' + escapeHtml(Number.isFinite(wt) ? wt.toFixed(1) + "%" : "—") + "</td>" +
              "<td>" + (!appState.offline ? '<button type="button" class="btn-link" data-edit-spot="' + id + '">编辑</button> · <button type="button" class="btn-link" style="color:var(--bear)" data-del-spot="' + id + '">删除</button>' : "只读") + "</td>" +
              "</tr>";
          }).join("") +
          "</tbody></table>";
      }
    }

    // 2. 合约风控（桌面宽表 + 手机双行折叠卡）
    const derivs = arr(ws.derivative_positions);
    if (dom.derivSummaryHint) {
      dom.derivSummaryHint.textContent = derivs.length ? "共 " + derivs.length + " 笔活跃合约" : "无活跃合约";
    }
    if (dom.derivTableWrap) {
      if (!derivs.length) {
        dom.derivTableWrap.innerHTML = '<div class="empty-hint">暂无合约仓位</div>';
      } else {
        dom.derivTableWrap.innerHTML =
          '<table class="fin-table sticky-table"><thead><tr>' +
          '<th>合约</th><th>方向</th><th>模式</th><th class="num">数量</th><th class="num">开仓均价</th><th class="num">标记价</th>' +
          '<th class="num">强平价 / 安全距离</th><th class="num">资金费率</th><th class="num">未实现盈亏</th><th>操作</th>' +
          "</tr></thead><tbody>" +
          derivs.map((d) => {
            const id = escapeHtml(d.id);
            const sideLabel = d.side === "short" ? "做空" : "做多";
            const sideCls = d.side === "short" ? "badge-bear" : "badge-bull";
            const modeLabel = d.margin_mode === "cross" ? "全仓" : "逐仓";
            const markNum = toNum(d.mark_price);
            const markText = (!isBlank(d.mark_price) && Number.isFinite(markNum) && markNum > 0) ? formatPrice(markNum) : "未取得报价";
            const buf = toNum(d.liq_buffer_pct);
            const liqText = d.liq_status === "无法估算" || !Number.isFinite(toNum(d.liq_price))
              ? "无法估算"
              : formatPrice(d.liq_price) + (Number.isFinite(buf) ? " (" + buf.toFixed(1) + "%)" : "");
            const fr = toNum(d.funding_rate);
            return "<tr>" +
              "<td>" + escapeHtml(d.symbol) + "</td>" +
              '<td><span class="badge ' + sideCls + '">' + sideLabel + "</span></td>" +
              "<td>" + modeLabel + "</td>" +
              '<td class="num">' + escapeHtml(String(d.qty ?? "0")) + "</td>" +
              '<td class="num">' + escapeHtml(formatPrice(d.entry_price)) + "</td>" +
              '<td class="num">' + escapeHtml(markText) + "</td>" +
              '<td class="num ' + (liqText === "无法估算" ? "tone-flat" : (Number.isFinite(buf) && buf < 15 ? "tone-down" : "")) + '">' + escapeHtml(liqText) + "</td>" +
              '<td class="num">' + escapeHtml(Number.isFinite(fr) ? (fr * 100).toFixed(4) + "%" : "—") + "</td>" +
              '<td class="num">' + amtHtml(d.unrealized_pnl_usd, { signed: true, cls: toneClass(d.unrealized_pnl_usd) }) + "</td>" +
              "<td>" + (!appState.offline ? '<button type="button" class="btn-link" data-edit-deriv="' + id + '">编辑</button> · <button type="button" class="btn-link" style="color:var(--bear)" data-del-deriv="' + id + '">删除</button>' : "只读") + "</td>" +
              "</tr>";
          }).join("") +
          "</tbody></table>";
      }
    }

    if (dom.derivMobileList) {
      if (!derivs.length) {
        dom.derivMobileList.innerHTML = '<div class="empty-hint">暂无合约仓位</div>';
      } else {
        dom.derivMobileList.innerHTML = derivs.map((d) => {
          const id = escapeHtml(d.id);
          const sideLabel = d.side === "short" ? "空" : "多";
          const sideCls = d.side === "short" ? "badge-bear" : "badge-bull";
          const markNum = toNum(d.mark_price);
          const markText = (!isBlank(d.mark_price) && Number.isFinite(markNum) && markNum > 0) ? formatPrice(markNum) : "未取得报价";
          const wt = toNum(d.weight_pct);
          const buf = toNum(d.liq_buffer_pct);
          const bufStr = d.liq_status === "无法估算" || !Number.isFinite(buf) ? "无法估算" : "安全垫 " + buf.toFixed(1) + "%";
          return '<div class="deriv-card">' +
            '<button type="button" class="deriv-card-toggle" data-deriv-toggle>' +
            '<span class="deriv-line-1"><span><b>' + escapeHtml(d.symbol) + '</b> <span class="badge ' + sideCls + '">' + sideLabel + '</span></span><span class="num-plain">' + escapeHtml(Number.isFinite(wt) ? wt.toFixed(1) + "% 净值" : "") + "</span></span>" +
            '<span class="deriv-line-2"><span class="num-plain">标记 ' + escapeHtml(markText) + '</span><span class="num-plain">' + escapeHtml(bufStr) + "</span></span>" +
            "</button>" +
            '<div class="deriv-details">' +
            '<div class="deriv-kv-grid">' +
            '<div><span>模式</span><b>' + (d.margin_mode === "cross" ? "全仓" : "逐仓") + "</b></div>" +
            '<div><span>开仓价</span><b class="num-plain">' + escapeHtml(formatPrice(d.entry_price)) + "</b></div>" +
            '<div><span>占用保证金</span><b>' + amtHtml(d.margin_usd) + "</b></div>" +
            '<div><span>未实现盈亏</span><b>' + amtHtml(d.unrealized_pnl_usd, { signed: true, cls: toneClass(d.unrealized_pnl_usd) }) + "</b></div>" +
            "</div>" +
            (!appState.offline ? '<div style="display:flex;gap:12px"><button type="button" class="btn btn-sm" data-edit-deriv="' + id + '">编辑</button><button type="button" class="btn btn-sm btn-ghost btn-danger" data-del-deriv="' + id + '">删除</button></div>' : "") +
            "</div></div>";
        }).join("");
      }
    }

    // 3. 策略对照表
    if (dom.strategyCompareWrap) {
      const cmp = arr(ws.strategy_comparison);
      if (cmp.length) {
        dom.strategyCompareWrap.innerHTML =
          '<table class="fin-table sticky-table" style="margin-bottom:14px"><thead><tr>' +
          '<th>策略模块</th><th class="num">目标占比</th><th class="num">实际占比</th><th class="num">偏离 (pp)</th><th class="num">目标预算</th>' +
          "</tr></thead><tbody>" +
          cmp.map((row) => {
            const k = row.bucket || row.strategy_id || row["sl" + "eeve"] || "";
            const name = row.name || BUCKET_NAMES[k] || k;
            const tgt = toNum(row.target_pct) || 0;
            const act = toNum(row.actual_pct) || 0;
            const diff = toNum(row.delta_pp);
            const budget = appState.capitalBase * (tgt / 100);
            return "<tr><td>" + escapeHtml(name) + "</td>" +
              '<td class="num">' + escapeHtml(formatWeight(tgt)) + "</td>" +
              '<td class="num">' + escapeHtml(formatWeight(act)) + "</td>" +
              '<td class="num ' + toneClass(diff) + '">' + escapeHtml(Number.isFinite(diff) ? (diff > 0 ? "+" : "") + diff.toFixed(1) + " pp" : "0.0 pp") + "</td>" +
              '<td class="num">' + amtHtml(budget) + "</td></tr>";
          }).join("") +
          "</tbody></table>";
      } else {
        dom.strategyCompareWrap.innerHTML = "";
      }
    }
  }

  function applyPositionFormKind(kind) {
    appState.pfKind = kind === "derivative" ? "derivative" : "spot";
    setActive(dom.pfKindSeg, "data-pfkind", appState.pfKind);
    const isDeriv = appState.pfKind === "derivative";
    if (dom.pfAssetClassWrap) dom.pfAssetClassWrap.hidden = isDeriv;
    if (dom.pfDirWrap) dom.pfDirWrap.hidden = !isDeriv;
    if (dom.pfMarginModeWrap) dom.pfMarginModeWrap.hidden = !isDeriv;
    if (dom.pfMarginWrap) dom.pfMarginWrap.hidden = !isDeriv;
    if (dom.pfLiqWrap) dom.pfLiqWrap.hidden = !isDeriv;
  }

  const positionAmountInputs = ["pfQty", "pfEntry", "pfMargin", "pfLiq"];

  function syncPositionAmountInputs() {
    if (!appState.showAmounts && !appState.maskedPositionValues) {
      appState.maskedPositionValues = positionAmountInputs.map((key) => dom[key].value);
    }
    positionAmountInputs.forEach((key, index) => {
      const input = dom[key];
      if (!appState.showAmounts) input.value = "";
      else if (appState.maskedPositionValues) input.value = appState.maskedPositionValues[index];
      input.disabled = !appState.showAmounts;
    });
    if (appState.showAmounts) appState.maskedPositionValues = null;
  }

  function positionFormData(amounts = positionAmountInputs.map((key) => dom[key].value)) {
    const [qty, entry, margin, liq] = amounts;
    const isDeriv = appState.pfKind === "derivative";
    const data = {
      account_id: dom.pfAccount.value.trim() || "main",
      symbol: dom.pfSymbol.value.trim().toUpperCase(),
      qty: qty.trim(),
      sleeve: dom.pfBucket.value,
      thesis: dom.pfNote.value.trim()
    };
    if (isDeriv) {
      data.side = dom.pfSide.value;
      data.margin_mode = dom.pfMarginMode.value;
      data.entry_price = entry.trim() || "0";
      data.margin_usd = margin.trim() || "0";
      data.venue_liq = liq.trim();
    } else {
      data.asset_class = dom.pfAssetClass.value;
      data.avg_cost = entry.trim() || "0";
    }
    return data;
  }

  function openPositionDrawer(kind, row) {
    if (appState.offline) return showToast("共享离线快照只读，禁止录入持仓", "error");
    const r = row || {};
    dom.positionForm.reset();
    applyPositionFormKind(kind || "spot");
    dom.pfKindSeg.querySelectorAll("button").forEach((btn) => { btn.disabled = Boolean(r.id); });
    dom.pfId.value = r.id ? String(r.id) : "";
    dom.pfAccount.value = r.account_name || r.account_id || "主账户";
    dom.pfSymbol.value = r.symbol || "";
    if (r.asset_class) ensureOption(dom.pfAssetClass, r.asset_class);
    if (r.side) ensureOption(dom.pfSide, r.side);
    if (r.margin_mode) ensureOption(dom.pfMarginMode, r.margin_mode);
    const amounts = [r.qty ?? "", r.entry_price ?? r.avg_cost ?? r.cost_basis ?? "0", r.margin_usd ?? "0", r.venue_liq ?? ""].map(String);
    appState.maskedPositionValues = appState.showAmounts ? null : amounts;
    if (appState.showAmounts) {
      positionAmountInputs.forEach((key, index) => { dom[key].value = amounts[index]; });
    }
    const bVal = r.strategy_id || r.bucket || r["sl" + "eeve"] || "";
    ensureOption(dom.pfBucket, bVal);
    dom.pfNote.value = r.thesis ?? r.note ?? "";
    appState.positionFormInitial = positionFormData(appState.maskedPositionValues ?? undefined);
    syncPositionAmountInputs();
    if (dom.posDrawerTitle) dom.posDrawerTitle.textContent = r.id ? "编辑持仓" : "录入持仓";
    openDrawer(dom.positionDrawer, dom.pfSymbol);
  }

  async function submitPosition(e) {
    e.preventDefault();
    if (appState.offline) return showToast("共享离线快照只读，禁止提交", "error");
    if (!appState.showAmounts) return showToast("请先显示金额，再保存持仓", "error");
    const data = positionFormData();
    if (!data.symbol) return showToast("请填写资产代码", "error");
    const qty = toNum(data.qty);
    const entity = appState.pfKind;
    if (!Number.isFinite(qty) || qty < 0 || (entity === "derivative" && qty === 0)) {
      return showToast(entity === "derivative" ? "合约数量必须大于 0" : "请填写有效的非负数量", "error");
    }
    const id = dom.pfId.value || undefined;
    const payload = id
      ? Object.fromEntries(Object.entries(data).filter(([key, value]) => value !== appState.positionFormInitial[key]))
      : data;
    if (id && !Object.keys(payload).length) return closeDrawer("position");

    dom.pfSaveBtn.disabled = true;
    try {
      const rev = toNum((appState.workspace || {}).revision) || 0;
      await apiJson("/api/v2/changes", {
        method: "POST",
        body: {
          request_id: newReqId(),
          expected_revision: rev,
          ops: [{ op: "put", entity, id, data: payload }]
        }
      });
      await refreshWorkspaceOnly();
      closeDrawer("position");
      showToast("持仓已保存", "ok");
    } catch (err) {
      showToast(err.message || "保存持仓失败", "error");
    } finally {
      dom.pfSaveBtn.disabled = false;
    }
  }

  async function deletePortfolioRow(entity, id) {
    if (appState.offline) return showToast("共享离线快照只读", "error");
    if (!window.confirm("确定删除该条持仓记录？")) return;
    try {
      const rev = toNum((appState.workspace || {}).revision) || 0;
      await apiJson("/api/v2/changes", {
        method: "POST",
        body: {
          request_id: newReqId(),
          expected_revision: rev,
          ops: [{ op: "remove", entity, id }]
        }
      });
      await refreshWorkspaceOnly();
      showToast("持仓已删除", "ok");
    } catch (err) {
      showToast(err.message || "删除失败", "error");
    }
  }

  function invalidateImportPreview() {
    appState.previewReqSeq += 1;
    appState.importPreview = null;
    dom.impCommitBtn.disabled = true;
    dom.impPreviewBtn.disabled = false;
    dom.importPreviewArea.hidden = true;
  }

  async function runImportPreview() {
    if (appState.offline) return showToast("共享离线快照只读", "error");
    const text = dom.impBody.value.trim();
    if (!text) return showToast("请先粘贴持仓文本或 CSV", "error");
    invalidateImportPreview();
    const previewReqSeq = appState.previewReqSeq;
    dom.impPreviewBtn.disabled = true;
    try {
      const res = await apiJson("/api/v2/import/preview", {
        method: "POST",
        body: {
          account_id: dom.impAccount.value.trim() || "main",
          format: dom.impFormat.value,
          mode: dom.impMode.value,
          text
        }
      });
      if (previewReqSeq !== appState.previewReqSeq) return;
      appState.importPreview = res;
      const issues = arr(res.issues);
      const errIssues = issues.filter((x) => x && x.severity === "error");
      const warnIssues = issues.filter((x) => x && x.severity !== "error");
      dom.impCommitBtn.disabled = res.can_commit !== true;

      const rawDiff = res && res.diff;
      let diffs = [];
      let removedCount = 0;
      let countsText = "";
      if (rawDiff && typeof rawDiff === "object" && !Array.isArray(rawDiff)) {
        const mapDiff = (label, tone, d) => ({
          label,
          tone,
          entity: d.entity === "derivative" ? "合约" : "现货",
          side: d.side === "short" ? "做空" : d.side === "long" ? "做多" : "",
          symbol: d.instrument_id || d.symbol || "",
          qty: d.qty != null ? String(d.qty) + (d.qty_unit ? " " + d.qty_unit : "") : "",
          summary: d.summary || ""
        });
        const added = arr(rawDiff.added).map((d) => mapDiff("新增", "badge-bull", d));
        const updated = arr(rawDiff.updated).map((d) => mapDiff("更新", "badge-accent", d));
        const removed = arr(rawDiff.removed).map((d) => mapDiff("将删除", "badge-bear", d));
        removedCount = removed.length;
        diffs = added.concat(updated, removed);
        const parts = [];
        if (added.length) parts.push("新增 " + added.length);
        if (updated.length) parts.push("更新 " + updated.length);
        if (removed.length) parts.push("删除 " + removed.length);
        countsText = parts.join(" · ");
      } else {
        diffs = arr(rawDiff || (res && res.ops)).map((d) => ({
          label: d.op === "remove" ? "将删除" : (d.action || d.op || "更新"),
          tone: d.op === "remove" ? "badge-bear" : "badge-accent",
          entity: d.entity === "derivative" ? "合约" : "现货",
          side: "",
          symbol: d.symbol || d.instrument_id || "",
          qty: d.qty != null ? String(d.qty) : "",
          summary: d.summary || ""
        }));
      }

      dom.importPreviewArea.hidden = false;
      dom.importPreviewArea.innerHTML =
        (errIssues.length
          ? '<div class="discipline-warn">' + errIssues.map((x) => escapeHtml(x.message || x)).join("<br>") + "</div>"
          : '<div class="discipline-ok">解析校验通过，共 ' + diffs.length + " 项待确认变更" + (countsText ? "（" + escapeHtml(countsText) + "）" : "") + "</div>") +
        (warnIssues.length
          ? '<div class="discipline-warn" style="margin-top:6px">' + warnIssues.map((x) => escapeHtml(x.message || x)).join("<br>") + "</div>"
          : "") +
        (removedCount
          ? '<div class="discipline-warn" style="margin-top:6px">核对覆盖提醒：确认提交后将删除未在本次清单中的 ' + removedCount + " 笔持仓</div>"
          : "") +
        '<div class="diff-list">' +
        diffs.map((d) =>
          '<div class="diff-item"><span><span class="badge ' + d.tone + '">' + escapeHtml(d.label) + "</span> " +
          escapeHtml(d.entity) + (d.side ? " · " + escapeHtml(d.side) : "") + " · <b>" + escapeHtml(d.symbol) + "</b></span>" +
          '<span class="num-plain">' + escapeHtml(d.qty ? "数量 " + d.qty : d.summary) + "</span></div>"
        ).join("") +
        "</div>";
    } catch (err) {
      if (previewReqSeq === appState.previewReqSeq) showToast(err.message || "预览解析失败", "error");
    } finally {
      if (previewReqSeq === appState.previewReqSeq) dom.impPreviewBtn.disabled = false;
    }
  }

  async function commitImportPreview() {
    const prev = appState.importPreview;
    if (!prev?.can_commit || !prev.preview_id || dom.impCommitBtn.disabled || appState.offline) return;
    dom.impCommitBtn.disabled = true;
    try {
      const rev = prev.expected_revision ?? prev.revision;
      await apiJson("/api/v2/import/commit", {
        method: "POST",
        body: {
          preview_id: prev.preview_id,
          request_id: newReqId(),
          expected_revision: rev
        }
      });
      if (appState.importPreview === prev) closeDrawer("import");
      await refreshWorkspaceOnly({ strict: true });
      showToast("批量持仓已提交", "ok");
    } catch (err) {
      if (err.status === 409 && appState.importPreview === prev) invalidateImportPreview();
      showToast(err.message || "提交导入失败", "error");
    } finally {
      if (appState.importPreview === prev) dom.impCommitBtn.disabled = false;
    }
  }

  async function refreshWorkspaceOnly(opts) {
    if (appState.offline) return;
    const hadWs = Boolean(appState.workspace);
    try {
      const ws = await apiJson("/api/v2/workspace?currency=" + encodeURIComponent(appState.currency));
      if (ws && typeof ws === "object") {
        appState.workspace = ws;
        renderAll();
      }
    } catch (err) {
      const status = err && err.status;
      if (hadWs || (opts && opts.strict) || (status !== 401 && status !== 403 && status !== 404)) {
        showToast("持仓工作区同步失败，请刷新重试", "error");
      }
    }
  }

  async function saveCurationRule(opKind, data, idToDelete) {
    if (appState.offline) return showToast("共享离线快照只读，禁止修改筛选规则", "error");
    const ruleObj = opKind === "put" ? Object.assign({ id: "rule-" + Date.now() }, data) : null;
    try {
      const rev = toNum((appState.workspace || {}).revision) || 0;
      await apiJson("/api/v2/changes", {
        method: "POST",
        body: {
          request_id: newReqId(),
          expected_revision: rev,
          ops: [opKind === "put" ? { op: "put", entity: "rule", data: ruleObj } : { op: "remove", entity: "rule", id: idToDelete }]
        }
      });
      await refreshWorkspaceOnly();
      return true;
    } catch (err) {
      showToast(err.message || "规则保存失败", "error");
      return false;
    }
  }

  /* ---------- 个股投研（含开仓纪律检查卡 & SEC EDGAR 披露） ---------- */

  const RESEARCH_MARKET_LABELS = { all: "全部", us: "美股", cn: "A股", hk: "港股", crypto: "加密" };

  function allStockProfiles() {
    return Object.assign({}, state().stock_profiles || {}, appState.customStocks || {});
  }

  function assetMarket(p) {
    if (!p) return "us";
    const explicit = String(p.market || "").trim().toLowerCase();
    if (explicit) return explicit;
    if (String(p.asset_type || "").toLowerCase() === "crypto") return "crypto";
    const sym = String(p.symbol || "").toUpperCase();
    if (/\.(SH|SZ|BJ)$/.test(sym)) return "cn";
    if (/\.HK$/.test(sym)) return "hk";
    return "us";
  }

  function assetCurrencySym(p) {
    if (p && !isBlank(p.currency_symbol)) return String(p.currency_symbol);
    const m = assetMarket(p);
    return m === "cn" ? "¥" : m === "hk" ? "HK$" : "$";
  }

  function filteredStockKeys() {
    const map = allStockProfiles();
    const keys = Object.keys(map);
    const mkt = appState.stockMarket || "all";
    if (mkt === "all") return keys;
    return keys.filter((k) => assetMarket(map[k]) === mkt);
  }

  function getActiveStock() {
    const map = allStockProfiles();
    const sym = String(appState.stockSymbol || "AAPL").toUpperCase();
    const mkt = appState.stockMarket || "all";
    if (map[sym] && (mkt === "all" || assetMarket(map[sym]) === mkt)) return map[sym];
    const mKeys = filteredStockKeys();
    if (mKeys.length) {
      appState.stockSymbol = mKeys[0];
      return map[mKeys[0]];
    }
    if (map[sym]) return map[sym];
    const keys = Object.keys(map);
    if (keys.length) {
      appState.stockSymbol = keys[0];
      return map[keys[0]];
    }
    return null;
  }

  function fmtMetricVal(val, decimals, suffix) {
    const num = toNum(val);
    if (!Number.isFinite(num)) return "—";
    return num.toFixed(decimals == null ? 2 : decimals) + (suffix || "");
  }

  function renderStockChips() {
    if (dom.stockMarketTabs) {
      setActive(dom.stockMarketTabs, "data-smarket", appState.stockMarket || "all");
    }
    if (!dom.stockChips) return;
    const map = allStockProfiles();
    const keys = filteredStockKeys();
    const mktFilter = appState.stockMarket || "all";
    if (!keys.length) {
      dom.stockChips.innerHTML = '<span class="panel-meta">该市场暂无预设标的，可在右侧输入代码或名称实时检索</span>';
      return;
    }
    dom.stockChips.innerHTML = keys.map((sym) => {
      const p = map[sym];
      const chg = toNum(p.change_pct);
      const on = sym === appState.stockSymbol;
      const m = assetMarket(p);
      const showCn = m !== "us" && !isBlank(p.name_cn) && p.name_cn !== sym;
      const mTag = mktFilter === "all" && m !== "us" ? '<span class="chip-mkt">' + escapeHtml(RESEARCH_MARKET_LABELS[m] || m) + "</span>" : "";
      return '<button type="button" class="chip' + (on ? " is-active" : "") + '" data-open-stock="' + escapeHtml(sym) + '" aria-pressed="' + String(on) + '">' +
        mTag +
        '<span class="num-plain">' + escapeHtml(sym) + "</span>" +
        (showCn ? '<span>' + escapeHtml(p.name_cn) + "</span>" : "") +
        (Number.isFinite(chg) ? '<span class="num-plain ' + toneClass(chg) + '">' + escapeHtml(formatSignedPct(chg)) + "</span>" : "") +
        "</button>";
    }).join("");
  }

  function renderStockBanner(p) {
    if (!dom.stockBanner) return;
    if (!p) {
      dom.stockBanner.innerHTML = '<div class="empty-hint">暂无投研数据，请在上方搜索框输入代码或名称检索</div>';
      return;
    }
    const chg = toNum(p.change_pct);
    const curSym = assetCurrencySym(p);
    const mktLbl = RESEARCH_MARKET_LABELS[assetMarket(p)] || "美股";
    dom.stockBanner.innerHTML =
      '<div class="sb-left">' +
      '<div class="sb-mark">' + escapeHtml(p.symbol) + "</div>" +
      '<div class="min-w-0">' +
      '<div class="sb-title-row">' +
      '<h2 class="sb-title">' + escapeHtml(assetMarket(p) === "us" ? (p.name_en || p.name_cn || p.symbol) : (p.name_cn || p.name_en || p.symbol)) + "</h2>" +
      '<span class="sb-price num-plain">' + escapeHtml(formatAssetPrice(p.price, curSym)) + "</span>" +
      '<span class="sb-chg num-plain ' + toneClass(chg) + '">' + escapeHtml(formatSignedPct(chg)) + "</span>" +
      "</div>" +
      '<div class="sb-meta">' +
      '<strong class="num-plain">' + escapeHtml(p.symbol) + "</strong>" +
      "<span>· " + escapeHtml(p.name_cn || "") + "</span>" +
      "<span>· " + escapeHtml(mktLbl) + " / " + escapeHtml(p.exchange || mktLbl) + "</span>" +
      "<span>· " + escapeHtml(p.sector_cn || "") + "</span>" +
      (!isBlank(p.as_of) ? '<span class="num-plain">· ' + escapeHtml(p.as_of) + "</span>" : "") +
      "</div></div></div>" +
      '<div class="sb-actions">' +
      '<button type="button" class="btn btn-sm" data-jump="today">返回今日</button>' +
      (!appState.offline ? '<button type="button" class="btn btn-sm btn-accent" id="stockReviewBtn">记入操作复盘</button>' : "") +
      "</div>";
  }

  function sliceStockChart(p) {
    const ch = p.chart || {};
    const dates = arr(ch.dates);
    const closes = arr(ch.closes).map(toNum).filter(Number.isFinite);
    const n = Math.min(dates.length, closes.length);
    if (!n) return { dates: [], closes: [], ret: null };
    const winMap = { "1w": 6, "1m": 22, "3m": 64, "6m": 127, "1y": n };
    const count = Math.min(n, winMap[appState.stockRange] || n);
    const dSlice = dates.slice(n - count, n);
    const cSlice = closes.slice(n - count, n);
    let ret = toNum(ch["return_" + appState.stockRange]);
    if (!Number.isFinite(ret) && cSlice.length >= 2 && cSlice[0] > 0) {
      ret = ((cSlice[cSlice.length - 1] - cSlice[0]) / cSlice[0]) * 100;
    }
    return { dates: dSlice, closes: cSlice, ret: Number.isFinite(ret) ? ret : null };
  }

  let activeChartPts = [];
  let activeChartCurSym = "$";

  function renderStockChart(p) {
    if (!dom.stockChartWrap) return;
    setActive(dom.stockRangeSeg, "data-srange", appState.stockRange);
    const curSym = assetCurrencySym(p);
    activeChartCurSym = curSym;
    const series = sliceStockChart(p);
    if (dom.stockChartRet) {
      if (Number.isFinite(series.ret)) {
        dom.stockChartRet.textContent = formatSignedPct(series.ret) + " (" + appState.stockRange.toUpperCase() + ")";
        dom.stockChartRet.className = "chart-ret num-plain " + toneClass(series.ret);
      } else {
        dom.stockChartRet.textContent = "";
      }
    }
    const closes = series.closes;
    const dates = series.dates;
    if (closes.length < 2) {
      activeChartPts = [];
      dom.stockChartWrap.innerHTML = '<div class="empty-hint">暂无历史价格走势数据</div>';
      return;
    }

    const W = 720, H = 252, padL = 8, padR = 64, padT = 14, padB = 24;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const minP = Math.min(...closes), maxP = Math.max(...closes);
    const span = Math.max(minP * 0.01, maxP - minP, 1e-6);
    const lo = minP - span * 0.08, hi = maxP + span * 0.08;
    const yOf = (v) => padT + ((hi - v) / (hi - lo)) * plotH;
    const xOf = (i) => padL + (i / Math.max(1, closes.length - 1)) * plotW;

    const pts = closes.map((v, i) => ({ x: xOf(i), y: yOf(v), price: v, date: dates[i] || "" }));
    activeChartPts = pts;

    const isUp = Number.isFinite(series.ret) ? series.ret >= 0 : closes[closes.length - 1] >= closes[0];
    const strokeCol = isUp ? "var(--bull)" : "var(--bear)";
    const polyPts = pts.map((pt) => pt.x.toFixed(1) + "," + pt.y.toFixed(1)).join(" ");
    const areaPts = polyPts + " " + pts[pts.length - 1].x.toFixed(1) + "," + (padT + plotH).toFixed(1) + " " + pts[0].x.toFixed(1) + "," + (padT + plotH).toFixed(1);

    const fmtAxis = (v) => curSym + (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toFixed(4));
    const gridLevels = [0, 0.5, 1].map((t) => {
      const val = hi - t * (hi - lo);
      const y = padT + t * plotH;
      return '<line class="chart-grid-line" x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '"/>' +
        '<text class="chart-axis-text" x="' + (W - padR + 6) + '" y="' + (y + 4).toFixed(1) + '">' + escapeHtml(fmtAxis(val)) + "</text>";
    }).join("");

    const lastPt = pts[pts.length - 1];
    const dateLabels =
      '<text class="chart-axis-text" x="' + padL + '" y="' + (H - 4) + '">' + escapeHtml(dates[0]) + "</text>" +
      '<text class="chart-axis-text" x="' + (padL + plotW / 2 - 28) + '" y="' + (H - 4) + '">' + escapeHtml(dates[Math.floor(dates.length / 2)]) + "</text>" +
      '<text class="chart-axis-text" x="' + (W - padR - 66) + '" y="' + (H - 4) + '">' + escapeHtml(dates[dates.length - 1]) + "</text>";

    if (dom.stockChartReadout) {
      dom.stockChartReadout.textContent = "最新收盘：" + lastPt.date + " · " + formatAssetPrice(lastPt.price, curSym) + "（移动鼠标或手指查看区间点位）";
    }

    dom.stockChartWrap.innerHTML =
      '<svg class="stock-svg" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" aria-label="' + escapeHtml(p.symbol) + ' 价格走势">' +
      "<defs><linearGradient id=\"stockAreaGrad\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
      '<stop offset="0%" stop-color="' + strokeCol + '" stop-opacity="0.28"/>' +
      '<stop offset="100%" stop-color="' + strokeCol + '" stop-opacity="0.01"/>' +
      "</linearGradient></defs>" +
      gridLevels + dateLabels +
      '<polygon points="' + areaPts + '" fill="url(#stockAreaGrad)"/>' +
      '<line class="chart-grid-line" x1="' + padL + '" y1="' + lastPt.y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + lastPt.y.toFixed(1) + '"/>' +
      '<polyline points="' + polyPts + '" fill="none" stroke="' + strokeCol + '" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>' +
      '<line id="stockCursorLine" class="chart-cursor-line" x1="0" y1="' + padT + '" x2="0" y2="' + (padT + plotH) + '" hidden/>' +
      '<circle id="stockCursorDot" cx="' + lastPt.x.toFixed(1) + '" cy="' + lastPt.y.toFixed(1) + '" r="4.5" fill="' + strokeCol + '" stroke="var(--surface)" stroke-width="2"/>' +
      "</svg>";
  }

  function handleChartPointer(clientX) {
    if (!dom.stockChartWrap || !activeChartPts.length) return;
    const svg = dom.stockChartWrap.querySelector("svg");
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const relX = ((clientX - rect.left) / rect.width) * 720;
    let best = activeChartPts[0], bestDist = Infinity;
    for (let i = 0; i < activeChartPts.length; i += 1) {
      const d = Math.abs(activeChartPts[i].x - relX);
      if (d < bestDist) { bestDist = d; best = activeChartPts[i]; }
    }
    const line = document.getElementById("stockCursorLine");
    const dot = document.getElementById("stockCursorDot");
    if (line) { line.setAttribute("x1", best.x.toFixed(1)); line.setAttribute("x2", best.x.toFixed(1)); line.removeAttribute("hidden"); }
    if (dot) { dot.setAttribute("cx", best.x.toFixed(1)); dot.setAttribute("cy", best.y.toFixed(1)); }
    if (dom.stockChartReadout) dom.stockChartReadout.textContent = best.date + " · 收盘 " + formatAssetPrice(best.price, activeChartCurSym);
  }

  function renderStockSummary(p) {
    if (!dom.stockSummaryPanel) return;
    const ov = p.overview || {}, val = p.valuation || {}, fin = p.financials || {}, health = p.health || {};
    const curSym = assetCurrencySym(p);
    const isCrypto = assetMarket(p) === "crypto";
    const cm = p.crypto_metrics || {};
    const score = Math.max(0, Math.min(10, toNum(health.score) || 0));
    const circ = 2 * Math.PI * 27;
    const dash = ((score / 10) * circ).toFixed(1);
    const lo52 = toNum(ov.low_52w), hi52 = toNum(ov.high_52w), px = toNum(p.price);
    let pos52 = 50;
    if (Number.isFinite(lo52) && Number.isFinite(hi52) && Number.isFinite(px) && hi52 > lo52) {
      pos52 = Math.max(2, Math.min(98, ((px - lo52) / (hi52 - lo52)) * 100));
    }
    const checksHtml = arr(health.checks).map((c) =>
      '<div class="health-chk' + (c.pass ? " is-pass" : "") + '"><span>' + (c.pass ? "✓" : "·") + "</span><span>" + escapeHtml(c.label) + "</span></div>"
    ).join("");

    const headlineHtml = isCrypto
      ? '<div><div class="val-kicker">盘面量能 · 趋势评分</div>' +
        '<div class="val-big"><span><span class="val-pe-num">' + escapeHtml(cm.turnover_24h_fmt || ov.turnover_fmt || "—") + '</span> <span class="val-sub">24h成交额</span></span>' +
        '<span class="val-sub num-plain">30日年化波动 ' + escapeHtml(cm.vol_30d_ann_pct != null ? cm.vol_30d_ann_pct + "%" : "—") + "</span>" +
        '<span class="val-sub num-plain">90日回撤 ' + escapeHtml(cm.max_drawdown_90d_pct != null ? cm.max_drawdown_90d_pct + "%" : "—") + "</span></div></div>"
      : '<div><div class="val-kicker">估值定位 · 健康评分</div>' +
        '<div class="val-big"><span><span class="val-pe-num">' + escapeHtml(fmtMetricVal(val.pe_ttm, 1)) + '</span> <span class="val-sub">P/E</span></span>' +
        '<span class="val-sub num-plain">远期 P/E ' + escapeHtml(fmtMetricVal(val.pe_forward, 1)) + "</span>" +
        '<span class="val-sub num-plain">P/S ' + escapeHtml(fmtMetricVal(val.ps_ttm, 1)) + "</span></div></div>";

    const kvHtml = isCrypto
      ? '<div class="mini-kv-grid">' +
        '<div class="mini-kv"><span>MA20 乖离</span><b class="' + toneClass(p.bias20_pct) + '">' + escapeHtml(Number.isFinite(toNum(p.bias20_pct)) ? formatSignedPct(p.bias20_pct) : "—") + "</b></div>" +
        '<div class="mini-kv"><span>MA50 乖离</span><b class="' + toneClass(p.bias50_pct) + '">' + escapeHtml(Number.isFinite(toNum(p.bias50_pct)) ? formatSignedPct(p.bias50_pct) : "—") + "</b></div>" +
        '<div class="mini-kv"><span>30日放量比</span><b>' + escapeHtml(cm.vol_ratio_30d != null ? cm.vol_ratio_30d + "x" : "—") + "</b></div>" +
        '<div class="mini-kv"><span>MA200 均线</span><b>' + escapeHtml(cm.ma200 != null ? formatAssetPrice(cm.ma200, curSym) : "—") + "</b></div></div>"
      : '<div class="mini-kv-grid">' +
        '<div class="mini-kv"><span>营收同比</span><b class="' + toneClass(parseFloat(fin.rev_growth_yoy)) + '">' + escapeHtml(fin.rev_growth_yoy || "—") + "</b></div>" +
        '<div class="mini-kv"><span>净利同比</span><b class="' + toneClass(parseFloat(fin.earnings_growth_yoy)) + '">' + escapeHtml(fin.earnings_growth_yoy || "—") + "</b></div>" +
        '<div class="mini-kv"><span>总市值</span><b>' + escapeHtml(ov.market_cap_fmt || "—") + "</b></div>" +
        '<div class="mini-kv"><span>股息率</span><b>' + escapeHtml(ov.dividend_yield_fmt || "—") + "</b></div></div>";

    dom.stockSummaryPanel.innerHTML =
      headlineHtml +
      '<div class="health-box"><div class="health-ring"><svg width="68" height="68" viewBox="0 0 68 68">' +
      '<circle cx="34" cy="34" r="27" fill="none" stroke="var(--rule-strong)" stroke-width="5"/>' +
      '<circle cx="34" cy="34" r="27" fill="none" stroke="var(--bull)" stroke-width="5" stroke-linecap="round" stroke-dasharray="' + dash + " " + circ.toFixed(1) + '"/>' +
      '</svg><span class="health-score">' + score + '</span></div><div class="health-checks">' + checksHtml + "</div></div>" +
      '<div class="range-52w"><div class="val-kicker">' + (isCrypto ? "365日价格区间" : "52 周价格区间") + "</div>" +
      '<div class="range-bar"><span class="range-fill" style="width:' + pos52.toFixed(1) + '%"></span><span class="range-thumb" style="left:' + pos52.toFixed(1) + '%"></span></div>' +
      '<div class="range-labels num-plain"><span>' + escapeHtml(formatAssetPrice(lo52, curSym)) + " (低)</span><span>现价 " + escapeHtml(formatAssetPrice(px, curSym)) + "</span><span>" + escapeHtml(formatAssetPrice(hi52, curSym)) + " (高)</span></div></div>" +
      kvHtml;
  }

  function findDeepReportFile(p) {
    if (p && !isBlank(p.deep_report_file)) return String(p.deep_report_file).trim();
    const sym = String((p && p.symbol) || "").trim().toLowerCase();
    if (!sym) return "";
    const symClean = sym.replace(/[^a-z0-9]/g, "");
    const p1 = "ta_" + sym + "_";
    const p2 = "ta_" + symClean + "_";
    const matches = arr(state().articles).filter((a) => {
      const fn = String((a && a.filename) || "").toLowerCase();
      return fn.indexOf(p1) === 0 || (symClean && fn.indexOf(p2) === 0);
    });
    if (!matches.length) return "";
    matches.sort((a, b) => String(b.mtime || b.filename || "").localeCompare(String(a.mtime || a.filename || "")));
    return String(matches[0].filename || "");
  }

  function computeQuickThesisFallback(p, relSignals, relKols) {
    const ov = p.overview || {}, fin = p.financials || {}, val = p.valuation || {}, chk = p.pre_trade_checklist || {};
    const rg = state().risk_gate || {};
    const px = toNum(p.price), tgt = toNum(ov.target_mean_price);
    const upside = Number.isFinite(tgt) && Number.isFinite(px) && px > 0 ? ((tgt - px) / px) * 100 : NaN;
    const bias20 = toNum(chk.bias20_pct ?? p.bias20_pct);
    const pe = toNum(val.pe_ttm);
    const revG = toNum(parseFloat(fin.revenue_growth_yoy));
    const earnG = toNum(parseFloat(fin.earnings_growth_yoy));
    const healthSc = toNum((p.financial_health || {}).score);
    const bullSigs = arr(relSignals).filter((s) => s.direction === "bullish").length;
    const bearSigs = arr(relSignals).filter((s) => s.direction === "bearish").length;
    const bullKols = arr(relKols).filter((k) => k.stance === "偏多").length;
    const bearKols = arr(relKols).filter((k) => k.stance === "偏空").length;

    let score = 55;
    if (Number.isFinite(upside)) score += upside >= 15 ? 12 : upside >= 5 ? 6 : upside <= -10 ? -12 : 0;
    if (Number.isFinite(revG)) score += revG >= 15 ? 8 : revG < 0 ? -8 : 0;
    if (Number.isFinite(healthSc)) score += healthSc >= 70 ? 8 : healthSc < 45 ? -8 : 0;
    if (Number.isFinite(bias20)) score += bias20 > 8 ? -12 : bias20 < -8 ? -4 : 3;
    if (Number.isFinite(pe) && pe > 45) score -= 6;
    const rgLight = String(rg.light || "").toUpperCase();
    if (rgLight === "RED") score -= 14;
    else if (rgLight === "YELLOW") score -= 6;
    else if (rgLight === "GREEN") score += 4;
    score += (bullSigs + bullKols - bearSigs - bearKols) * 3;
    score = Math.max(15, Math.min(92, Math.round(score)));

    let stance = "中性观望";
    if (chk.chase_alert === "overextended_long" || (Number.isFinite(bias20) && bias20 > 8)) stance = "追高警示";
    else if (score >= 65) stance = "偏多顺风";
    else if (score <= 42 || rgLight === "RED") stance = "偏空逆风";

    const bulls = [];
    if (Number.isFinite(upside) && upside > 0) bulls.push("华尔街目标均价隐含上行空间 " + formatSignedPct(upside));
    if (Number.isFinite(revG) && revG > 0) bulls.push("最新营收同比增速 " + formatSignedPct(revG));
    if (Number.isFinite(earnG) && earnG > 0) bulls.push("净利润同比保持扩张 " + formatSignedPct(earnG));
    if (Number.isFinite(healthSc) && healthSc >= 60) bulls.push("财务健康综合评分 " + healthSc + " 分，基本面支撑稳健");
    if (bullSigs + bullKols > bearSigs + bearKols) bulls.push("站内关联快讯与观点偏多共振 (" + (bullSigs + bullKols) + " 条)");
    if (!bulls.length && !isBlank(ov.analyst_rating)) bulls.push("机构一致评级为 " + ov.analyst_rating);
    if (!bulls.length) bulls.push("持续跟踪近期财报与核心催化兑现节奏");

    const bears = [];
    if (Number.isFinite(bias20) && bias20 > 8) bears.push("MA20 乖离率偏高 (" + formatSignedPct(bias20) + ")，短线存在获利回吐压力");
    else if (Number.isFinite(bias20) && bias20 < -8) bears.push("MA20 负向偏离较大 (" + formatSignedPct(bias20) + ")，需防趋势延续下探");
    if (Number.isFinite(pe) && pe > 40) bears.push("动态市盈率 P/E " + pe.toFixed(1) + " 倍，高估值对利率与业绩更敏感");
    if (rgLight === "RED" || rgLight === "YELLOW") bears.push("当前全局风控为" + (RISK_LIGHT_TEXT[rgLight] || "控险") + "，单标的仓位应从严控制");
    if (bearSigs + bearKols > 0) bears.push("站内存在 " + (bearSigs + bearKols) + " 条偏空提醒，留意节奏分化");
    if (!bears.length) bears.push("关注下一次财报与宏观利率路径切换带来的波动");

    const actionHint = stance === "追高警示"
      ? "短线乖离处于追高区间，暂缓追涨，等待回踩均线或分批低吸窗口。"
      : stance === "偏多顺风"
        ? "基本面与情绪形成支撑，可依风控上限分批关注，设好止损边界。"
        : stance === "偏空逆风"
          ? "宏观门控与风险因素偏多，优先防御并收缩新开多头仓位。"
          : "多空信号仍在拉锯，维持观察并紧盯均线结构与基本面催化。";

    return {
      stance,
      score,
      bull_points: bulls.slice(0, 3),
      bear_points: bears.slice(0, 3),
      action_hint: actionHint
    };
  }

  function buildQuickThesisCard(p, relSignals, relKols) {
    const raw = p && p.quick_thesis && typeof p.quick_thesis === "object" && !isBlank(p.quick_thesis.stance)
      ? p.quick_thesis
      : computeQuickThesisFallback(p || {}, relSignals, relKols);
    const stance = String(raw.stance || "中性观望").trim();
    const score = toNum(raw.score);
    const stanceCls = stance === "偏多顺风" ? "badge-bull" : (stance === "偏空逆风" || stance === "追高警示") ? "badge-bear" : "badge-warn";
    const bulls = arr(raw.bull_points).filter((x) => !isBlank(x)).slice(0, 3);
    const bears = arr(raw.bear_points).filter((x) => !isBlank(x)).slice(0, 3);
    const actionHint = String(raw.action_hint || "").trim();
    const deepFile = findDeepReportFile(p);
    const deepArt = deepFile ? (arr(state().articles).find((a) => a.filename === deepFile) || null) : null;
    const deepStamp = deepArt && !isBlank(deepArt.mtime) ? "更新于 " + escapeHtml(deepArt.mtime) : "";

    const deepBar = deepFile
      ? '<div class="qt-deep-bar"><button type="button" class="btn btn-sm btn-accent" data-open-article="' + escapeHtml(deepFile) + '">查阅多智能体深研纪要</button>' +
        (deepStamp ? '<span class="panel-meta num-plain">' + deepStamp + "</span>" : "") + "</div>"
      : '<div class="qt-deep-bar"><span class="panel-meta">暂无专项深研纪要，可在桌面端运行「多智能体深研」并同步至网页。</span></div>';

    return '<section class="panel quick-thesis-card">' +
      '<div class="panel-head" style="margin-bottom:10px">' +
      '<h3 class="sec-title">多空速判</h3>' +
      '<div class="qt-head-badges"><span class="badge ' + stanceCls + '">' + escapeHtml(stance) + "</span>" +
      (Number.isFinite(score) ? '<span class="qt-score num-plain">' + Math.round(score) + " 分</span>" : "") + "</div>" +
      "</div>" +
      '<div class="qt-cols">' +
      '<div class="qt-col qt-bull"><div class="qt-col-title">利多支撑</div><ul>' + bulls.map((b) => "<li>" + escapeHtml(stripEmojis(b)) + "</li>").join("") + "</ul></div>" +
      '<div class="qt-col qt-bear"><div class="qt-col-title">风险与纪律</div><ul>' + bears.map((b) => "<li>" + escapeHtml(stripEmojis(b)) + "</li>").join("") + "</ul></div>" +
      "</div>" +
      (actionHint ? '<p class="qt-action"><b>操作提示</b>' + escapeHtml(stripEmojis(actionHint)) + "</p>" : "") +
      deepBar +
      "</section>";
  }

  function buildDisciplineCard(p) {
    const chk = p.pre_trade_checklist || {};
    const closes = arr((p.chart || {}).closes).map(toNum).filter(Number.isFinite);
    const px = toNum(p.price);
    const avg = (n) => closes.length >= n ? closes.slice(-n).reduce((a, b) => a + b, 0) / n : NaN;
    const ma20 = Number.isFinite(toNum(chk.ma20 ?? p.ma20)) ? toNum(chk.ma20 ?? p.ma20) : avg(20);
    const ma50 = Number.isFinite(toNum(chk.ma50 ?? p.ma50)) ? toNum(chk.ma50 ?? p.ma50) : avg(50);
    const bias20 = Number.isFinite(toNum(chk.bias20_pct ?? p.bias20_pct)) ? toNum(chk.bias20_pct ?? p.bias20_pct) : (Number.isFinite(ma20) && ma20 > 0 ? ((px - ma20) / ma20) * 100 : NaN);
    const bias50 = Number.isFinite(toNum(chk.bias50_pct ?? p.bias50_pct)) ? toNum(chk.bias50_pct ?? p.bias50_pct) : (Number.isFinite(ma50) && ma50 > 0 ? ((px - ma50) / ma50) * 100 : NaN);
    const ov = p.overview || {};
    const lo52 = toNum(ov.low_52w), hi52 = toNum(ov.high_52w);
    const rawPos52 = toNum(chk.range_52w_pos ?? p.range_52w_pos);
    const pos52 = Number.isFinite(rawPos52)
      ? rawPos52
      : (Number.isFinite(lo52) && Number.isFinite(hi52) && hi52 > lo52 && Number.isFinite(px) ? ((px - lo52) / (hi52 - lo52)) * 100 : NaN);

    const rg = state().risk_gate || {};
    const isWarnAlert = chk.chase_alert === "overextended_long" || chk.chase_alert === "overextended_short" || chk.risk_gate_light === "RED";
    let alertHtml = "";
    if (!isBlank(chk.verdict)) {
      alertHtml = '<div class="' + (isWarnAlert ? "discipline-warn" : "discipline-ok") + '">' + escapeHtml(chk.verdict) + "</div>";
    } else if (!isBlank(chk.warning)) {
      alertHtml = '<div class="discipline-warn">' + escapeHtml(chk.warning) + "</div>";
    } else if (Number.isFinite(bias20) && bias20 > 8) {
      alertHtml = '<div class="discipline-warn">顺势追高警示：MA20 乖离偏高 (' + formatSignedPct(bias20) + ")，避免大幅追涨</div>";
    } else if (Number.isFinite(bias20) && bias20 < -8) {
      alertHtml = '<div class="discipline-warn">超跌波动警示：MA20 偏离 (' + formatSignedPct(bias20) + ")，谨防盲目追空或接飞刀</div>";
    } else {
      alertHtml = '<div class="discipline-ok">均线乖离处于常规区间 · 风控门控：' + escapeHtml(rg.label || "正常") + "</div>";
    }

    const checksRows = arr(chk.checks).slice(0, 4).map((c) =>
      '<div class="rel-item" style="margin-top:8px"><div class="rel-item-head"><span>' + escapeHtml(c.label || "") + '</span><span class="badge ' +
      (c.status === "pass" ? "badge-bull" : c.status === "block" ? "badge-bear" : "badge-warn") + '">' +
      (c.status === "pass" ? "通过" : c.status === "block" ? "拦截" : "提示") + '</span></div><div class="panel-meta">' + escapeHtml(c.detail || "") + "</div></div>"
    ).join("");

    return '<section class="panel discipline-card">' +
      '<h3 class="sec-title">开仓纪律检查</h3>' +
      '<div class="discipline-grid">' +
      '<div class="discipline-item"><span>MA20 乖离率</span><b class="' + toneClass(bias20) + '">' + escapeHtml(Number.isFinite(bias20) ? formatSignedPct(bias20) : "—") + "</b></div>" +
      '<div class="discipline-item"><span>MA50 乖离率</span><b class="' + toneClass(bias50) + '">' + escapeHtml(Number.isFinite(bias50) ? formatSignedPct(bias50) : "—") + "</b></div>" +
      '<div class="discipline-item"><span>52周区间位置</span><b>' + escapeHtml(Number.isFinite(pos52) ? pos52.toFixed(1) + "%" : "—") + "</b></div>" +
      '<div class="discipline-item"><span>量化门控状态</span><b>' + escapeHtml(RISK_LIGHT_TEXT[String(rg.light || "").toUpperCase()] || "正常") + "</b></div>" +
      "</div>" + alertHtml + checksRows + "</section>";
  }

  function hideStockSuggest() {
    if (dom.stockSuggest) {
      dom.stockSuggest.hidden = true;
      dom.stockSuggest.innerHTML = "";
    }
  }

  function renderStockSuggest(items) {
    if (!dom.stockSuggest) return;
    if (!items || !items.length) return hideStockSuggest();
    dom.stockSuggest.innerHTML = items.map((it) => {
      const m = it.market || "us";
      const lbl = RESEARCH_MARKET_LABELS[m] || m;
      return '<button type="button" class="stock-suggest-item" role="option" data-pick-stock="' + escapeHtml(it.symbol) + '">' +
        '<span class="ss-left"><span class="ss-sym">' + escapeHtml(it.symbol) + '</span><span class="ss-name">' + escapeHtml(it.name || it.symbol) + "</span></span>" +
        '<span class="badge">' + escapeHtml(lbl) + "</span></button>";
    }).join("");
    dom.stockSuggest.hidden = false;
  }

  function renderStockLower(p) {
    const ov = p.overview || {}, fin = p.financials || {}, val = p.valuation || {};
    const mkt = assetMarket(p);
    const isCrypto = mkt === "crypto";
    const curSym = assetCurrencySym(p);
    const cm = p.crypto_metrics || {};
    const ch = p.chart || {};

    if (dom.stockInsightsCol) {
      const tgt = toNum(ov.target_mean_price), px = toNum(p.price);
      const upside = (Number.isFinite(tgt) && Number.isFinite(px) && px > 0) ? ((tgt - px) / px) * 100 : null;
      const sym = String(p.symbol || "").toUpperCase(), symShort = sym.split(".")[0], cn = String(p.name_cn || "");
      const relSignals = arr(state().signals).filter((s) => {
        const tks = arr(s.tickers).map((x) => String(x).toUpperCase());
        const txt = String(s.title || "") + " " + String(s.summary || "");
        const up = txt.toUpperCase();
        return tks.indexOf(sym) !== -1 || tks.indexOf(symShort) !== -1 || up.indexOf(sym) !== -1 || (symShort.length >= 3 && up.indexOf(symShort) !== -1) || (cn && txt.indexOf(cn) !== -1);
      }).slice(0, 5);
      const relKols = arr(state().kol_digest).map((k) => {
        const kTickers = arr(k.tickers).map((x) => String(x || "").trim().toUpperCase());
        const bestTickers = arr(k.best_tickers).map((x) => String(x || "").trim().toUpperCase());
        const tickerHit = kTickers.includes(sym) || (symShort.length >= 2 && kTickers.includes(symShort));
        const bestHit = bestTickers.includes(sym) || (symShort.length >= 2 && bestTickers.includes(symShort));
        const txt = [k.headline, k.takeaway, k.why_usable].join(" ");
        const up = txt.toUpperCase();
        const textHit = up.indexOf(sym) !== -1 || (symShort.length >= 3 && up.indexOf(symShort) !== -1) || (cn && txt.indexOf(cn) !== -1);
        const rank = (tickerHit ? 1000 : 0) + (bestHit ? 200 : 0) + (textHit ? 50 : 0) + (toNum(k.reliability_score) || 0);
        return { k, matched: tickerHit || textHit, rank };
      }).filter((x) => x.matched)
        .sort((a, b) => b.rank - a.rank)
        .slice(0, 4)
        .map((x) => x.k);
      const secFilings = arr(p.sec_filings).filter((f) => f && !isBlank(f.form));
      const secSection = (!isCrypto && secFilings.length)
        ? '<section class="panel sec-filings-panel">' +
          '<h3 class="sec-title">官方财报与监管披露</h3>' +
          '<div class="sec-filings-list">' +
          secFilings.map((f) => {
            const fUrl = safeHttpUrl(f.url);
            return '<div class="sec-filing-row">' +
              '<span class="badge badge-outline sec-form-tag num-plain">' + escapeHtml(f.form) + "</span>" +
              '<span class="sec-filing-label">' + escapeHtml(!isBlank(f.label_cn) ? f.label_cn : f.form) + "</span>" +
              (!isBlank(f.filing_date) ? '<span class="sec-filing-date num-plain">' + escapeHtml(f.filing_date) + "</span>" : "") +
              (fUrl ? '<a class="btn-link sec-filing-link" href="' + escapeHtml(fUrl) + '" target="_blank" rel="noopener noreferrer">查阅 ↗</a>' : "") +
              "</div>";
          }).join("") +
          "</div></section>"
        : "";

      const hasConsensus = !isCrypto && ((!isBlank(ov.analyst_rating) && ov.analyst_rating !== "—") || ov.analyst_count != null || Number.isFinite(tgt));
      const consensusSection = hasConsensus
        ? '<section class="panel"><h3 class="sec-title">机构一致预期</h3>' +
          '<div class="mini-kv-grid" style="margin-top:12px;border-top:0;padding-top:0">' +
          '<div class="mini-kv"><span>综合评级</span><b>' + escapeHtml(ov.analyst_rating || "—") + "</b></div>" +
          '<div class="mini-kv"><span>覆盖机构</span><b>' + escapeHtml(ov.analyst_count != null ? String(ov.analyst_count) + " 家" : "—") + "</b></div>" +
          '<div class="mini-kv"><span>目标均价</span><b>' + escapeHtml(Number.isFinite(tgt) ? formatAssetPrice(tgt, curSym) : "—") + "</b></div>" +
          '<div class="mini-kv"><span>隐含空间</span><b class="' + toneClass(upside) + '">' + escapeHtml(Number.isFinite(upside) ? formatSignedPct(upside) : "—") + "</b></div>" +
          "</div></section>"
        : "";

      dom.stockInsightsCol.innerHTML =
        buildQuickThesisCard(p, relSignals, relKols) +
        '<section class="panel"><h3 class="sec-title">' + (isCrypto ? "资产定位与驱动" : "核心业务与护城河") + "</h3>" +
        '<p class="overview-copy" style="margin-top:10px">' + escapeHtml(stripEmojis(p.business_overview_cn || "—")) + "</p></section>" +
        buildDisciplineCard(p) +
        consensusSection +
        secSection +
        '<section class="panel"><div class="panel-head"><h3 class="sec-title">站内关联快讯与观点</h3><button type="button" class="btn-link" data-jump="today">看全部日报</button></div>' +
        '<div class="rel-list">' +
        (relSignals.length || relKols.length
          ? relSignals.map((s) => '<div class="rel-item"><div class="rel-item-head"><span>' + escapeHtml(s.source) + '</span><span class="num-plain">' + escapeHtml(s.time_bjt || "") + '</span></div><div class="rel-item-title">' + escapeHtml(stripEmojis(s.title)) + "</div></div>").join("") +
            relKols.map((k) => {
              const handle = String(k.handle || "");
              const shownHandle = handle && !handle.startsWith("@") ? "@" + handle : handle;
              const authorStr = !isBlank(k.name) && String(k.name).toLowerCase() !== handle.replace(/^@/, "").toLowerCase()
                ? k.name + " " + shownHandle
                : shownHandle;
              const winStr = kolWinRateText(k);
              const badgeStr = !isBlank(k.reliability_badge) ? String(k.reliability_badge) : "";
              return '<div class="rel-item"><div class="rel-item-head"><span class="rel-kol-author">' + escapeHtml(authorStr) +
                (!isBlank(k.role_label) ? ' <span class="badge badge-outline">' + escapeHtml(k.role_label) + "</span>" : "") +
                (badgeStr ? ' <span class="badge ' + kolReliabilityTone(badgeStr) + '">' + escapeHtml(badgeStr) + "</span>" : "") +
                (winStr && winStr !== badgeStr ? ' <span class="num-plain rel-win">' + escapeHtml(winStr) + "</span>" : "") +
                '</span><span class="badge badge-' + (STANCE_CLS[k.stance] || "neutral") + '">' + escapeHtml(k.stance) + '</span></div>' +
                '<div class="rel-item-title">' + escapeHtml(stripEmojis(k.headline)) + "</div>" +
                (!isBlank(k.why_usable) ? '<div class="panel-meta" style="margin-top:4px">' + escapeHtml(stripEmojis(k.why_usable)) + "</div>" : "") +
                "</div>";
            }).join("")
          : '<div class="empty-hint">今日快讯库暂无直接提及 ' + escapeHtml(sym) + " 的增量信号</div>") +
        "</div></section>";
    }

    if (dom.stockSectionsCol) {
      const mCard = (lbl, valStr, cls) =>
        '<div class="metric-card"><div class="metric-lbl">' + escapeHtml(lbl) + '</div><div class="metric-val' + (cls ? " " + cls : "") + '">' + escapeHtml(valStr || "—") + "</div></div>";
      const allMap = allStockProfiles();

      if (isCrypto) {
        const cPeers = Object.keys(allMap).map((k) => allMap[k]).filter((x) => assetMarket(x) === "crypto").slice(0, 8);
        const cPeerTable = cPeers.length
          ? '<h4 class="subsec-title">加密核心资产对比（点击切换）</h4><div class="table-scroll"><table class="fin-table sticky-table"><thead><tr><th>资产</th><th>名称</th><th>最新价</th><th>24h涨跌</th><th>30日波动</th><th>90日回撤</th><th>24h成交额</th></tr></thead><tbody>' +
            cPeers.map((pr) => {
              const prCm = pr.crypto_metrics || {};
              const cChg = toNum(pr.change_pct);
              return '<tr class="' + (pr.symbol === p.symbol ? "is-current" : "") + '" data-open-stock="' + escapeHtml(pr.symbol) + '">' +
                "<td>" + escapeHtml(pr.symbol) + "</td><td>" + escapeHtml(pr.name_cn) + "</td><td>" + escapeHtml(formatAssetPrice(pr.price, assetCurrencySym(pr))) + '</td><td class="' + toneClass(cChg) + '">' + escapeHtml(Number.isFinite(cChg) ? formatSignedPct(cChg) : "—") + "</td><td>" + escapeHtml(prCm.vol_30d_ann_pct != null ? prCm.vol_30d_ann_pct + "%" : "—") + "</td><td>" + escapeHtml(prCm.max_drawdown_90d_pct != null ? prCm.max_drawdown_90d_pct + "%" : "—") + "</td><td>" + escapeHtml(prCm.turnover_24h_fmt || "—") + "</td></tr>";
            }).join("") +
            "</tbody></table></div>"
          : "";
        dom.stockSectionsCol.innerHTML =
          '<section class="panel"><h3 class="sec-title">盘面与流动性概览</h3>' +
          '<div class="metric-grid-3" style="margin-top:14px">' +
          mCard("24h 成交额", cm.turnover_24h_fmt || ov.turnover_fmt) +
          mCard("24h 成交量", cm.base_vol_24h_fmt || ov.volume_fmt) +
          mCard("30日量比", cm.vol_ratio_30d != null ? cm.vol_ratio_30d + "x" : "—") +
          mCard("24h 最高", cm.high_24h != null ? formatAssetPrice(cm.high_24h, curSym) : "—") +
          mCard("24h 最低", cm.low_24h != null ? formatAssetPrice(cm.low_24h, curSym) : "—") +
          mCard("365日高点", ov.high_52w != null ? formatAssetPrice(ov.high_52w, curSym) : "—") +
          "</div></section>" +
          '<section class="panel"><h3 class="sec-title">波动率与周期表现</h3>' +
          '<div class="metric-grid-3" style="margin-top:14px">' +
          mCard("30日年化波动", cm.vol_30d_ann_pct != null ? cm.vol_30d_ann_pct + "%" : "—") +
          mCard("90日最大回撤", cm.max_drawdown_90d_pct != null ? cm.max_drawdown_90d_pct + "%" : "—", toneClass(cm.max_drawdown_90d_pct)) +
          mCard("MA200 均线", cm.ma200 != null ? formatAssetPrice(cm.ma200, curSym) : "—") +
          mCard("近7日收益", ch.return_1w != null ? formatSignedPct(ch.return_1w) : "—", toneClass(ch.return_1w)) +
          mCard("近30日收益", ch.return_1m != null ? formatSignedPct(ch.return_1m) : "—", toneClass(ch.return_1m)) +
          mCard("近1年收益", ch.return_1y != null ? formatSignedPct(ch.return_1y) : "—", toneClass(ch.return_1y)) +
          "</div></section>" +
          '<section class="panel"><h3 class="sec-title">同板块资产对照</h3>' +
          cPeerTable + "</section>";
        return;
      }

      const quarters = arr(fin.quarters);
      const qColsAll = [
        { key: "revenue_fmt", label: "营收" },
        { key: "gross_profit_fmt", label: "毛利润" },
        { key: "op_income_fmt", label: "营业利润" },
        { key: "net_income_fmt", label: "净利润" },
        { key: "fcf_fmt", label: "现金流" }
      ];
      const qCols = qColsAll.filter((col) => quarters.some((q) => {
        const v = q && q[col.key];
        return v && v !== "—" && v !== "$0.00" && v !== "¥0.00" && v !== "HK$0.00";
      }));
      const qTable = quarters.length && qCols.length
        ? '<h4 class="subsec-title">近期财报简表</h4><div class="table-scroll"><table class="fin-table sticky-table"><thead><tr><th>报告期</th>' +
          qCols.map((col) => "<th>" + escapeHtml(col.label) + "</th>").join("") +
          "</tr></thead><tbody>" +
          quarters.map((q) => "<tr><td>" + escapeHtml(q.period) + "</td>" + qCols.map((col) => "<td>" + escapeHtml(q[col.key] || "—") + "</td>").join("") + "</tr>").join("") +
          "</tbody></table></div>"
        : "";

      let peers = Object.keys(allMap).map((k) => allMap[k]).filter((x) => x.peer_group === p.peer_group && assetMarket(x) === mkt).slice(0, 6);
      if (peers.length <= 1) {
        peers = Object.keys(allMap).map((k) => allMap[k]).filter((x) => assetMarket(x) === mkt).slice(0, 6);
      }
      const peerTable = peers.length
        ? '<h4 class="subsec-title">同市场对比（点击切换标的）</h4><div class="table-scroll"><table class="fin-table sticky-table"><thead><tr><th>标的</th><th>名称</th><th>最新价</th><th>P/E</th><th>P/B</th><th>ROE</th><th>毛利率</th></tr></thead><tbody>' +
          peers.map((pr) =>
            '<tr class="' + (pr.symbol === p.symbol ? "is-current" : "") + '" data-open-stock="' + escapeHtml(pr.symbol) + '">' +
            "<td>" + escapeHtml(pr.symbol) + "</td><td>" + escapeHtml(pr.name_cn) + "</td><td>" + escapeHtml(formatAssetPrice(pr.price, assetCurrencySym(pr))) + "</td><td>" + escapeHtml(fmtMetricVal((pr.valuation || {}).pe_ttm, 1)) + "</td><td>" + escapeHtml(fmtMetricVal((pr.valuation || {}).pb, 1)) + "</td><td>" + escapeHtml((pr.financials || {}).roe || "—") + "</td><td>" + escapeHtml((pr.financials || {}).gross_margin || "—") + "</td></tr>"
          ).join("") +
          "</tbody></table></div>"
        : "";

      dom.stockSectionsCol.innerHTML =
        '<section class="panel"><h3 class="sec-title">公司概览</h3>' +
        '<div class="metric-grid-3" style="margin-top:14px">' +
        mCard("总市值", ov.market_cap_fmt) + mCard("市盈率 P/E", fmtMetricVal(ov.pe_ttm, 2)) + mCard("远期 P/E", fmtMetricVal(ov.pe_forward, 2)) +
        mCard("每股收益 EPS", ov.eps_ttm != null ? curSym + fmtMetricVal(ov.eps_ttm, 2) : "—") + mCard("股息率", ov.dividend_yield_fmt) + mCard("成交量", ov.volume_fmt) +
        mCard("成交额", ov.turnover_fmt) + mCard("Beta 系数", fmtMetricVal(ov.beta, 2)) + mCard("机构持股占比", ov.institution_pct) +
        "</div></section>" +
        '<section class="panel"><h3 class="sec-title">财务体检</h3>' +
        '<div class="metric-grid-3" style="margin-top:14px">' +
        mCard("营收规模", fin.ttm_revenue_fmt) + mCard("EBITDA TTM", fin.ttm_ebitda_fmt) + mCard("经营/自由现金流", fin.ttm_fcf_fmt !== "—" ? fin.ttm_fcf_fmt : fin.ttm_ocf_fmt) +
        mCard("毛利率", fin.gross_margin) + mCard("营业利润率", fin.operating_margin) + mCard("净利率", fin.net_margin) +
        mCard("净资产收益率 ROE", fin.roe) + mCard("总资产收益率 ROA", fin.roa) + mCard("流动比率", fin.current_ratio) +
        mCard("现金储备", fin.total_cash_fmt) + mCard("总负债", fin.total_debt_fmt) + mCard("负债/权益", fin.debt_to_equity && fin.debt_to_equity !== "—" ? fin.debt_to_equity + "%" : "—") +
        "</div>" + qTable + "</section>" +
        '<section class="panel"><h3 class="sec-title">估值判断与同行对比</h3>' +
        '<div class="metric-grid-3" style="margin-top:14px">' +
        mCard("市盈率 P/E", fmtMetricVal(val.pe_ttm, 2)) + mCard("远期 P/E", fmtMetricVal(val.pe_forward, 2)) + mCard("市销率 P/S", fmtMetricVal(val.ps_ttm, 2)) +
        mCard("市净率 P/B", fmtMetricVal(val.pb, 2)) + mCard("EV / EBITDA", fmtMetricVal(val.ev_ebitda, 2)) + mCard("PEG", fmtMetricVal(val.peg, 2)) +
        "</div>" + peerTable + "</section>";
    }
  }

  function renderResearch() {
    renderStockChips();
    const p = getActiveStock();
    renderStockBanner(p);
    if (!p) return;
    renderStockChart(p);
    renderStockSummary(p);
    renderStockLower(p);
  }

  async function openStockResearch(sym, switchToView) {
    let raw = String(sym || "").trim().replace(/^([A-Za-z0-9]+)\/([A-Za-z0-9]+)$/, "$1-$2");
    if (!raw) return;
    hideStockSuggest();
    if (switchToView) setView("research", { keepScroll: false });
    const up = raw.toUpperCase();
    const existing = allStockProfiles()[up];
    if (existing) {
      appState.stockSymbol = up;
      const m = assetMarket(existing);
      if (appState.stockMarket !== "all" && appState.stockMarket !== m) {
        appState.stockMarket = m;
      }
      renderResearch();
      syncUrlHash();
      return;
    }
    if (!appState.offline) {
      try {
        showToast("正在检索 " + raw + " 投研数据…");
        const prof = await apiJson("/api/stock?symbol=" + encodeURIComponent(raw));
        if (prof && prof.symbol) {
          appState.customStocks[prof.symbol] = prof;
          appState.stockSymbol = prof.symbol;
          const m = assetMarket(prof);
          if (appState.stockMarket !== "all" && appState.stockMarket !== m) {
            appState.stockMarket = m;
          }
          if (dom.stockSearchInput) dom.stockSearchInput.value = "";
        }
      } catch (err) {
        showToast(err.message || "未找到该标的投研数据", "error");
      }
    } else {
      showToast("离线快照仅支持浏览预设标的", "error");
    }
    renderResearch();
    syncUrlHash();
  }

  function commitStockSearch() {
    const raw = (dom.stockSearchInput ? dom.stockSearchInput.value : "").trim();
    if (!raw) return;
    openStockResearch(raw, false);
  }

  /* ---------- 宏观与关联 ---------- */

  function synthToneBadge(tone) {
    if (tone === "bull") return "badge-bull";
    if (tone === "bear") return "badge-bear";
    if (tone === "warn") return "badge-warn";
    return "badge-outline";
  }

  function renderMarketSynthesis() {
    if (!dom.marketSynthesisPanel) return;
    const ma = state().market_analysis;
    if (!ma || typeof ma !== "object") {
      dom.marketSynthesisPanel.hidden = true;
      return;
    }
    const dims = arr(ma.market_dimensions);
    const links = arr(ma.cross_source_links);
    const alerts = arr(ma.divergence_alerts);
    const hasVerdict = !isBlank(ma.headline_verdict);
    if (!hasVerdict && !dims.length && !links.length && !alerts.length) {
      dom.marketSynthesisPanel.hidden = true;
      return;
    }
    dom.marketSynthesisPanel.hidden = false;

    if (dom.marketStanceBadge) {
      const st = String(ma.stance || "").trim();
      const sCls = STANCE_CLS[st] || "neutral";
      dom.marketStanceBadge.innerHTML = st
        ? '<span class="badge badge-' + sCls + '">' + escapeHtml(st) + "</span>"
        : "";
    }
    if (dom.marketAsOf) {
      dom.marketAsOf.textContent = !isBlank(ma.as_of) ? String(ma.as_of) : "";
    }
    if (dom.marketVerdict) {
      dom.marketVerdict.hidden = !hasVerdict;
      dom.marketVerdict.textContent = hasVerdict ? String(ma.headline_verdict) : "";
    }
    if (dom.marketDimensions) {
      dom.marketDimensions.hidden = !dims.length;
      dom.marketDimensions.innerHTML = dims.map((d) => {
        const tone = String(d.tone || "neutral");
        const bCls = synthToneBadge(tone);
        const cardTone = (tone === "bull" || tone === "bear" || tone === "warn") ? " tone-" + tone : "";
        return '<article class="market-dim-card' + cardTone + '">' +
          '<div class="mdim-head"><span class="mdim-name">' + escapeHtml(d.name || "") + "</span>" +
          (!isBlank(d.status) ? '<span class="badge ' + bCls + '">' + escapeHtml(d.status) + "</span>" : "") + "</div>" +
          (!isBlank(d.metric) ? '<div class="mdim-metric num-plain">' + escapeHtml(d.metric) + "</div>" : "") +
          (!isBlank(d.implication) ? '<p class="mdim-imp">' + escapeHtml(d.implication) + "</p>" : "") +
          "</article>";
      }).join("");
    }
    if (dom.marketDivergenceAlerts) {
      if (alerts.length) {
        dom.marketDivergenceAlerts.hidden = false;
        dom.marketDivergenceAlerts.innerHTML = alerts.map((al) => {
          const tks = arr(al.tickers).slice(0, 4).map(tickerPillHtml).join("");
          return '<div class="synth-alert-item tone-' + escapeHtml(al.tone || "warn") + '">' +
            '<div class="synth-alert-head"><b>' + escapeHtml(al.title || "异常联动") + "</b>" +
            (tks ? '<span class="synth-alert-tickers">' + tks + "</span>" : "") + "</div>" +
            (!isBlank(al.detail) ? '<p class="synth-alert-detail">' + escapeHtml(al.detail) + "</p>" : "") +
            "</div>";
        }).join("");
      } else {
        dom.marketDivergenceAlerts.hidden = true;
        dom.marketDivergenceAlerts.innerHTML = "";
      }
    }
    if (dom.crossSourceHead) dom.crossSourceHead.hidden = !links.length;
    if (dom.crossSourceCount) dom.crossSourceCount.textContent = links.length ? links.length + " 组联动" : "";
    if (dom.crossSourceLinks) {
      dom.crossSourceLinks.hidden = !links.length;
      dom.crossSourceLinks.innerHTML = links.map((lk) => {
        const tone = String(lk.tone || "neutral");
        const conf = toNum(lk.confidence);
        const lanes = [
          { label: "快讯", items: arr(lk.news_evidence).filter((x) => !isBlank(x)) },
          { label: "高胜率KOL", items: arr(lk.kol_evidence).filter((x) => !isBlank(x)) },
          { label: "大佬长文", items: arr(lk.guru_evidence).filter((x) => !isBlank(x)) }
        ].filter((ln) => ln.items.length > 0);
        const tks = arr(lk.tickers).slice(0, 5).map(tickerPillHtml).join("");
        const artFile = !isBlank(lk.related_article_file) ? String(lk.related_article_file) : "";
        return '<article class="xlink-card tone-' + escapeHtml(tone) + '">' +
          '<div class="xlink-head">' +
          '<h4 class="xlink-title">' + escapeHtml(lk.focus || "") + "</h4>" +
          '<div class="xlink-badges">' +
          (!isBlank(lk.pattern) ? '<span class="badge ' + synthToneBadge(tone) + '">' + escapeHtml(lk.pattern) + "</span>" : "") +
          (Number.isFinite(conf) ? '<span class="badge badge-outline num-plain">置信度 ' + Math.round(conf) + "%</span>" : "") +
          "</div></div>" +
          (!isBlank(lk.conclusion) ? '<p class="xlink-conclusion">' + escapeHtml(lk.conclusion) + "</p>" : "") +
          (lanes.length
            ? '<div class="xlink-lanes">' + lanes.map((ln) =>
                '<div class="xlink-lane"><span class="xlink-lane-lbl">' + escapeHtml(ln.label) + '</span><ul>' +
                ln.items.slice(0, 3).map((txt) => "<li>" + escapeHtml(txt) + "</li>").join("") +
                "</ul></div>"
              ).join("") + "</div>"
            : "") +
          ((!isBlank(lk.market_check) || !isBlank(lk.watch_trigger))
            ? '<div class="xlink-checks">' +
              (!isBlank(lk.market_check) ? '<div class="xlink-chk"><b>盘面检验</b><span>' + escapeHtml(lk.market_check) + "</span></div>" : "") +
              (!isBlank(lk.watch_trigger) ? '<div class="xlink-chk"><b>观察触发</b><span>' + escapeHtml(lk.watch_trigger) + "</span></div>" : "") +
              "</div>"
            : "") +
          ((tks || artFile)
            ? '<div class="xlink-foot">' +
              (tks ? '<div class="xlink-tickers">' + tks + "</div>" : "<span></span>") +
              (artFile ? '<button type="button" class="btn btn-sm" data-open-article="' + escapeHtml(artFile) + '">阅读关联长文</button>' : "") +
              "</div>"
            : "") +
          "</article>";
      }).join("");
    }
  }

  function forumStatusCls(status) {
    const s = String(status || "").trim();
    if (s === "实时在线" || s === "免登直采") return "online";
    if (s === "部分受限") return "limited";
    return "auth";
  }

  function forumSnapshotContext(d) {
    const legacy = {
      "热度偏高": "关注较高",
      "当期关注较高": "关注较高",
      "散户过热": "看多分歧",
      "当期看多分歧": "看多分歧",
      "出圈见顶": "关注较高",
      "恐慌见底": "看空分歧",
      "当期看空分歧": "看空分歧",
      "机构抢跑": "专业偏强",
      "专业倾向较强": "专业偏强",
      "仅社区样本": "仅社区帖"
    };
    const tag = String((d && d.divergence_tag) || "");
    return legacy[tag] ? Object.assign({}, d, {
      divergence_tag: legacy[tag], tone: "neutral", action_hint: "仅作当期截面参考。"
    }) : d;
  }

  const DISCUSSION_ASSET_LABELS = {
    ASHARE_HK: "A股与港股",
    US_AI: "美股AI与科技",
    GOLD: "黄金与贵金属",
    BTC: "比特币与加密",
    FED_MACRO: "美联储与宏观"
  };
  const DISCUSSION_STATES = { warming: "持续升温", cooling: "关注回落", stable: "趋势平稳", high: "持续高热" };
  const DISCUSSION_PLATFORM_LABELS = {
    weibo: "微博", baidu: "百度", toutiao: "头条", douyin: "抖音", zhihu: "知乎", "bilibili-hot-search": "B站"
  };

  function currentDiscussionTrends(fs) {
    const trends = fs && fs.discussion_trends;
    if (!trends || trends.method !== "paired-coverage-v3" ||
        !/^\d{4}-\d{2}-\d{2}$/.test(String(trends.as_of || ""))) return [];
    const todayBjt = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
    const age = Date.parse(todayBjt) - Date.parse(trends.as_of);
    if (!Number.isFinite(age) || age < 0 || age > 86400000) return [];
    return arr(trends.assets).filter((a) => a && Object.hasOwn(DISCUSSION_ASSET_LABELS, a.asset));
  }

  function discussionWindows(a) {
    return arr(a.windows).filter((w) => w && [7, 30, 90].includes(w.days) &&
      Object.hasOwn(DISCUSSION_STATES, w.state) && w.platforms >= 2 &&
      w.sample_days >= Math.ceil(Math.floor(w.days / 2) * .6) + Math.ceil(Math.ceil(w.days / 2) * .6));
  }

  function renderDiscussionTrendHtml(trends) {
    const activeAssets = trends.filter((a) => discussionWindows(a).length);
    if (!activeAssets.length) return "";
    const rows = activeAssets.map((a) => {
      const labels = discussionWindows(a).map((w) => {
        const copy = w.days + "天 " + DISCUSSION_STATES[w.state] + " · " + w.platforms + "平台/" + w.sample_days + "天";
        const platforms = arr(w.sources).filter((s) => s && Object.hasOwn(DISCUSSION_PLATFORM_LABELS, s.platform) &&
          Object.hasOwn(DISCUSSION_STATES, s.state)).map((s) => DISCUSSION_PLATFORM_LABELS[s.platform] + " " +
            DISCUSSION_STATES[s.state] + " " + s.sample_days + "天").join(" · ");
        return '<details class="trend-win-detail"><summary class="badge ' + (w.state === "warming" ? "badge-warn" : w.state === "cooling" ? "badge-bull" : "badge-outline") + '">' +
          escapeHtml(copy) + "</summary>" + (platforms ? '<span class="panel-meta trend-win-platforms">' + escapeHtml(platforms) + "</span>" : "") + "</details>";
      }).join("");
      return '<div class="forum-sample-row trend-asset-row"><b class="forum-sample-author">' +
        escapeHtml(DISCUSSION_ASSET_LABELS[a.asset]) + '</b><div class="synth-head-left">' +
        labels + "</div></div>";
    }).join("");
    return '<div class="forum-hot-wrap forum-trend-wrap"><div class="forum-hot-head"><h3 class="subsec-title">多日讨论趋势</h3></div><div class="forum-trend-list">' + rows + "</div></div>";
  }

  function forumBarMetrics(score, opts) {
    const raw = toNum(score);
    const val = Number.isFinite(raw) ? Math.max(-100, Math.min(100, raw)) : 0;
    const half = Math.abs(val) / 2;
    const left = val >= 0 ? 50 : 50 - half;
    const isWarn = opts && opts.warn;
    const fillCls = isWarn ? "is-warn" : (val > 5 ? "is-bull" : val < -5 ? "is-bear" : "is-neutral");
    const signed = Number.isFinite(raw) ? (val > 0 ? "+" : "") + Math.round(val) : "—";
    return { left, width: Math.max(half, 1.5), fillCls, signed };
  }

  function renderForumMacroPillsHtml(mp) {
    if (!mp || typeof mp !== "object") return "";
    const pills = [];
    if (!isBlank(mp.a_turnover_display)) {
      const shPct = toNum(mp.sh_pct);
      const sub = Number.isFinite(shPct) ? ("上证 " + (shPct >= 0 ? "+" : "") + shPct.toFixed(1) + "%") : "";
      const subCls = Number.isFinite(shPct) ? (shPct >= 0 ? "is-bull" : "is-bear") : "";
      pills.push(
        '<div class="fms-pill">' +
        '<span class="fms-lbl">沪深成交</span>' +
        '<span class="fms-val num-plain">' + escapeHtml(String(mp.a_turnover_display)) + "</span>" +
        (sub ? '<span class="fms-sub num-plain ' + subCls + '">' + escapeHtml(sub) + "</span>" : "") +
        "</div>"
      );
    }
    if (!isBlank(mp.rzrq_balance_display)) {
      const rzNet = toNum(mp.rzrq_net_buy_yi);
      const rzBuy = toNum(mp.rzrq_buy_yi);
      const sub = Number.isFinite(rzNet)
        ? ("净" + (rzNet >= 0 ? "+" : "") + Math.round(rzNet) + "亿")
        : (Number.isFinite(rzBuy) ? ("买" + Math.round(rzBuy) + "亿") : "");
      const subCls = Number.isFinite(rzNet) ? (rzNet >= 0 ? "is-bull" : "is-bear") : "";
      pills.push(
        '<div class="fms-pill">' +
        '<span class="fms-lbl">两融余额</span>' +
        '<span class="fms-val num-plain">' + escapeHtml(String(mp.rzrq_balance_display)) + "</span>" +
        (sub ? '<span class="fms-sub num-plain ' + subCls + '">' + escapeHtml(sub) + "</span>" : "") +
        "</div>"
      );
    }
    const sb = toNum(mp.southbound_net_yi_hkd);
    if (Number.isFinite(sb)) {
      const sbCls = sb > 5 ? "is-bull" : sb < -5 ? "is-bear" : "";
      const sb5d = toNum(mp.southbound_5d_net_yi_hkd);
      const sub = Number.isFinite(sb5d) ? ("5日" + (sb5d >= 0 ? "+" : "") + sb5d.toFixed(0) + "亿") : "";
      pills.push(
        '<div class="fms-pill">' +
        '<span class="fms-lbl">南向资金</span>' +
        '<span class="fms-val num-plain ' + sbCls + '">' + escapeHtml((sb >= 0 ? "+" : "") + sb.toFixed(1) + "亿港元") + "</span>" +
        (sub ? '<span class="fms-sub num-plain">' + escapeHtml(sub) + "</span>" : "") +
        "</div>"
      );
    }
    const massPct = toNum(mp.mass_penetration_pct);
    const totalSlots = toNum(mp.mass_total_slots);
    const finHits = toNum(mp.mass_finance_hits);
    const ahkHits = toNum(mp.mass_ahk_hits);
    if (Number.isFinite(massPct) && Number.isFinite(totalSlots) && totalSlots > 0) {
      const isHot = massPct >= 8.0 || (Number.isFinite(ahkHits) && ahkHits >= 5);
      const sub = Number.isFinite(finHits) ? (Math.round(finHits) + "/" + Math.round(totalSlots) + "条") : "";
      pills.push(
        '<div class="fms-pill' + (isHot ? " is-warn" : "") + '" title="微博、百度、头条、抖音、知乎、B站总热榜中的财经破圈占比">' +
        '<span class="fms-lbl">大众热榜占榜</span>' +
        '<span class="fms-val num-plain">' + escapeHtml(massPct.toFixed(1) + "%") + "</span>" +
        (sub ? '<span class="fms-sub num-plain">' + escapeHtml(sub) + "</span>" : "") +
        "</div>"
      );
    }
    return pills.join("");
  }

  function renderForumSentiment() {
    if (!dom.forumSentimentPanel) return;
    const fs = state().forum_sentiment;
    if (!fs || typeof fs !== "object") {
      dom.forumSentimentPanel.hidden = true;
      return;
    }
    const channels = arr(fs.channels_status);
    const divs = arr(fs.divergences).filter(Boolean).map(forumSnapshotContext);
    const hots = arr(fs.hot_discussions);
    const trends = currentDiscussionTrends(fs);
    if (!channels.length && !divs.length && !trends.length) {
      dom.forumSentimentPanel.hidden = true;
      return;
    }
    dom.forumSentimentPanel.hidden = false;

    if (dom.forumContrarianBadge) {
      const warming = trends.filter((a) => discussionWindows(a).some((w) => w.state === "warming"));
      dom.forumContrarianBadge.innerHTML = warming.length
        ? '<span class="badge badge-warn">持续升温 · ' + escapeHtml(warming.map((a) => DISCUSSION_ASSET_LABELS[a.asset]).slice(0, 2).join(" / ")) + "</span>"
        : '<span class="panel-meta">当期讨论与专业观点对照</span>';
    }

    if (dom.forumOverallBadge) {
      const temp = toNum(fs.overall_crowd_temp);
      const lbl = String(fs.overall_label || "").trim();
      if (Number.isFinite(temp)) {
        const parts = ["当期社区情绪 " + Math.round(temp)];
        if (lbl) parts.push(lbl);
        dom.forumOverallBadge.innerHTML = '<span class="badge badge-accent num-plain">' + escapeHtml(parts.join(" · ")) + "</span>";
      } else {
        dom.forumOverallBadge.innerHTML = '<span class="badge badge-outline">' + escapeHtml(lbl || "暂无样本") + "</span>";
      }
    }

    if (dom.forumChannelsBar) {
      dom.forumChannelsBar.innerHTML = channels.map((ch) => {
        const name = String(ch.name || ch.id || "").trim();
        const st = String(ch.status || "").trim();
        const cnt = toNum(ch.count);
        const note = String(ch.note || "").trim();
        return '<span class="forum-ch-pill is-' + forumStatusCls(st) + '"' +
          (note ? ' title="' + escapeHtml(note) + '"' : "") + ">" +
          '<span class="forum-ch-dot" aria-hidden="true"></span>' +
          "<span>" + escapeHtml(name + (st ? " · " + st : "") + (Number.isFinite(cnt) && cnt > 0 ? " (" + Math.round(cnt) + ")" : "")) + "</span>" +
          "</span>";
      }).join("");
    }

    if (dom.forumMacroStrip) {
      const macroPills = renderForumMacroPillsHtml(fs.market_pulse);
      dom.forumMacroStrip.innerHTML = macroPills;
      dom.forumMacroStrip.hidden = !macroPills;
    }

    if (dom.forumDivergenceGrid) {
      dom.forumDivergenceGrid.innerHTML = divs.map((d) => {
        const tone = String(d.tone || "neutral");
        const dCnt = toNum(d.desk_count);
        const sCnt = toNum(d.street_count);
        const deskM = forumBarMetrics(d.desk_score, { warn: false });
        const streetHeat = toNum(d.street_heat);
        const streetScore = toNum(d.street_score);
        const hasStreetSample = Number.isFinite(streetHeat) && Number.isFinite(streetScore);
        const streetM = hasStreetSample
          ? forumBarMetrics(streetScore, { warn: streetHeat >= 70 || tone === "warn" })
          : { left: 50, width: 0, fillCls: "is-neutral", signed: "暂无样本" };
        const deskBarText = Number.isFinite(dCnt) && dCnt === 0
          ? "暂无样本"
          : ((d.desk_bias ? d.desk_bias + " " : "") + deskM.signed + (Number.isFinite(dCnt) && dCnt > 0 ? " · " + Math.round(dCnt) + "条" : ""));
        const streetBarText = hasStreetSample
          ? (streetM.signed + " · 热度" + Math.round(streetHeat) + (Number.isFinite(sCnt) && sCnt > 0 ? " · " + Math.round(sCnt) + "条" : ""))
          : "暂无样本";
        const deskSamples = arr(d.desk_samples).slice(0, 2);
        const streetSamples = arr(d.street_samples).slice(0, 2);
        return '<article class="forum-div-card tone-' + escapeHtml(tone) + '">' +
          '<div class="forum-div-head">' +
          '<h3 class="forum-div-topic">' + escapeHtml(d.topic || "") + "</h3>" +
          (!isBlank(d.divergence_tag) ? '<span class="badge ' + synthToneBadge(tone) + '">' + escapeHtml(d.divergence_tag) + "</span>" : "") +
          "</div>" +
          '<div class="forum-bars">' +
          '<div class="forum-bar-row">' +
          '<span class="forum-bar-lbl">专业层</span>' +
          '<div class="forum-bar-track" aria-hidden="true"><span class="forum-bar-mid"></span><i class="forum-bar-fill ' + deskM.fillCls + '" style="left:' + deskM.left + "%;width:" + deskM.width + '%"></i></div>' +
          '<span class="forum-bar-val num-plain">' + escapeHtml(deskBarText) + "</span>" +
          "</div>" +
          '<div class="forum-bar-row">' +
          '<span class="forum-bar-lbl">散户层</span>' +
          '<div class="forum-bar-track" aria-hidden="true"><span class="forum-bar-mid"></span><i class="forum-bar-fill ' + streetM.fillCls + '" style="left:' + streetM.left + "%;width:" + streetM.width + '%"></i></div>' +
          '<span class="forum-bar-val num-plain">' + escapeHtml(streetBarText) + "</span>" +
          "</div>" +
          "</div>" +
          (!isBlank(d.action_hint) ? '<p class="forum-div-hint">' + escapeHtml(d.action_hint) + "</p>" : "") +
          ((deskSamples.length || streetSamples.length)
            ? '<div class="forum-samples">' +
              deskSamples.map((s) => {
                const u = safeHttpUrl(s.url);
                const sumTxt = stripEmojis(s.summary || "");
                const zhBr = stripEmojis(s.zh_brief || "");
                const showDeskZh = Boolean(zhBr) && isMostlyEnglishText(sumTxt);
                return '<div class="forum-sample-row"><span class="badge badge-outline">专业</span>' +
                  (!isBlank(s.author) ? '<b class="forum-sample-author">' + escapeHtml(s.author) + "</b>" : "") +
                  '<span class="forum-sample-txt">' + escapeHtml(showDeskZh ? (zhBr + "（" + sumTxt + "）") : sumTxt) + "</span>" +
                  (u ? '<a class="forum-sample-link" href="' + escapeHtml(u) + '" target="_blank" rel="noopener noreferrer">原帖 ↗</a>' : "") +
                  "</div>";
              }).join("") +
              streetSamples.map((s) => {
                const u = safeHttpUrl(s.url);
                const cleanTitle = stripEmojis(s.title || "");
                const zhBr = stripEmojis(s.zh_brief || "");
                const showStreetZh = Boolean(zhBr) && isMostlyEnglishText(cleanTitle);
                return '<div class="forum-sample-row"><span class="badge badge-warn">' + escapeHtml(s.platform || "社区") + "</span>" +
                  '<div class="forum-sample-main">' +
                  (u
                    ? '<a class="forum-sample-txt forum-sample-title" href="' + escapeHtml(u) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(cleanTitle) + "\u00a0↗</a>"
                    : '<span class="forum-sample-txt">' + escapeHtml(cleanTitle) + "</span>") +
                  (showStreetZh ? '<span class="forum-zh-brief"><b class="memo-tag" title="投资备忘录">Memo</b>' + escapeHtml(zhBr) + "</span>" : "") +
                  "</div></div>";
              }).join("") +
              "</div>"
            : "") +
          "</article>";
      }).join("");
    }

    if (dom.forumDiscussionTrend) {
      const trendHtml = renderDiscussionTrendHtml(trends);
      dom.forumDiscussionTrend.innerHTML = trendHtml;
      dom.forumDiscussionTrend.hidden = !trendHtml;
    }

    if (dom.forumHotWrap && dom.forumHotList) {
      if (dom.forumUpdatedAt) dom.forumUpdatedAt.textContent = !isBlank(fs.updated_at_bjt) ? String(fs.updated_at_bjt) : "";
      if (hots.length) {
        dom.forumHotWrap.hidden = false;
        dom.forumHotList.innerHTML = hots.slice(0, 6).map((h) => {
          const u = safeHttpUrl(h.url);
          const sCls = STANCE_CLS[h.sentiment] || "neutral";
          const tks = arr(h.tickers).slice(0, 3).map(tickerPillHtml).filter(Boolean).join("");
          const heat = toNum(h.heat);
          const cleanTitle = stripEmojis(h.title || "");
          const zhBr = stripEmojis(h.zh_brief || "");
          const showHotZh = Boolean(zhBr) && isMostlyEnglishText(cleanTitle);
          return '<div class="forum-hot-item">' +
            '<div class="forum-hot-meta">' +
            (!isBlank(h.platform) ? '<span class="badge badge-outline">' + escapeHtml(h.platform) + "</span>" : "") +
            (!isBlank(h.sentiment) ? '<span class="badge badge-' + sCls + '">' + escapeHtml(h.sentiment) + "</span>" : "") +
            (Number.isFinite(heat) ? '<span class="num-plain forum-hot-heat">热度 ' + Math.round(heat) + "</span>" : "") +
            "</div>" +
            (u
              ? '<a class="forum-hot-title" href="' + escapeHtml(u) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(cleanTitle) + "\u00a0↗</a>"
              : '<span class="forum-hot-title">' + escapeHtml(cleanTitle) + "</span>") +
            (showHotZh ? '<p class="forum-zh-brief"><b class="memo-tag" title="投资备忘录">Memo</b>' + escapeHtml(zhBr) + "</p>" : "") +
            (tks ? '<div class="forum-hot-tickers">' + tks + "</div>" : "") +
            "</div>";
        }).join("");
      } else {
        dom.forumHotWrap.hidden = true;
        dom.forumHotList.innerHTML = "";
      }
    }
  }

  function renderTodaySentimentCard() {
    if (!dom.todaySentimentCard) return;
    const fs = state().forum_sentiment;
    if (!fs || typeof fs !== "object") {
      dom.todaySentimentCard.hidden = true;
      dom.todaySentimentCard.innerHTML = "";
      return;
    }
    const channels = arr(fs.channels_status);
    const divs = arr(fs.divergences).filter(Boolean).map(forumSnapshotContext);
    const hots = arr(fs.hot_discussions);
    const trends = currentDiscussionTrends(fs);
    if (!channels.length && !divs.length && !hots.length && !trends.length) {
      dom.todaySentimentCard.hidden = true;
      dom.todaySentimentCard.innerHTML = "";
      return;
    }
    dom.todaySentimentCard.hidden = false;

    const temp = toNum(fs.overall_crowd_temp);
    const lbl = String(fs.overall_label || "").trim();
    const tempBadge = Number.isFinite(temp)
      ? '<span class="badge badge-accent num-plain">当期情绪指数 ' + Math.round(temp) + (lbl ? " · " + escapeHtml(lbl) : "") + "</span>"
      : '<span class="badge badge-outline">' + escapeHtml(lbl || "暂无样本") + "</span>";

    const warming = trends.find((a) => discussionWindows(a).some((w) => w.state === "warming"));
    const contrarianHtml = warming
      ? '<span class="badge badge-warn">持续升温 · ' + escapeHtml(DISCUSSION_ASSET_LABELS[warming.asset]) + "</span>"
      : "";

    const rankedDivs = divs.slice().sort((a, b) => {
      const hasSample = (d) => d && Number.isFinite(toNum(d.street_heat)) && Number.isFinite(toNum(d.street_score));
      const rankOf = (d) => {
        if (!d) return 0;
        const base = hasSample(d) ? 200 : 0;
        if (d.divergence_tag && d.divergence_tag !== "情绪平稳" && d.divergence_tag !== "暂无样本") return base + 500;
        return base;
      };
      const scoreA = rankOf(a) + Math.abs(toNum(a.desk_score) || 0) + Math.abs(toNum(a.street_score) || 0);
      const scoreB = rankOf(b) + Math.abs(toNum(b.desk_score) || 0) + Math.abs(toNum(b.street_score) || 0);
      return scoreB - scoreA;
    }).slice(0, 4);

    const indexCardsHtml = rankedDivs.map((d) => {
      const tone = String(d.tone || "neutral");
      const deskVal = toNum(d.desk_score);
      const streetVal = toNum(d.street_score);
      const dCnt = toNum(d.desk_count);
      const hasStreetSample = Number.isFinite(streetVal) && Number.isFinite(toNum(d.street_heat));
      const fmtS = (v) => Number.isFinite(v) ? (v > 0 ? "+" : "") + Math.round(v) : "0";
      const deskText = Number.isFinite(dCnt) && dCnt === 0 ? "专业 暂无" : ("专业 " + fmtS(deskVal));
      const streetText = hasStreetSample ? ("散户 " + fmtS(streetVal)) : "散户 暂无";
      return '<button type="button" class="ts-index-card" data-jump="sentiment">' +
        '<div class="ts-index-head">' +
        '<span class="ts-index-topic">' + escapeHtml(d.topic || "") + "</span>" +
        '<span class="badge ' + synthToneBadge(tone) + '">' + escapeHtml(d.divergence_tag || "情绪平稳") + "</span>" +
        "</div>" +
        '<div class="ts-index-nums num-plain">' + escapeHtml(deskText + " · " + streetText) + "</div>" +
        "</button>";
    }).join("");

    const macroPills = renderForumMacroPillsHtml(fs.market_pulse);
    const macroStripHtml = macroPills ? '<div class="forum-macro-strip ts-macro-strip">' + macroPills + "</div>" : "";

    dom.todaySentimentCard.innerHTML =
      '<div class="panel-head ts-head">' +
      '<div class="synth-head-left">' +
      '<h2 class="sec-title">全网舆论风向</h2>' +
      tempBadge +
      contrarianHtml +
      "</div>" +
      '<div class="ts-head-right">' +
      '<button type="button" class="btn btn-sm btn-ghost" data-jump="sentiment">详细热度与原帖 →</button>' +
      "</div>" +
      "</div>" +
      macroStripHtml +
      (indexCardsHtml ? '<div class="ts-index-grid">' + indexCardsHtml + "</div>" : "");
  }

  function renderMacroGroups() {
    if (!dom.macroGroups) return;
    const items = validMacroItems();
    if (dom.macroCount) dom.macroCount.textContent = items.length + " 项实测指标";
    const order = [], grouped = {};
    items.forEach((m) => {
      const g = m.group || "宏观指标";
      if (!grouped[g]) { grouped[g] = []; order.push(g); }
      grouped[g].push(m);
    });
    dom.macroGroups.innerHTML = order.map((g) => '<section class="macro-group"><h3 class="macro-group-title">' + escapeHtml(g) + "</h3>" +
      grouped[g].map((m) => '<div class="macro-row"><div class="macro-row-main"><span class="macro-label">' + escapeHtml(m.label) + "</span>" +
        '<span class="macro-val num-plain">' + escapeHtml(m.value_display) + "</span>" +
        (!isBlank(m.change_display) ? '<span class="macro-chg num-plain ' + toneClass(m.change_num) + '">' + escapeHtml(m.change_display) + "</span>" : "") +
        '</div><div class="macro-row-sub">' + (!isBlank(m.source) ? "<span>" + escapeHtml(m.source) + "</span>" : "") +
        (!isBlank(m.as_of) ? '<span class="num-plain">' + escapeHtml(m.as_of) + "</span>" : "") +
        (!isBlank(m.note) ? "<span>" + escapeHtml(m.note) + "</span>" : "") + "</div></div>").join("") +
      "</section>").join("");
  }

  function corrCellStyle(v, diag) {
    if (diag) return "background:var(--surface-2);color:var(--sub)";
    const a = Math.min(1, Math.abs(v));
    if (a < 0.08) return "background:var(--corr-mid);color:var(--ink-2)";
    const alpha = (0.14 + a * 0.62).toFixed(2);
    const rgb = v >= 0 ? (a >= 0.45 ? "14,110,110" : "31,157,85") : "214,69,61";
    const fg = a >= 0.52 ? "#FFFFFF" : "var(--ink)";
    return "background:rgba(" + rgb + "," + alpha + ");color:" + fg;
  }

  function corrAt(i, j, win) {
    const corr = state().correlations || {};
    const mat = win === "90d" ? corr.matrix_90d : corr.matrix_30d;
    return mat && mat[i] ? toNum(mat[i][j]) : NaN;
  }

  function renderCorrReadout(i, j) {
    if (!dom.corrReadout) return;
    const assets = arr((state().correlations || {}).assets);
    const a = assets[i], b = assets[j];
    if (!a || !b) { dom.corrReadout.textContent = appState.corrDefaultText; return; }
    const v30 = corrAt(i, j, "30d"), v90 = corrAt(i, j, "90d");
    const delta = Number.isFinite(v30) && Number.isFinite(v90) ? v30 - v90 : NaN;
    dom.corrReadout.textContent = a.label + " × " + b.label + "：30日 " + (formatCorr(v30) || "—") +
      " ｜ 90日 " + (formatCorr(v90) || "—") + (Number.isFinite(delta) ? " ｜ 变化 " + formatCorr(delta) : "");
  }

  function restoreCorrReadout() {
    if (appState.corrPinned) renderCorrReadout(appState.corrPinned.i, appState.corrPinned.j);
    else if (dom.corrReadout) dom.corrReadout.textContent = appState.corrDefaultText;
  }

  function renderCorrMatrix() {
    if (!dom.corrMatrix) return;
    const corr = state().correlations || {};
    const assets = arr(corr.assets);
    const mat = appState.corrWindow === "90d" ? arr(corr.matrix_90d) : arr(corr.matrix_30d);
    if (!assets.length || !mat.length) {
      dom.corrMatrix.innerHTML = '<tbody><tr><td class="empty-hint">暂无相关性矩阵数据</td></tr></tbody>';
      return;
    }
    const head = "<thead><tr><th></th>" + assets.map((a) => '<th scope="col">' + escapeHtml(a.label) + "</th>").join("") + "</tr></thead>";
    const body = "<tbody>" + assets.map((ra, i) => "<tr><th scope=\"row\">" + escapeHtml(ra.label) + "</th>" +
      assets.map((ca, j) => {
        const v = toNum((mat[i] || [])[j]), diag = i === j;
        return '<td class="corr-cell' + (diag ? " is-diag" : "") + '" data-i="' + i + '" data-j="' + j + '" style="' + corrCellStyle(v, diag) + '">' + escapeHtml(Number.isFinite(v) ? formatCorr(v) : "—") + "</td>";
      }).join("") + "</tr>").join("") + "</tbody>";
    dom.corrMatrix.innerHTML = head + body;
  }

  function renderPairs() {
    if (!dom.pairsList) return;
    const corr = state().correlations || {};
    if (dom.pairsAsOf) dom.pairsAsOf.textContent = isBlank(corr.as_of) ? "" : corr.as_of + " 收盘基准";
    const idx = {};
    arr(corr.assets).forEach((a, i) => { idx[a.id] = i; });
    dom.pairsList.innerHTML = arr(corr.pairs).map((p) => {
      const lvl = DIVERGENCE_LEVEL[p.divergence];
      const cls = lvl ? " lvl-" + lvl : "";
      const bcls = lvl === "alert" ? "badge-bear" : lvl === "warn" ? "badge-warn" : "";
      const i = idx[p.a], j = idx[p.b];
      const dataAttr = i != null && j != null ? ' data-pair-i="' + i + '" data-pair-j="' + j + '"' : "";
      return '<button type="button" class="pair-card' + cls + '"' + dataAttr + ">" +
        '<div class="pair-head"><span>' + escapeHtml(p.label) + '</span><span class="badge ' + bcls + '">' + escapeHtml(p.divergence) + "</span></div>" +
        '<div class="pair-figs num-plain"><span>30日 ' + escapeHtml(formatCorr(p.corr_30d)) + "</span><span>90日 " + escapeHtml(formatCorr(p.corr_90d)) + "</span>" +
        '<span class="' + toneClass(p.delta) + '">差值 ' + escapeHtml(formatCorr(p.delta)) + "</span></div>" +
        (!isBlank(p.note) ? '<span class="pair-note">' + escapeHtml(p.note) + "</span>" : "") + "</button>";
    }).join("");
  }

  /* ---------- KOL 观点与历史胜率 ---------- */

  function kolFiltered(ignoreTopic) {
    const f = appState.kol, kw = f.keyword.toLowerCase();
    return arr(state().kol_digest).filter((k) => {
      if (!ignoreTopic && f.topic !== "all" && k.topic !== f.topic) return false;
      if (f.stance !== "all" && k.stance !== f.stance) return false;
      if (f.highWinOnly && !isHighWinKol(k)) return false;
      if (kw && [k.handle, k.name, k.role_label, k.headline, k.takeaway, k.why_usable, k.topic, k.group, arr(k.tickers).join(" ")].join(" ").toLowerCase().indexOf(kw) === -1) return false;
      return true;
    });
  }

  function kolScorecardsFiltered() {
    const f = appState.kol, kw = f.keyword.toLowerCase();
    return arr(state().kol_scorecards).filter((c) => {
      if (f.highWinOnly && !isHighWinKol(c)) return false;
      if (kw && [c.handle, c.name, c.role_label, c.category_label, c.focus, c.latest_headline, arr(c.best_tickers).join(" ")].join(" ").toLowerCase().indexOf(kw) === -1) return false;
      return true;
    });
  }

  function renderKolTopicChips() {
    if (!dom.kolTopicChips) return;
    const topics = [];
    arr(state().kol_digest).forEach((k) => {
      if (!isBlank(k.topic) && topics.indexOf(k.topic) === -1) topics.push(k.topic);
    });
    if (appState.kol.topic !== "all" && topics.indexOf(appState.kol.topic) === -1) appState.kol.topic = "all";
    dom.kolTopicChips.innerHTML = ["all"].concat(topics).map((tp) => {
      const on = appState.kol.topic === tp;
      return '<button type="button" class="chip' + (on ? " is-active" : "") + '" data-topic="' + escapeHtml(tp) + '" aria-pressed="' + String(on) + '">' + escapeHtml(tp === "all" ? "全部议题" : tp) + "</button>";
    }).join("");
  }

  function renderKolTug() {
    if (!dom.kolTug) return;
    const list = kolFiltered(false);
    const total = list.length || 1;
    const bull = list.filter((k) => k.stance === "偏多").length;
    const bear = list.filter((k) => k.stance === "偏空").length;
    const neu = Math.max(0, list.length - bull - bear);
    const scores = list.map((k) => toNum(k.score)).filter(Number.isFinite);
    const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 0;
    dom.kolTug.innerHTML =
      '<div class="tug-head"><span class="bull num-plain">偏多 ' + bull + " 位 (" + Math.round((bull / total) * 100) + '%)</span>' +
      '<span class="bear num-plain">偏空 ' + bear + " 位 (" + Math.round((bear / total) * 100) + "%)</span></div>" +
      '<div class="tug-bar" aria-hidden="true"><span class="bull" style="width:' + (bull / total) * 100 + '%"></span>' +
      '<span class="neutral" style="width:' + (neu / total) * 100 + '%"></span><span class="bear" style="width:' + (bear / total) * 100 + '%"></span></div>' +
      '<div class="tug-meta num-plain">共 ' + list.length + " 条去重观点 ｜ 中性 " + neu + " 位 ｜ 情绪均值 " + (avg > 0 ? "+" : "") + avg.toFixed(2) + "</div>";
  }

  function renderKolTopicBars() {
    if (!dom.kolTopicBars) return;
    const groups = {};
    kolFiltered(true).forEach((k) => {
      const tp = k.topic || "市场综合";
      if (!groups[tp]) groups[tp] = { bull: 0, bear: 0, total: 0 };
      groups[tp].total += 1;
      if (k.stance === "偏多") groups[tp].bull += 1;
      else if (k.stance === "偏空") groups[tp].bear += 1;
    });
    const keys = Object.keys(groups).sort((a, b) => groups[b].total - groups[a].total);
    const maxSide = Math.max(1, ...keys.map((tp) => Math.max(groups[tp].bull, groups[tp].bear)));
    dom.kolTopicBars.innerHTML = keys.map((tp) => {
      const g = groups[tp], on = appState.kol.topic === tp;
      return '<button type="button" class="topic-bar' + (on ? " is-active" : "") + '" data-topic="' + escapeHtml(tp) + '">' +
        '<span class="topic-name">' + escapeHtml(tp) + '</span><span class="topic-split" aria-hidden="true">' +
        '<span class="l"><i style="width:' + (g.bull / maxSide) * 100 + '%"></i></span><span class="r"><i style="width:' + (g.bear / maxSide) * 100 + '%"></i></span></span>' +
        '<span class="topic-count num-plain">' + (g.bull === 0 && g.bear === 0 && g.total > 0 ? g.total + "中性" : g.bull + "多 / " + g.bear + "空") + "</span></button>";
    }).join("");
  }

  function kolCardHtml(k) {
    const cls = STANCE_CLS[k.stance] || "neutral";
    const handle = String(k.handle || "");
    const rawHandle = handle.replace(/^@/, "");
    const shownHandle = handle && !handle.startsWith("@") ? "@" + handle : handle;
    const hasDistinctName = !isBlank(k.name) && String(k.name).trim().toLowerCase() !== rawHandle.toLowerCase();
    const roleOrGroup = !isBlank(k.role_label) ? k.role_label : (!isBlank(k.group) ? k.group : "");
    const relBadge = !isBlank(k.reliability_badge) ? String(k.reliability_badge) : "";
    const winText = kolWinRateText(k);
    const sc = toNum(k.score);
    const take = String(k.takeaway || "").trim();
    const whyUsable = String(k.why_usable || "").trim();
    const tickers = arr(k.tickers).slice(0, 5).map(tickerPillHtml).filter(Boolean).join("");
    const url = safeHttpUrl(k.url);
    return '<article class="tl-card"><div class="tl-head">' +
      (hasDistinctName ? '<span class="tl-author-name">' + escapeHtml(k.name) + "</span>" : "") +
      (shownHandle ? '<span class="tl-handle">' + escapeHtml(shownHandle) + "</span>" : "") +
      (roleOrGroup ? '<span class="badge badge-outline">' + escapeHtml(roleOrGroup) + "</span>" : "") +
      (relBadge ? '<span class="badge ' + kolReliabilityTone(relBadge) + '">' + escapeHtml(relBadge) + "</span>" : "") +
      (winText && winText !== relBadge ? '<span class="tl-win num-plain">' + escapeHtml(winText) + "</span>" : "") +
      (!isBlank(k.topic) ? '<span class="badge">' + escapeHtml(k.topic) + "</span>" : "") +
      '<span class="badge badge-' + cls + '">' + escapeHtml(k.stance || "中性") + (Number.isFinite(sc) ? " " + (sc > 0 ? "+" : "") + sc.toFixed(2) : "") + "</span>" +
      "</div>" +
      (!isBlank(k.headline) ? '<h3 class="tl-headline">' + escapeHtml(stripEmojis(k.headline)) + "</h3>" : "") +
      (tickers ? '<div class="tl-tickers">' + tickers + "</div>" : "") +
      (take ? '<p class="tl-take">' + escapeHtml(stripEmojis(take)) + "</p>" : "") +
      (whyUsable ? '<p class="tl-why"><b>操作参考</b>' + escapeHtml(stripEmojis(whyUsable)) + "</p>" : "") +
      '<div class="tl-actions">' +
      (!isBlank(k.article_file) ? '<button type="button" class="btn btn-sm" data-open-article="' + escapeHtml(k.article_file) + '">阅读关联长文</button>' : "") +
      (url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">原帖 ↗</a>' : "") +
      (take.length > 130 ? '<button type="button" class="btn-link" data-expand>展开全文</button>' : "") +
      "</div></article>";
  }

  function renderKolTimeline() {
    if (!dom.kolTimeline) return;
    const list = kolFiltered(false);
    if (!list.length) {
      dom.kolTimeline.innerHTML = '<div class="empty-hint">没有符合条件的 KOL 观点 · <button type="button" class="btn-link" data-clear-filters="kol">清除筛选</button></div>';
      return;
    }
    const byDate = {};
    list.forEach((k) => { const d = k.date || "近期"; (byDate[d] = byDate[d] || []).push(k); });
    const dates = Object.keys(byDate).sort((a, b) => b.localeCompare(a));
    const legend = '<div class="tl-legend" aria-hidden="true"><span class="l">偏多阵营</span><span></span><span class="r">偏空阵营</span></div>';
    dom.kolTimeline.innerHTML = legend + dates.map((d) => {
      const items = byDate[d];
      const col = (stance, cls) => {
        const rows = items.filter((k) => (stance ? k.stance === stance : k.stance !== "偏多" && k.stance !== "偏空"));
        return '<div class="tl-col ' + cls + '">' + rows.map(kolCardHtml).join("") + "</div>";
      };
      return '<section class="tl-day" aria-label="' + escapeHtml(d) + '"><div class="tl-date num-plain">' + escapeHtml(d) + "</div>" +
        col("偏多", "tl-col-bull") + col("偏空", "tl-col-bear") + col("", "tl-col-neutral") + "</section>";
    }).join("");
  }

  function fmtWinRateCell(val) {
    const n = toNum(val);
    if (val === null || val === undefined || !Number.isFinite(n)) return "—";
    return n.toFixed(1) + "%";
  }

  function renderKolScorecards() {
    if (!dom.kolScorecardWrap) return;
    const rawList = arr(state().kol_scorecards);
    const list = kolScorecardsFiltered();
    if (dom.kolScorecardCount) {
      dom.kolScorecardCount.textContent = rawList.length
        ? (list.length === rawList.length ? "共 " + list.length + " 位博主" : "显示 " + list.length + " / " + rawList.length + " 位")
        : "";
    }
    if (!rawList.length) {
      dom.kolScorecardWrap.innerHTML = '<div class="empty-hint">暂无历史胜率榜单数据</div>';
      return;
    }
    if (!list.length) {
      dom.kolScorecardWrap.innerHTML = '<div class="empty-hint">没有符合条件的博主记录 · <button type="button" class="btn-link" data-clear-filters="kol">清除筛选</button></div>';
      return;
    }
    dom.kolScorecardWrap.innerHTML =
      '<table class="fin-table sticky-table kol-score-table"><thead><tr>' +
      '<th class="ks-col-left">博主</th><th class="ks-col-left">领域</th><th class="num">综合评分</th><th class="ks-col-left">可靠标签</th>' +
      '<th class="num">已核验次数(N)</th><th class="num">5日胜率</th><th class="num">20日胜率</th><th class="num">20日均幅</th>' +
      '<th class="ks-col-left">擅长标的</th><th class="ks-col-left">最新态度</th>' +
      "</tr></thead><tbody>" +
      list.map((c) => {
        const rawHandle = String(c.handle || "").replace(/^@/, "");
        const shownHandle = rawHandle ? "@" + rawHandle : "";
        const hasName = !isBlank(c.name) && String(c.name).trim().toLowerCase() !== rawHandle.toLowerCase();
        const relScore = toNum(c.reliability_score);
        const verified = toNum(c.verified_calls);
        const edge20 = toNum(c.avg_edge_20d_pct);
        const bestTks = arr(c.best_tickers).slice(0, 3).map(tickerPillHtml).filter(Boolean).join("");
        const stance = String(c.latest_stance || "").trim();
        const stanceCls = STANCE_CLS[stance] || "neutral";
        return '<tr class="ks-row" data-kol-handle="' + escapeHtml(rawHandle) + '" title="点击查看 ' + escapeHtml(shownHandle || c.name || "") + ' 观点动态">' +
          '<td><div class="ks-author"><b>' + escapeHtml(hasName ? c.name : shownHandle) + "</b>" +
          '<span class="ks-sub">' + (hasName ? escapeHtml(shownHandle) : "") +
          (!isBlank(c.role_label) ? (hasName ? " · " : "") + escapeHtml(c.role_label) : "") + "</span></div></td>" +
          '<td class="ks-text">' + escapeHtml(c.category_label || c.focus || "—") + "</td>" +
          '<td class="num">' + escapeHtml(Number.isFinite(relScore) ? String(Math.round(relScore)) : "—") + "</td>" +
          '<td>' + (!isBlank(c.reliability_badge) ? '<span class="badge ' + kolReliabilityTone(c.reliability_badge) + '">' + escapeHtml(c.reliability_badge) + "</span>" : "—") + "</td>" +
          '<td class="num">' + escapeHtml(Number.isFinite(verified) ? String(Math.round(verified)) : "0") + "</td>" +
          '<td class="num">' + escapeHtml(fmtWinRateCell(c.win_rate_5d)) + "</td>" +
          '<td class="num">' + escapeHtml(fmtWinRateCell(c.win_rate_20d)) + "</td>" +
          '<td class="num ' + (c.avg_edge_20d_pct != null && Number.isFinite(edge20) ? toneClass(edge20) : "tone-flat") + '">' +
          escapeHtml(c.avg_edge_20d_pct != null && Number.isFinite(edge20) ? formatSignedPct(edge20) : "—") + "</td>" +
          '<td class="ks-tickers">' + (bestTks || '<span class="tone-flat">—</span>') + "</td>" +
          '<td class="ks-stance">' +
          (stance ? '<span class="badge badge-' + stanceCls + '">' + escapeHtml(stance) + "</span>" : "—") +
          (!isBlank(c.latest_date) ? '<span class="ks-date num-plain">' + escapeHtml(c.latest_date) + "</span>" : "") +
          "</td></tr>";
      }).join("") +
      "</tbody></table>";
  }

  function renderKolCrowdStrip() {
    if (!dom.kolCrowdStrip) return;
    const fs = state().forum_sentiment;
    if (!fs || typeof fs !== "object") {
      dom.kolCrowdStrip.hidden = true;
      return;
    }
    const divs = arr(fs.divergences).filter(Boolean).map(forumSnapshotContext);
    const temp = toNum(fs.overall_crowd_temp);
    if (!divs.length && !Number.isFinite(temp)) {
      dom.kolCrowdStrip.hidden = true;
      return;
    }
    dom.kolCrowdStrip.hidden = false;
    const label = String(fs.overall_label || "").trim();
    const badges = divs
      .filter((d) => !isBlank(d.divergence_tag) && d.divergence_tag !== "情绪平稳" && d.divergence_tag !== "暂无样本")
      .slice(0, 3);
    const shownDivs = badges.length ? badges : divs.slice(0, 2);
    dom.kolCrowdStrip.innerHTML =
      '<div class="kol-crowd-main">' +
      '<span class="kol-crowd-lbl">散户/机构温差</span>' +
      (Number.isFinite(temp)
        ? '<span class="badge badge-accent num-plain">当期社区情绪 ' + Math.round(temp) + (label ? " · " + escapeHtml(label) : "") + "</span>"
        : "") +
      shownDivs.map((d) =>
        '<span class="kol-crowd-chip">' +
        escapeHtml(d.topic || "") +
        ' <span class="badge ' + synthToneBadge(d.tone) + '">' + escapeHtml(d.divergence_tag || "") + "</span>" +
        "</span>"
      ).join("") +
      "</div>" +
      '<button type="button" class="btn btn-sm btn-ghost" data-jump-forum>全网舆论明细 →</button>';
  }

  function renderKol() {
    renderKolCrowdStrip();
    const isScore = appState.kol.view === "scorecard";
    if (dom.kolViewSwitch) setActive(dom.kolViewSwitch, "data-kview", isScore ? "scorecard" : "feed");
    if (dom.kolHighWinBtn) {
      dom.kolHighWinBtn.classList.toggle("is-active", appState.kol.highWinOnly);
      dom.kolHighWinBtn.setAttribute("aria-pressed", String(appState.kol.highWinOnly));
    }
    if (dom.kolTugPanel) dom.kolTugPanel.hidden = isScore;
    if (dom.kolTimeline) dom.kolTimeline.hidden = isScore;
    if (dom.kolScorecardPanel) dom.kolScorecardPanel.hidden = !isScore;
    if (dom.kolStanceChips) dom.kolStanceChips.hidden = isScore;
    if (dom.kolTopicChips) dom.kolTopicChips.hidden = isScore;

    if (isScore) {
      renderKolScorecards();
    } else {
      renderKolTopicChips();
      renderKolTug();
      renderKolTopicBars();
      renderKolTimeline();
    }
  }

  /* ---------- 长文待读 ---------- */

  function articleRecord(fname) {
    return (appState.journal.article_status || {})[fname] || null;
  }

  function articleStatus(fname) {
    const rec = articleRecord(fname);
    return rec && ARTICLE_STATUS_LABELS[rec.status] ? rec.status : "unread";
  }

  function articleScoreBadge(a) {
    if (!a || isBlank(a.score)) return "";
    const sc = toNum(a.score);
    return Number.isFinite(sc)
      ? '<span class="badge badge-accent num-plain">评分 ' + escapeHtml(String(sc)) + "</span>"
      : "";
  }

  function articleBaseList() {
    const f = appState.articles, kw = f.keyword.toLowerCase();
    return arr(state().articles).filter((a) => {
      if (f.source !== "all" && a.source !== f.source) return false;
      if (kw && [a.title, a.excerpt, a.source, a.publisher, a.author_role, arr(a.tickers).join(" ")].join(" ").toLowerCase().indexOf(kw) === -1) return false;
      return true;
    });
  }

  function renderArticles() {
    if (!dom.articleList) return;
    const base = articleBaseList();
    const counts = { all: base.length, unread: 0, reading: 0, read: 0, starred: 0 };
    base.forEach((a) => { counts[articleStatus(a.filename)] += 1; });
    document.querySelectorAll("[data-acount]").forEach((el) => {
      const n = counts[el.getAttribute("data-acount")];
      el.textContent = n ? String(n) : "";
    });
    const list = appState.articles.status === "all" ? base : base.filter((a) => articleStatus(a.filename) === appState.articles.status);
    if (!list.length) {
      dom.articleList.innerHTML = '<li class="empty-hint">没有符合条件的长文 · <button type="button" class="btn-link" data-clear-filters="articles">清除筛选</button></li>';
      return;
    }
    dom.articleList.innerHTML = list.map((a) => {
      const st = articleStatus(a.filename);
      const rec = articleRecord(a.filename);
      const note = rec && !isBlank(rec.note) ? String(rec.note) : "";
      const fname = escapeHtml(a.filename);
      const tks = arr(a.tickers).slice(0, 4).map(tickerPillHtml).filter(Boolean).join("");
      return '<li class="article-item"><div class="article-main">' +
        '<div class="article-meta"><span class="badge status-badge-' + st + '">' + ARTICLE_STATUS_LABELS[st] + "</span>" +
        (!isBlank(a.source) ? "<span>" + escapeHtml(a.source) + "</span>" : "") +
        (!isBlank(a.publisher) ? '<span class="badge badge-outline">' + escapeHtml(a.publisher) + "</span>" : "") +
        (!isBlank(a.author_role) ? '<span class="badge badge-outline">' + escapeHtml(a.author_role) + "</span>" : "") +
        articleScoreBadge(a) +
        (!isBlank(a.mtime) ? '<span class="num-plain">' + escapeHtml(a.mtime) + "</span>" : "") + "</div>" +
        '<button type="button" class="article-title" data-open-article="' + fname + '">' + escapeHtml(a.title || a.filename) + "</button>" +
        (!isBlank(a.excerpt) ? '<p class="article-excerpt">' + escapeHtml(a.excerpt) + "</p>" : "") +
        (tks ? '<div class="article-tickers">' + tks + "</div>" : "") +
        (note ? '<p class="article-note">笔记：' + escapeHtml(note) + "</p>" : "") +
        '</div><div class="article-side"><button type="button" class="btn btn-sm" data-open-article="' + fname + '">阅读</button></div></li>';
    }).join("");
  }

  /* ---------- 信源导航 ---------- */

  function sourceKolWinText(k) {
    const calls = toNum(k && k.verified_calls);
    const w20 = toNum(k && k.win_rate_20d);
    if (k && k.win_rate_20d != null && Number.isFinite(w20) && Number.isFinite(calls) && calls >= 5) {
      return "20日胜率 " + w20.toFixed(1) + "% · " + Math.round(calls) + "次";
    }
    return "样本积累";
  }

  function sourceChainStockPill(sym, label) {
    const up = String(sym || "").trim().toUpperCase();
    if (!up) return "";
    return '<button type="button" class="ticker source-chain-pill" data-open-stock="' + escapeHtml(up) + '" title="打开 ' + escapeHtml(up) + ' 投研">' +
      escapeHtml(label || up) + "</button>";
  }

  function renderSources() {
    if (!dom.sourcesGrid) return;
    const mx = state().asset_source_matrix;
    const cats = arr(mx && mx.categories).filter((c) => c && typeof c === "object" && !isBlank(c.id));
    if (!mx || !cats.length) {
      if (dom.sourceCategoryChips) dom.sourceCategoryChips.innerHTML = "";
      if (dom.sourcesMeta) dom.sourcesMeta.textContent = "";
      dom.sourcesGrid.innerHTML = '<div class="empty-hint">暂无信源导航矩阵数据</div>';
      return;
    }

    if (appState.sources.category !== "all" && !cats.some((c) => c.id === appState.sources.category)) {
      appState.sources.category = "all";
    }
    if (dom.sourceCategoryChips) {
      const chipList = [{ id: "all", name: "全部品类" }].concat(cats);
      dom.sourceCategoryChips.innerHTML = chipList.map((c) => {
        const on = appState.sources.category === c.id;
        return '<button type="button" class="chip' + (on ? " is-active" : "") + '" data-scategory="' + escapeHtml(c.id) + '" aria-pressed="' + String(on) + '">' + escapeHtml(c.name || c.id) + "</button>";
      }).join("");
    }

    if (dom.sourcesMeta) {
      const sum = mx.summary || {};
      const parts = [];
      parts.push((Number.isFinite(toNum(sum.categories_count)) ? Math.round(toNum(sum.categories_count)) : cats.length) + " 大品类");
      if (Number.isFinite(toNum(sum.verified_kols_linked))) parts.push(Math.round(toNum(sum.verified_kols_linked)) + " 位关联博主");
      if (Number.isFinite(toNum(sum.supply_chain_nodes))) parts.push(Math.round(toNum(sum.supply_chain_nodes)) + " 个产业链节点");
      if (!isBlank(mx.updated_at_bjt)) parts.push(String(mx.updated_at_bjt));
      dom.sourcesMeta.textContent = parts.join(" · ");
    }

    const shown = appState.sources.category === "all"
      ? cats
      : cats.filter((c) => c.id === appState.sources.category);

    dom.sourcesGrid.innerHTML = shown.map((c) => {
      const venues = arr(c.primary_venues);
      const kols = arr(c.top_kols);
      const longforms = arr(c.guru_longforms);
      const warnCh = c.crowd_warning_channel && typeof c.crowd_warning_channel === "object" ? c.crowd_warning_channel : null;
      const bridges = arr(c.supply_chain_bridges);

      const venuesHtml = venues.length
        ? '<div class="source-sec"><h4 class="source-sec-title">去哪看</h4><div class="source-venues">' +
          venues.map((v) => {
            const u = safeHttpUrl(v.url);
            return '<div class="source-venue-row">' +
              '<div class="source-venue-top">' +
              (u
                ? '<a class="source-venue-link" href="' + escapeHtml(u) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(v.name || "") + " ↗</a>"
                : '<span class="source-venue-name">' + escapeHtml(v.name || "") + "</span>") +
              (!isBlank(v.type) ? '<span class="badge badge-outline">' + escapeHtml(v.type) + "</span>" : "") +
              (!isBlank(v.cadence) ? '<span class="source-cadence">' + escapeHtml(v.cadence) + "</span>" : "") +
              "</div>" +
              (!isBlank(v.focus) ? '<p class="source-venue-focus">' + escapeHtml(v.focus) + "</p>" : "") +
              "</div>";
          }).join("") +
          "</div></div>"
        : "";

      const longformHtml = longforms.length
        ? '<div class="source-sec"><h4 class="source-sec-title">大佬长文源</h4><div class="source-longforms">' +
          longforms.map((g) => {
            const u = safeHttpUrl(g.url);
            const label = (g.name || "") + (!isBlank(g.publication) ? " · " + g.publication : "");
            return '<div class="source-longform-chip">' +
              (u
                ? '<a href="' + escapeHtml(u) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(label) + " ↗</a>"
                : "<span>" + escapeHtml(label) + "</span>") +
              (!isBlank(g.focus) ? '<span class="source-lf-focus">' + escapeHtml(g.focus) + "</span>" : "") +
              "</div>";
          }).join("") +
          "</div></div>"
        : "";

      const kolsHtml = kols.length
        ? '<div class="source-sec"><h4 class="source-sec-title">看谁的</h4><div class="source-kol-list">' +
          kols.map((k) => {
            const rawHandle = String(k.handle || "").replace(/^@/, "");
            const shownHandle = rawHandle ? "@" + rawHandle : "";
            const dispName = !isBlank(k.name) ? k.name : shownHandle;
            const winStr = sourceKolWinText(k);
            const u = safeHttpUrl(k.url);
            const bestTks = arr(k.best_tickers).slice(0, 3).map((t) => String(t || "").toUpperCase()).filter(Boolean).join(" / ");
            return '<div class="source-kol-item">' +
              '<button type="button" class="source-kol-pill" data-jump-kol="' + escapeHtml(rawHandle) + '" title="点击筛选 ' + escapeHtml(shownHandle || dispName) + ' 观点">' +
              '<span class="source-kol-head">' +
              '<b class="source-kol-name">' + escapeHtml(dispName) + "</b>" +
              (shownHandle && dispName !== shownHandle ? '<span class="source-kol-handle">' + escapeHtml(shownHandle) + "</span>" : "") +
              (!isBlank(k.role_label) ? '<span class="badge badge-outline">' + escapeHtml(k.role_label) + "</span>" : "") +
              (!isBlank(k.reliability_badge) ? '<span class="badge ' + kolReliabilityTone(k.reliability_badge) + '">' + escapeHtml(k.reliability_badge) + "</span>" : "") +
              '<span class="source-kol-win num-plain">' + escapeHtml(winStr) + "</span>" +
              "</span>" +
              (!isBlank(k.why_follow) || bestTks
                ? '<span class="source-kol-why">' +
                  (bestTks ? '<strong class="num-plain">' + escapeHtml(bestTks) + "</strong> · " : "") +
                  escapeHtml(k.why_follow || "") +
                  "</span>"
                : "") +
              "</button>" +
              (u ? '<a class="source-kol-ext" href="' + escapeHtml(u) + '" target="_blank" rel="noopener noreferrer" title="打开原主页">↗</a>' : "") +
              "</div>";
          }).join("") +
          "</div></div>"
        : "";

      const warnHtml = warnCh && (!isBlank(warnCh.platform) || !isBlank(warnCh.what_to_watch))
        ? '<div class="source-warn-box">' +
          '<span class="badge badge-warn">散户反指</span>' +
          '<div class="source-warn-body">' +
          (!isBlank(warnCh.platform) ? "<b>" + escapeHtml(warnCh.platform) + "</b>" : "") +
          (!isBlank(warnCh.what_to_watch) ? "<span>" + escapeHtml(warnCh.what_to_watch) + "</span>" : "") +
          "</div></div>"
        : "";

      const bridgesHtml = bridges.length
        ? '<div class="source-sec"><h4 class="source-sec-title">中美产业链映射</h4><div class="source-bridges">' +
          bridges.map((b) => {
            const aList = arr(b.a_shares);
            const usLbl = b.us_leader + (!isBlank(b.us_name) && b.us_name !== b.us_leader ? " " + b.us_name : "");
            return '<div class="source-bridge-row">' +
              '<div class="source-bridge-lead">' +
              sourceChainStockPill(b.us_leader, usLbl) +
              (!isBlank(b.focus) ? '<span class="source-bridge-focus">' + escapeHtml(b.focus) + "</span>" : "") +
              "</div>" +
              (aList.length
                ? '<div class="source-bridge-ashares">' +
                  '<span class="source-bridge-arrow" aria-hidden="true">→</span>' +
                  aList.map((a) => {
                    const sym = String(a.symbol || "").trim();
                    const lbl = (a.name ? a.name + " " : "") + sym + (a.sector ? " · " + a.sector : "");
                    return sourceChainStockPill(sym, lbl);
                  }).join("") +
                  "</div>"
                : "") +
              "</div>";
          }).join("") +
          "</div></div>"
        : "";

      return '<article class="panel source-cat-card">' +
        '<div class="source-cat-head">' +
        '<h3 class="sec-title">' + escapeHtml(c.name || c.id || "") + "</h3>" +
        '<span class="badge badge-accent num-plain">' + kols.length + " 位追踪博主</span>" +
        "</div>" +
        (!isBlank(c.core_question) ? '<p class="source-core-q">' + escapeHtml(c.core_question) + "</p>" : "") +
        venuesHtml +
        kolsHtml +
        longformHtml +
        warnHtml +
        bridgesHtml +
        "</article>";
    }).join("");
  }

  /* ---------- 抽屉管理 & 长文阅读抽屉 ---------- */

  function anyDrawerOpen() {
    return (dom.articleDrawer && !dom.articleDrawer.hidden) ||
      (dom.pushDrawer && !dom.pushDrawer.hidden) ||
      (dom.positionDrawer && !dom.positionDrawer.hidden) ||
      (dom.importDrawer && !dom.importDrawer.hidden) ||
      (dom.ingestDrDrawer && !dom.ingestDrDrawer.hidden);
  }

  function openDrawer(el, focusEl) {
    if (!el) return;
    if (!anyDrawerOpen()) appState.drawerReturnFocus = document.activeElement;
    el.hidden = false;
    document.body.classList.add("drawer-open");
    if (focusEl) focusEl.focus({ preventScroll: true });
  }

  function closeDrawer(which) {
    const map = {
      article: dom.articleDrawer,
      push: dom.pushDrawer,
      position: dom.positionDrawer,
      import: dom.importDrawer,
      ingestDr: dom.ingestDrDrawer
    };
    const el = map[which] || dom.articleDrawer;
    if (!el || el.hidden) return;
    el.hidden = true;
    if (which === "article") appState.drawerArticle = null;
    if (which === "import") invalidateImportPreview();
    if (which === "position") {
      for (const key of positionAmountInputs) dom[key].value = "";
      appState.positionFormInitial = null;
      appState.maskedPositionValues = null;
    }
    if (!anyDrawerOpen()) {
      document.body.classList.remove("drawer-open");
      const back = appState.drawerReturnFocus;
      appState.drawerReturnFocus = null;
      if (back && typeof back.focus === "function" && document.contains(back)) back.focus({ preventScroll: true });
    }
  }

  function stripLeadingTitle(md, title) {
    const m = String(md || "").match(/^\s*#\s+(.+?)\s*(?:\n|$)/);
    if (m && title && m[1].replace(/\s+/g, " ").trim() === String(title).replace(/\s+/g, " ").trim()) {
      return String(md).slice(m[0].length);
    }
    return md;
  }

  function syncArticleStatusUi() {
    const cur = appState.drawerArticle;
    if (!cur || !dom.articleStatusSeg) return;
    const st = articleStatus(cur.filename);
    dom.articleStatusSeg.querySelectorAll("[data-set-status]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.getAttribute("data-set-status") === st));
    });
  }

  function fillArticle(art) {
    const cur = appState.drawerArticle;
    const title = art.title || cur.filename;
    dom.articleTitle.textContent = title;
    const meta = [];
    if (!isBlank(art.source)) meta.push('<span class="badge badge-outline">' + escapeHtml(art.source) + "</span>");
    if (!isBlank(art.publisher)) meta.push('<span class="badge badge-outline">' + escapeHtml(art.publisher) + "</span>");
    if (!isBlank(art.author_role)) meta.push('<span class="badge badge-outline">' + escapeHtml(art.author_role) + "</span>");
    const sb = articleScoreBadge(art);
    if (sb) meta.push(sb);
    arr(art.tickers).slice(0, 4).forEach((sym) => {
      const s = String(sym || "").trim().toUpperCase();
      if (s) meta.push('<span class="ticker">' + escapeHtml(s) + "</span>");
    });
    if (!isBlank(art.mtime)) meta.push('<span class="num-plain">' + escapeHtml(art.mtime) + "</span>");
    const artUrl = safeHttpUrl(art.url);
    if (artUrl) {
      meta.push('<a class="btn btn-sm btn-ghost article-origin-btn" href="' + escapeHtml(artUrl) + '" target="_blank" rel="noopener noreferrer">原文 ↗</a>');
    }
    dom.articleMeta.innerHTML = meta.join("");
    dom.articleBody.innerHTML = renderMarkdown(stripLeadingTitle(art.body_md, title));
  }

  async function openArticle(fname) {
    if (isBlank(fname)) return;
    const local = arr(state().articles).find((a) => a.filename === fname) || null;
    appState.drawerArticle = { filename: fname };
    const rec = articleRecord(fname);
    dom.articleNote.value = rec && rec.note ? rec.note : "";
    dom.noteSavedHint.textContent = rec && rec.note && rec.updated_at ? "上次保存 " + shortStamp(rec.updated_at) : "";
    syncArticleStatusUi();
    dom.articleScroll.scrollTop = 0;
    openDrawer(dom.articleDrawer, dom.closeArticleBtn);

    if (local && !isBlank(local.body_md)) { fillArticle(local); return; }
    dom.articleMeta.innerHTML = "";
    dom.articleTitle.textContent = local && local.title ? local.title : "深度长文";
    if (appState.offline) {
      dom.articleBody.innerHTML = '<p class="empty-hint">离线快照没有收录这篇长文的全文，请在本地服务中打开阅读</p>';
      return;
    }
    dom.articleBody.innerHTML = '<p class="empty-hint">正在载入全文…</p>';
    try {
      const art = await apiJson("/api/article?file=" + encodeURIComponent(fname));
      if (!appState.drawerArticle || appState.drawerArticle.filename !== fname) return;
      fillArticle(Object.assign({}, local || {}, art || {}));
    } catch (err) {
      if (!appState.drawerArticle || appState.drawerArticle.filename !== fname) return;
      dom.articleBody.innerHTML = '<p class="empty-hint">' + escapeHtml(err.message || "全文载入失败") + "</p>";
    }
  }

  async function setArticleStatus(status, withNote) {
    if (appState.offline) return showToast("共享离线快照只读，禁止写入私人笔记", "error");
    const cur = appState.drawerArticle;
    if (!cur || !ARTICLE_STATUS_LABELS[status]) return;
    const payload = { filename: cur.filename, status };
    if (withNote) payload.note = dom.articleNote.value;
    const btn = withNote ? dom.saveNoteBtn : null;
    if (btn) btn.disabled = true;
    try {
      await journalMutate("articleStatus", payload);
      syncArticleStatusUi();
      renderArticles();
      renderCounts();
      if (withNote) dom.noteSavedHint.textContent = "已保存 " + shortStamp(nowBjtIso()).slice(11);
      else showToast("已标记为「" + ARTICLE_STATUS_LABELS[status] + "」", "ok");
    } catch (err) {
      showToast(err.message || "保存失败", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function canRunOnlineDr() {
    return !appState.offline && Boolean(appState.drCapabilities && appState.drCapabilities.can_run_demolab_dr);
  }

  function syncDrDrawerModeUi() {
    const online = canRunOnlineDr();
    if (dom.drModeSwitch) dom.drModeSwitch.hidden = !online;
    if (!online) appState.drMode = "paste";
    const isRun = online && appState.drMode === "run";
    if (dom.drModeSwitch) {
      dom.drModeSwitch.querySelectorAll("[data-drmode]").forEach((btn) => {
        const active = btn.getAttribute("data-drmode") === (isRun ? "run" : "paste");
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", String(active));
      });
    }
    if (dom.drRunForm) dom.drRunForm.hidden = !isRun;
    if (dom.ingestDrForm) dom.ingestDrForm.hidden = isRun;
  }

  function setDrMode(mode) {
    appState.drMode = mode === "run" && canRunOnlineDr() ? "run" : "paste";
    syncDrDrawerModeUi();
  }

  function openDrWorkbench(opts) {
    const o = opts && typeof opts === "object" ? opts : {};
    const targetMode = o.mode || (canRunOnlineDr() ? "run" : "paste");
    setDrMode(targetMode);
    if (appState.drMode === "run") {
      if (dom.drRunPromptInput && typeof o.prompt === "string") dom.drRunPromptInput.value = o.prompt;
      if (dom.drRunTitleInput && typeof o.title === "string") dom.drRunTitleInput.value = o.title;
      if (dom.drRunTickersInput && typeof o.tickers === "string") dom.drRunTickersInput.value = o.tickers;
      return openDrawer(dom.ingestDrDrawer, dom.drRunPromptInput || dom.drBodyInput);
    }
    if (dom.drBodyInput && typeof o.body === "string") dom.drBodyInput.value = o.body;
    return openDrawer(dom.ingestDrDrawer, dom.drBodyInput);
  }

  async function loadDrCapabilities() {
    if (appState.offline) {
      appState.drCapabilities = null;
      syncDrDrawerModeUi();
      return null;
    }
    try {
      const caps = await apiJson("/api/research/capabilities");
      appState.drCapabilities = caps && typeof caps === "object" ? caps : null;
    } catch (_) {
      appState.drCapabilities = null;
    }
    if (dom.openIngestDrBtn) {
      const isCons = Boolean((appState.drCapabilities && appState.drCapabilities.consumer_mode) || appState.serverRole === "consumer");
      dom.openIngestDrBtn.hidden = Boolean(appState.offline) || isCons;
    }
    syncDrDrawerModeUi();
    if (appState.drawerArticle && dom.articleDrawer && !dom.articleDrawer.hidden) {
      const cur = arr(state().articles).find((a) => a.filename === appState.drawerArticle.filename);
      if (cur && !isBlank(cur.body_md)) fillArticle(cur);
    }
    return appState.drCapabilities;
  }

  function renderDrJobStatus(job) {
    if (!dom.drJobBox || !dom.drJobStatusBadge || !dom.drJobMetaText) return;
    if (!job) {
      dom.drJobBox.hidden = true;
      return;
    }
    dom.drJobBox.hidden = false;
    const st = String(job.status || "");
    const lbl = String(job.stage_label || "处理中");
    dom.drJobStatusBadge.textContent = lbl;
    dom.drJobStatusBadge.className = "badge " + (st === "completed" ? "badge-ok" : (st === "failed" ? "badge-bear" : "badge-accent"));
    const gaps = arr(job.unresolved_gaps);
    if (st === "completed") {
      dom.drJobMetaText.textContent = "已完成 " + Number(job.tracks_succeeded || 0) + "/" + Number(job.tracks_total || 3) + " 路并行核查" + (gaps.length ? " · 含 " + gaps.length + " 项待复核缺口" : "");
    } else if (st === "failed") {
      dom.drJobMetaText.textContent = String(job.error || "深研未完成，可切换粘贴导入补充");
    } else {
      dom.drJobMetaText.textContent = "三路多智能体并行核查中（每路独立核实一手信源与反方阈值）";
    }
  }

  function stopDrJobPoll() {
    if (appState.drJobPollTimer) {
      window.clearInterval(appState.drJobPollTimer);
      appState.drJobPollTimer = 0;
    }
  }

  async function pollDrJobOnce(jobId) {
    if (!jobId || appState.offline) return;
    try {
      const res = await apiJson("/api/research/jobs?job_id=" + encodeURIComponent(jobId));
      const job = res && res.job ? res.job : null;
      if (!job) return;
      renderDrJobStatus(job);
      if (job.status === "completed") {
        stopDrJobPoll();
        appState.busy.runDr = false;
        if (dom.drRunBtn) dom.drRunBtn.disabled = false;
        const art = job.article;
        if (art && art.filename) {
          if (!appState.raw || typeof appState.raw !== "object") appState.raw = {};
          const prev = arr(appState.raw.articles).filter((a) => a.filename !== art.filename);
          appState.raw.articles = [art].concat(prev);
        }
        renderArticles();
        renderCounts();
        showToast("并行深研已完成并入库", "ok");
        closeDrawer("ingestDr");
        if (art && art.filename) await openArticle(art.filename);
      } else if (job.status === "failed") {
        stopDrJobPoll();
        appState.busy.runDr = false;
        if (dom.drRunBtn) dom.drRunBtn.disabled = false;
        showToast(job.error || "并行深研执行失败", "error");
      }
    } catch (_) {
      /* transient poll skip */
    }
  }

  async function submitRunDr(e) {
    e.preventDefault();
    if (!canRunOnlineDr()) return showToast("当前环境仅支持粘贴导入", "error");
    if (appState.busy.runDr) return showToast("已有深研任务运行中", "error");
    const prompt = dom.drRunPromptInput ? dom.drRunPromptInput.value.trim() : "";
    const title = dom.drRunTitleInput ? dom.drRunTitleInput.value.trim() : "";
    const tickers = dom.drRunTickersInput ? dom.drRunTickersInput.value.trim() : "";
    if (!prompt) return showToast("请输入深研课题提问词", "error");

    appState.busy.runDr = true;
    if (dom.drRunBtn) dom.drRunBtn.disabled = true;
    try {
      const res = await apiJson("/api/research/run", {
        method: "POST",
        body: { prompt, title, tickers }
      });
      const job = res && res.job ? res.job : null;
      if (!job || !job.job_id) throw new Error("未返回有效深研任务编号");
      appState.drActiveJobId = job.job_id;
      renderDrJobStatus(job);
      stopDrJobPoll();
      appState.drJobPollTimer = window.setInterval(() => { pollDrJobOnce(job.job_id); }, 2000);
      showToast("已启动 3 路并行深研，可稍候或留在本页查看进度", "ok");
    } catch (err) {
      appState.busy.runDr = false;
      if (dom.drRunBtn) dom.drRunBtn.disabled = false;
      showToast(err.message || "启动深研失败", "error");
    }
  }

  async function submitIngestDr(e) {
    e.preventDefault();
    if (appState.offline) return showToast("共享离线快照只读，无法导入深研", "error");
    if (appState.busy.ingestDr) return;
    const title = dom.drTitleInput ? dom.drTitleInput.value.trim() : "";
    const tickers = dom.drTickersInput ? dom.drTickersInput.value.trim() : "";
    const bodyMd = dom.drBodyInput ? dom.drBodyInput.value.trim() : "";
    if (!bodyMd) return showToast("请输入深研 Markdown 正文", "error");

    appState.busy.ingestDr = true;
    if (dom.drSaveBtn) dom.drSaveBtn.disabled = true;
    try {
      const res = await apiJson("/api/research/ingest", {
        method: "POST",
        body: { title, tickers, body_md: bodyMd }
      });
      const art = res && res.article ? res.article : null;
      if (art) {
        if (!appState.raw || typeof appState.raw !== "object") appState.raw = {};
        const prev = arr(appState.raw.articles).filter((a) => a.filename !== art.filename);
        appState.raw.articles = [art].concat(prev);
      }
      if (dom.drTitleInput) dom.drTitleInput.value = "";
      if (dom.drTickersInput) dom.drTickersInput.value = "";
      if (dom.drBodyInput) dom.drBodyInput.value = "";
      closeDrawer("ingestDr");
      renderArticles();
      renderCounts();
      showToast("深研已入库", "ok");
      if (art && art.filename) {
        await openArticle(art.filename);
      }
    } catch (err) {
      showToast(err.message || "导入深研失败", "error");
    } finally {
      appState.busy.ingestDr = false;
      if (dom.drSaveBtn) dom.drSaveBtn.disabled = false;
    }
  }

  /* ---------- 复盘与随笔（严禁离线写入 + expected_rev 并发校验 + 草稿隔离） ---------- */

  function normalizeJournal(j) {
    const src = j && typeof j === "object" ? j : {};
    return {
      trade_reviews: arr(src.trade_reviews),
      essays: arr(src.essays),
      article_status: src.article_status && typeof src.article_status === "object" ? src.article_status : {}
    };
  }

  const JOURNAL_ENDPOINTS = {
    saveReview: "/api/journal/review",
    deleteReview: "/api/journal/review/delete",
    saveEssay: "/api/journal/essay",
    deleteEssay: "/api/journal/essay/delete",
    articleStatus: "/api/article/status"
  };

  async function journalMutate(kind, payload) {
    if (appState.offline) {
      throw new Error("共享离线快照为只读模式，禁止触发私人写入");
    }
    const res = await apiJson(JOURNAL_ENDPOINTS[kind], { method: "POST", body: payload });
    appState.journal = normalizeJournal(kind === "articleStatus" ? res : await apiJson("/api/journal"));
  }

  function renderJournalGate() {
    if (!dom.journalGate) return;
    const rg = state().risk_gate || {};
    const cap = toNum(rg.long_risk_cap);
    const reasons = gateReasons(rg);
    const rows = [];
    if (Number.isFinite(cap)) rows.push(["多头风险上限", '<span class="num-plain">' + escapeHtml(String(cap)) + " 倍</span>"]);
    if (!isBlank(rg.summary_exposure)) rows.push(["当前敞口", escapeHtml(rg.summary_exposure)]);
    if (reasons.length) rows.push(["触发依据", escapeHtml(reasons.join("；"))]);
    dom.journalGate.innerHTML = (!isBlank(rg.label) ? '<div class="gate-label">' + escapeHtml(rg.label) + "</div>" : "") +
      rows.map((r) => '<span class="jg-k">' + r[0] + '</span><span class="jg-v">' + r[1] + "</span>").join("");
    dom.journalGate.hidden = !rows.length && isBlank(rg.label);
  }

  function renderJournalPositions() {
    if (!dom.journalPositions) return;
    const positions = arr(state().positions);
    const head = '<div class="jpos jpos-head"><span>仓位</span><span class="num-cell">基准比例</span><span class="num-cell">建议比例</span>' +
      '<span class="num-cell">参考金额</span><span class="jpos-note">执行要点</span></div>';
    dom.journalPositions.innerHTML = head + positions.map((pos) => {
      const pct = positionPct(pos);
      return '<div class="jpos">' +
        '<div class="jpos-name">' + escapeHtml(pos.name) + '<br><span class="badge ' + (pct > 0 ? "badge-accent" : "") + '">' + escapeHtml(pos.state || "") + "</span></div>" +
        '<span class="num-cell" data-label="基准">' + escapeHtml(formatWeight(pos.baseline_pct)) + "</span>" +
        '<span class="num-cell" data-label="建议">' + escapeHtml(formatWeight(pct)) + "</span>" +
        '<span class="num-cell" data-label="金额">' + amtHtml(positionNotional(pos)) + "</span>" +
        '<p class="jpos-note">' + escapeHtml(isBlank(pos.note) ? "" : pos.note) + "</p></div>";
    }).join("");
    if (dom.journalCapitalHint) dom.journalCapitalHint.innerHTML = "资金基数 " + amtHtml(appState.capitalBase);
  }

  function renderPositionsAll() {
    renderExecPositions();
    renderJournalPositions();
    renderPortfolioView();
  }

  function positionNameMap() {
    const map = {};
    arr(state().positions).forEach((p) => { map[p.id] = p.name; });
    return map;
  }

  function isLegacyAutoSeedReview(r) {
    if (!r || typeof r !== "object") return false;
    const sym = String(r.symbol || "");
    return (
      Number(r.rev || 0) === 0 &&
      r.direction === "跟踪" &&
      ["btc_trend", "gold_hedge", "bear_basket", "crash_bounce"].includes(sym) &&
      isBlank(r.execution_note) &&
      isBlank(r.lesson) &&
      isBlank(r.position_id)
    );
  }

  function reviewStatusBadge(status) {
    const cls = status === "已止盈" ? "badge-bull" : status === "已止损" ? "badge-bear" : (status === "持有中" || status === "计划跟进") ? "badge-accent" : (status === "当前跟踪" || status === "未采用·观察") ? "badge-outline" : "";
    return '<span class="badge ' + cls + '">' + escapeHtml(status) + "</span>";
  }

  function reviewOutcomeBadge(block, pendingFallback) {
    if (!block || typeof block !== "object") return '<span class="badge badge-outline">' + escapeHtml(pendingFallback || "跟踪中") + "</span>";
    const o = String(block.outcome || "pending");
    const cls = o === "hit" ? "badge-bull" : o === "miss" ? "badge-bear" : o === "flat" ? "badge-outline" : "badge-warn";
    return '<span class="badge ' + cls + '">' + escapeHtml(block.verdict || pendingFallback || "跟踪中") + "</span>";
  }

  function realUserReviews() {
    return arr(appState.journal.trade_reviews).filter((r) => !isLegacyAutoSeedReview(r));
  }

  function standaloneReviewCardHtml(r, names) {
    const dirCls = r.direction === "做多" ? "badge-bull" : (r.direction === "做空" || r.direction === "防守减仓") ? "badge-bear" : "";
    const price = toNum(r.price_usd);
    const pnl = toNum(r.pnl_usd);
    const showPnl = Number.isFinite(pnl) && (pnl !== 0 || CLOSED_REVIEW_STATUSES.has(r.status));
    const notes = [["布局逻辑", r.thesis], ["实操备忘", r.execution_note], ["复盘补充", r.lesson]]
      .filter((n) => !isBlank(n[1]))
      .map((n) => "<div><dt>" + n[0] + "</dt><dd>" + escapeHtml(n[1]) + "</dd></div>").join("");
    const tags = arr(r.tags).filter((t) => !isBlank(t)).map((t) => '<span class="badge badge-outline">#' + escapeHtml(t) + "</span>").join("");
    const id = escapeHtml(r.id);
    return '<article class="review-card"><div class="review-head">' +
      '<span class="review-symbol">' + escapeHtml(names[r.symbol] || r.symbol) + "</span>" +
      '<span class="badge badge-outline">手记实操</span>' +
      (!isBlank(r.direction) ? '<span class="badge ' + dirCls + '">' + escapeHtml(r.direction) + "</span>" : "") +
      (!isBlank(r.status) ? reviewStatusBadge(r.status) : "") +
      '<span class="review-date num-plain">' + escapeHtml(r.trade_date) + "</span>" +
      (!appState.offline ? '<div class="review-actions"><button type="button" class="btn btn-sm" data-edit-review="' + id + '">编辑</button><button type="button" class="btn btn-sm btn-ghost btn-danger" data-del-review="' + id + '">删除</button></div>' : "") +
      '</div><div class="review-figs"><span>仓位<b>' + escapeHtml(formatWeight(r.size_pct)) + "</b></span>" +
      (price > 0 ? "<span>参考均价<b>" + escapeHtml(formatPrice(price)) + "</b></span>" : "") +
      (showPnl ? "<span>盈亏<b>" + amtHtml(pnl, { signed: true, cls: toneClass(pnl) }) + "</b></span>" : "") +
      "</div>" + (notes ? '<dl class="review-notes">' + notes + "</dl>" : "") +
      (tags ? '<div class="essay-tags">' + tags + "</div>" : "") + "</article>";
  }

  function signalPlanCardHtml(p, linkedMemo) {
    const dirCls = p.stance_sign > 0 ? "badge-bull" : "badge-warn";
    const setupCls = p.setup_code === "overheat_trim" ? "badge-warn" : p.setup_code === "oversold_rebound" ? "badge-accent" : "badge-bull";
    const ret = toNum(p.live_return_pct);
    const retCls = Number.isFinite(ret) && ret !== 0 ? (ret > 0 ? "is-bull" : "is-bear") : "";
    const retStr = Number.isFinite(ret) ? " (" + (ret >= 0 ? "+" : "") + ret.toFixed(2) + "%)" : "";
    const pidEsc = escapeHtml(p.plan_id);
    const adoptHtml = linkedMemo
      ? reviewStatusBadge(linkedMemo.status || "已记实操")
      : '<span class="badge badge-outline">未采用·自动跟踪</span>';
    const sr = p.short_review || {};
    const mr = p.mid_review || {};

    let memoHtml = "";
    if (linkedMemo) {
      const mPrice = toNum(linkedMemo.price_usd);
      const mPnl = toNum(linkedMemo.pnl_usd);
      const ridEsc = escapeHtml(linkedMemo.id);
      memoHtml = '<div class="sigplan-memo-box"><div class="sigplan-rev-head"><span>实操备忘 · ' +
        escapeHtml(linkedMemo.status || "已记录") + "（仓位 " + escapeHtml(formatWeight(linkedMemo.size_pct)) +
        (mPrice > 0 ? " · 成本 " + amtHtml(mPrice) : "") +
        (Number.isFinite(mPnl) && mPnl !== 0 ? " · 盈亏 " + amtHtml(mPnl, { signed: true, cls: toneClass(mPnl) }) : "") +
        "）</span>" +
        (!appState.offline ? '<span class="review-actions"><button type="button" class="btn btn-sm" data-edit-review="' + ridEsc + '">编辑</button><button type="button" class="btn btn-sm btn-ghost btn-danger" data-del-review="' + ridEsc + '">删除</button></span>' : "") +
        "</div>" +
        (!isBlank(linkedMemo.execution_note) ? '<p class="sigplan-rev-sum"><b>执行计划：</b>' + escapeHtml(linkedMemo.execution_note) + "</p>" : "") +
        (!isBlank(linkedMemo.lesson) ? '<p class="sigplan-rev-sum"><b>复盘补充：</b>' + escapeHtml(linkedMemo.lesson) + "</p>" : "") +
        "</div>";
    }

    return '<article class="review-card sigplan-card" data-plan-id="' + pidEsc + '">' +
      '<div class="review-head">' +
      '<span class="review-symbol">' + escapeHtml(p.symbol) + (p.name && p.name !== p.symbol ? " · " + escapeHtml(p.name) : "") + "</span>" +
      '<span class="badge ' + setupCls + '">' + escapeHtml(p.setup_label || "日线信号") + "</span>" +
      '<span class="badge ' + dirCls + '">' + escapeHtml(p.direction || "做多") + "</span>" +
      adoptHtml +
      '<span class="review-date num-plain">' + escapeHtml(p.signal_date) + " · " + escapeHtml(p.horizon_label || "5–20交易日") + "</span>" +
      (!appState.offline
        ? '<div class="review-actions"><button type="button" class="btn btn-sm" data-memo-plan="' + pidEsc + '">' + (linkedMemo ? "编辑实操" : "写实操备忘") + "</button></div>"
        : "") +
      "</div>" +
      '<div class="review-figs">' +
      '<span>信号价<b>' + escapeHtml(formatPrice(p.entry_price)) + "</b></span>" +
      '<span>现价<b class="num-plain ' + retCls + '">' + escapeHtml(formatPrice(p.current_price)) + escapeHtml(retStr) + "</b></span>" +
      '<span>目标参考<b>' + escapeHtml(formatPrice(p.target_price)) + "</b></span>" +
      '<span>失效保护<b>' + escapeHtml(formatPrice(p.stop_price)) + "</b></span>" +
      "</div>" +
      (!isBlank(p.signal_reason) ? '<p class="sigplan-plan-text"><b>触发依据</b>' + escapeHtml(p.signal_reason) + "</p>" : "") +
      (!isBlank(p.action_plan) ? '<p class="sigplan-plan-text"><b>布局建议</b>' + escapeHtml(p.action_plan) + "</p>" : "") +
      (!isBlank(p.invalidation) ? '<p class="sigplan-plan-text"><b>失效条件</b>' + escapeHtml(p.invalidation) + "</p>" : "") +
      (!isBlank(p.context_note) ? '<p class="sigplan-plan-text"><b>市场环境</b>' + escapeHtml(p.context_note) + "</p>" : "") +
      '<div class="sigplan-reviews-grid">' +
      '<div class="sigplan-rev-box"><div class="sigplan-rev-head"><span>短期复盘 · 5交易日</span>' + reviewOutcomeBadge(sr, "第0/5日") + '</div><p class="sigplan-rev-sum">' + escapeHtml(sr.summary || "等待满 5 个交易日自动复盘") + "</p></div>" +
      '<div class="sigplan-rev-box"><div class="sigplan-rev-head"><span>中期复盘 · 20交易日</span>' + reviewOutcomeBadge(mr, "第0/20日") + '</div><p class="sigplan-rev-sum">' + escapeHtml(mr.summary || "等待满 20 个交易日检验反弹/反转") + "</p></div>" +
      "</div>" +
      memoHtml +
      "</article>";
  }

  function renderReviews() {
    if (!dom.reviewList) return;
    const names = positionNameMap();
    const plans = arr(state().signal_plans);
    const pstats = state().signal_plan_stats || {};
    if (dom.signalPlanStatsBar) {
      if (plans.length) {
        const sDone = Number(pstats.short_completed || 0);
        const sHit = Number(pstats.short_hits || 0);
        const mDone = Number(pstats.mid_completed || 0);
        const mHit = Number(pstats.mid_hits || 0);
        dom.signalPlanStatsBar.hidden = false;
        dom.signalPlanStatsBar.innerHTML =
          '<span>日线级别信号 <b>' + (pstats.total || plans.length) + "</b> 条</span>" +
          '<span>跟踪中 <b>' + (pstats.active_count != null ? pstats.active_count : plans.length) + "</b></span>" +
          '<span>5日短期兑现 <b>' + sHit + "/" + sDone + "</b></span>" +
          '<span>20日中期兑现 <b>' + mHit + "/" + mDone + "</b></span>" +
          '<span class="sub">未采用信号亦自动执行 T+5 / T+20 复盘跟踪</span>';
      } else {
        dom.signalPlanStatsBar.hidden = true;
        dom.signalPlanStatsBar.innerHTML = "";
      }
    }

    const realMemos = realUserReviews();
    const memoByPlan = {};
    realMemos.forEach((r) => {
      if (!isBlank(r.position_id) && !memoByPlan[r.position_id]) {
        memoByPlan[r.position_id] = r;
      }
    });

    const f = appState.reviewStatus || "all";
    const filteredPlans = plans.filter((p) => {
      const linked = memoByPlan[p.plan_id] || null;
      const isMidDone = p.mid_review && p.mid_review.status === "completed";
      const isShortDone = p.short_review && p.short_review.status === "completed";
      if (f === "active") return !isMidDone;
      if (f === "reviewed") return isShortDone || isMidDone;
      if (f === "adopted") return Boolean(linked);
      return true;
    });
    const standaloneMemos = (f === "all" || f === "adopted")
      ? realMemos.filter((r) => isBlank(r.position_id) || !plans.some((p) => p.plan_id === r.position_id))
      : [];

    if (!filteredPlans.length && !standaloneMemos.length) {
      dom.reviewList.innerHTML = '<p class="empty-hint">' +
        (plans.length || realMemos.length
          ? "当前筛选下没有信号或实操记录"
          : (appState.offline ? "共享离线快照未公开私人实操备忘" : "暂无日线信号或实操备忘，点击「+ 记实操备忘」开始记录")) +
        "</p>";
      return;
    }

    dom.reviewList.innerHTML =
      filteredPlans.map((p) => signalPlanCardHtml(p, memoByPlan[p.plan_id] || null)).join("") +
      standaloneMemos.map((r) => standaloneReviewCardHtml(r, names)).join("");
  }

  function ensureOption(select, value) {
    if (!select || isBlank(value)) return;
    if (!Array.from(select.options).some((o) => o.value === value)) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value;
      select.appendChild(opt);
    }
    select.value = value;
  }

  function openReviewForm(row) {
    if (appState.offline) return showToast("共享离线快照只读，禁止新建或编辑实操备忘", "error");
    const r = row || appState.drafts.review || {};
    dom.rvId.value = r.id ? String(r.id) : "";
    if (dom.rvRev) dom.rvRev.value = r.rev != null ? String(r.rev) : "";
    if (dom.rvPlanId) dom.rvPlanId.value = r.position_id ? String(r.position_id) : "";
    dom.rvDate.value = r.trade_date || todayBjt();
    dom.rvSymbol.value = r.symbol || "";
    if (r.direction) ensureOption(dom.rvDirection, r.direction);
    else dom.rvDirection.selectedIndex = 0;
    dom.rvSize.value = String(Number.isFinite(toNum(r.size_pct)) ? toNum(r.size_pct) : 0);
    dom.rvPrice.value = String(Number.isFinite(toNum(r.price_usd)) ? toNum(r.price_usd) : 0);
    dom.rvPnl.value = String(Number.isFinite(toNum(r.pnl_usd)) ? toNum(r.pnl_usd) : 0);
    if (r.status) ensureOption(dom.rvStatus, r.status);
    else dom.rvStatus.selectedIndex = 0;
    dom.rvThesis.value = r.thesis || "";
    dom.rvExec.value = r.execution_note || "";
    dom.rvLesson.value = r.lesson || "";
    appState.editingReviewTags = arr(r.tags);
    dom.rvSaveBtn.textContent = r.id ? "更新备忘" : "保存备忘";
    dom.reviewForm.hidden = false;
    dom.reviewForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
    (dom.rvPlanId && dom.rvPlanId.value && !r.id ? dom.rvExec : dom.rvSymbol).focus({ preventScroll: true });
  }

  async function submitReview(event) {
    event.preventDefault();
    if (appState.offline) return showToast("共享离线快照只读", "error");
    const symbol = dom.rvSymbol.value.trim();
    const date = dom.rvDate.value;
    const size = Number(dom.rvSize.value || 0);
    const price = Number(dom.rvPrice.value || 0);
    const pnl = Number(dom.rvPnl.value || 0);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return showToast("请选择日期", "error");
    if (!symbol) { dom.rvSymbol.focus(); return showToast("请填写标的", "error"); }
    if (![size, price, pnl].every(Number.isFinite) || size < 0 || price < 0) return showToast("仓位与均价需为有效的非负数字", "error");
    const payload = {
      trade_date: date,
      symbol,
      direction: dom.rvDirection.value,
      size_pct: size,
      price_usd: price,
      pnl_usd: pnl,
      status: dom.rvStatus.value,
      thesis: dom.rvThesis.value.trim(),
      execution_note: dom.rvExec.value.trim(),
      lesson: dom.rvLesson.value.trim(),
      position_id: dom.rvPlanId ? dom.rvPlanId.value.trim() : "",
      tags: appState.editingReviewTags
    };
    if (dom.rvId.value) {
      payload.id = Number(dom.rvId.value);
      if (dom.rvRev && dom.rvRev.value !== "") payload.expected_rev = Number(dom.rvRev.value);
    }
    dom.rvSaveBtn.disabled = true;
    try {
      await journalMutate("saveReview", payload);
      appState.drafts.review = null;
      dom.reviewForm.hidden = true;
      renderReviews();
      renderCounts();
      showToast(payload.id ? "实操备忘已更新" : "实操备忘已保存", "ok");
    } catch (err) {
      showToast(err.message || "保存失败", "error");
    } finally {
      dom.rvSaveBtn.disabled = false;
    }
  }

  async function deleteReview(id) {
    if (appState.offline) return showToast("共享离线快照只读", "error");
    const row = arr(appState.journal.trade_reviews).find((r) => String(r.id) === String(id));
    if (!row) return;
    if (!window.confirm("确定删除这条复盘记录吗？删除后无法恢复。")) return;
    try {
      await journalMutate("deleteReview", { id: Number(id) });
      if (dom.rvId.value === String(id)) dom.reviewForm.hidden = true;
      renderReviews();
      renderCounts();
      showToast("复盘已删除", "ok");
    } catch (err) {
      showToast(err.message || "删除失败", "error");
    }
  }

  /* ---------- 随笔 ---------- */

  function renderEssays() {
    if (!dom.essayList) return;
    const list = arr(appState.journal.essays);
    if (!list.length) {
      dom.essayList.innerHTML = '<p class="empty-hint">' + (appState.offline ? "共享离线快照未公开私人随笔" : "还没有随笔，点击「+ 写随笔」记下今天的想法") + "</p>";
      return;
    }
    dom.essayList.innerHTML = list.map((e) => {
      const body = String(e.body_md || "");
      const isLong = body.length > 280 || body.split("\n").length > 7;
      const pinned = Boolean(Number(e.pinned)) || e.pinned === true;
      const tags = arr(e.tags).filter((t) => !isBlank(t)).map((t) => '<span class="badge badge-outline">#' + escapeHtml(t) + "</span>").join("");
      const id = escapeHtml(e.id);
      return '<article class="essay-card' + (pinned ? " is-pinned" : "") + '"><div class="essay-head">' +
        (pinned ? '<span class="badge badge-accent">置顶</span>' : "") +
        '<h3 class="essay-title">' + escapeHtml(e.title) + "</h3>" +
        (!isBlank(e.mood) ? '<span class="badge">' + escapeHtml(e.mood) + "</span>" : "") +
        '<span class="essay-date num-plain">' + escapeHtml(shortStamp(e.updated_at || e.created_at)) + "</span>" +
        (!appState.offline
          ? '<div class="essay-actions"><button type="button" class="btn btn-sm" data-pin-essay="' + id + '">' + (pinned ? "取消置顶" : "置顶") + '</button><button type="button" class="btn btn-sm" data-edit-essay="' + id + '">编辑</button><button type="button" class="btn btn-sm btn-ghost btn-danger" data-del-essay="' + id + '">删除</button></div>'
          : "") +
        "</div>" + (tags ? '<div class="essay-tags">' + tags + "</div>" : "") +
        '<div class="essay-body prose' + (isLong ? " is-long" : "") + '">' + renderMarkdown(body) + "</div>" +
        (isLong ? '<div class="essay-more"><button type="button" class="btn-link" data-expand>展开全文</button></div>' : "") + "</article>";
    }).join("");
  }

  function findEssay(id) {
    return arr(appState.journal.essays).find((e) => String(e.id) === String(id)) || null;
  }

  function openEssayForm(row) {
    if (appState.offline) return showToast("共享离线快照只读", "error");
    const e = row || appState.drafts.essay || {};
    dom.esId.value = e.id ? String(e.id) : "";
    if (dom.esRev) dom.esRev.value = e.rev != null ? String(e.rev) : "";
    dom.esTitle.value = e.title || "";
    if (e.mood) ensureOption(dom.esMood, e.mood);
    else dom.esMood.selectedIndex = 0;
    dom.esTags.value = Array.isArray(e.tags) ? e.tags.join(", ") : (e.tags || "");
    dom.esBody.value = e.body_md || "";
    dom.esPinned.checked = Boolean(Number(e.pinned)) || e.pinned === true;
    dom.esSaveBtn.textContent = e.id ? "更新随笔" : "保存随笔";
    dom.essayForm.hidden = false;
    dom.essayForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
    dom.esTitle.focus({ preventScroll: true });
  }

  function essayPayload(e, overrides) {
    const out = Object.assign({
      id: Number(e.id),
      title: e.title || "",
      mood: e.mood || "冷静",
      tags: arr(e.tags),
      body_md: e.body_md || "",
      pinned: Boolean(Number(e.pinned)) || e.pinned === true
    }, overrides || {});
    if (e.rev != null) out.expected_rev = Number(e.rev);
    return out;
  }

  async function submitEssay(event) {
    event.preventDefault();
    if (appState.offline) return showToast("共享离线快照只读", "error");
    const title = dom.esTitle.value.trim();
    const body = dom.esBody.value.trim();
    if (!title) { dom.esTitle.focus(); return showToast("请填写标题", "error"); }
    if (!body) { dom.esBody.focus(); return showToast("请填写正文", "error"); }
    const payload = {
      title,
      mood: dom.esMood.value,
      tags: dom.esTags.value.split(/[,，、]/).map((t) => t.trim()).filter(Boolean),
      body_md: body,
      pinned: dom.esPinned.checked
    };
    if (dom.esId.value) {
      payload.id = Number(dom.esId.value);
      if (dom.esRev && dom.esRev.value !== "") payload.expected_rev = Number(dom.esRev.value);
    }
    dom.esSaveBtn.disabled = true;
    try {
      await journalMutate("saveEssay", payload);
      appState.drafts.essay = null;
      dom.essayForm.hidden = true;
      renderEssays();
      renderCounts();
      showToast(payload.id ? "随笔已更新" : "随笔已保存", "ok");
    } catch (err) {
      showToast(err.message || "保存失败", "error");
    } finally {
      dom.esSaveBtn.disabled = false;
    }
  }

  async function togglePinEssay(id) {
    if (appState.offline) return showToast("共享离线快照只读", "error");
    const e = findEssay(id);
    if (!e) return;
    try {
      await journalMutate("saveEssay", essayPayload(e, { pinned: !(Boolean(Number(e.pinned)) || e.pinned === true) }));
      renderEssays();
    } catch (err) { showToast(err.message || "操作失败", "error"); }
  }

  async function deleteEssay(id) {
    if (appState.offline) return showToast("共享离线快照只读", "error");
    if (!findEssay(id)) return;
    if (!window.confirm("确定删除这篇随笔吗？删除后无法恢复。")) return;
    try {
      await journalMutate("deleteEssay", { id: Number(id) });
      if (dom.esId.value === String(id)) dom.essayForm.hidden = true;
      renderEssays();
      renderCounts();
      showToast("随笔已删除", "ok");
    } catch (err) { showToast(err.message || "删除失败", "error"); }
  }

  /* ---------- 推送中心（脱敏 Card 2.0 & 微信 Markdown） & Meta Muse ---------- */

  function httpOrigin() {
    return window.location.protocol.startsWith("http") ? window.location.origin : "";
  }

  function buildSanitizedPreviewData() {
    const s = state();
    const risk = s.risk_gate || {};
    const wsSummary = workspaceSummary();
    const sum = wsSummary || {};
    const netDir = toNum(sum.net_direction_pct);
    const effLev = toNum(sum.effective_leverage);
    const cap = toNum(risk.long_risk_cap);
    const dirStr = Number.isFinite(netDir) ? formatSignedPct(netDir) : wsSummary ? "不可计算" : (risk.summary_exposure ? risk.summary_exposure.split("·")[0].trim() : "0%");
    const levStr = Number.isFinite(effLev) ? effLev.toFixed(2) + "x" : wsSummary ? "不可计算" : (Number.isFinite(cap) ? cap.toFixed(1) + "x 上限" : "1.0x");
    const modeTitle = appState.pushScenario === "wind_alert" ? "持仓逆风预警" : appState.pushScenario === "deviation" ? "持仓偏离巡检" : "每日决策简报";

    const sigs = arr(s.signals).filter((x) => x.importance === "高" || x.selected).slice(0, 3);
    const posLines = arr(s.positions).map((p) => "- " + p.name + "：" + (p.state || "") + " (" + formatWeight(positionPct(p)) + ")");
    const md = [
      "### 【" + modeTitle + "】" + (risk.label || ""),
      "- 净方向性敞口：" + dirStr + " | 有效杠杆：" + levStr,
      "",
      "**策略配置占比**",
      ...posLines,
      "",
      "**重点信号**"
    ].concat(sigs.map((x, i) => (i + 1) + ". [" + (x.source || "") + "] " + (x.title || ""))).join("\n");

    return {
      title: modeTitle + " · " + (risk.label || "风险提示"),
      light: String(risk.light || "YELLOW").toUpperCase(),
      kpis: [
        { label: "净方向性敞口", val: dirStr },
        { label: "有效杠杆", val: levStr },
        { label: "风控状态", val: RISK_LIGHT_TEXT[String(risk.light || "").toUpperCase()] || "提示" }
      ],
      bullets: posLines.concat(sigs.map((x) => "• " + (x.title || ""))),
      wechat_md: md
    };
  }

  function renderPushVisual() {
    const p = appState.lastPushPayload || buildSanitizedPreviewData();
    const isCard2 = appState.pushPreviewFormat === "card2";
    if (dom.pushCard2Preview) {
      dom.pushCard2Preview.hidden = !isCard2;
      if (isCard2) {
        const kpis = arr(p.kpis);
        const bullets = arr(p.bullets);
        dom.pushCard2Preview.innerHTML =
          '<div class="card2-hdr" data-light="' + escapeHtml(p.light || "YELLOW") + '">' + escapeHtml(p.title || "宏观决策简报") + "</div>" +
          (kpis.length ? '<div class="card2-kpis">' + kpis.map((k) => '<div class="card2-kpi"><span>' + escapeHtml(k.label) + '</span><b>' + escapeHtml(k.val) + "</b></div>").join("") + "</div>" : "") +
          '<div class="card2-sec">' + (bullets.length ? bullets.map((b) => escapeHtml(b)).join("<br>") : escapeHtml(p.wechat_md || "")) + "</div>";
      }
    }
    if (dom.pushPreview) {
      dom.pushPreview.hidden = isCard2;
      dom.pushPreview.textContent = p.wechat_md || [p.title, p.text].filter((x) => !isBlank(x)).join("\n\n");
    }
  }

  function buildMuseMarkdown(st) {
    const s = st || {};
    const risk = s.risk_gate || {};
    const lines = ["# MacroCockpit 市场简报", "", "更新时间：" + (s.generated_at_bjt || "—"), "## 风险门控：" + (risk.label || "—"), String(risk.summary_exposure || "")];
    const reasons = gateReasons(risk);
    if (reasons.length) lines.push("", "依据：" + reasons.slice(0, 3).join("；"));
    const positions = arr(s.positions);
    lines.push("", "## 辅助仓位");
    if (positions.length) positions.slice(0, 8).forEach((p) => lines.push("- " + (p.name || "") + "：" + (p.state || "") + "，建议 " + formatWeight(positionPct(p))));
    const macro = validMacroItems().slice(0, 12);
    if (macro.length) {
      lines.push("", "## 宏观快照");
      macro.forEach((m) => lines.push("- " + (m.label || m.id) + ": " + m.value_display + (isBlank(m.as_of) ? "" : "（" + m.as_of + "）")));
    }
    const signals = arr(s.signals).filter((x) => x.selected || x.importance === "高").slice(0, 8);
    if (signals.length) {
      lines.push("", "## 重点信号");
      signals.forEach((x) => { const url = safeHttpUrl(x.url); lines.push(url ? "- [" + (x.title || "市场信号") + "](" + url + ")" : "- " + (x.title || "市场信号")); });
    }
    const base = httpOrigin();
    if (base) lines.push("", "[打开线上完整情报站](" + base + ")");
    return lines.join("\n").trim() + "\n";
  }

  function renderMuseUrls() {
    const base = httpOrigin();
    if (dom.museDigestUrl) dom.museDigestUrl.textContent = base + "/api/digest.md?topic=all";
    if (dom.museFeedUrl) dom.museFeedUrl.textContent = base + "/feed.xml?topic=all";
  }

  function renderPushChannels() {
    if (!dom.pushChannels) return;
    const rawCh = state().push_channels || {};
    const allKeys = Array.from(new Set(Object.keys(rawCh).concat(["feishu_dm", "wecom_bot", "wechat_push", "ntfy", "bark", "telegram", "webhook"])));
    if (!appState.pushChecked) {
      appState.pushChecked = new Set(allKeys.filter((k) => rawCh[k] && rawCh[k].configured && k === "feishu_dm"));
    }
    dom.pushChannels.innerHTML = allKeys.filter((k) => rawCh[k] || CHANNEL_DEFAULT_LABELS[k]).map((k) => {
      const ch = rawCh[k] || {};
      const on = Boolean(ch.configured);
      const label = ch.label || CHANNEL_DEFAULT_LABELS[k] || k;
      return '<label class="channel-row' + (on ? "" : " is-off") + '">' +
        '<input type="checkbox" data-channel="' + escapeHtml(k) + '"' + (on && appState.pushChecked.has(k) ? " checked" : "") + (on ? "" : " disabled") + ">" +
        "<span>" + escapeHtml(label) + "</span>" +
        '<span class="badge ' + (on ? "badge-bull" : "") + '">' + (on ? "已配置" : "未配置") + "</span></label>";
    }).join("");
  }

  function selectedChannels() {
    return Array.from(dom.pushChannels.querySelectorAll("input[data-channel]:checked")).map((el) => el.getAttribute("data-channel"));
  }

  function syncLiveBtn() {
    if (dom.pushLiveBtn) dom.pushLiveBtn.disabled = appState.offline || !appState.pushPreviewed || !selectedChannels().length;
  }

  function channelLabel(key) {
    const ch = (state().push_channels || {})[key];
    return (ch && ch.label) || CHANNEL_DEFAULT_LABELS[key] || key;
  }

  function summarizeResults(results) {
    return Object.keys(results || {}).map((k) => {
      const r = results[k] || {};
      return channelLabel(k) + "：" + (r.detail || (r.ok ? "成功" : "失败"));
    }).join("；");
  }

  function openPushDrawer() {
    renderPushChannels();
    syncLiveBtn();
    appState.lastPushPayload = buildSanitizedPreviewData();
    renderPushVisual();
    openDrawer(dom.pushDrawer, dom.closePushBtn);
  }

  async function pushPreview() {
    if (appState.offline) {
      appState.lastPushPayload = buildSanitizedPreviewData();
      renderPushVisual();
      dom.pushHint.textContent = "离线快照仅支持脱敏预览，无法外发推送";
      return;
    }
    if (appState.busy.push) return;
    appState.busy.push = true;
    dom.pushPreviewBtn.disabled = true;
    dom.pushHint.textContent = "正在生成脱敏试运行预览…";
    try {
      const res = await apiJson("/api/push", {
        method: "POST",
        body: { dry_run: true, mode: appState.pushScenario, channels: selectedChannels() }
      });
      const payload = (res && res.payload) || {};
      const fallback = buildSanitizedPreviewData();
      appState.lastPushPayload = {
        title: payload.title || fallback.title,
        light: fallback.light,
        kpis: fallback.kpis,
        bullets: (payload.text || "").split("\n").filter((l) => l.trim()).slice(0, 8),
        wechat_md: payload.wechat_markdown || [payload.title, payload.text].filter((x) => !isBlank(x)).join("\n\n") || fallback.wechat_md
      };
      renderPushVisual();
      appState.pushPreviewed = true;
      const summary = summarizeResults(res && res.results);
      dom.pushHint.textContent = "试运行完成（已隐去绝对金额）" + (summary ? "：" + summary : "");
    } catch (err) {
      appState.pushPreviewed = false;
      dom.pushHint.textContent = err.message || "试运行失败";
      showToast(err.message || "试运行失败", "error");
    } finally {
      appState.busy.push = false;
      dom.pushPreviewBtn.disabled = false;
      syncLiveBtn();
    }
  }

  async function pushLive() {
    const channels = selectedChannels();
    if (appState.offline || !appState.pushPreviewed || !channels.length || appState.busy.push) return;
    appState.busy.push = true;
    dom.pushLiveBtn.disabled = true;
    dom.pushHint.textContent = "正在发送…";
    try {
      const res = await apiJson("/api/push", {
        method: "POST",
        body: { live: true, mode: appState.pushScenario, channels }
      });
      const results = (res && res.results) || {};
      const okAll = Object.keys(results).length > 0 && Object.keys(results).every((k) => results[k] && results[k].ok);
      dom.pushHint.textContent = (okAll ? "已发送：" : "发送结果：") + summarizeResults(results);
      showToast(okAll ? "推送已发送" : "部分通道发送失败", okAll ? "ok" : "error");
    } catch (err) {
      dom.pushHint.textContent = err.message || "发送失败";
      showToast(err.message || "发送失败", "error");
    } finally {
      appState.busy.push = false;
      appState.pushPreviewed = false;
      syncLiveBtn();
    }
  }

  /* ---------- 刷新 / 登录状态 / 共享离线只读守卫 ---------- */

  async function reloadLatestSnapshot() {
    const prevGen = Number(appState.lastGeneration) || 0;
    const st = await apiJson("/api/state");
    let journal = st && st.journal;
    if (!journal) {
      try { journal = await apiJson("/api/journal"); } catch (_) { journal = {}; }
    }
    appState.raw = st || {};
    appState.lastGeneration = Number((st && st.generation) || 0) || prevGen;
    appState.journal = normalizeJournal(journal);
    await refreshWorkspaceOnly();
    renderAll();
    return {
      prevGen,
      nextGen: appState.lastGeneration,
      changed: appState.lastGeneration > prevGen
    };
  }

  async function refreshState() {
    if (appState.offline) return showToast("共享离线快照只读，请在本地服务刷新数据");
    if (appState.busy.refresh) return;
    appState.busy.refresh = true;
    dom.refreshBtn.classList.add("is-busy");
    dom.refreshBtn.textContent = "刷新中…";
    try {
      if (appState.serverRole === "consumer") {
        const res = await reloadLatestSnapshot();
        showToast(res.changed ? "已同步最新快照 (#" + res.nextGen + ")" : "当前已是最新快照", "ok");
      } else {
        try {
          const prevGen = Number(appState.lastGeneration) || 0;
          const st = await apiJson("/api/refresh", { method: "POST", body: { refresh_fred: false } });
          let journal = st && st.journal;
          if (!journal) journal = await apiJson("/api/journal");
          appState.raw = st || {};
          appState.lastGeneration = Number((st && st.generation) || 0) || prevGen;
          appState.journal = normalizeJournal(journal);
          await refreshWorkspaceOnly();
          renderAll();
          showToast("数据已刷新", "ok");
        } catch (err) {
          if (err && err.status === 403) {
            appState.serverRole = "consumer";
            const res = await reloadLatestSnapshot();
            showToast(res.changed ? "已同步最新快照 (#" + res.nextGen + ")" : "当前已是最新快照", "ok");
          } else {
            throw err;
          }
        }
      }
    } catch (err) {
      showToast(err.message || "刷新失败", "error");
    } finally {
      appState.busy.refresh = false;
      dom.refreshBtn.classList.remove("is-busy");
      dom.refreshBtn.textContent = "刷新";
    }
  }

  async function checkHealth() {
    try {
      const h = await apiJson("/api/health");
      if (h && typeof h === "object") {
        if (h.role) appState.serverRole = String(h.role);
        if (Number.isFinite(Number(h.generation)) && !appState.lastGeneration) {
          appState.lastGeneration = Number(h.generation);
        }
      }
      if (dom.logoutBtn) dom.logoutBtn.hidden = !(h && h.auth_enabled === true) || appState.offline;
      return h || null;
    } catch (_) {
      if (dom.logoutBtn) dom.logoutBtn.hidden = true;
      return null;
    }
  }

  function isUserEditingOrDrawerOpen() {
    if (document.querySelector(".drawer:not([hidden]), .composer:not([hidden])")) return true;
    const ae = document.activeElement;
    return Boolean(ae && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName));
  }

  async function pollLatestIfDue() {
    if (appState.offline || document.hidden || appState.busy.refresh || isUserEditingOrDrawerOpen()) return;
    appState.lastPollAt = Date.now();
    try {
      const h = await apiJson("/api/health");
      if (!h || typeof h !== "object") return;
      if (h.role) appState.serverRole = String(h.role);
      const nextGen = Number(h.generation) || 0;
      if (nextGen > (Number(appState.lastGeneration) || 0) && !isUserEditingOrDrawerOpen()) {
        await reloadLatestSnapshot();
      }
    } catch (_) { /* silent background poll */ }
  }

  function startAutoRefreshPoll() {
    if (appState.pollTimerId || appState.offline) return;
    appState.lastPollAt = Date.now();
    appState.pollTimerId = window.setInterval(pollLatestIfDue, 60000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && Date.now() - (appState.lastPollAt || 0) >= 60000) {
        pollLatestIfDue();
      }
    });
  }

  async function logout() {
    try { await apiJson("/api/logout", { method: "POST", body: {} }); } catch (_) { /* ignore */ }
    window.location.href = "/login";
  }

  function applyModeUi() {
    const ro = Boolean(appState.offline);
    const isCons = Boolean((appState.drCapabilities && appState.drCapabilities.consumer_mode) || appState.serverRole === "consumer");
    if (dom.exportOpmlBtn) dom.exportOpmlBtn.hidden = ro;
    if (dom.openIngestDrBtn) dom.openIngestDrBtn.hidden = ro || isCons;
    if (dom.openAddPosBtn) dom.openAddPosBtn.hidden = ro;
    if (dom.openImportBtn) dom.openImportBtn.hidden = ro;
    if (dom.newReviewBtn) dom.newReviewBtn.hidden = ro;
    if (dom.newEssayBtn) dom.newEssayBtn.hidden = ro;
    if (dom.saveNoteBtn) dom.saveNoteBtn.disabled = ro;
    if (dom.articleStatusSeg) {
      dom.articleStatusSeg.querySelectorAll("button").forEach((b) => { b.disabled = ro; });
    }
    if (ro) {
      dom.refreshBtn.setAttribute("aria-disabled", "true");
      dom.refreshBtn.title = "共享离线快照只读";
      dom.pushLiveBtn.disabled = true;
      dom.pushHint.textContent = "离线快照仅支持脱敏预览，无法发送推送";
      dom.logoutBtn.hidden = true;
      if (dom.journalModeHint) dom.journalModeHint.textContent = "共享离线快照 · 只读模式";
    } else {
      dom.refreshBtn.removeAttribute("aria-disabled");
      dom.refreshBtn.title = "重新生成驾驶舱数据";
      if (dom.journalModeHint) dom.journalModeHint.textContent = "已同步至本地数据库";
    }
  }

  /* ---------- 渲染总入口 ---------- */

  function renderAll() {
    renderHeader();
    renderFocusStrip();
    renderTodayOverviewBar();
    renderExecGate();
    renderExecPositions();
    renderGlance();
    renderTodaySentimentCard();
    renderCurationRulesPanel();
    renderSectorRibbon();
    renderNewsModeSwitch();
    renderSignals();
    renderTodaySideCards();
    renderEquities();
    renderRadar();
    renderPortfolioView();
    renderResearch();
    renderMarketSynthesis();
    renderForumSentiment();
    renderMacroGroups();
    renderCorrMatrix();
    renderPairs();
    renderKol();
    renderArticles();
    renderSources();
    renderJournalGate();
    renderJournalPositions();
    renderReviews();
    renderEssays();
    renderPushChannels();
    renderCounts();
    if (appState.drawerArticle) syncArticleStatusUi();
  }

  /* ---------- 事件绑定 ---------- */

  function onEnter(input, handler) {
    if (!input) return;
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !isImeEnter(e)) {
        e.preventDefault();
        handler();
      }
    });
  }

  function commitDailyKeyword() {
    appState.daily.keyword = dom.keywordInput.value.trim();
    resetSignalLimit();
    renderSectorRibbon();
    renderSignals();
  }

  function commitKolKeyword() {
    appState.kol.keyword = dom.kolSearchInput.value.trim();
    renderKol();
  }

  function commitArticleKeyword() {
    appState.articles.keyword = dom.articleSearchInput.value.trim();
    renderArticles();
  }

  function clearFilters(scope) {
    if (scope === "daily") {
      resetDailyFilters();
    } else if (scope === "kol") {
      appState.kol = { topic: "all", stance: "all", keyword: "", view: appState.kol.view || "feed", highWinOnly: false };
      dom.kolSearchInput.value = "";
      setActive(dom.kolStanceChips, "data-stance", "all");
      renderKol();
    } else if (scope === "articles") {
      appState.articles = { status: "all", source: "all", keyword: "" };
      dom.articleSearchInput.value = "";
      setActive(dom.articleStatusTabs, "data-astatus", "all");
      setActive(dom.articleSourceChips, "data-asource", "all");
      renderArticles();
    }
  }

  function toggleExpand(btn) {
    const card = btn.closest(".signal, .tl-row, .tl-card, .essay-card");
    if (!card) return;
    const on = card.classList.toggle("is-expanded");
    btn.textContent = on ? "收起" : "展开全文";
  }

  function handleClick(e) {
    const t = e.target;
    if (!(t instanceof Element)) return;
    const hit = (sel) => t.closest(sel);
    let el;

    // Close open signal popover or stock suggest dropdown if clicking outside
    if (!hit(".sig-menu-wrap") && appState.openSignalMenu) {
      appState.openSignalMenu = "";
      renderSignals();
    }
    if (!hit(".search-suggest-wrap") && dom.stockSuggest && !dom.stockSuggest.hidden) {
      hideStockSuggest();
    }

    if ((el = hit(".view-tab[data-view]"))) return setView(el.getAttribute("data-view"));
    if ((el = hit("[data-mtab]"))) {
      const tab = el.getAttribute("data-mtab");
      if (tab === appState.view) return window.scrollTo({ top: 0, behavior: "smooth" });
      return setView(tab);
    }
    if ((el = hit("[data-psub]"))) return setSubTab("portfolio", el.getAttribute("data-psub"));
    if ((el = hit("[data-rsub]"))) {
      setSubTab("research", el.getAttribute("data-rsub"));
      try { window.history.replaceState(null, "", currentHash()); } catch (_) { /* ignore */ }
      return undefined;
    }
    if ((el = hit("[data-recsub]"))) {
      setSubTab("records", el.getAttribute("data-recsub"));
      try { window.history.replaceState(null, "", currentHash()); } catch (_) { /* ignore */ }
      return undefined;
    }
    if ((el = hit("[data-pick-stock]"))) return openStockResearch(el.getAttribute("data-pick-stock"), false);
    if ((el = hit("#stockMarketTabs [data-smarket]"))) {
      appState.stockMarket = el.getAttribute("data-smarket") || "all";
      renderResearch();
      syncUrlHash();
      return;
    }
    if ((el = hit("[data-open-stock]"))) return openStockResearch(el.getAttribute("data-open-stock"), true);
    if ((el = hit("#stockRangeSeg [data-srange]"))) {
      appState.stockRange = el.getAttribute("data-srange") || "1y";
      const p = getActiveStock();
      if (p) renderStockChart(p);
      return;
    }
    if (hit("#stockSearchBtn")) return commitStockSearch();
    if (hit("#stockReviewBtn")) {
      const p = getActiveStock();
      setView("records", { sub: "reviews" });
      if (p && dom.reviewForm) {
        openReviewForm();
        dom.rvSymbol.value = p.symbol;
        dom.rvPrice.value = toNum(p.price) || 0;
        dom.rvThesis.value = assetMarket(p) === "crypto"
          ? "关注 " + p.symbol + " (30日波动 " + ((p.crypto_metrics || {}).vol_30d_ann_pct ?? "—") + "%)"
          : "关注 " + p.symbol + " (P/E " + fmtMetricVal((p.valuation || {}).pe_ttm, 1) + ")";
      }
      return;
    }
    if (hit("[data-signals-more]")) {
      appState.daily.visibleLimit = (Number(appState.daily.visibleLimit) || defaultSignalLimit()) + 20;
      return renderSignals();
    }
    if (hit("[data-signals-all]")) {
      appState.daily.visibleLimit = 500;
      return renderSignals();
    }
    if ((el = hit("[data-jump]"))) return setView(el.getAttribute("data-jump"));
    if ((el = hit("[data-close-drawer]"))) return closeDrawer(el.getAttribute("data-close-drawer"));
    if ((el = hit("[data-open-article]"))) return openArticle(el.getAttribute("data-open-article"));
    if ((el = hit("[data-expand]"))) return toggleExpand(el);
    if ((el = hit("[data-clear-filters]"))) return clearFilters(el.getAttribute("data-clear-filters"));
    if ((el = hit("[data-radar]"))) return jumpToSignal(el.getAttribute("data-radar"));

    // Currency toggle
    if ((el = hit("#currencySeg [data-currency]"))) return applyCurrency(el.getAttribute("data-currency"), true);

    if ((el = hit("#feedViewSwitch [data-fview]"))) {
      const fv = el.getAttribute("data-fview") === "wire" ? "wire" : "events";
      if (appState.daily.feedView === fv) return undefined;
      appState.daily.feedView = fv;
      safeStorageSet(STORAGE_KEYS.feedView, fv);
      resetSignalLimit();
      renderFeedViewSwitch();
      renderSectorRibbon();
      return renderSignals();
    }
    if ((el = hit("[data-event-expand]"))) {
      const eid = el.getAttribute("data-event-expand");
      const ver = Number(el.getAttribute("data-event-ver") || 1);
      if (eid) {
        if (appState.expandedEvents.has(eid)) appState.expandedEvents.delete(eid);
        else appState.expandedEvents.add(eid);
        markEventRead(eid, ver);
        renderSignals();
        const targetBtn = dom.signalList && dom.signalList.querySelector('[data-event-expand="' + eid.replace(/"/g, '\\"') + '"]');
        if (targetBtn && typeof targetBtn.focus === "function") targetBtn.focus({ preventScroll: true });
      }
      return undefined;
    }
    if ((el = hit("[data-event-link]"))) {
      markEventRead(el.getAttribute("data-event-link"), el.getAttribute("data-event-ver"));
      return undefined;
    }
    if ((el = hit("#newsModeSwitch [data-nmode]"))) {
      const m = el.getAttribute("data-nmode") === "timeline" ? "timeline" : "ranked";
      if (appState.daily.mode === m) return undefined;
      appState.daily.mode = m;
      safeStorageSet(STORAGE_KEYS.newsMode, m);
      resetSignalLimit();
      renderNewsModeSwitch();
      return renderSignals();
    }
    if ((el = hit("#sectorRibbon [data-sector]"))) {
      return enterSector(el.getAttribute("data-sector"), { scrollToTop: false });
    }
    if (hit("#backToOverviewBtn")) {
      return enterSector("all", { scrollToTop: true });
    }
    if ((el = hit("#subtopicChips [data-subtopic]"))) {
      appState.daily.subtopic = el.getAttribute("data-subtopic") || "all";
      resetSignalLimit();
      return renderSignals();
    }
    if ((el = hit("[data-jump-subtopic]"))) {
      const sec = el.getAttribute("data-jump-sector");
      const sub = el.getAttribute("data-jump-subtopic");
      return enterSector(sec, { subtopic: sub, scrollToTop: false });
    }

    if ((el = hit("[data-scope-jump]"))) {
      const targetScope = el.getAttribute("data-scope-jump") || "all";
      const hasHeld = hasHeldPositions();
      const nextScope = targetScope === "held" && !hasHeld ? "all" : targetScope;
      appState.daily.scope = appState.daily.scope === nextScope && nextScope === "held" ? "all" : nextScope;
      setActive(dom.signalScopeChips, "data-scope", appState.daily.scope);
      renderSectorRibbon();
      renderSignals();
      if (dom.signalsHeading) dom.signalsHeading.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return undefined;
    }

    // Signal scope, rules, card quick actions, score breakdown
    if ((el = hit("#signalScopeChips [data-scope]"))) {
      const sc = el.getAttribute("data-scope");
      if (sc === "rules") {
        if (dom.curationRulesPanel) dom.curationRulesPanel.hidden = !dom.curationRulesPanel.hidden;
        return undefined;
      }
      appState.daily.scope = sc;
      resetSignalLimit();
      setActive(dom.signalScopeChips, "data-scope", sc);
      renderSectorRibbon();
      return renderSignals();
    }
    if (hit("#closeRulesPanelBtn") && dom.curationRulesPanel) {
      dom.curationRulesPanel.hidden = true;
      return undefined;
    }
    if ((el = hit("[data-del-rule]"))) {
      return saveCurationRule("remove", null, el.getAttribute("data-del-rule"));
    }
    if ((el = hit("[data-sig-menu]"))) {
      const id = el.getAttribute("data-sig-menu");
      appState.openSignalMenu = appState.openSignalMenu === id ? "" : id;
      return renderSignals();
    }
    if ((el = hit("[data-quick-rule]"))) {
      const action = el.getAttribute("data-quick-rule");
      const kind = el.getAttribute("data-kind");
      const target = el.getAttribute("data-target");
      appState.openSignalMenu = "";
      if (!target) return undefined;
      const ruleId = "rule-" + Date.now();
      renderSignals();
      return saveCurationRule("put", { id: ruleId, kind, action, target, points: action === "mute" ? 8 : 0 }, null)
        .then((saved) => {
          if (saved) showUndoBar("已添加规则：" + target, () => saveCurationRule("remove", null, ruleId));
        });
    }
    if ((el = hit("[data-score-toggle]"))) {
      const id = el.getAttribute("data-score-toggle");
      appState.openScoreId = appState.openScoreId === id ? "" : id;
      return renderSignals();
    }
    if ((el = hit("#signalList .tl-row[data-sig-id]")) && !hit("button, input, select, .sig-menu-wrap, .sig-popover")) {
      const sigId = el.getAttribute("data-sig-id");
      const clickedLink = Boolean(hit(".tl-row-title a"));
      toggleSignalRead(sigId, clickedLink ? true : undefined);
      return undefined;
    }
    if (hit("#undoBtn") && typeof appState.lastUndoAction === "function") {
      const fn = appState.lastUndoAction;
      appState.lastUndoAction = null;
      if (dom.undoBar) dom.undoBar.hidden = true;
      return fn();
    }
    if (hit("#undoDismissBtn") && dom.undoBar) {
      dom.undoBar.hidden = true;
      appState.lastUndoAction = null;
      return undefined;
    }

    if ((el = hit("#polarityChips [data-pol]"))) {
      appState.daily.pol = el.getAttribute("data-pol");
      resetSignalLimit();
      setActive(dom.polarityChips, "data-pol", appState.daily.pol);
      renderSectorRibbon();
      return renderSignals();
    }
    if (hit("#highOnlyBtn")) {
      appState.daily.highOnly = !appState.daily.highOnly;
      resetSignalLimit();
      dom.highOnlyBtn.classList.toggle("is-active", appState.daily.highOnly);
      dom.highOnlyBtn.setAttribute("aria-pressed", String(appState.daily.highOnly));
      renderSectorRibbon();
      return renderSignals();
    }
    if (hit("#keywordApplyBtn")) return commitDailyKeyword();

    // Portfolio actions & mobile deriv cards
    if (hit("#openAddPosBtn")) return openPositionDrawer("spot", null);
    if (hit("#closePosDrawerBtn") || hit("#pfCancelBtn")) return closeDrawer("position");
    if ((el = hit("#pfKindSeg [data-pfkind]"))) return applyPositionFormKind(el.getAttribute("data-pfkind"));
    if (hit("#openImportBtn")) {
      if (appState.offline) return showToast("共享离线快照只读", "error");
      invalidateImportPreview();
      return openDrawer(dom.importDrawer, dom.impBody);
    }
    if (hit("#closeImportDrawerBtn")) return closeDrawer("import");
    if (hit("#impPreviewBtn")) return runImportPreview();
    if (hit("#impCommitBtn")) return commitImportPreview();
    if ((el = hit("[data-deriv-toggle]"))) {
      const card = el.closest(".deriv-card");
      if (card) card.classList.toggle("is-expanded");
      return undefined;
    }
    if ((el = hit("[data-edit-spot]"))) {
      const id = el.getAttribute("data-edit-spot");
      const row = arr((appState.workspace || {}).spot_balances).find((r) => String(r.id) === id);
      return openPositionDrawer("spot", row || null);
    }
    if ((el = hit("[data-del-spot]"))) return deletePortfolioRow("spot", el.getAttribute("data-del-spot"));
    if ((el = hit("[data-edit-deriv]"))) {
      const id = el.getAttribute("data-edit-deriv");
      const row = arr((appState.workspace || {}).derivative_positions).find((r) => String(r.id) === id);
      return openPositionDrawer("derivative", row || null);
    }
    if ((el = hit("[data-del-deriv]"))) return deletePortfolioRow("derivative", el.getAttribute("data-del-deriv"));

    if ((el = hit("#corrWindowSeg [data-corr]"))) {
      appState.corrWindow = el.getAttribute("data-corr") === "90d" ? "90d" : "30d";
      setActive(dom.corrWindowSeg, "data-corr", appState.corrWindow);
      return renderCorrMatrix();
    }
    if ((el = hit(".corr-cell[data-i]"))) {
      const i = Number(el.getAttribute("data-i")), j = Number(el.getAttribute("data-j"));
      const p = appState.corrPinned;
      appState.corrPinned = p && p.i === i && p.j === j ? null : { i, j };
      return restoreCorrReadout();
    }
    if ((el = hit("[data-pair-i]"))) {
      appState.corrPinned = { i: Number(el.getAttribute("data-pair-i")), j: Number(el.getAttribute("data-pair-j")) };
      restoreCorrReadout();
      return dom.corrReadout.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    if ((el = hit("#kolViewSwitch [data-kview]"))) {
      appState.kol.view = el.getAttribute("data-kview") === "scorecard" ? "scorecard" : "feed";
      return renderKol();
    }
    if (hit("#kolHighWinBtn")) {
      appState.kol.highWinOnly = !appState.kol.highWinOnly;
      return renderKol();
    }
    if ((el = hit("#kolScorecardWrap tr[data-kol-handle]"))) {
      const handle = el.getAttribute("data-kol-handle") || "";
      appState.kol.view = "feed";
      appState.kol.topic = "all";
      appState.kol.stance = "all";
      appState.kol.keyword = handle;
      if (dom.kolSearchInput) dom.kolSearchInput.value = handle;
      setActive(dom.kolStanceChips, "data-stance", "all");
      renderKol();
      if (dom.kolToolbar) dom.kolToolbar.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return undefined;
    }
    if ((el = hit("[data-jump-kol]"))) {
      const handle = String(el.getAttribute("data-jump-kol") || "").replace(/^@/, "");
      appState.kol.view = "feed";
      appState.kol.topic = "all";
      appState.kol.stance = "all";
      appState.kol.highWinOnly = false;
      appState.kol.keyword = handle;
      if (dom.kolSearchInput) dom.kolSearchInput.value = handle;
      setActive(dom.kolStanceChips, "data-stance", "all");
      setView("kol");
      renderKol();
      if (dom.kolToolbar) dom.kolToolbar.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return undefined;
    }
    if (hit("[data-jump-forum]")) {
      setView("sentiment");
      window.scrollTo({ top: 0, behavior: "smooth" });
      return undefined;
    }
    if ((el = hit("#sourceCategoryChips [data-scategory]"))) {
      appState.sources.category = el.getAttribute("data-scategory") || "all";
      return renderSources();
    }
    if ((el = hit("[data-topic]"))) {
      const topic = el.getAttribute("data-topic");
      appState.kol.topic = topic === appState.kol.topic && topic !== "all" ? "all" : topic;
      return renderKol();
    }
    if ((el = hit("#kolStanceChips [data-stance]"))) {
      appState.kol.stance = el.getAttribute("data-stance");
      setActive(dom.kolStanceChips, "data-stance", appState.kol.stance);
      return renderKol();
    }
    if (hit("#kolSearchBtn")) return commitKolKeyword();

    if ((el = hit("#articleStatusTabs [data-astatus]"))) {
      appState.articles.status = el.getAttribute("data-astatus");
      setActive(dom.articleStatusTabs, "data-astatus", appState.articles.status);
      return renderArticles();
    }
    if ((el = hit("#articleSourceChips [data-asource]"))) {
      appState.articles.source = el.getAttribute("data-asource");
      setActive(dom.articleSourceChips, "data-asource", appState.articles.source);
      return renderArticles();
    }
    if (hit("#articleSearchBtn")) return commitArticleKeyword();
    if (hit("#openIngestDrBtn")) {
      if (appState.offline) return showToast("共享离线快照只读", "error");
      return openDrWorkbench();
    }
    if ((el = hit("#drModeSwitch [data-drmode]"))) {
      return setDrMode(el.getAttribute("data-drmode"));
    }
    if (hit("#closeIngestDrBtn") || hit("#drCancelBtn") || hit("#drRunCancelBtn")) return closeDrawer("ingestDr");
    if ((el = hit("[data-copy-code]"))) {
      const wrap = el.closest(".code-copy-wrap");
      const codeEl = wrap ? wrap.querySelector("pre code, pre") : null;
      const text = codeEl ? codeEl.textContent : "";
      if (!text) return undefined;
      return copyText(text).then((ok) => showToast(ok ? "提问词已复制" : "复制失败", ok ? "ok" : "error"));
    }
    if ((el = hit("[data-run-dr-code]"))) {
      const wrap = el.closest(".code-copy-wrap");
      const codeEl = wrap ? wrap.querySelector("pre code, pre") : null;
      const text = codeEl ? codeEl.textContent.trim() : "";
      if (!text) return undefined;
      closeDrawer("article");
      return openDrWorkbench({ mode: "run", prompt: text, title: "", tickers: "" });
    }
    if ((el = hit("[data-set-status]"))) return setArticleStatus(el.getAttribute("data-set-status"), false);
    if (hit("#saveNoteBtn")) {
      const cur = appState.drawerArticle;
      return setArticleStatus(cur ? articleStatus(cur.filename) : "unread", true);
    }
    if (hit("#closeArticleBtn")) return closeDrawer("article");

    if ((el = hit("#reviewStatusChips [data-rstatus]"))) {
      appState.reviewStatus = el.getAttribute("data-rstatus");
      setActive(dom.reviewStatusChips, "data-rstatus", appState.reviewStatus);
      return renderReviews();
    }
    if (hit("#newReviewBtn")) return openReviewForm({ status: "计划跟进" });
    if (hit("#rvCancelBtn")) { appState.drafts.review = null; dom.reviewForm.hidden = true; return undefined; }
    if ((el = hit("[data-memo-plan]"))) {
      const pid = el.getAttribute("data-memo-plan") || "";
      const plan = arr(state().signal_plans).find((p) => p.plan_id === pid);
      const existing = arr(appState.journal.trade_reviews).find((r) => r.position_id === pid) || null;
      if (existing) return openReviewForm(existing);
      if (plan) {
        return openReviewForm({
          position_id: plan.plan_id,
          trade_date: plan.signal_date,
          symbol: plan.symbol,
          direction: plan.direction,
          price_usd: plan.entry_price,
          status: "未采用·观察",
          thesis: [plan.signal_reason, plan.action_plan].filter(Boolean).join("；")
        });
      }
      return undefined;
    }
    if ((el = hit("[data-edit-review]"))) {
      const id = el.getAttribute("data-edit-review");
      return openReviewForm(arr(appState.journal.trade_reviews).find((r) => String(r.id) === id) || null);
    }
    if ((el = hit("[data-del-review]"))) return deleteReview(el.getAttribute("data-del-review"));

    if (hit("#newEssayBtn")) return openEssayForm(null);
    if (hit("#esCancelBtn")) { appState.drafts.essay = null; dom.essayForm.hidden = true; return undefined; }
    if ((el = hit("[data-edit-essay]"))) return openEssayForm(findEssay(el.getAttribute("data-edit-essay")));
    if ((el = hit("[data-pin-essay]"))) return togglePinEssay(el.getAttribute("data-pin-essay"));
    if ((el = hit("[data-del-essay]"))) return deleteEssay(el.getAttribute("data-del-essay"));

    if (hit("#applyCapitalBtn")) return commitCapital();
    if (hit("#toggleAmtBtn")) return applyShowAmounts(!appState.showAmounts, true);
    if (hit("#themeToggleBtn")) return applyTheme(appState.theme === "dark" ? "light" : "dark", true);
    if (hit("#refreshBtn")) return refreshState();
    if (hit("#logoutBtn")) return logout();
    if (hit("#openPushBtn")) return openPushDrawer();
    if (hit("#closePushBtn")) return closeDrawer("push");
    if ((el = hit("#pushScenarioSeg [data-pmode]"))) {
      appState.pushScenario = el.getAttribute("data-pmode") || "daily";
      setActive(dom.pushScenarioSeg, "data-pmode", appState.pushScenario);
      appState.lastPushPayload = buildSanitizedPreviewData();
      renderPushVisual();
      return undefined;
    }
    if ((el = hit("#pushPreviewFormatSeg [data-pfmt]"))) {
      appState.pushPreviewFormat = el.getAttribute("data-pfmt") || "card2";
      setActive(dom.pushPreviewFormatSeg, "data-pfmt", appState.pushPreviewFormat);
      renderPushVisual();
      return undefined;
    }
    if (hit("#pushPreviewBtn")) return pushPreview();
    if (hit("#pushLiveBtn")) return pushLive();
    if ((el = hit("[data-copy-from]"))) {
      const src = document.getElementById(el.getAttribute("data-copy-from"));
      const text = src ? src.textContent.trim() : "";
      if (!text) return undefined;
      return copyText(text).then((ok) => showToast(ok ? "链接已复制" : "复制失败", ok ? "ok" : "error"));
    }
    if (hit("#copyMuseBtn")) {
      return copyText(buildMuseMarkdown(state())).then((ok) => showToast(ok ? "今日 Muse 简报已复制" : "复制失败", ok ? "ok" : "error"));
    }
    return undefined;
  }

  function bindEvents() {
    document.addEventListener("click", handleClick);

    onEnter(dom.capitalInput, commitCapital);
    onEnter(dom.keywordInput, commitDailyKeyword);
    onEnter(dom.stockSearchInput, commitStockSearch);
    onEnter(dom.kolSearchInput, commitKolKeyword);
    onEnter(dom.articleSearchInput, commitArticleKeyword);

    if (dom.stockSearchInput) {
      let suggestTimer = 0;
      let suggestSeq = 0;
      dom.stockSearchInput.addEventListener("input", () => {
        window.clearTimeout(suggestTimer);
        const q = (dom.stockSearchInput.value || "").trim();
        if (!q || appState.offline) return hideStockSuggest();
        const mySeq = ++suggestSeq;
        suggestTimer = window.setTimeout(async () => {
          try {
            const res = await apiJson("/api/stock/search?q=" + encodeURIComponent(q));
            if (mySeq === suggestSeq) renderStockSuggest((res && res.items) || []);
          } catch (_) {
            if (mySeq === suggestSeq) hideStockSuggest();
          }
        }, 250);
      });
    }

    if (dom.stockChartWrap) {
      dom.stockChartWrap.addEventListener("mousemove", (e) => handleChartPointer(e.clientX));
      dom.stockChartWrap.addEventListener("touchmove", (e) => {
        if (e.touches && e.touches[0]) handleChartPointer(e.touches[0].clientX);
      }, { passive: true });
      dom.stockChartWrap.addEventListener("mouseleave", () => {
        const line = document.getElementById("stockCursorLine");
        if (line) line.setAttribute("hidden", "");
      });
    }

    if (dom.ruleQuickForm) {
      dom.ruleQuickForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const target = (dom.ruleTargetInput ? dom.ruleTargetInput.value : "").trim();
        if (!target) return showToast("请输入规则目标", "error");
        const kind = dom.ruleKindSelect.value;
        const action = dom.ruleActionSelect.value;
        if (await saveCurationRule("put", { kind, action, target, points: action === "mute" ? 8 : 0 }, null)) {
          if (dom.ruleTargetInput.value.trim() === target) dom.ruleTargetInput.value = "";
          showToast("筛选规则已添加", "ok");
        }
      });
    }

    if (dom.reviewForm) {
      dom.reviewForm.addEventListener("submit", submitReview);
      dom.reviewForm.addEventListener("input", () => {
        if (!dom.reviewForm.hidden) {
          appState.drafts.review = {
            id: dom.rvId.value, rev: dom.rvRev ? dom.rvRev.value : "",
            trade_date: dom.rvDate.value, symbol: dom.rvSymbol.value,
            direction: dom.rvDirection.value, size_pct: dom.rvSize.value,
            price_usd: dom.rvPrice.value, pnl_usd: dom.rvPnl.value,
            status: dom.rvStatus.value, thesis: dom.rvThesis.value,
            execution_note: dom.rvExec.value, lesson: dom.rvLesson.value
          };
        }
      });
    }
    if (dom.essayForm) {
      dom.essayForm.addEventListener("submit", submitEssay);
      dom.essayForm.addEventListener("input", () => {
        if (!dom.essayForm.hidden) {
          appState.drafts.essay = {
            id: dom.esId.value, rev: dom.esRev ? dom.esRev.value : "",
            title: dom.esTitle.value, mood: dom.esMood.value,
            tags: dom.esTags.value, body_md: dom.esBody.value,
            pinned: dom.esPinned.checked
          };
        }
      });
    }
    if (dom.positionForm) dom.positionForm.addEventListener("submit", submitPosition);
    if (dom.ingestDrForm) dom.ingestDrForm.addEventListener("submit", submitIngestDr);
    if (dom.drRunForm) dom.drRunForm.addEventListener("submit", submitRunDr);
    for (const input of [dom.impBody, dom.impAccount, dom.impFormat, dom.impMode]) {
      input?.addEventListener("input", invalidateImportPreview);
      input?.addEventListener("change", invalidateImportPreview);
    }

    if (dom.pushChannels) {
      dom.pushChannels.addEventListener("change", (e) => {
        const input = e.target instanceof Element ? e.target.closest("input[data-channel]") : null;
        if (!input || !appState.pushChecked) return;
        const key = input.getAttribute("data-channel");
        if (input.checked) appState.pushChecked.add(key);
        else appState.pushChecked.delete(key);
        syncLiveBtn();
      });
    }

    if (dom.corrMatrix) {
      dom.corrMatrix.addEventListener("mouseover", (e) => {
        const cell = e.target instanceof Element ? e.target.closest(".corr-cell[data-i]") : null;
        if (cell) renderCorrReadout(Number(cell.getAttribute("data-i")), Number(cell.getAttribute("data-j")));
      });
      dom.corrMatrix.addEventListener("mouseleave", restoreCorrReadout);
    }

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeDrawer("article");
        closeDrawer("push");
        closeDrawer("position");
        closeDrawer("import");
        closeDrawer("ingestDr");
        return;
      }
      if ((e.key === "Enter" || e.key === " ") && e.target instanceof Element) {
        if (e.target.matches("#signalList .tl-row[data-sig-id]")) {
          e.preventDefault();
          toggleSignalRead(e.target.getAttribute("data-sig-id"));
        } else if (e.target.matches("[data-jump][role='button']")) {
          e.preventDefault();
          setView(e.target.getAttribute("data-jump"));
        }
      }
    });

    window.addEventListener("hashchange", () => {
      const h = window.location.hash.replace(/^#/, "");
      if (!h) return;
      const isTodayRoute = h === "today" || h.indexOf("today:") === 0;
      if (h === "today") {
        appState.daily.cat = "all";
        appState.daily.subtopic = "all";
      }
      setView(h, { keepScroll: false });
      if (appState.raw) {
        if (appState.view === "today" && isTodayRoute) {
          renderSectorRibbon();
          renderSignals();
        } else if (appState.view === "research" && appState.researchSub === "stocks") {
          renderResearch();
        }
      }
    });
  }

  /* ---------- 初始化 ---------- */

  function showLoadError(msg) {
    if (dom.generatedAt) dom.generatedAt.textContent = "数据未能载入";
    showToast(msg, "error");
  }

  async function loadInitialState() {
    const boot = window.__INITIAL_COCKPIT_STATE__;
    if (boot && typeof boot === "object") {
      appState.offline = true;
      appState.raw = boot;
      appState.journal = { trade_reviews: [], essays: [], article_status: {} };
      applyModeUi();
      renderAll();
      return;
    }
    if (window.location.protocol === "file:") {
      showLoadError("请使用打包后的离线文件或通过本地服务打开驾驶舱");
      return;
    }
    let st = null;
    let stateFetchFailed = false;
    try {
      st = await apiJson("/api/state");
    } catch (err) {
      stateFetchFailed = true;
      if (err && typeof err.status === "number" && err.status >= 500) {
        showLoadError(err.message || "服务端数据接口异常");
        return;
      }
    }
    if (!stateFetchFailed) {
      appState.offline = false;
      appState.raw = st || {};
      appState.lastGeneration = Number((st && st.generation) || 0) || 0;
      appState.journal = normalizeJournal(st && st.journal);
      applyModeUi();
      try {
        renderAll();
      } catch (renderErr) {
        showLoadError("界面渲染异常：" + (renderErr && renderErr.message ? renderErr.message : "请刷新重试"));
        return;
      }
      await refreshWorkspaceOnly();
      await checkHealth();
      await loadDrCapabilities();
      startAutoRefreshPoll();
      return;
    }
    try {
      const resp = await fetch("../data/cockpit_state.json", { cache: "no-store" });
      if (resp.ok) {
        const st = await resp.json();
        appState.offline = true;
        appState.raw = st || {};
        appState.journal = { trade_reviews: [], essays: [], article_status: {} };
        applyModeUi();
        renderAll();
        return;
      }
    } catch (_) { /* error toast */ }
    showLoadError("无法读取驾驶舱数据，请确认本地服务已启动");
  }

  function init() {
    cacheDom();
    loadPreferences();
    bindEvents();
    renderMuseUrls();
    const fromHash = window.location.hash.replace(/^#/, "");
    appState.view = "";
    setView(fromHash || "today", { keepScroll: true });
    loadInitialState();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
