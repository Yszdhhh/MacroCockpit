/* 观澜 Pro · Meridian 桌面专业研判台交互逻辑 */
(function () {
  "use strict";

  const THEME_KEY = "macro_cockpit_desktop_theme_v1";
  const DEFAULT_SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA", "META", "SPY", "BTC", "ETH", "600519", "00700"];
  const SECTOR_LABELS = {
    equities: "美股",
    ashare: "A股·港股",
    ai: "AI",
    crypto: "加密",
    macro: "宏观"
  };

  const state = {
    tab: "debate",
    debateMode: "fast",
    agyTransport: "acp",
    overview: null,
    debates: [],
    activeDebateId: "",
    activeDebate: null,
    widgets: [],
    busy: {}
  };

  const dom = {};
  let toastTimer = null;

  function cacheDom() {
    const ids = [
      "metaThemeColor", "obbStatusBadge", "agyStatusBadge", "themeToggleBtn", "backToWebBtn",
      "desktopNav", "debateSymbolInput", "debateModeSeg", "debateSyncCheck", "runDebateBtn",
      "debateQuickSymbols", "debateResultArea", "refreshDebatesBtn", "debateHistoryList",
      "obbProbeSummary", "obbMountUrl", "copyObbUrlBtn", "obbDatasetSelect", "obbSymbolInput",
      "obbAllowSdkCheck", "obbRunQueryBtn", "obbQueryResult", "reloadWidgetsBtn",
      "obbWidgetsGrid", "obbWidgetPreview", "agyOverviewCard", "mcpCmdText", "copyMcpCmdBtn",
      "agyTransportSeg", "agyQuickPrompts", "agyPromptInput", "agyAskMeta", "agyAskBtn",
      "agyResponseBox", "syncKpiGrid", "runCloudSyncBtn", "syncTargetLabel", "syncResultBox",
      "toast"
    ];
    ids.forEach((id) => { dom[id] = document.getElementById(id); });
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function arr(v) { return Array.isArray(v) ? v : []; }

  function isBlank(v) {
    return v === null || v === undefined || String(v).trim() === "";
  }

  function readToken() {
    const meta = document.querySelector('meta[name="desktop-token"]');
    const tok = meta ? String(meta.getAttribute("content") || "").trim() : "";
    return tok && tok !== "__DESKTOP_TOKEN__" ? tok : "";
  }

  function showToast(msg, tone) {
    if (!dom.toast) return;
    dom.toast.textContent = msg;
    dom.toast.className = "toast" + (tone ? " is-" + tone : "");
    dom.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { dom.toast.hidden = true; }, 3200);
  }

  async function apiJson(path, opts) {
    const o = opts || {};
    const headers = Object.assign({
      "X-Macro-Request": "1",
      "Accept": "application/json"
    }, o.headers || {});
    const tok = readToken();
    if (tok) headers["X-Desktop-Token"] = tok;
    let body = o.body;
    if (body && typeof body === "object" && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const resp = await fetch(path, {
      method: o.method || "GET",
      headers,
      body,
      credentials: "same-origin"
    });
    const text = await resp.text();
    let parsed = null;
    try { parsed = text ? JSON.parse(text) : {}; } catch (_) { parsed = { raw: text }; }
    if (!resp.ok) {
      const msg = (parsed && (parsed.error || parsed.message || parsed.detail)) || ("请求失败 (" + resp.status + ")");
      throw new Error(msg);
    }
    return parsed;
  }

  async function publicJson(path) {
    const resp = await fetch(path, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    const text = await resp.text();
    let parsed = null;
    try { parsed = text ? JSON.parse(text) : {}; } catch (_) { parsed = { raw: text }; }
    if (!resp.ok) {
      const msg = (parsed && (parsed.error || parsed.message)) || ("请求失败 (" + resp.status + ")");
      throw new Error(msg);
    }
    return parsed;
  }

  async function copyText(text, successMsg) {
    try {
      await navigator.clipboard.writeText(text);
      showToast(successMsg || "已复制", "ok");
      return;
    } catch (_) { /* fallback below */ }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      showToast(successMsg || "已复制", "ok");
    } catch (_) {
      showToast("复制失败，请手动复制", "error");
    } finally {
      document.body.removeChild(ta);
    }
  }

  /* ---------- 主题与视图切换 ---------- */

  function applyTheme(theme) {
    const next = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    if (dom.metaThemeColor) dom.metaThemeColor.setAttribute("content", next === "dark" ? "#100F09" : "#FAFAF9");
    if (dom.themeToggleBtn) dom.themeToggleBtn.textContent = next === "dark" ? "浅色" : "深色";
    try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* ignore */ }
  }

  function setTab(name) {
    const valid = ["debate", "openbb", "agy", "sync"].includes(name) ? name : "debate";
    state.tab = valid;
    if (dom.desktopNav) {
      dom.desktopNav.querySelectorAll(".view-tab[data-dtab]").forEach((btn) => {
        const on = btn.getAttribute("data-dtab") === valid;
        btn.classList.toggle("is-active", on);
        if (on) btn.setAttribute("aria-current", "page");
        else btn.removeAttribute("aria-current");
      });
    }
    document.querySelectorAll("main > section.dview[data-dview]").forEach((sec) => {
      sec.hidden = sec.getAttribute("data-dview") !== valid;
    });
    try { window.history.replaceState(null, "", "#" + valid); } catch (_) { /* ignore */ }
  }

  /* ---------- 状态总览渲染 ---------- */

  function renderTopBadges(ov) {
    const obb = (ov && (ov.openbb || ov.openbb_status)) || {};
    const agy = (ov && (ov.agy || ov.agy_status)) || {};

    if (dom.obbStatusBadge) {
      const httpOn = Boolean(obb.http_online || obb.api_online);
      const installed = obb.installed !== false && Boolean(obb.version || obb.sdk_installed);
      const cls = httpOn ? "is-ok" : installed ? "is-warn" : "is-bad";
      const txt = httpOn
        ? "OpenBB HTTP 在线"
        : installed
          ? ("OpenBB " + (obb.version || "SDK") + " · 本地回退")
          : "OpenBB 回退模式";
      dom.obbStatusBadge.className = "status-pill " + cls;
      dom.obbStatusBadge.innerHTML = '<span class="dot" aria-hidden="true"></span><span>' + escapeHtml(txt) + "</span>";
    }

    if (dom.agyStatusBadge) {
      const acpOk = Boolean(agy.acp_ready || agy.acp_ok || agy.available || agy.cli_available);
      const mcpList = arr(agy.mcp_servers);
      const cls = acpOk ? "is-ok" : "is-warn";
      const txt = acpOk
        ? ("AGY ACP 就绪" + (mcpList.length ? " · MCP " + mcpList.length : ""))
        : "AGY 通道待检测";
      dom.agyStatusBadge.className = "status-pill " + cls;
      dom.agyStatusBadge.innerHTML = '<span class="dot" aria-hidden="true"></span><span>' + escapeHtml(txt) + "</span>";
    }

    if (dom.backToWebBtn && ov && !isBlank(ov.web_url)) {
      dom.backToWebBtn.setAttribute("href", String(ov.web_url));
    }
  }

  function renderQuickSymbols(ov) {
    if (!dom.debateQuickSymbols) return;
    const extra = arr(ov && (ov.quick_symbols || ov.portfolio_symbols)).map((x) => String(x || "").toUpperCase()).filter(Boolean);
    const merged = Array.from(new Set(extra.concat(DEFAULT_SYMBOLS)));
    const cur = String(dom.debateSymbolInput ? dom.debateSymbolInput.value : "NVDA").trim().toUpperCase();
    dom.debateQuickSymbols.innerHTML = merged.map((sym) => {
      const active = sym === cur;
      return '<button type="button" class="chip' + (active ? " is-active" : "") + '" data-quick-sym="' + escapeHtml(sym) + '">' + escapeHtml(sym) + "</button>";
    }).join("");
  }

  function renderOpenbbProbe(ov) {
    if (!dom.obbProbeSummary) return;
    const obb = (ov && (ov.openbb || ov.openbb_status)) || {};
    const keys = obb.configured_keys;
    let keyStr = "未检测到外部密钥";
    if (Array.isArray(keys) && keys.length) {
      keyStr = keys.join("、");
    } else if (keys && typeof keys === "object") {
      const enabled = Object.keys(keys).filter((k) => Boolean(keys[k]));
      keyStr = enabled.length ? enabled.join("、") : "暂无外部密钥";
    }
    const cards = [
      { label: "SDK 版本", val: obb.version ? String(obb.version) : "本地回退源" },
      { label: "Platform API (6900)", val: (obb.http_online || obb.api_online) ? "在线" : "未启动（按需懒加载/回退）" },
      { label: "已配置数据源名", val: keyStr }
    ];
    dom.obbProbeSummary.innerHTML = '<div class="probe-kv-grid">' +
      cards.map((c) => '<div class="probe-kv"><span>' + escapeHtml(c.label) + '</span><b>' + escapeHtml(c.val) + "</b></div>").join("") +
      "</div>";
  }

  function renderAgyOverview(ov) {
    if (!dom.agyOverviewCard) return;
    const agy = (ov && (ov.agy || ov.agy_status)) || {};
    const mcp = (ov && ov.mcp) || {};
    const servers = arr(agy.mcp_servers).map((s) => typeof s === "string" ? s : (s.name || JSON.stringify(s)));
    const toolsCount = mcp.tools_count != null ? String(mcp.tools_count) + " 工具" : "12 工具";
    const cards = [
      { label: "CLI 可执行状态", val: (agy.available || agy.cli_available) ? "已就绪" : "不可用" },
      { label: "ACP NDJSON 通道", val: (agy.acp_ready || agy.acp_ok) ? "支持" : "按 CLI 降级" },
      { label: "已挂接 MCP 服务", val: (servers.length ? servers.join("、") : "macrocockpit-desktop") + " (" + toolsCount + ")" }
    ];
    dom.agyOverviewCard.innerHTML = '<div class="probe-kv-grid">' +
      cards.map((c) => '<div class="probe-kv"><span>' + escapeHtml(c.label) + '</span><b>' + escapeHtml(c.val) + "</b></div>").join("") +
      "</div>";
    if (dom.mcpCmdText && mcp.stdio_command) {
      dom.mcpCmdText.textContent = String(mcp.stdio_command);
    }
  }

  function renderSyncOverview(ov) {
    const snap = (ov && (ov.snapshot || ov.coverage || ov.cockpit)) || {};
    const catMap = snap.category_breakdown || snap.categories || {};
    const sectorText = Object.keys(SECTOR_LABELS)
      .filter((k) => catMap[k] != null)
      .map((k) => SECTOR_LABELS[k] + " " + catMap[k])
      .join(" · ");

    if (dom.syncKpiGrid) {
      const kpis = [
        { label: "最近快照时间", val: snap.generated_at_bjt || snap.generated_at || "未生成" },
        { label: "可见快讯信号", val: String(snap.signal_count ?? snap.signals_count ?? 0) + " 条" },
        { label: "五大板块分布", val: sectorText || "五板块同步覆盖" },
        { label: "个股投研档案", val: String(snap.stock_profile_count ?? snap.stock_profiles_count ?? 0) + " 只" },
        { label: "深度研报与长文", val: String(snap.article_count ?? snap.articles_count ?? 0) + " 篇" },
        { label: "桌面深研留档", val: String(state.debates.length || snap.debate_count || 0) + " 场" }
      ];
      dom.syncKpiGrid.innerHTML = kpis.map((k) =>
        '<div class="kpi-card"><div class="kpi-lbl">' + escapeHtml(k.label) + '</div><div class="kpi-val">' + escapeHtml(k.val) + "</div></div>"
      ).join("");
    }
    if (dom.syncTargetLabel && ov && !isBlank(ov.sync_target_url)) {
      dom.syncTargetLabel.textContent = "目标站点：" + String(ov.sync_target_url);
    }
  }

  /* ---------- Tab 1：多智能体深研 ---------- */

  function stanceBadgeClass(stance) {
    const s = String(stance || "");
    if (/多|顺风|看涨|买入|bull/i.test(s)) return "badge-bull";
    if (/空|逆风|警示|回避|bear/i.test(s)) return "badge-bear";
    return "badge-warn";
  }

  function renderAnalystCard(title, eyebrow, block) {
    if (!block) {
      return '<article class="analyst-card"><div class="sec-eyebrow">' + escapeHtml(eyebrow) + '</div><h3>' + escapeHtml(title) + '</h3><p class="analyst-body">当前报告未返回该栏细节</p></article>';
    }
    if (typeof block === "string") {
      return '<article class="analyst-card"><div class="sec-eyebrow">' + escapeHtml(eyebrow) + '</div><h3>' + escapeHtml(title) + '</h3><p class="analyst-body">' + escapeHtml(block) + "</p></article>";
    }
    const stance = block.stance || block.rating || block.tone || "";
    const summary = block.summary || block.thesis || block.conclusion || block.detail || "";
    const points = arr(block.points || block.key_points || block.bullets || block.evidence || block.highlights);
    return '<article class="analyst-card">' +
      '<div class="panel-head"><div class="sec-eyebrow" style="margin:0">' + escapeHtml(eyebrow) + "</div>" +
      (stance ? '<span class="badge ' + stanceBadgeClass(stance) + '">' + escapeHtml(stance) + "</span>" : "") + "</div>" +
      "<h3>" + escapeHtml(title) + "</h3>" +
      (summary ? '<p class="analyst-body" style="margin:0">' + escapeHtml(summary) + "</p>" : "") +
      (points.length ? '<ul class="analyst-points">' + points.map((pt) => "<li>" + escapeHtml(typeof pt === "string" ? pt : JSON.stringify(pt)) + "</li>").join("") + "</ul>" : "") +
      "</article>";
  }

  function unwrapDebateDoc(raw) {
    if (!raw || typeof raw !== "object") return {};
    if (raw.debate && typeof raw.debate === "object" && (raw.debate.symbol || raw.debate.trader_verdict || raw.debate.technical_analyst)) {
      return raw.debate;
    }
    return raw.report || raw.debate_result || raw;
  }

  function renderDebateResult(raw) {
    if (!dom.debateResultArea) return;
    const rep = unwrapDebateDoc(raw);
    if (!rep || (!rep.symbol && !rep.trader_verdict && !rep.technical_analyst)) {
      dom.debateResultArea.innerHTML = '<div class="panel empty-panel">输入标的代码或点击快捷标的，运行后将在此展示三路分析师简报、多空对抗辩论与交易员风控裁决。</div>';
      return;
    }

    const sym = rep.symbol || "";
    const effMode = rep.effective_mode || rep.mode;
    const modeLabel = effMode === "llm" ? "AGY 多智能体辩论" : "极速量化推演";
    const degradeNote = rep.degrade_reason || rep.fallback_note || "";
    const verdict = rep.trader_verdict || {};
    const debate = (rep.debate && typeof rep.debate === "object" && !rep.debate.symbol) ? rep.debate : {};
    const artFile = rep.report_file || rep.article_file || "";
    const syncInfo = (raw && raw.sync_result) || rep.sync_result || null;

    const bullList = Array.isArray(debate.bull_case) ? debate.bull_case : (debate.bull_case ? [debate.bull_case] : []);
    const bearList = Array.isArray(debate.bear_case) ? debate.bear_case : (debate.bear_case ? [debate.bear_case] : []);

    dom.debateResultArea.innerHTML =
      '<section class="panel">' +
      '<div class="panel-head">' +
      '<div><div class="sec-eyebrow">DEEP DEBATE REPORT</div><h2 class="sec-title">' + escapeHtml(sym) + (rep.name ? " · " + escapeHtml(rep.name) : "") + "</h2></div>" +
      '<div class="status-badges">' +
      '<span class="badge badge-outline">' + escapeHtml(modeLabel) + "</span>" +
      (degradeNote ? '<span class="badge badge-warn">' + escapeHtml(degradeNote) + "</span>" : "") +
      (!isBlank(rep.generated_at_bjt || rep.created_at) ? '<span class="panel-meta num-plain">' + escapeHtml(rep.generated_at_bjt || rep.created_at) + "</span>" : "") +
      "</div></div>" +
      (artFile ? '<p class="panel-meta" style="margin:8px 0 0">公共深研纪要已生成：<code class="mono-url">' + escapeHtml(artFile) + "</code>" + (syncInfo && syncInfo.ok ? " · 已同步至云端" : "") + "</p>" : "") +
      "</section>" +

      '<div class="analyst-grid-3">' +
      renderAnalystCard("技术与乖离", "TECHNICAL ANALYST", rep.technical_analyst) +
      renderAnalystCard("基本面与披露", "FUNDAMENTAL ANALYST", rep.fundamental_analyst) +
      renderAnalystCard("快讯与情绪", "SENTIMENT ANALYST", rep.sentiment_analyst) +
      "</div>" +

      '<section class="panel">' +
      '<div class="sec-eyebrow">ADVERSARIAL DEBATE</div>' +
      '<h2 class="sec-title" style="margin-bottom:12px">多空对抗辩论</h2>' +
      '<div class="debate-clash">' +
      '<div class="clash-box is-bull"><div class="clash-title">多方立论</div>' +
      (bullList.length ? '<ul class="analyst-points">' + bullList.map((x) => "<li>" + escapeHtml(x) + "</li>").join("") + "</ul>" : '<p class="analyst-body" style="margin:0">暂无多方补充</p>') + "</div>" +
      '<div class="clash-box is-bear"><div class="clash-title">空方反驳</div>' +
      (bearList.length ? '<ul class="analyst-points">' + bearList.map((x) => "<li>" + escapeHtml(x) + "</li>").join("") + "</ul>" : '<p class="analyst-body" style="margin:0">暂无空方补充</p>') + "</div>" +
      "</div>" +
      (!isBlank(debate.rebuttal) ? '<div class="rebuttal-box"><strong>交锋平衡裁定：</strong>' + escapeHtml(debate.rebuttal) + "</div>" : "") +
      "</section>" +

      '<section class="panel verdict-panel">' +
      '<div class="panel-head"><div><div class="sec-eyebrow">TRADER VERDICT</div><h2 class="sec-title">交易员与风控裁决</h2></div>' +
      (verdict.stance ? '<span class="badge ' + stanceBadgeClass(verdict.stance) + '">' + escapeHtml(verdict.stance) + "</span>" : "") +
      "</div>" +
      '<div class="verdict-grid">' +
      '<div class="verdict-cell"><span>综合置信度</span><b class="num-plain">' + escapeHtml(verdict.confidence != null ? String(verdict.confidence) : "—") + "</b></div>" +
      '<div class="verdict-cell"><span>风控检查结果</span><b>' + escapeHtml(verdict.checklist_passed === true ? "通过纪律检查" : verdict.checklist_passed === false ? "触发风控约束" : String(verdict.checklist_passed || "—")) + "</b></div>" +
      '<div class="verdict-cell"><span>仓位上限提示</span><b>' + escapeHtml(verdict.position_cap_hint || "严守单标的上限") + "</b></div>" +
      '<div class="verdict-cell"><span>观点失效边界</span><b>' + escapeHtml(verdict.invalidation_rule || "跌破关键均线或逻辑证伪") + "</b></div>" +
      "</div></section>";
  }

  function debateIdOf(item, idx) {
    if (!item || typeof item !== "object") return String(idx || 0);
    return String(item.id || item.report_file || item.article_file || (String(item.symbol || "debate") + "-" + (idx ?? 0)));
  }

  function renderDebateHistory() {
    if (!dom.debateHistoryList) return;
    if (!state.debates.length) {
      dom.debateHistoryList.innerHTML = '<p class="panel-meta">暂无历史深研留档</p>';
      return;
    }
    dom.debateHistoryList.innerHTML = state.debates.map((item, idx) => {
      const id = debateIdOf(item, idx);
      const effMode = item.effective_mode || item.mode;
      const v = item.trader_verdict || {};
      const active = state.activeDebateId === id;
      return '<button type="button" class="history-item' + (active ? " is-active" : "") + '" data-history-idx="' + idx + '">' +
        '<div class="history-top"><strong>' + escapeHtml(item.symbol || "标的") + '</strong><span class="badge ' + stanceBadgeClass(v.stance) + '">' + escapeHtml(v.stance || "已完成") + "</span></div>" +
        '<div class="history-top"><span class="panel-meta">' + escapeHtml(effMode === "llm" ? "llm 辩论" : "fast 量化") + '</span><span class="panel-meta num-plain">' + escapeHtml(item.generated_at_bjt || item.created_at || "") + "</span></div>" +
        "</button>";
    }).join("");
  }

  async function loadDebates(selectLatest) {
    try {
      const res = await apiJson("/api/desktop/debates");
      const list = Array.isArray(res) ? res : arr(res.debates || res.items);
      state.debates = list;
      if (selectLatest && list.length && !state.activeDebate) {
        state.activeDebate = list[0];
        state.activeDebateId = debateIdOf(list[0], 0);
        renderDebateResult(state.activeDebate);
      }
      renderDebateHistory();
    } catch (err) {
      if (dom.debateHistoryList) dom.debateHistoryList.innerHTML = '<p class="panel-meta">' + escapeHtml(err.message) + "</p>";
    }
  }

  async function runDebate() {
    if (state.busy.debate) return;
    const sym = String(dom.debateSymbolInput ? dom.debateSymbolInput.value : "").trim().toUpperCase();
    if (!sym) return showToast("请输入标的代码", "error");
    state.busy.debate = true;
    if (dom.runDebateBtn) {
      dom.runDebateBtn.disabled = true;
      dom.runDebateBtn.textContent = state.debateMode === "llm" ? "AGY 辩论中…" : "推演中…";
    }
    try {
      const res = await apiJson("/api/desktop/debate", {
        method: "POST",
        body: {
          symbol: sym,
          mode: state.debateMode,
          sync_to_web: Boolean(dom.debateSyncCheck && dom.debateSyncCheck.checked)
        }
      });
      const rep = unwrapDebateDoc(res);
      state.activeDebate = rep;
      state.activeDebateId = debateIdOf(rep, 0);
      renderDebateResult(rep);
      await loadDebates(false);
      await loadOverview();
      showToast(sym + " 多智能体深研完成", "ok");
    } catch (err) {
      showToast(err.message || "深研执行失败", "error");
    } finally {
      state.busy.debate = false;
      if (dom.runDebateBtn) {
        dom.runDebateBtn.disabled = false;
        dom.runDebateBtn.textContent = "运行多智能体深研";
      }
    }
  }

  /* ---------- Tab 2：OpenBB 数据与组件桥 ---------- */

  function renderStructuredData(targetEl, payload, title) {
    if (!targetEl) return;
    targetEl.hidden = false;
    const rows = Array.isArray(payload) ? payload : arr(payload && (payload.rows || payload.data || payload.results));
    let tableHtml = "";
    if (rows.length && typeof rows[0] === "object" && rows[0] !== null) {
      const cols = Object.keys(rows[0]).slice(0, 7);
      tableHtml = '<div style="overflow-x:auto"><table class="data-table"><thead><tr>' +
        cols.map((c) => "<th>" + escapeHtml(c) + "</th>").join("") +
        "</tr></thead><tbody>" +
        rows.slice(0, 20).map((r) => "<tr>" + cols.map((c) => {
          const val = r[c];
          const isNum = typeof val === "number";
          return '<td class="' + (isNum ? "is-num" : "") + '">' + escapeHtml(val == null ? "—" : (typeof val === "object" ? JSON.stringify(val) : String(val))) + "</td>";
        }).join("") + "</tr>").join("") +
        "</tbody></table></div>";
    }
    if (payload && typeof payload.raw === "string" && Object.keys(payload).length === 1) {
      targetEl.innerHTML =
        '<div class="panel-head"><strong>' + escapeHtml(title || "返回结果") + "</strong></div>" +
        "<pre>" + escapeHtml(payload.raw) + "</pre>";
      return;
    }
    targetEl.innerHTML =
      '<div class="panel-head"><strong>' + escapeHtml(title || "返回结果") + "</strong>" +
      (payload && payload.source ? '<span class="badge badge-outline">数据源 · ' + escapeHtml(payload.source) + "</span>" : "") +
      "</div>" +
      tableHtml +
      "<details" + (tableHtml ? "" : " open") + ' style="margin-top:8px"><summary class="btn-link">查看原始结构</summary><pre>' +
      escapeHtml(JSON.stringify(payload, null, 2)) + "</pre></details>";
  }

  async function runOpenbbQuery() {
    if (state.busy.obb) return;
    const dataset = dom.obbDatasetSelect ? dom.obbDatasetSelect.value : "equity_quote";
    const symbol = String(dom.obbSymbolInput ? dom.obbSymbolInput.value : "").trim();
    const allowSdk = Boolean(dom.obbAllowSdkCheck && dom.obbAllowSdkCheck.checked);
    state.busy.obb = true;
    if (dom.obbRunQueryBtn) { dom.obbRunQueryBtn.disabled = true; dom.obbRunQueryBtn.textContent = "查询中…"; }
    try {
      const qs = new URLSearchParams({ dataset, symbol, allow_sdk: allowSdk ? "1" : "0" });
      const res = await apiJson("/api/desktop/openbb?" + qs.toString());
      renderStructuredData(dom.obbQueryResult, res, dataset + (symbol ? " · " + symbol : ""));
    } catch (err) {
      if (dom.obbQueryResult) {
        dom.obbQueryResult.hidden = false;
        dom.obbQueryResult.innerHTML = '<p class="panel-meta" style="color:var(--bear)">' + escapeHtml(err.message) + "</p>";
      }
    } finally {
      state.busy.obb = false;
      if (dom.obbRunQueryBtn) { dom.obbRunQueryBtn.disabled = false; dom.obbRunQueryBtn.textContent = "查询数据"; }
    }
  }

  function normalizeWidgets(raw) {
    if (Array.isArray(raw)) return raw;
    if (raw && typeof raw === "object") {
      return Object.keys(raw).map((id) => Object.assign({ id }, raw[id] || {}));
    }
    return [];
  }

  async function loadWidgets() {
    if (!dom.obbWidgetsGrid) return;
    try {
      const raw = await publicJson("/widgets.json");
      const list = normalizeWidgets(raw);
      state.widgets = list;
      if (!list.length) {
        dom.obbWidgetsGrid.innerHTML = '<p class="panel-meta">未检测到已注册组件</p>';
        return;
      }
      dom.obbWidgetsGrid.innerHTML = list.map((w) => {
        const ep = w.endpoint || ("/obb/" + w.id);
        return '<article class="widget-card">' +
          '<div class="widget-head"><h3 class="widget-title">' + escapeHtml(w.name || w.id) + "</h3>" +
          (w.category ? '<span class="badge badge-outline">' + escapeHtml(w.category) + "</span>" : "") + "</div>" +
          '<p class="widget-desc">' + escapeHtml(w.description || "") + "</p>" +
          '<div class="widget-foot"><code class="mono-url">' + escapeHtml(ep) + "</code>" +
          '<button type="button" class="btn btn-sm" data-preview-widget="' + escapeHtml(ep) + '" data-widget-name="' + escapeHtml(w.name || w.id) + '">预览数据</button></div>' +
          "</article>";
      }).join("");
    } catch (err) {
      dom.obbWidgetsGrid.innerHTML = '<p class="panel-meta">' + escapeHtml(err.message) + "</p>";
    }
  }

  let widgetPreviewReqId = 0;

  async function previewWidget(ep, name) {
    const reqId = ++widgetPreviewReqId;
    try {
      const data = await publicJson(ep);
      if (reqId !== widgetPreviewReqId) return;
      renderStructuredData(dom.obbWidgetPreview, data, "组件预览 · " + (name || ep));
    } catch (err) {
      if (reqId !== widgetPreviewReqId) return;
      showToast(err.message || "组件预览失败", "error");
    }
  }

  /* ---------- Tab 3：AGY 协同台 ---------- */

  async function askAgy() {
    if (state.busy.agy) return;
    const prompt = String(dom.agyPromptInput ? dom.agyPromptInput.value : "").trim();
    if (!prompt) return showToast("请输入指令内容", "error");
    state.busy.agy = true;
    if (dom.agyAskBtn) { dom.agyAskBtn.disabled = true; dom.agyAskBtn.textContent = "等待 AGY 回复…"; }
    if (dom.agyAskMeta) dom.agyAskMeta.textContent = "正在通过 " + state.agyTransport.toUpperCase() + " 通道调用本机 AGY…";
    const t0 = performance.now();
    try {
      const res = await apiJson("/api/desktop/agy/ask", {
        method: "POST",
        body: { prompt, transport: state.agyTransport }
      });
      const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
      const reply = (res && (res.reply || res.text || res.output || res.answer)) || JSON.stringify(res, null, 2);
      if (dom.agyResponseBox) {
        dom.agyResponseBox.hidden = false;
        dom.agyResponseBox.textContent = reply;
      }
      if (dom.agyAskMeta) dom.agyAskMeta.textContent = "通道 " + (res.transport || state.agyTransport).toUpperCase() + " · 耗时 " + elapsed + " 秒";
    } catch (err) {
      if (dom.agyAskMeta) dom.agyAskMeta.textContent = err.message || "调用失败";
      showToast(err.message || "AGY 调用失败", "error");
    } finally {
      state.busy.agy = false;
      if (dom.agyAskBtn) { dom.agyAskBtn.disabled = false; dom.agyAskBtn.textContent = "发送到 AGY"; }
    }
  }

  /* ---------- Tab 4：云端同步 ---------- */

  async function runCloudSync() {
    if (state.busy.sync) return;
    state.busy.sync = true;
    if (dom.runCloudSyncBtn) { dom.runCloudSyncBtn.disabled = true; dom.runCloudSyncBtn.textContent = "正在重建并同步…"; }
    try {
      const res = await apiJson("/api/desktop/sync", { method: "POST", body: { refresh_state: true } });
      renderStructuredData(dom.syncResultBox, res, "云端同步执行结果");
      await loadOverview();
      showToast("云端同步已完成", "ok");
    } catch (err) {
      renderStructuredData(dom.syncResultBox, { ok: false, error: err.message }, "同步未能完成");
      showToast(err.message || "同步失败", "error");
    } finally {
      state.busy.sync = false;
      if (dom.runCloudSyncBtn) { dom.runCloudSyncBtn.disabled = false; dom.runCloudSyncBtn.textContent = "重建快照并同步至云端"; }
    }
  }

  /* ---------- 初始化与事件绑定 ---------- */

  async function loadOverview() {
    try {
      const ov = await apiJson("/api/desktop/overview");
      state.overview = ov;
      renderTopBadges(ov);
      renderQuickSymbols(ov);
      renderOpenbbProbe(ov);
      renderAgyOverview(ov);
      renderSyncOverview(ov);
    } catch (err) {
      renderTopBadges({});
      renderQuickSymbols({});
      renderSyncOverview({});
      if (dom.obbProbeSummary) dom.obbProbeSummary.textContent = err.message || "桌面服务未连接";
      if (dom.agyOverviewCard) dom.agyOverviewCard.textContent = err.message || "桌面服务未连接";
    }
  }

  function bindEvents() {
    document.addEventListener("click", (e) => {
      const t = e.target;
      if (!(t instanceof Element)) return;
      const hit = (sel) => t.closest(sel);
      let el;

      if (hit("#themeToggleBtn")) {
        const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
        return applyTheme(cur === "dark" ? "light" : "dark");
      }
      if ((el = hit("[data-dtab]"))) return setTab(el.getAttribute("data-dtab"));
      if ((el = hit("[data-jump-dtab]"))) return setTab(el.getAttribute("data-jump-dtab"));

      if ((el = hit("#debateQuickSymbols [data-quick-sym]"))) {
        const sym = el.getAttribute("data-quick-sym") || "";
        if (dom.debateSymbolInput) dom.debateSymbolInput.value = sym;
        renderQuickSymbols(state.overview);
        return undefined;
      }
      if ((el = hit("#debateModeSeg [data-dmode]"))) {
        state.debateMode = el.getAttribute("data-dmode") === "llm" ? "llm" : "fast";
        dom.debateModeSeg.querySelectorAll("[data-dmode]").forEach((b) => {
          const on = b.getAttribute("data-dmode") === state.debateMode;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-pressed", String(on));
        });
        return undefined;
      }
      if (hit("#runDebateBtn")) return runDebate();
      if (hit("#refreshDebatesBtn")) return loadDebates(false);
      if ((el = hit("#debateHistoryList [data-history-idx]"))) {
        const idx = Number(el.getAttribute("data-history-idx"));
        const item = state.debates[idx];
        if (item) {
          state.activeDebate = item;
          state.activeDebateId = debateIdOf(item, idx);
          if (dom.debateSymbolInput && item.symbol) dom.debateSymbolInput.value = item.symbol;
          renderQuickSymbols(state.overview);
          renderDebateResult(item);
          renderDebateHistory();
        }
        return undefined;
      }

      if (hit("#copyObbUrlBtn") && dom.obbMountUrl) return copyText(dom.obbMountUrl.textContent.trim(), "已复制 OpenBB 挂载地址");
      if (hit("#copyMcpCmdBtn") && dom.mcpCmdText) return copyText(dom.mcpCmdText.textContent.trim(), "已复制桌面 MCP 启动命令");
      if (hit("#obbRunQueryBtn")) return runOpenbbQuery();
      if (hit("#reloadWidgetsBtn")) return loadWidgets();
      if ((el = hit("[data-preview-widget]"))) {
        return previewWidget(el.getAttribute("data-preview-widget"), el.getAttribute("data-widget-name"));
      }

      if ((el = hit("#agyTransportSeg [data-transport]"))) {
        state.agyTransport = el.getAttribute("data-transport") === "cli" ? "cli" : "acp";
        dom.agyTransportSeg.querySelectorAll("[data-transport]").forEach((b) => {
          const on = b.getAttribute("data-transport") === state.agyTransport;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-pressed", String(on));
        });
        return undefined;
      }
      if ((el = hit("[data-agy-prompt]")) && dom.agyPromptInput) {
        dom.agyPromptInput.value = el.getAttribute("data-agy-prompt") || "";
        dom.agyPromptInput.focus();
        return undefined;
      }
      if (hit("#agyAskBtn")) return askAgy();
      if (hit("#runCloudSyncBtn")) return runCloudSync();
      return undefined;
    });

    if (dom.debateSymbolInput) {
      dom.debateSymbolInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.isComposing) {
          e.preventDefault();
          runDebate();
        }
      });
    }
    if (dom.obbSymbolInput) {
      dom.obbSymbolInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.isComposing) {
          e.preventDefault();
          runOpenbbQuery();
        }
      });
    }
    if (dom.agyPromptInput) {
      dom.agyPromptInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing) {
          e.preventDefault();
          askAgy();
        }
      });
    }
  }

  function init() {
    cacheDom();
    try { applyTheme(localStorage.getItem(THEME_KEY) || "light"); } catch (_) { applyTheme("light"); }
    if (dom.obbMountUrl && window.location.protocol.indexOf("http") === 0) {
      dom.obbMountUrl.textContent = window.location.origin;
    }
    bindEvents();
    const h = window.location.hash.replace(/^#/, "");
    setTab(["debate", "openbb", "agy", "sync"].includes(h) ? h : "debate");
    loadOverview();
    loadDebates(true);
    loadWidgets();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
