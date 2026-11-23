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
    newsMode: "macro_cockpit_news_mode_v1"
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

  const appState = {
    raw: null,
    workspace: null,
    journal: { trade_reviews: [], essays: [], article_status: {} },
    offline: false,
    capitalBase: DEFAULT_CAPITAL,
    currency: "USD",
    showAmounts: true,
    theme: "light",
    view: "today",
    portfolioSub: "spot",
    researchSub: "stocks",
    recordsSub: "reviews",
    stockSymbol: "AAPL",
    stockRange: "1y",
    customStocks: {},
    daily: {
      scope: "all",
      cat: "all",
      subtopic: "all",
      pol: "all",
      highOnly: false,
      keyword: "",
      mode: (() => {
        try {
          return localStorage.getItem(STORAGE_KEYS.newsMode) === "timeline" ? "timeline" : "ranked";
        } catch (_e) {
          return "ranked";
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
    openSignalMenu: "",
    openScoreId: "",
    lastUndoAction: null,
    kol: { topic: "all", stance: "all", keyword: "" },
    articles: { status: "all", source: "all", keyword: "" },
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
      "todayOverviewBar", "sectorRibbon", "newsModeSwitch", "dailyGlance", "execGate", "execPositions",
      "signalScopeChips", "polarityChips", "highOnlyBtn", "keywordInput",
      "keywordApplyBtn", "dailyCounter", "curationRulesPanel", "closeRulesPanelBtn",
      "ruleQuickForm", "ruleKindSelect", "ruleActionSelect", "ruleTargetInput",
      "curationRulesList", "sectorDrillBar", "backToOverviewBtn", "subtopicChips",
      "signalList", "todayCalendarCard", "todayRiskCard",
      "eqSummary", "eqAsOf", "eqIndices", "eqWatch", "eqSectors", "heatRadar",
      "portfolioModeHint", "openAddPosBtn", "openImportBtn", "portfolioSummaryBar",
      "portfolioSubTabs", "spotFxHint", "spotTableWrap", "derivSummaryHint",
      "derivTableWrap", "derivMobileList", "journalCapitalHint", "journalGate",
      "strategyCompareWrap", "journalPositions",
      "researchSubTabs", "stockChips", "stockSearchInput", "stockSearchBtn",
      "stockBanner", "stockChartRet", "stockRangeSeg", "stockChartReadout",
      "stockChartWrap", "stockSummaryPanel", "stockInsightsCol", "stockSectionsCol",
      "macroCount", "macroGroups", "corrWindowSeg", "corrReadout", "corrMatrix",
      "pairsAsOf", "pairsList",
      "kolTopicChips", "kolStanceChips", "kolSearchInput", "kolSearchBtn",
      "kolTug", "kolTopicBars", "kolTimeline",
      "articleStatusTabs", "articleSourceChips", "articleSearchInput", "articleSearchBtn",
      "exportOpmlBtn", "articleList",
      "recordsSubTabs", "journalModeHint", "newReviewBtn", "reviewStatusChips",
      "reviewForm", "rvId", "rvRev", "rvDate", "rvSymbol", "rvDirection", "rvSize",
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

  function formatPrice(num) {
    const n = toNum(num);
    if (!Number.isFinite(n) || n <= 0) return "";
    const digits = n >= 1000 ? 2 : n >= 1 ? 2 : 4;
    return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: digits });
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
        out.push("<pre><code>" + buf.join("\n") + "</code></pre>");
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
      const next = ["stocks", "macro", "kol", "articles"].includes(val) ? val : "stocks";
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
      kol: { view: "research", sub: "kol" },
      articles: { view: "research", sub: "articles" },
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
      records: arr(appState.journal.trade_reviews).length + arr(appState.journal.essays).length
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

  function renderTodayOverviewBar() {
    if (!dom.todayOverviewBar) return;
    const rg = state().risk_gate || {};
    const sum = workspaceSummary();
    const fx = getFxContext();
    const netEq = sum && sum.net_equity_usd != null ? toNum(sum.net_equity_usd) : appState.capitalBase;
    const equitySourceNote = sum && sum.net_equity_usd != null ? "实盘持仓汇总" : "参考资金基数";

    const netDir = sum && sum.net_direction_pct != null ? toNum(sum.net_direction_pct) : NaN;
    const effLev = sum && sum.effective_leverage != null ? toNum(sum.effective_leverage) : NaN;
    const cap = toNum(rg.long_risk_cap);
    const light = String(rg.light || "").toUpperCase();
    const stamp = shortStamp(String(state().generated_at_bjt || "").replace(/\+08:00$/, ""));

    dom.todayOverviewBar.innerHTML =
      '<div class="ov-cell"><span class="ov-label">账户净值</span><b class="ov-val">' + amtHtml(netEq) + '</b><span class="ov-sub">' + escapeHtml(equitySourceNote) + "</span></div>" +
      '<div class="ov-cell"><span class="ov-label">净方向性敞口</span><b class="ov-val num-plain ' + (Number.isFinite(netDir) ? toneClass(netDir) : "") + '">' +
      escapeHtml(Number.isFinite(netDir) ? formatSignedPct(netDir) : sum ? "不可计算" : (rg.summary_exposure ? rg.summary_exposure.split("·")[0].trim() : "0.00%")) +
      '</b><span class="ov-sub">多空净比</span></div>' +
      '<div class="ov-cell"><span class="ov-label">有效杠杆</span><b class="ov-val num-plain">' +
      escapeHtml(Number.isFinite(effLev) ? effLev.toFixed(2) + "x" : sum ? "不可计算" : (Number.isFinite(cap) ? cap.toFixed(1) + "x 上限" : "1.0x")) +
      '</b><span class="ov-sub">总风险敞口 / 净值</span></div>' +
      '<div class="ov-cell"><span class="ov-label">策略门控</span><b class="ov-val">' +
      escapeHtml(RISK_LIGHT_TEXT[light] || "提示") + '</b><span class="ov-sub">' + escapeHtml(rg.label || "") + "</span></div>" +
      '<div class="ov-cell"><span class="ov-label">估值汇率与时点</span><b class="ov-val num-plain">' +
      escapeHtml(fx.currency === "CNY" ? (fx.available ? "1 USD = " + fx.rate.toFixed(4) : "不可用") : "USD 计价") +
      '</b><span class="ov-sub num-plain">' + escapeHtml((fx.currency === "CNY" ? fx.label + " · " : "") + (stamp || "")) + "</span></div>";
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
    dom.dailyGlance.innerHTML = GLANCE_IDS.filter((id) => map[id]).map((id) => {
      const m = map[id];
      return '<button type="button" class="glance" data-jump="macro" title="' + escapeHtml(m.note || m.label) + '">' +
        '<span class="glance-label">' + escapeHtml(m.label) + "</span>" +
        '<span class="glance-value num-plain">' + escapeHtml(m.value_display) + "</span>" +
        (!isBlank(m.change_display) ? '<span class="glance-change num-plain ' + toneClass(m.change_num) + '">' + escapeHtml(m.change_display) + "</span>" : "") +
        "</button>";
    }).join("");
  }

  /* ---------- 今日：信号筛选规则、持仓雷达、分值分解与快讯流 ---------- */

  function activeCurationRules() {
    return arr((appState.workspace || {}).curation_rules);
  }

  function heldSymbolsSet() {
    const ws = appState.workspace || {};
    const syms = new Set();
    const normBase = (raw) => String(raw || "").trim().toUpperCase().split(/[-/]/)[0];
    arr(ws.spot_balances).forEach((r) => {
      if (toNum(r.qty) > 0 && !isBlank(r.symbol) && String(r.asset_class || "") !== "cash") {
        const base = normBase(r.symbol);
        if (base) syms.add(base);
      }
    });
    arr(ws.derivative_positions).forEach((r) => {
      if (toNum(r.qty) !== 0 && !isBlank(r.symbol)) {
        const base = normBase(r.symbol);
        if (base) syms.add(base);
      }
    });
    return syms;
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
    const sigTickers = arr(s.tickers).map((t) => String(t).toUpperCase());
    const matched = sigTickers.filter((t) => heldSet.has(t));
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

  function filteredSignals(opts) {
    const f = appState.daily;
    const kw = f.keyword.toLowerCase();
    const catFilter = opts && opts.overrideCat !== undefined ? opts.overrideCat : f.cat;
    const ignoreSubtopic = Boolean(opts && opts.ignoreSubtopic);
    const heldSet = heldSymbolsSet();
    const rulesInfo = watchedSymbolsAndKeywords();
    const curated = curatedSignalList();
    const candidates = arr(state().signal_candidates);
    const rawList = curated ?? (candidates.length && (f.scope === "held" || f.scope === "watch")
      ? candidates
      : arr(state().signals));

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
    return copy.sort((a, b) => {
      const selDiff = (b && b.selected === true ? 1 : 0) - (a && a.selected === true ? 1 : 0);
      if (selDiff !== 0) return selDiff;
      const scoreDiff = signalRankScore(b) - signalRankScore(a);
      if (scoreDiff !== 0) return scoreDiff;
      const impDiff = impWeight(b && b.importance) - impWeight(a && a.importance);
      if (impDiff !== 0) return impDiff;
      return parseSignalTimeMs(b && b.time_bjt) - parseSignalTimeMs(a && a.time_bjt);
    });
  }

  function signalTickersHtml(s) {
    return arr(s && s.tickers).filter((t) => !isBlank(t)).map((t) => {
      const sym = String(t).toUpperCase();
      const canOpen = Boolean(allStockProfiles()[sym]);
      return canOpen
        ? '<button type="button" class="ticker" data-open-stock="' + escapeHtml(sym) + '" title="打开 ' + escapeHtml(sym) + ' 个股投研">' + escapeHtml(sym) + "</button>"
        : '<span class="ticker">' + escapeHtml(t) + "</span>";
    }).join("");
  }

  function signalTopicTagsHtml(s) {
    const cat = String((s && s.category) || "");
    if (!cat) return "";
    return signalTopics(s).slice(0, 3).map((t) =>
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
    const hasBase = !isBlank(s && s.base_score) && Number.isFinite(toNum(s.base_score));
    const hasPersonal = !isBlank(s && s.personal_score) && Number.isFinite(toNum(s.personal_score));
    const hasFinal = !isBlank(s && s.final_score) && Number.isFinite(toNum(s.final_score));
    const hasAtt = !isBlank(s && s.attention_score) && Number.isFinite(toNum(s.attention_score));
    let label = "";
    if (hasBase && (hasPersonal || hasFinal)) {
      const pScore = hasPersonal ? toNum(s.personal_score) : toNum(s.final_score);
      label = "客观 " + toNum(s.base_score) + " · 个人 " + pScore;
    } else if (hasFinal) {
      label = (reasons.length ? "相关性 " : "综合 ") + toNum(s.final_score);
    } else if (hasAtt) {
      label = "关注度 " + toNum(s.attention_score);
    }
    if (!label) return "";
    return '<span class="score-bubble-wrap">' +
      (reasons.length
        ? '<button type="button" class="score-btn num-plain" data-score-toggle="' + escapeHtml(sigId) + '" title="' + escapeHtml(reasons.join(" · ")) + '">' + escapeHtml(label) + "</button>"
        : '<span class="score num-plain">' + escapeHtml(label) + "</span>") +
      "</span>";
  }

  function signalMetaHtml(s, dir, radar, omitTime) {
    const merged = toNum(s && s.merged_count);
    const mergedSources = Array.isArray(s && s.merged_sources)
      ? arr(s.merged_sources).filter((x) => !isBlank(x)).join(" · ")
      : (!isBlank(s && s.merged_sources) ? String(s.merged_sources).trim() : "");
    const radarCls = radar && radar.status === "顺风" ? "badge-bull" : radar && radar.status === "逆风" ? "badge-bear" : "badge-warn";
    return [
      '<span class="dot dot-' + dir.cls + '" aria-hidden="true"></span>',
      '<span class="signal-src">' + escapeHtml(s.source) + "</span>",
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
    const title = escapeHtml(s.title);
    const summary = String(s.summary || "").trim();
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

    return '<li class="signal" data-sig-id="' + escapeHtml(sigId) + '">' +
      '<span class="signal-idx num-plain" aria-hidden="true">' + (idx + 1) + "</span>" +
      '<div class="signal-body">' +
      '<div class="signal-head-row"><div class="signal-meta">' + meta + "</div>" + menuHtml + "</div>" +
      '<h3 class="signal-title">' + (url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + title + "</a>" : title) + "</h3>" +
      (summary ? '<p class="signal-summary">' + escapeHtml(summary) + "</p>" : "") +
      (!isBlank(s.why_it_matters) ? '<p class="signal-why"><b>影响研判</b>' + escapeHtml(s.why_it_matters) + "</p>" : "") +
      '<div class="signal-foot">' + signalTickersHtml(s) + signalTopicTagsHtml(s) +
      '<span class="badge badge-' + dir.cls + '">' + dir.label + "</span>" +
      scoreHtml +
      (summary.length > 110 ? '<button type="button" class="btn-link" data-expand>展开全文</button>' : "") +
      "</div>" + breakdownHtml + "</div></li>";
  }

  function timelineSignalHtml(s, idx, isFirst, isLast) {
    const dir = DIRECTION_META[s.direction] || DIRECTION_META.neutral;
    const url = safeHttpUrl(s.url);
    const title = escapeHtml(s.title);
    const summary = String(s.summary || "").trim();
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
      (summary ? '<p class="tl-row-summary">' + escapeHtml(summary) + "</p>" : "") +
      '<div class="signal-foot">' + signalTickersHtml(s) + signalTopicTagsHtml(s) +
      '<span class="badge badge-' + dir.cls + '">' + dir.label + "</span>" +
      (summary.length > 110 ? '<button type="button" class="btn-link" data-expand>展开全文</button>' : "") +
      "</div></div></li>";
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

  function renderTimelineHtml(sorted) {
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
      const rows = grp.items.map((s, j) => timelineSignalHtml(s, seqIdx++, j === 0, j === grp.items.length - 1)).join("");
      return head + rows;
    }).join("");
  }

  function renderSignals() {
    const curated = curatedSignalList();
    const all = curated ?? arr(state().signals);
    const f = appState.daily;
    const mode = f.mode === "timeline" ? "timeline" : "ranked";
    const filtered = filteredSignals();
    const list = curated && mode === "ranked" ? filtered : sortSignalsForMode(filtered, mode);

    renderNewsModeSwitch();
    renderSectorDrilldown(f.cat);

    if (dom.signalList) {
      dom.signalList.hidden = false;
      dom.signalList.classList.toggle("is-timeline", mode === "timeline" && list.length > 0);
      if (!list.length) {
        dom.signalList.innerHTML = '<li class="empty-hint">没有符合条件的快讯 · <button type="button" class="btn-link" data-clear-filters="daily">清除筛选</button></li>';
      } else {
        dom.signalList.innerHTML = mode === "timeline"
          ? renderTimelineHtml(list)
          : list.map(signalHtml).join("");
      }
    }

    if (dom.dailyCounter) dom.dailyCounter.textContent = "显示 " + list.length + " / " + all.length + " 条";
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
        const rg = state().risk_gate || {};
        dom.todayRiskCard.hidden = false;
        dom.todayRiskCard.innerHTML =
          '<div class="side-head"><h3>风控优先级提示</h3><button type="button" class="btn-link" data-jump="portfolio:derivatives">录入合约 →</button></div>' +
          '<p class="panel-meta" style="margin-top:6px">' + escapeHtml(rg.summary_exposure || "当前未录入衍生品仓位，风控按模型基准执行") + "</p>";
      }
    }
  }

  function equityTile(row, showTag) {
    const sym = String(row.symbol || "").toUpperCase();
    const hasProfile = Boolean(allStockProfiles()[sym]);
    const attr = hasProfile ? ' data-open-stock="' + escapeHtml(sym) + '" role="button" tabindex="0" title="查看 ' + escapeHtml(sym) + ' 个股投研"' : "";
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
      '<span class="radar-title">' + escapeHtml(s.title) + "</span>" +
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
      mode: appState.daily.mode || "ranked"
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
      resetDailyFilters();
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

  function allStockProfiles() {
    return Object.assign({}, state().stock_profiles || {}, appState.customStocks || {});
  }

  function getActiveStock() {
    const map = allStockProfiles();
    const sym = String(appState.stockSymbol || "AAPL").toUpperCase();
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
    if (!dom.stockChips) return;
    const map = allStockProfiles();
    const keys = Object.keys(map);
    dom.stockChips.innerHTML = keys.map((sym) => {
      const p = map[sym];
      const chg = toNum(p.change_pct);
      const on = sym === appState.stockSymbol;
      return '<button type="button" class="chip' + (on ? " is-active" : "") + '" data-open-stock="' + escapeHtml(sym) + '" aria-pressed="' + String(on) + '">' +
        '<span class="num-plain">' + escapeHtml(sym) + "</span>" +
        (Number.isFinite(chg) ? '<span class="num-plain ' + toneClass(chg) + '">' + escapeHtml(formatSignedPct(chg)) + "</span>" : "") +
        "</button>";
    }).join("");
  }

  function renderStockBanner(p) {
    if (!dom.stockBanner) return;
    if (!p) {
      dom.stockBanner.innerHTML = '<div class="empty-hint">暂无个股投研数据</div>';
      return;
    }
    const chg = toNum(p.change_pct);
    dom.stockBanner.innerHTML =
      '<div class="sb-left">' +
      '<div class="sb-mark">' + escapeHtml(p.symbol) + "</div>" +
      '<div class="min-w-0">' +
      '<div class="sb-title-row">' +
      '<h2 class="sb-title">' + escapeHtml(p.name_en || p.name_cn || p.symbol) + "</h2>" +
      '<span class="sb-price num-plain">' + escapeHtml(formatPrice(p.price)) + "</span>" +
      '<span class="sb-chg num-plain ' + toneClass(chg) + '">' + escapeHtml(formatSignedPct(chg)) + "</span>" +
      "</div>" +
      '<div class="sb-meta">' +
      '<strong class="num-plain">' + escapeHtml(p.symbol) + "</strong>" +
      "<span>· " + escapeHtml(p.name_cn || "") + "</span>" +
      "<span>· " + escapeHtml(p.exchange || "美股") + "</span>" +
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

  function renderStockChart(p) {
    if (!dom.stockChartWrap) return;
    setActive(dom.stockRangeSeg, "data-srange", appState.stockRange);
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

    const W = 720, H = 252, padL = 8, padR = 58, padT = 14, padB = 24;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const minP = Math.min(...closes), maxP = Math.max(...closes);
    const span = Math.max(0.01, maxP - minP);
    const lo = minP - span * 0.08, hi = maxP + span * 0.08;
    const yOf = (v) => padT + ((hi - v) / (hi - lo)) * plotH;
    const xOf = (i) => padL + (i / Math.max(1, closes.length - 1)) * plotW;

    const pts = closes.map((v, i) => ({ x: xOf(i), y: yOf(v), price: v, date: dates[i] || "" }));
    activeChartPts = pts;

    const isUp = Number.isFinite(series.ret) ? series.ret >= 0 : closes[closes.length - 1] >= closes[0];
    const strokeCol = isUp ? "var(--bull)" : "var(--bear)";
    const polyPts = pts.map((pt) => pt.x.toFixed(1) + "," + pt.y.toFixed(1)).join(" ");
    const areaPts = polyPts + " " + pts[pts.length - 1].x.toFixed(1) + "," + (padT + plotH).toFixed(1) + " " + pts[0].x.toFixed(1) + "," + (padT + plotH).toFixed(1);

    const gridLevels = [0, 0.5, 1].map((t) => {
      const val = hi - t * (hi - lo);
      const y = padT + t * plotH;
      return '<line class="chart-grid-line" x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '"/>' +
        '<text class="chart-axis-text" x="' + (W - padR + 6) + '" y="' + (y + 4).toFixed(1) + '">$' + val.toFixed(0) + "</text>";
    }).join("");

    const lastPt = pts[pts.length - 1];
    const dateLabels =
      '<text class="chart-axis-text" x="' + padL + '" y="' + (H - 4) + '">' + escapeHtml(dates[0]) + "</text>" +
      '<text class="chart-axis-text" x="' + (padL + plotW / 2 - 28) + '" y="' + (H - 4) + '">' + escapeHtml(dates[Math.floor(dates.length / 2)]) + "</text>" +
      '<text class="chart-axis-text" x="' + (W - padR - 66) + '" y="' + (H - 4) + '">' + escapeHtml(dates[dates.length - 1]) + "</text>";

    if (dom.stockChartReadout) {
      dom.stockChartReadout.textContent = "最新收盘：" + lastPt.date + " · " + formatPrice(lastPt.price) + "（移动鼠标或手指查看区间点位）";
    }

    dom.stockChartWrap.innerHTML =
      '<svg class="stock-svg" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" aria-label="' + escapeHtml(p.symbol) + ' 股价走势">' +
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
    if (dom.stockChartReadout) dom.stockChartReadout.textContent = best.date + " · 收盘 " + formatPrice(best.price);
  }

  function renderStockSummary(p) {
    if (!dom.stockSummaryPanel) return;
    const ov = p.overview || {}, val = p.valuation || {}, fin = p.financials || {}, health = p.health || {};
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

    dom.stockSummaryPanel.innerHTML =
      '<div><div class="val-kicker">估值定位 · 健康评分</div>' +
      '<div class="val-big"><span><span class="val-pe-num">' + escapeHtml(fmtMetricVal(val.pe_ttm, 1)) + '</span> <span class="val-sub">P/E</span></span>' +
      '<span class="val-sub num-plain">远期 P/E ' + escapeHtml(fmtMetricVal(val.pe_forward, 1)) + "</span>" +
      '<span class="val-sub num-plain">P/S ' + escapeHtml(fmtMetricVal(val.ps_ttm, 1)) + "</span></div></div>" +
      '<div class="health-box"><div class="health-ring"><svg width="68" height="68" viewBox="0 0 68 68">' +
      '<circle cx="34" cy="34" r="27" fill="none" stroke="var(--rule-strong)" stroke-width="5"/>' +
      '<circle cx="34" cy="34" r="27" fill="none" stroke="var(--bull)" stroke-width="5" stroke-linecap="round" stroke-dasharray="' + dash + " " + circ.toFixed(1) + '"/>' +
      '</svg><span class="health-score">' + score + '</span></div><div class="health-checks">' + checksHtml + "</div></div>" +
      '<div class="range-52w"><div class="val-kicker">52 周价格区间</div>' +
      '<div class="range-bar"><span class="range-fill" style="width:' + pos52.toFixed(1) + '%"></span><span class="range-thumb" style="left:' + pos52.toFixed(1) + '%"></span></div>' +
      '<div class="range-labels num-plain"><span>' + escapeHtml(formatPrice(lo52)) + " (低)</span><span>现价 " + escapeHtml(formatPrice(px)) + "</span><span>" + escapeHtml(formatPrice(hi52)) + " (高)</span></div></div>" +
      '<div class="mini-kv-grid">' +
      '<div class="mini-kv"><span>营收同比</span><b class="' + toneClass(parseFloat(fin.rev_growth_yoy)) + '">' + escapeHtml(fin.rev_growth_yoy || "—") + "</b></div>" +
      '<div class="mini-kv"><span>净利同比</span><b class="' + toneClass(parseFloat(fin.earnings_growth_yoy)) + '">' + escapeHtml(fin.earnings_growth_yoy || "—") + "</b></div>" +
      '<div class="mini-kv"><span>总市值</span><b>' + escapeHtml(ov.market_cap_fmt || "—") + "</b></div>" +
      '<div class="mini-kv"><span>股息率</span><b>' + escapeHtml(ov.dividend_yield_fmt || "—") + "</b></div></div>";
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

    const deepBar = deepFile
      ? '<div class="qt-deep-bar"><button type="button" class="btn btn-sm btn-accent" data-open-article="' + escapeHtml(deepFile) + '">查阅多智能体深研纪要</button><span class="panel-meta num-plain">' + escapeHtml(deepFile) + "</span></div>"
      : '<div class="qt-deep-bar"><span class="panel-meta">暂无专项深研纪要，可在桌面端运行「多智能体深研」并同步至网页。</span></div>';

    return '<section class="panel quick-thesis-card">' +
      '<div class="panel-head" style="margin-bottom:10px">' +
      '<div><div class="sec-eyebrow">QUICK THESIS</div><h3 class="sec-title">多空速判</h3></div>' +
      '<div class="qt-head-badges"><span class="badge ' + stanceCls + '">' + escapeHtml(stance) + "</span>" +
      (Number.isFinite(score) ? '<span class="qt-score num-plain">' + Math.round(score) + " 分</span>" : "") + "</div>" +
      "</div>" +
      '<div class="qt-cols">' +
      '<div class="qt-col qt-bull"><div class="qt-col-title">利多支撑</div><ul>' + bulls.map((b) => "<li>" + escapeHtml(b) + "</li>").join("") + "</ul></div>" +
      '<div class="qt-col qt-bear"><div class="qt-col-title">风险与纪律</div><ul>' + bears.map((b) => "<li>" + escapeHtml(b) + "</li>").join("") + "</ul></div>" +
      "</div>" +
      (actionHint ? '<p class="qt-action"><b>操作提示</b>' + escapeHtml(actionHint) + "</p>" : "") +
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
      '<div class="sec-eyebrow">PRE-TRADE DISCIPLINE</div>' +
      '<h3 class="sec-title">开仓纪律检查</h3>' +
      '<div class="discipline-grid">' +
      '<div class="discipline-item"><span>MA20 乖离率</span><b class="' + toneClass(bias20) + '">' + escapeHtml(Number.isFinite(bias20) ? formatSignedPct(bias20) : "—") + "</b></div>" +
      '<div class="discipline-item"><span>MA50 乖离率</span><b class="' + toneClass(bias50) + '">' + escapeHtml(Number.isFinite(bias50) ? formatSignedPct(bias50) : "—") + "</b></div>" +
      '<div class="discipline-item"><span>52周区间位置</span><b>' + escapeHtml(Number.isFinite(pos52) ? pos52.toFixed(1) + "%" : "—") + "</b></div>" +
      '<div class="discipline-item"><span>量化门控状态</span><b>' + escapeHtml(RISK_LIGHT_TEXT[String(rg.light || "").toUpperCase()] || "正常") + "</b></div>" +
      "</div>" + alertHtml + checksRows + "</section>";
  }

  function renderStockLower(p) {
    const ov = p.overview || {}, fin = p.financials || {}, val = p.valuation || {};

    if (dom.stockInsightsCol) {
      const tgt = toNum(ov.target_mean_price), px = toNum(p.price);
      const upside = (Number.isFinite(tgt) && Number.isFinite(px) && px > 0) ? ((tgt - px) / px) * 100 : null;
      const sym = String(p.symbol || "").toUpperCase(), cn = String(p.name_cn || "");
      const relSignals = arr(state().signals).filter((s) => {
        const tks = arr(s.tickers).map((x) => String(x).toUpperCase());
        const txt = String(s.title || "") + " " + String(s.summary || "");
        return tks.indexOf(sym) !== -1 || txt.toUpperCase().indexOf(sym) !== -1 || (cn && txt.indexOf(cn) !== -1);
      }).slice(0, 5);
      const relKols = arr(state().kol_digest).filter((k) => {
        const txt = String(k.headline || "") + " " + String(k.takeaway || "");
        return txt.toUpperCase().indexOf(sym) !== -1 || (cn && txt.indexOf(cn) !== -1);
      }).slice(0, 3);
      const secFilings = arr(p.sec_filings).filter((f) => f && !isBlank(f.form));
      const secSection = secFilings.length
        ? '<section class="panel sec-filings-panel">' +
          '<div class="sec-eyebrow">SEC EDGAR FILINGS</div>' +
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

      dom.stockInsightsCol.innerHTML =
        buildQuickThesisCard(p, relSignals, relKols) +
        '<section class="panel"><div class="sec-eyebrow">BUSINESS PROFILE</div><h3 class="sec-title">核心业务与护城河</h3>' +
        '<p class="overview-copy" style="margin-top:10px">' + escapeHtml(p.business_overview_cn || "—") + "</p></section>" +
        buildDisciplineCard(p) +
        '<section class="panel"><div class="sec-eyebrow">WALL STREET CONSENSUS</div><h3 class="sec-title">华尔街一致预期</h3>' +
        '<div class="mini-kv-grid" style="margin-top:12px;border-top:0;padding-top:0">' +
        '<div class="mini-kv"><span>综合评级</span><b>' + escapeHtml(ov.analyst_rating || "—") + "</b></div>" +
        '<div class="mini-kv"><span>覆盖机构</span><b>' + escapeHtml(ov.analyst_count != null ? String(ov.analyst_count) + " 家" : "—") + "</b></div>" +
        '<div class="mini-kv"><span>目标均价</span><b>' + escapeHtml(Number.isFinite(tgt) ? formatPrice(tgt) : "—") + "</b></div>" +
        '<div class="mini-kv"><span>隐含空间</span><b class="' + toneClass(upside) + '">' + escapeHtml(Number.isFinite(upside) ? formatSignedPct(upside) : "—") + "</b></div>" +
        "</div></section>" +
        secSection +
        '<section class="panel"><div class="panel-head"><h3 class="sec-title">站内关联快讯与观点</h3><button type="button" class="btn-link" data-jump="today">看全部日报</button></div>' +
        '<div class="rel-list">' +
        (relSignals.length || relKols.length
          ? relSignals.map((s) => '<div class="rel-item"><div class="rel-item-head"><span>' + escapeHtml(s.source) + '</span><span class="num-plain">' + escapeHtml(s.time_bjt || "") + '</span></div><div class="rel-item-title">' + escapeHtml(s.title) + "</div></div>").join("") +
            relKols.map((k) => '<div class="rel-item"><div class="rel-item-head"><span>' + escapeHtml(k.handle) + '</span><span class="badge badge-' + (STANCE_CLS[k.stance] || "neutral") + '">' + escapeHtml(k.stance) + '</span></div><div class="rel-item-title">' + escapeHtml(k.headline) + "</div></div>").join("")
          : '<div class="empty-hint">今日快讯库暂无直接提及 ' + escapeHtml(sym) + " 的增量信号</div>") +
        "</div></section>";
    }

    if (dom.stockSectionsCol) {
      const mCard = (lbl, valStr, cls) =>
        '<div class="metric-card"><div class="metric-lbl">' + escapeHtml(lbl) + '</div><div class="metric-val' + (cls ? " " + cls : "") + '">' + escapeHtml(valStr || "—") + "</div></div>";

      const quarters = arr(fin.quarters);
      const qColsAll = [
        { key: "revenue_fmt", label: "营收" },
        { key: "gross_profit_fmt", label: "毛利润" },
        { key: "op_income_fmt", label: "营业利润" },
        { key: "net_income_fmt", label: "净利润" },
        { key: "fcf_fmt", label: "自由现金流" }
      ];
      const qCols = qColsAll.filter((col) => quarters.some((q) => q && q[col.key] && q[col.key] !== "—" && q[col.key] !== "$0.00"));
      const qTable = quarters.length && qCols.length
        ? '<h4 class="subsec-title">最近四季度财报简表</h4><div class="table-scroll"><table class="fin-table sticky-table"><thead><tr><th>季度</th>' +
          qCols.map((col) => "<th>" + escapeHtml(col.label) + "</th>").join("") +
          "</tr></thead><tbody>" +
          quarters.map((q) => "<tr><td>" + escapeHtml(q.period) + "</td>" + qCols.map((col) => "<td>" + escapeHtml(q[col.key] || "—") + "</td>").join("") + "</tr>").join("") +
          "</tbody></table></div>"
        : "";

      const allMap = allStockProfiles();
      const peers = Object.keys(allMap).map((k) => allMap[k]).filter((x) => x.peer_group === p.peer_group).slice(0, 6);
      const peerTable = peers.length
        ? '<h4 class="subsec-title">同赛道估值对比（点击切换标的）</h4><div class="table-scroll"><table class="fin-table sticky-table"><thead><tr><th>标的</th><th>名称</th><th>最新价</th><th>P/E</th><th>P/B</th><th>ROE</th><th>毛利率</th></tr></thead><tbody>' +
          peers.map((pr) =>
            '<tr class="' + (pr.symbol === p.symbol ? "is-current" : "") + '" data-open-stock="' + escapeHtml(pr.symbol) + '">' +
            "<td>" + escapeHtml(pr.symbol) + "</td><td>" + escapeHtml(pr.name_cn) + "</td><td>" + escapeHtml(formatPrice(pr.price)) + "</td><td>" + escapeHtml(fmtMetricVal((pr.valuation || {}).pe_ttm, 1)) + "</td><td>" + escapeHtml(fmtMetricVal((pr.valuation || {}).pb, 1)) + "</td><td>" + escapeHtml((pr.financials || {}).roe || "—") + "</td><td>" + escapeHtml((pr.financials || {}).gross_margin || "—") + "</td></tr>"
          ).join("") +
          "</tbody></table></div>"
        : "";

      dom.stockSectionsCol.innerHTML =
        '<section class="panel"><div class="sec-eyebrow">SECTION 01</div><h3 class="sec-title">公司概览<span class="sec-sub">Overview</span></h3>' +
        '<div class="metric-grid-3" style="margin-top:14px">' +
        mCard("总市值", ov.market_cap_fmt) + mCard("市盈率 P/E", fmtMetricVal(ov.pe_ttm, 2)) + mCard("远期 P/E", fmtMetricVal(ov.pe_forward, 2)) +
        mCard("每股收益 EPS", ov.eps_ttm != null ? "$" + fmtMetricVal(ov.eps_ttm, 2) : "—") + mCard("股息率", ov.dividend_yield_fmt) + mCard("成交量", ov.volume_fmt) +
        mCard("Beta 系数", fmtMetricVal(ov.beta, 2)) + mCard("做空天数比", fmtMetricVal(ov.short_ratio, 2)) + mCard("机构持股占比", ov.institution_pct) +
        "</div></section>" +
        '<section class="panel"><div class="sec-eyebrow">SECTION 02</div><h3 class="sec-title">财务体检<span class="sec-sub">Financial Health</span></h3>' +
        '<div class="metric-grid-3" style="margin-top:14px">' +
        mCard("营收 TTM", fin.ttm_revenue_fmt) + mCard("EBITDA TTM", fin.ttm_ebitda_fmt) + mCard("自由现金流 TTM", fin.ttm_fcf_fmt) +
        mCard("毛利率", fin.gross_margin) + mCard("营业利润率", fin.operating_margin) + mCard("净利率", fin.net_margin) +
        mCard("净资产收益率 ROE", fin.roe) + mCard("总资产收益率 ROA", fin.roa) + mCard("流动比率", fin.current_ratio) +
        mCard("现金储备", fin.total_cash_fmt) + mCard("总负债", fin.total_debt_fmt) + mCard("负债/权益", fin.debt_to_equity && fin.debt_to_equity !== "—" ? fin.debt_to_equity + "%" : "—") +
        "</div>" + qTable + "</section>" +
        '<section class="panel"><div class="sec-eyebrow">SECTION 03</div><h3 class="sec-title">估值判断与同行对比<span class="sec-sub">Valuation & Peers</span></h3>' +
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
    const clean = String(sym || "").trim().toUpperCase();
    if (!clean) return;
    appState.stockSymbol = clean;
    if (switchToView) setView("research", { keepScroll: false });
    if (!allStockProfiles()[clean] && !appState.offline) {
      try {
        showToast("正在检索 " + clean + " 投研数据…");
        const prof = await apiJson("/api/stock?symbol=" + encodeURIComponent(clean));
        if (prof && prof.symbol) appState.customStocks[clean] = prof;
      } catch (err) {
        showToast(err.message || "未找到该股票代码", "error");
      }
    }
    renderResearch();
  }

  function commitStockSearch() {
    const sym = (dom.stockSearchInput ? dom.stockSearchInput.value : "").trim().toUpperCase();
    if (!sym) return;
    openStockResearch(sym, false);
  }

  /* ---------- 宏观与关联 ---------- */

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

  /* ---------- KOL 观点树 ---------- */

  function kolFiltered(ignoreTopic) {
    const f = appState.kol, kw = f.keyword.toLowerCase();
    return arr(state().kol_digest).filter((k) => {
      if (!ignoreTopic && f.topic !== "all" && k.topic !== f.topic) return false;
      if (f.stance !== "all" && k.stance !== f.stance) return false;
      if (kw && [k.handle, k.headline, k.takeaway, k.topic, k.group].join(" ").toLowerCase().indexOf(kw) === -1) return false;
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
        '<span class="topic-count num-plain">' + g.bull + "多 / " + g.bear + "空</span></button>";
    }).join("");
  }

  function kolCardHtml(k) {
    const cls = STANCE_CLS[k.stance] || "neutral";
    const handle = String(k.handle || "");
    const shownHandle = handle && !handle.startsWith("@") ? "@" + handle : handle;
    const sc = toNum(k.score);
    const take = String(k.takeaway || "").trim();
    const url = safeHttpUrl(k.url);
    return '<article class="tl-card"><div class="tl-head">' +
      '<span class="tl-handle">' + escapeHtml(shownHandle) + "</span>" +
      (!isBlank(k.group) ? '<span class="badge">' + escapeHtml(k.group) + "</span>" : "") +
      (!isBlank(k.topic) ? '<span class="badge badge-outline">' + escapeHtml(k.topic) + "</span>" : "") +
      '<span class="badge badge-' + cls + '">' + escapeHtml(k.stance || "中性") + (Number.isFinite(sc) ? " " + (sc > 0 ? "+" : "") + sc.toFixed(2) : "") + "</span>" +
      "</div>" + (!isBlank(k.headline) ? '<h3 class="tl-headline">' + escapeHtml(k.headline) + "</h3>" : "") +
      (take ? '<p class="tl-take">' + escapeHtml(take) + "</p>" : "") +
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

  function renderKol() {
    renderKolTopicChips();
    renderKolTug();
    renderKolTopicBars();
    renderKolTimeline();
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
      if (kw && [a.title, a.excerpt, a.source, a.publisher].join(" ").toLowerCase().indexOf(kw) === -1) return false;
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
      return '<li class="article-item"><div class="article-main">' +
        '<div class="article-meta"><span class="badge status-badge-' + st + '">' + ARTICLE_STATUS_LABELS[st] + "</span>" +
        (!isBlank(a.source) ? "<span>" + escapeHtml(a.source) + "</span>" : "") +
        (!isBlank(a.publisher) ? '<span class="badge badge-outline">' + escapeHtml(a.publisher) + "</span>" : "") +
        articleScoreBadge(a) +
        (!isBlank(a.mtime) ? '<span class="num-plain">' + escapeHtml(a.mtime) + "</span>" : "") + "</div>" +
        '<button type="button" class="article-title" data-open-article="' + fname + '">' + escapeHtml(a.title || a.filename) + "</button>" +
        (!isBlank(a.excerpt) ? '<p class="article-excerpt">' + escapeHtml(a.excerpt) + "</p>" : "") +
        (note ? '<p class="article-note">笔记：' + escapeHtml(note) + "</p>" : "") +
        '</div><div class="article-side"><button type="button" class="btn btn-sm" data-open-article="' + fname + '">阅读</button></div></li>';
    }).join("");
  }

  /* ---------- 抽屉管理 & 长文阅读抽屉 ---------- */

  function anyDrawerOpen() {
    return (dom.articleDrawer && !dom.articleDrawer.hidden) ||
      (dom.pushDrawer && !dom.pushDrawer.hidden) ||
      (dom.positionDrawer && !dom.positionDrawer.hidden) ||
      (dom.importDrawer && !dom.importDrawer.hidden);
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
      import: dom.importDrawer
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
    const sb = articleScoreBadge(art);
    if (sb) meta.push(sb);
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

  function reviewStatusBadge(status) {
    const cls = status === "已止盈" ? "badge-bull" : status === "已止损" ? "badge-bear" : status === "持有中" ? "badge-accent" : status === "当前跟踪" ? "badge-outline" : "";
    return '<span class="badge ' + cls + '">' + escapeHtml(status) + "</span>";
  }

  function filteredReviews() {
    const f = appState.reviewStatus;
    return arr(appState.journal.trade_reviews).filter((r) => {
      if (f === "all") return true;
      if (f === "closed") return CLOSED_REVIEW_STATUSES.has(r.status);
      return r.status === f;
    });
  }

  function renderReviews() {
    if (!dom.reviewList) return;
    const names = positionNameMap();
    const list = filteredReviews();
    if (!list.length) {
      dom.reviewList.innerHTML = '<p class="empty-hint">' + (arr(appState.journal.trade_reviews).length ? "当前筛选下没有复盘记录" : (appState.offline ? "共享离线快照未公开私人复盘记录" : "还没有复盘记录，点击「+ 新建复盘」开始记录")) + "</p>";
      return;
    }
    dom.reviewList.innerHTML = list.map((r) => {
      const dirCls = r.direction === "做多" ? "badge-bull" : r.direction === "做空" ? "badge-bear" : "";
      const price = toNum(r.price_usd);
      const pnl = toNum(r.pnl_usd);
      const showPnl = Number.isFinite(pnl) && (pnl !== 0 || CLOSED_REVIEW_STATUSES.has(r.status));
      const notes = [["核心逻辑", r.thesis], ["执行细节", r.execution_note], ["教训反思", r.lesson]]
        .filter((n) => !isBlank(n[1]))
        .map((n) => "<div><dt>" + n[0] + "</dt><dd>" + escapeHtml(n[1]) + "</dd></div>").join("");
      const tags = arr(r.tags).filter((t) => !isBlank(t)).map((t) => '<span class="badge badge-outline">#' + escapeHtml(t) + "</span>").join("");
      const id = escapeHtml(r.id);
      return '<article class="review-card"><div class="review-head">' +
        '<span class="review-symbol">' + escapeHtml(names[r.symbol] || r.symbol) + "</span>" +
        (!isBlank(r.direction) ? '<span class="badge ' + dirCls + '">' + escapeHtml(r.direction) + "</span>" : "") +
        (!isBlank(r.status) ? reviewStatusBadge(r.status) : "") +
        '<span class="review-date num-plain">' + escapeHtml(r.trade_date) + "</span>" +
        (!appState.offline ? '<div class="review-actions"><button type="button" class="btn btn-sm" data-edit-review="' + id + '">编辑</button><button type="button" class="btn btn-sm btn-ghost btn-danger" data-del-review="' + id + '">删除</button></div>' : "") +
        '</div><div class="review-figs"><span>仓位<b>' + escapeHtml(formatWeight(r.size_pct)) + "</b></span>" +
        (price > 0 ? "<span>参考均价<b>" + escapeHtml(formatPrice(price)) + "</b></span>" : "") +
        (showPnl ? "<span>盈亏<b>" + amtHtml(pnl, { signed: true, cls: toneClass(pnl) }) + "</b></span>" : "") +
        "</div>" + (notes ? '<dl class="review-notes">' + notes + "</dl>" : "") +
        (tags ? '<div class="essay-tags">' + tags + "</div>" : "") + "</article>";
    }).join("");
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
    if (appState.offline) return showToast("共享离线快照只读，禁止新建或编辑复盘", "error");
    const r = row || appState.drafts.review || {};
    dom.rvId.value = r.id ? String(r.id) : "";
    if (dom.rvRev) dom.rvRev.value = r.rev != null ? String(r.rev) : "";
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
    dom.rvSaveBtn.textContent = r.id ? "更新复盘" : "保存复盘";
    dom.reviewForm.hidden = false;
    dom.reviewForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
    dom.rvSymbol.focus({ preventScroll: true });
  }

  async function submitReview(event) {
    event.preventDefault();
    if (appState.offline) return showToast("共享离线快照只读", "error");
    const symbol = dom.rvSymbol.value.trim();
    const date = dom.rvDate.value;
    const size = Number(dom.rvSize.value || 0);
    const price = Number(dom.rvPrice.value || 0);
    const pnl = Number(dom.rvPnl.value || 0);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return showToast("请选择交易日期", "error");
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
      showToast(payload.id ? "复盘已更新" : "复盘已保存", "ok");
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

  async function refreshState() {
    if (appState.offline) return showToast("共享离线快照只读，请在本地服务刷新数据");
    if (appState.busy.refresh) return;
    appState.busy.refresh = true;
    dom.refreshBtn.classList.add("is-busy");
    dom.refreshBtn.textContent = "刷新中…";
    try {
      const st = await apiJson("/api/refresh", { method: "POST", body: { refresh_fred: false } });
      const journal = await apiJson("/api/journal");
      appState.raw = st || {};
      appState.journal = normalizeJournal(journal);
      await refreshWorkspaceOnly();
      renderAll();
      showToast("数据已刷新", "ok");
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
      if (dom.logoutBtn) dom.logoutBtn.hidden = !(h && h.auth_enabled === true) || appState.offline;
    } catch (_) {
      if (dom.logoutBtn) dom.logoutBtn.hidden = true;
    }
  }

  async function logout() {
    try { await apiJson("/api/logout", { method: "POST", body: {} }); } catch (_) { /* ignore */ }
    window.location.href = "/login";
  }

  function applyModeUi() {
    const ro = Boolean(appState.offline);
    if (dom.exportOpmlBtn) dom.exportOpmlBtn.hidden = ro;
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
    renderCurationRulesPanel();
    renderSectorRibbon();
    renderNewsModeSwitch();
    renderSignals();
    renderTodaySideCards();
    renderEquities();
    renderRadar();
    renderPortfolioView();
    renderResearch();
    renderMacroGroups();
    renderCorrMatrix();
    renderPairs();
    renderKol();
    renderArticles();
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
    renderSectorRibbon();
    renderSignals();
  }

  function commitKolKeyword() {
    appState.kol.keyword = dom.kolSearchInput.value.trim();
    renderKolTug();
    renderKolTimeline();
  }

  function commitArticleKeyword() {
    appState.articles.keyword = dom.articleSearchInput.value.trim();
    renderArticles();
  }

  function clearFilters(scope) {
    if (scope === "daily") {
      resetDailyFilters();
    } else if (scope === "kol") {
      appState.kol = { topic: "all", stance: "all", keyword: "" };
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
    const card = btn.closest(".signal, .tl-card, .essay-card");
    if (!card) return;
    const on = card.classList.toggle("is-expanded");
    btn.textContent = on ? "收起" : "展开全文";
  }

  function handleClick(e) {
    const t = e.target;
    if (!(t instanceof Element)) return;
    const hit = (sel) => t.closest(sel);
    let el;

    // Close open signal popover if clicking outside
    if (!hit(".sig-menu-wrap") && appState.openSignalMenu) {
      appState.openSignalMenu = "";
      renderSignals();
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
        dom.rvThesis.value = "关注 " + p.symbol + " (P/E " + fmtMetricVal((p.valuation || {}).pe_ttm, 1) + ")";
      }
      return;
    }
    if ((el = hit("[data-jump]"))) return setView(el.getAttribute("data-jump"));
    if ((el = hit("[data-close-drawer]"))) return closeDrawer(el.getAttribute("data-close-drawer"));
    if ((el = hit("[data-open-article]"))) return openArticle(el.getAttribute("data-open-article"));
    if ((el = hit("[data-expand]"))) return toggleExpand(el);
    if ((el = hit("[data-clear-filters]"))) return clearFilters(el.getAttribute("data-clear-filters"));
    if ((el = hit("[data-radar]"))) return jumpToSignal(el.getAttribute("data-radar"));

    // Currency toggle
    if ((el = hit("#currencySeg [data-currency]"))) return applyCurrency(el.getAttribute("data-currency"), true);

    if ((el = hit("#newsModeSwitch [data-nmode]"))) {
      const m = el.getAttribute("data-nmode") === "timeline" ? "timeline" : "ranked";
      if (appState.daily.mode === m) return undefined;
      appState.daily.mode = m;
      safeStorageSet(STORAGE_KEYS.newsMode, m);
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
      return renderSignals();
    }
    if ((el = hit("[data-jump-subtopic]"))) {
      const sec = el.getAttribute("data-jump-sector");
      const sub = el.getAttribute("data-jump-subtopic");
      return enterSector(sec, { subtopic: sub, scrollToTop: false });
    }

    // Signal scope, rules, card quick actions, score breakdown
    if ((el = hit("#signalScopeChips [data-scope]"))) {
      const sc = el.getAttribute("data-scope");
      if (sc === "rules") {
        if (dom.curationRulesPanel) dom.curationRulesPanel.hidden = !dom.curationRulesPanel.hidden;
        return undefined;
      }
      appState.daily.scope = sc;
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
      setActive(dom.polarityChips, "data-pol", appState.daily.pol);
      renderSectorRibbon();
      return renderSignals();
    }
    if (hit("#highOnlyBtn")) {
      appState.daily.highOnly = !appState.daily.highOnly;
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

    if ((el = hit("[data-topic]"))) {
      const topic = el.getAttribute("data-topic");
      appState.kol.topic = topic === appState.kol.topic && topic !== "all" ? "all" : topic;
      return renderKol();
    }
    if ((el = hit("#kolStanceChips [data-stance]"))) {
      appState.kol.stance = el.getAttribute("data-stance");
      setActive(dom.kolStanceChips, "data-stance", appState.kol.stance);
      return renderKolTimeline();
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
    if (hit("#newReviewBtn")) return openReviewForm(null);
    if (hit("#rvCancelBtn")) { appState.drafts.review = null; dom.reviewForm.hidden = true; return undefined; }
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
        return;
      }
      if ((e.key === "Enter" || e.key === " ") && e.target instanceof Element) {
        if (e.target.matches("#signalList .tl-row[data-sig-id]")) {
          e.preventDefault();
          toggleSignalRead(e.target.getAttribute("data-sig-id"));
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
      appState.journal = normalizeJournal(st && st.journal);
      applyModeUi();
      try {
        renderAll();
      } catch (renderErr) {
        showLoadError("界面渲染异常：" + (renderErr && renderErr.message ? renderErr.message : "请刷新重试"));
        return;
      }
      await refreshWorkspaceOnly();
      checkHealth();
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
