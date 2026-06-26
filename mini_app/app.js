const telegramInitData = window.Telegram?.WebApp?.initData || "";
window.Telegram?.WebApp?.ready();

const fmt = (value, digits = 2) => Number(value || 0).toLocaleString("ru-RU", { maximumFractionDigits: digits });
const signed = value => `${Number(value || 0) > 0 ? "+" : ""}${fmt(value)}`;
const priceState = new Map();
const candleCache = new Map();
const candleUpdatedAt = new Map();
const expandedTrades = new Set();
const chartAnimations = new Map();
const expandedMarkets = new Set();
const editingTrades = new Set();
const tradeChartIntervals = new Map();
const marketChartIntervals = new Map();
const chartIntervals = ["1m", "5m", "15m", "1h", "4h", "1d"];
let chartInterval = "1m";
let currentOpenTrades = [];
let currentTrades = [];
let currentJournal = [];
let currentPriceItems = [];
let priceTimer = null;
let marketTimer = null;
let currentSessions = [];
let currentWatchlist = [];
let activeSession = null;
let sessionRealizedPnl = 0;

document.querySelectorAll(".tab").forEach(button => {
  button.addEventListener("click", async () => {
    document.querySelectorAll(".tab").forEach(item => item.classList.remove("active"));
    document.querySelectorAll(".view").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.view).classList.add("active");
    if (button.dataset.view === "market") await loadMarketTop();
    if (button.dataset.view === "sessions") await loadSessions();
    if (button.dataset.view === "analytics") renderAnalytics();
  });
});

document.querySelectorAll(".seg").forEach(button => {
  button.addEventListener("click", async () => {
    document.querySelectorAll(".seg").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    await loadTrades(button.dataset.status);
  });
});

document.querySelectorAll(".tf-btn").forEach(button => {
  button.addEventListener("click", async () => {
    chartInterval = chartIntervals.includes(button.dataset.timeframe) ? button.dataset.timeframe : "1m";
    [...currentOpenTrades, ...currentTrades].forEach(row => tradeChartIntervals.set(Number(row.id), chartInterval));
    document.querySelectorAll(".tf-btn").forEach(item => item.classList.toggle("active", item.dataset.timeframe === chartInterval));
    renderPrices(lastPriceItems());
    renderTrades("openTrades", currentOpenTrades, true);
    renderJournal();
  });
});

document.getElementById("refreshBtn").addEventListener("click", loadAll);
document.getElementById("riskForm").addEventListener("input", calculateRisk);
document.getElementById("reviewBtn").addEventListener("click", reviewTrade);
document.getElementById("suggestBtn").addEventListener("click", suggestTrade);
document.getElementById("openTradeBtn").addEventListener("click", () => switchView("calculator"));
document.getElementById("sessionForm").addEventListener("submit", createSession);
document.getElementById("watchlistToggle").addEventListener("click", toggleWatchlistEditor);
document.getElementById("watchlistForm").addEventListener("submit", addWatchlistSymbol);
["journalSymbol", "journalFrom", "journalTo", "journalSearch"].forEach(id => {
  document.getElementById(id).addEventListener("input", renderJournal);
});

document.addEventListener("click", async event => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  if (target.tagName === "SELECT") return;
  const id = Number(target.dataset.id || 0);
  const symbol = cleanSymbol(target.dataset.symbol || "");
  switch (target.dataset.action) {
    case "fill-symbol": fillSymbol(symbol); break;
    case "remove-watchlist": await removeWatchlistSymbol(symbol); break;
    case "toggle-market": toggleMarketCard(target, symbol); break;
    case "set-market-timeframe": event.stopPropagation(); setMarketChartInterval(symbol, target.dataset.timeframe); break;
    case "analyze-market":
      event.stopPropagation(); fillSymbol(symbol); switchView("calculator"); break;
    case "stop-propagation": event.stopPropagation(); break;
    case "save-trade": event.stopPropagation(); await saveTradeEdit(id); break;
    case "toggle-edit": event.stopPropagation(); toggleEditTrade(id); break;
    case "set-trade-timeframe": event.stopPropagation(); setTradeChartInterval(id, target.dataset.timeframe, event); break;
    case "toggle-trade": toggleTrade(id, event); break;
    case "close-trade": event.stopPropagation(); await closeTrade(id); break;
    case "cancel-trade": event.stopPropagation(); await cancelTrade(id); break;
    case "archive-session": await archiveSession(id); break;
    case "activate-session": await activateSession(id); break;
    case "download-obsidian": event.stopPropagation(); await downloadObsidianExport(); break;
    case "download-session-obsidian": event.stopPropagation(); await downloadObsidianExport(id); break;
    case "model-placeholder": event.stopPropagation(); showModelPlaceholder(); break;
  }
});

document.addEventListener("change", event => {
  const target = event.target.closest('select[data-action="set-trade-timeframe"]');
  if (target) setTradeChartInterval(Number(target.dataset.id), target.value, event);
});

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (telegramInitData) headers.set("Authorization", `tma ${telegramInitData}`);
  const method = String(options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && !headers.has("Idempotency-Key")) {
    const fallback = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    headers.set("Idempotency-Key", globalThis.crypto?.randomUUID?.() || fallback);
  }
  return fetch(path, { ...options, headers });
}

async function api(path, options = undefined) {
  const response = await apiFetch(path, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function jsonRequest(method, payload) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

function switchView(view) {
  const button = document.querySelector(`.tab[data-view="${view}"]`);
  button?.click();
}

async function loadDashboard() {
  const data = await api("/api/dashboard");
  currentOpenTrades = data.open_trades;
  document.getElementById("netPnl").textContent = `${signed(data.stats.net_pnl)} USDT`;
  document.getElementById("netPnl").className = data.stats.net_pnl >= 0 ? "positive" : "negative";
  document.getElementById("winrate").textContent = `${fmt(data.stats.winrate)}%`;
  document.getElementById("openRisk").textContent = `${fmt(data.open_risk)} USDT`;
  document.getElementById("alertCount").textContent = data.active_alerts.length;
  currentWatchlist = data.watchlist || [];
  renderWatchlist();
  document.getElementById("planText").textContent = data.plan ? `${data.plan.allowed_symbols || "без монет"} | риск ${data.plan.max_daily_risk_percent}% | стоп ${fmt(data.plan.max_daily_loss)} USDT` : "План дня не задан";
  document.querySelector(".eyebrow").textContent = `Кабинет трейдера · ${data.session?.name || "без активной сессии"}`;
  activeSession = data.session || null;
  sessionRealizedPnl = Number(data.stats.net_pnl || 0);
  updateSessionBalance();
  renderTrades("openTrades", currentOpenTrades, true);
  await loadPrices(data.watchlist, data.open_trades);
}

function renderWatchlist() {
  const target = document.getElementById("watchlist");
  target.innerHTML = currentWatchlist.map(rawSymbol => {
    const symbol = cleanSymbol(rawSymbol);
    return `
    <span class="favorite-item">
      <button class="chip favorite-chip" type="button" data-action="fill-symbol" data-symbol="${symbol}">★ ${symbol}</button>
      <button class="favorite-remove" type="button" title="Убрать ${symbol}" aria-label="Убрать ${symbol}" data-action="remove-watchlist" data-symbol="${symbol}">×</button>
    </span>
  `;
  }).join("") || "<span class='chip'>Список пуст</span>";
}

function toggleWatchlistEditor() {
  const form = document.getElementById("watchlistForm");
  form.hidden = !form.hidden;
  if (!form.hidden) document.getElementById("watchlistSymbol").focus();
}

async function addWatchlistSymbol(event) {
  event.preventDefault();
  const input = document.getElementById("watchlistSymbol");
  const symbol = input.value.trim();
  if (!symbol) return;
  try {
    const data = await api("/api/watchlist", jsonRequest("POST", { symbol }));
    currentWatchlist = data.items || [];
    input.value = "";
    document.getElementById("watchlistForm").hidden = true;
    renderWatchlist();
    await loadPrices(currentWatchlist, currentOpenTrades);
  } catch {
    alert("Не удалось добавить монету");
  }
}

async function removeWatchlistSymbol(symbol) {
  try {
    const response = await apiFetch(`/api/watchlist?symbol=${encodeURIComponent(symbol)}`, { method: "DELETE" });
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json();
    currentWatchlist = data.items || [];
    renderWatchlist();
    await loadPrices(currentWatchlist, currentOpenTrades);
  } catch {
    alert("Не удалось убрать монету");
  }
}

async function loadPrices(watchlist = [], openTrades = []) {
  const symbols = [...new Set([
    ...watchlist,
    ...openTrades.map(trade => trade.symbol),
    ...currentOpenTrades.map(trade => trade.symbol),
  ].filter(Boolean))];
  const query = symbols.length ? `&symbols=${encodeURIComponent(symbols.join(","))}` : "";
  const status = document.getElementById("priceStatus");
  try {
    const data = await api(`/api/prices?${query.replace(/^&/, "")}`);
    const items = Array.isArray(data.items) ? data.items : [];
    const now = new Date();
    status.textContent = `live ${now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    status.className = "live-status is-live";
    try {
      renderPrices(items);
      refreshTradeMetrics(currentOpenTrades);
    } catch (renderError) {
      console.error("Price render failed", renderError);
      status.textContent = "live, ошибка UI";
      status.className = "live-status is-offline";
    }
  } catch (error) {
    console.error("Price request failed", error);
    status.textContent = "нет связи";
    status.className = "live-status is-offline";
  }
}

function renderPrices(items) {
  currentPriceItems = items;
  const target = document.getElementById("priceTape");
  target.innerHTML = items.map(item => {
    const symbol = cleanSymbol(item.symbol);
    const previous = priceState.get(symbol);
    const direction = previous == null ? "" : item.price > previous ? "tick-up" : item.price < previous ? "tick-down" : "";
    priceState.set(symbol, item.price);
    const changeClass = Number(item.price_change_percent) >= 0 ? "positive" : "negative";
    return `
      <button class="price-card ${direction}" type="button" data-action="fill-symbol" data-symbol="${symbol}">
        <span class="price-symbol">${symbol.replace("USDT", "")}<small>USDT</small></span>
        <strong>${fmt(item.price, item.price > 10 ? 2 : 6)}</strong>
        <span class="${changeClass}">${signed(item.price_change_percent)}% за 24ч</span>
        <small>диапазон ${fmt(item.intraday_range_percent)}% · high ${fmt(item.high_price)} · low ${fmt(item.low_price)}</small>
        <canvas id="price-chart-${symbol}" class="mini-trend-chart" width="360" height="118"></canvas>
        <em id="price-trend-${symbol}" class="trend-caption">загрузка ${chartIntervalLabel()}</em>
      </button>
    `;
  }).join("") || "<div class='price-card empty-price'>Добавь монеты в watchlist</div>";
  items.forEach(item => loadMiniTrend(cleanSymbol(item.symbol), `price-chart-${cleanSymbol(item.symbol)}`, `price-trend-${cleanSymbol(item.symbol)}`));
}

function lastPriceItems() {
  return currentPriceItems.length ? currentPriceItems : [];
}

async function loadMarketTop() {
  const status = document.getElementById("marketStatus");
  try {
    const data = await api("/api/market/top?limit=30");
    status.textContent = "live";
    status.className = "live-status is-live";
    document.getElementById("marketTop").innerHTML = data.items.map((item, index) => {
      const position = ((item.price - item.low_price) / Math.max(item.high_price - item.low_price, 0.000001)) * 100;
      const direction = item.price_change_percent > 1 ? "бычий импульс" : item.price_change_percent < -1 ? "медвежий импульс" : "нейтрально";
      const marketSymbol = cleanSymbol(item.symbol);
      const scannerScore = Math.min(99, Math.round(44 + Math.min(item.intraday_range_percent, 15) * 2.2 + Math.min(Math.log10(Math.max(item.quote_volume / 1000000, 1)), 4) * 4));
      return `<article class="market-card ${expandedMarkets.has(marketSymbol) ? "expanded" : ""}" data-action="toggle-market" data-symbol="${marketSymbol}">
        <div class="market-title"><span class="market-rank">${index + 1}</span><strong>${marketSymbol.replace("USDT", "")}<small>/USDT</small></strong></div>
        <span class="scanner-score">${scannerScore}<small>/100</small></span>
        <b>${fmt(item.price, item.price > 10 ? 2 : 6)}</b>
        <small class="market-change ${item.price_change_percent >= 0 ? "positive" : "negative"}">${item.price_change_percent >= 0 ? "↑" : "↓"} ${signed(item.price_change_percent)}% · 24ч</small>
        <span class="direction-pill ${item.price_change_percent >= 0 ? "positive" : "negative"}">${direction}</span>
        <small>VOL ${fmt(item.quote_volume / 1000000, 0)}M · RANGE ${fmt(item.intraday_range_percent)}%</small>
        <progress class="range-meter market-range" max="100" value="${Math.max(0, Math.min(100, position))}"></progress>
        <small class="range-labels"><i>${fmt(item.low_price, 4)}</i><i>${escapeHtml(item.exchange)}</i><i>${fmt(item.high_price, 4)}</i></small>
        <div class="market-detail">
          <small>Цена на ${fmt(position, 0)}% суточного диапазона</small>
          <div class="market-timeframe-switch" data-action="stop-propagation" aria-label="Таймфрейм графика монеты">
            ${chartIntervals.map(tf => `<button type="button" data-action="set-market-timeframe" data-symbol="${marketSymbol}" data-timeframe="${tf}" class="${marketChartInterval(marketSymbol) === tf ? "active" : ""}">${tf.toUpperCase()}</button>`).join("")}
          </div>
          <canvas id="market-chart-${cleanSymbol(item.symbol)}" class="mini-trend-chart" width="360" height="118"></canvas>
          <em id="market-trend-${cleanSymbol(item.symbol)}" class="trend-caption">Нажми для графика</em>
          <button class="mini-action" data-action="analyze-market" data-symbol="${cleanSymbol(item.symbol)}">Разобрать вход</button>
        </div>
      </article>`;
    }).join("");
  } catch (error) {
    status.textContent = "нет связи";
    status.className = "live-status is-offline";
  }
}

function toggleMarketCard(card, symbol) {
  card.classList.toggle("expanded");
  if (card.classList.contains("expanded")) {
    expandedMarkets.add(symbol);
    loadMiniTrend(symbol, `market-chart-${symbol}`, `market-trend-${symbol}`, marketChartInterval(symbol));
  } else {
    expandedMarkets.delete(symbol);
  }
}

function marketChartInterval(symbol) {
  return marketChartIntervals.get(cleanSymbol(symbol)) || chartInterval;
}

function setMarketChartInterval(symbol, interval) {
  const clean = cleanSymbol(symbol);
  if (!clean || !chartIntervals.includes(interval)) return;
  marketChartIntervals.set(clean, interval);
  const card = document.querySelector(`.market-card[data-symbol="${clean}"]`);
  if (card) {
    card.querySelectorAll("[data-action='set-market-timeframe']").forEach(button => {
      button.classList.toggle("active", button.dataset.timeframe === interval);
    });
  }
  loadMiniTrend(clean, `market-chart-${clean}`, `market-trend-${clean}`, interval, true);
}

async function loadTrades(status = "") {
  const query = status ? `&status=${status}` : "";
  const data = await api(`/api/trades?${query.replace(/^&/, "")}`);
  currentTrades = data.items;
  renderTrades("tradesTable", currentTrades, false);
  updateJournalResults();
}

function renderTrades(targetId, rows, compact = false) {
  document.getElementById(targetId).innerHTML = rows.map(row => renderTradeCard(row, compact)).join("") || emptyRow("Нет данных");
  hydrateProtectedImages(document.getElementById(targetId));
  rows.forEach(row => {
    if (row.status === "open" || expandedTrades.has(Number(row.id))) loadTradeChart(row);
  });
}

function renderTradeCard(row, compact) {
  const tradeId = safeId(row.id);
  const symbol = cleanSymbol(row.symbol);
  const side = safeTradeSide(row.side);
  const status = safeTradeStatus(row.status);
  const livePrice = priceState.get(symbol);
  const isOpen = status === "open";
  const markPrice = isOpen ? livePrice : row.exit_price;
  const pnl = markPrice ? calcPnl(row, markPrice) : Number(row.pnl || 0);
  const pnlPct = markPrice ? pnlPercent(row, markPrice) : 0;
  const margin = Number(row.entry_price) * Number(row.quantity) / Math.max(Number(row.leverage || 1), 1);
  const marginRoi = margin > 0 ? pnl / margin * 100 : 0;
  const progress = tradeProgress(row, markPrice);
  const pnlClass = pnl >= 0 ? "positive" : "negative";
  const attachmentStrip = tradeAttachmentImages(row.attachments || []);
  const tradeTf = tradeChartInterval(row);
  const editPanel = isOpen ? `
    <div class="trade-edit" data-action="stop-propagation">
      <div class="edit-grid">
        <label>Вход<input id="edit-entry-${tradeId}" type="number" step="any" value="${numberAttr(row.entry_price)}"></label>
        <label>Стоп<input id="edit-stop-${tradeId}" type="number" step="any" value="${numberAttr(row.stop_price)}"></label>
        <label>Тейк<input id="edit-target-${tradeId}" type="number" step="any" value="${numberAttr(row.target_price)}"></label>
        <label>Количество<input id="edit-qty-${tradeId}" type="number" step="any" value="${numberAttr(row.quantity)}"></label>
        <label>Таймфрейм<select id="edit-timeframe-${tradeId}" data-action="set-trade-timeframe" data-id="${tradeId}">${chartIntervals.map(tf => `<option ${tradeTf === tf ? "selected" : ""}>${tf}</option>`).join("")}</select></label>
        <label>Комментарий<input id="edit-note-${tradeId}" placeholder="Почему перенес стоп или тейк"></label>
        <label class="photo-picker">Добавить фото<input id="edit-photo-${tradeId}" type="file" accept="image/jpeg,image/png,image/webp" multiple></label>
      </div>
      <div class="edit-actions"><button class="primary-action compact" data-action="save-trade" data-id="${tradeId}">Сохранить</button><button class="mini-action" data-action="toggle-edit" data-id="${tradeId}">Отмена</button></div>
    </div>` : "";
  const details = `
    <div class="trade-details">
      <div class="trade-chart-panel">
        <div class="trade-timeframe-switch" aria-label="Таймфрейм графика сделки">
          ${chartIntervals.map(tf => `<button type="button" data-trade-timeframe="${tf}" data-action="set-trade-timeframe" data-id="${tradeId}" data-timeframe="${tf}" class="${tradeTf === tf ? "active" : ""}">${tf.toUpperCase()}</button>`).join("")}
        </div>
        <canvas id="chart-${tradeId}" class="trade-chart" width="760" height="260"></canvas>
      </div>
      <div class="trade-stats">
        <span>Цена сейчас <b data-trade-current-price>${markPrice ? fmt(markPrice, markPrice > 10 ? 2 : 6) : "-"}</b></span>
        <span>До стопа <b data-trade-stop-distance>${distanceTo(row.stop_price, markPrice)}</b></span>
        <span>До тейка <b data-trade-target-distance>${row.target_price ? distanceTo(row.target_price, markPrice) : "-"}</b></span>
        <span>R/R <b>${rrText(row)}</b></span>
        <span>Таймфрейм <b data-trade-timeframe-label>${chartIntervalLabel(tradeTf)}</b></span>
        <span>Источник <b>Binance Futures</b></span>
        <span>Количество <b>${fmt(row.quantity, 8)} ${symbol.replace("USDT", "")}</b></span>
        <span>Плечо <b>${fmt(row.leverage || 1, 2)}x</b></span>
        <span>Маржа <b>${fmt(margin, 2)} USDT</b></span>
        <span>ROI на маржу <b data-trade-margin-roi class="${pnlClass}">${markPrice || status === "closed" ? signed(marginRoi) : "-"}%</b></span>
        <span>Теги <b>${escapeHtml(row.tags || `coin:${symbol.replace("USDT", "")}`)}</b></span>
      </div>
      ${attachmentStrip ? `<div class="trade-media">${attachmentStrip}</div>` : ""}
    </div>
    ${editPanel}
  `;
  return `
    <article class="trade-card ${compact ? "compact-trade" : ""} ${expandedTrades.has(Number(tradeId)) ? "expanded" : ""} ${editingTrades.has(Number(tradeId)) ? "editing" : ""}" data-trade-id="${tradeId}" data-action="toggle-trade" data-id="${tradeId}">
      <div class="trade-main">
        <strong>#${tradeId} ${symbol}<small>${side.toUpperCase()} ${status}${row.close_reason ? ` · ${escapeHtml(closeReasonText(row.close_reason))}` : ""}</small></strong>
        <span>Entry ${fmt(row.entry_price, 6)}<small>Stop ${fmt(row.stop_price, 6)}</small></span>
        <span>Target ${row.target_price ? fmt(row.target_price, 6) : "-"}</span>
        <span data-trade-pnl class="${pnlClass}">${markPrice ? signed(pnl) : (row.pnl == null ? "-" : signed(row.pnl))} USDT<small data-trade-pnl-percent>${markPrice ? signed(pnlPct) : "0"}%</small></span>
        <span class="trade-actions" data-action="stop-propagation">
          ${isOpen ? `<button class="mini-action" data-action="toggle-edit" data-id="${tradeId}">Изменить</button><button class="mini-action" data-action="close-trade" data-id="${tradeId}">Закрыть</button><button class="mini-action" data-action="cancel-trade" data-id="${tradeId}">Отменить</button>` : ""}
        </span>
      </div>
      <progress class="progress-rail" data-trade-progress max="100" value="${progress}"></progress>
      ${details}
    </article>
  `;
}

function refreshTradeMetrics(rows) {
  rows.forEach(row => {
    const markPrice = priceState.get(cleanSymbol(row.symbol));
    if (!markPrice) return;
    const pnl = calcPnl(row, markPrice);
    const pnlPct = pnlPercent(row, markPrice);
    const pnlClass = pnl >= 0 ? "positive" : "negative";
    const margin = Number(row.entry_price) * Number(row.quantity) / Math.max(Number(row.leverage || 1), 1);
    const marginRoi = margin > 0 ? pnl / margin * 100 : 0;
    document.querySelectorAll(`[data-trade-id="${row.id}"]`).forEach(card => {
      const pnlNode = card.querySelector("[data-trade-pnl]");
      if (pnlNode) {
        pnlNode.className = pnlClass;
        pnlNode.firstChild.textContent = `${signed(pnl)} USDT`;
      }
      const pctNode = card.querySelector("[data-trade-pnl-percent]");
      if (pctNode) pctNode.textContent = `${signed(pnlPct)}%`;
      const priceNode = card.querySelector("[data-trade-current-price]");
      if (priceNode) priceNode.textContent = fmt(markPrice, markPrice > 10 ? 2 : 6);
      const stopNode = card.querySelector("[data-trade-stop-distance]");
      if (stopNode) stopNode.textContent = distanceTo(row.stop_price, markPrice);
      const targetNode = card.querySelector("[data-trade-target-distance]");
      if (targetNode) targetNode.textContent = row.target_price ? distanceTo(row.target_price, markPrice) : "-";
      const roiNode = card.querySelector("[data-trade-margin-roi]");
      if (roiNode) { roiNode.textContent = `${signed(marginRoi)}%`; roiNode.className = pnlClass; }
      const progressNode = card.querySelector("[data-trade-progress]");
      if (progressNode) progressNode.value = tradeProgress(row, markPrice);
    });
    if (expandedTrades.has(Number(row.id))) loadTradeChart(row);
  });
  updateSessionBalance();
  updateJournalResults();
}

function updateSessionBalance() {
  const balanceNode = document.getElementById("sessionBalance");
  const detailsNode = document.getElementById("sessionBalanceDetails");
  const progressNode = document.getElementById("sessionBalanceProgress");
  if (!balanceNode || !activeSession) {
    if (balanceNode) balanceNode.textContent = "—";
    if (detailsNode) detailsNode.textContent = "Нет активной сессии";
    if (progressNode) progressNode.value = 0;
    return;
  }
  const unrealized = currentOpenTrades.reduce((sum, trade) => {
    const mark = priceState.get(cleanSymbol(trade.symbol));
    return sum + (mark ? calcPnl(trade, mark) : 0);
  }, 0);
  const start = Number(activeSession.start_balance || 0);
  const equity = start + sessionRealizedPnl + unrealized;
  const totalPnl = sessionRealizedPnl + unrealized;
  const target = Number(activeSession.target_balance || 0);
  const progress = target > start ? (equity - start) / (target - start) * 100 : 0;
  balanceNode.textContent = `${fmt(equity)} USDT`;
  balanceNode.className = totalPnl >= 0 ? "positive" : "negative";
  detailsNode.textContent = `Старт ${fmt(start)} · закрыто ${signed(sessionRealizedPnl)} · открыто ${signed(unrealized)}`;
  progressNode.value = Math.max(0, Math.min(100, progress));
}

function toggleTrade(id, event) {
  const card = event?.currentTarget || document.querySelector(`[data-trade-id="${id}"]`);
  if (!card) return;
  card.classList.toggle("expanded");
  if (card.classList.contains("expanded")) expandedTrades.add(Number(id));
  else expandedTrades.delete(Number(id));
  const row = [...currentOpenTrades, ...currentTrades].find(item => Number(item.id) === Number(id));
  if (row) loadTradeChart(row, true);
}

async function loadTradeChart(row, force = false) {
  const canvases = [...document.querySelectorAll(`[data-trade-id="${row.id}"] canvas.trade-chart`)];
  if (!canvases.length) return;
  const symbol = cleanSymbol(row.symbol);
  const interval = tradeChartInterval(row);
  try {
    const cacheKey = `trade:${row.id}:${interval}`;
    const stale = Date.now() - (candleUpdatedAt.get(cacheKey) || 0) > 10000;
    if (force || stale || !candleCache.has(cacheKey)) {
      const data = await api(`/api/trades/${row.id}/chart?interval=${interval}`);
      candleCache.set(cacheKey, data.items);
      candleUpdatedAt.set(cacheKey, Date.now());
    }
    canvases.forEach((canvas, index) => {
      if (row.status === "closed" && candleCache.get(cacheKey)?.length > 2) animateTradeChart(canvas, candleCache.get(cacheKey), row, interval, `${row.id}-${index}`);
      else drawTradeChart(canvas, candleCache.get(cacheKey), row, interval);
    });
  } catch {
    canvases.forEach(canvas => drawTradeChart(canvas, [], row, interval));
  }
}

function tradeChartInterval(row) {
  const saved = String(row?.timeframe || "").toLowerCase();
  return tradeChartIntervals.get(Number(row?.id)) || (chartIntervals.includes(saved) ? saved : chartInterval);
}

function setTradeChartInterval(id, interval, event) {
  event?.stopPropagation();
  if (!chartIntervals.includes(interval)) return;
  const numericId = Number(id);
  tradeChartIntervals.set(numericId, interval);
  document.querySelectorAll(`[data-trade-id="${numericId}"]`).forEach(card => {
    card.querySelectorAll("[data-trade-timeframe]").forEach(button => button.classList.toggle("active", button.dataset.tradeTimeframe === interval));
    const label = card.querySelector("[data-trade-timeframe-label]");
    if (label) label.textContent = chartIntervalLabel(interval);
  });
  const row = [...currentOpenTrades, ...currentTrades].find(item => Number(item.id) === numericId);
  if (row) loadTradeChart(row, true);
}

function animateTradeChart(canvas, candles, row, interval, animationKey = row.id) {
  clearInterval(chartAnimations.get(animationKey));
  let count = 2;
  const step = Math.max(1, Math.ceil(candles.length / 45));
  const timer = setInterval(() => {
    count += step;
    if (count >= candles.length) count = 2;
    drawTradeChart(canvas, candles.slice(0, count), row, interval);
  }, 170);
  chartAnimations.set(animationKey, timer);
}

async function loadMiniTrend(symbol, canvasId, captionId, interval = chartInterval, force = false) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !symbol) return;
  const caption = document.getElementById(captionId);
  try {
    const cacheKey = candlesKey(symbol, interval);
    const stale = Date.now() - (candleUpdatedAt.get(cacheKey) || 0) > 10000;
    if (force || stale || !candleCache.has(cacheKey)) {
      const data = await api(`/api/klines?symbol=${symbol}&interval=${interval}&limit=80`);
      candleCache.set(cacheKey, data.items);
      candleUpdatedAt.set(cacheKey, Date.now());
    }
    const trend = drawMiniTrend(canvas, candleCache.get(cacheKey));
    if (caption) caption.textContent = `${chartIntervalLabel(interval)} · ${trend.label} · ${signed(trend.change)}%`;
    if (caption) caption.className = `trend-caption ${trend.className}`;
  } catch {
    drawMiniTrend(canvas, []);
    if (caption) caption.textContent = `${chartIntervalLabel(interval)} · нет данных`;
  }
}

function candlesKey(symbol, interval = chartInterval) {
  return `${cleanSymbol(symbol)}:${interval}`;
}

function drawTradeChart(canvas, candles, row, interval = chartInterval) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "rgba(5, 8, 16, .7)";
  ctx.fillRect(0, 0, w, h);
  const rows = (candles || []).map(c => ({
    time: Number(c.open_time),
    open: Number(c.open),
    high: Number(c.high),
    low: Number(c.low),
    close: Number(c.close),
  })).filter(c => [c.open, c.high, c.low, c.close].every(Number.isFinite));
  if (rows.length < 2) {
    ctx.fillStyle = "rgba(177, 191, 222, .72)";
    ctx.font = "13px system-ui";
    ctx.fillText("Загружаю свечи...", 16, h / 2);
    return;
  }
  const prices = rows.map(c => c.close);
  const levels = [row?.entry_price, row?.stop_price, row?.target_price]
    .filter(value => value !== null && value !== undefined && value !== "")
    .map(Number)
    .filter(value => Number.isFinite(value) && value > 0);
  const scaledPrices = [...rows.flatMap(c => [c.high, c.low]), ...levels];
  const rawMax = Math.max(...scaledPrices);
  const rawMin = Math.min(...scaledPrices);
  const padding = Math.max((rawMax - rawMin) * .18, prices[prices.length - 1] * .0012);
  const max = rawMax + padding;
  const min = rawMin - padding;
  const y = price => h - ((price - min) / Math.max(max - min, 0.000001)) * (h - 22) - 11;
  const safeY = price => Math.max(11, Math.min(h - 11, y(price)));
  const plotStart = 12;
  const plotEnd = w * 0.74;
  ctx.fillStyle = "rgba(168, 85, 247, .055)";
  ctx.fillRect(plotEnd, 0, w - plotEnd, h);
  ctx.fillStyle = "rgba(177, 191, 222, .35)";
  ctx.font = "10px system-ui";
  ctx.fillText("ПРОСТРАНСТВО ЦЕНЫ", plotEnd + 10, 16);
  ctx.strokeStyle = "rgba(177, 191, 222, .1)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const gridY = (h / 4) * i;
    ctx.beginPath();
    ctx.moveTo(plotStart, gridY);
    ctx.lineTo(plotEnd, gridY);
    ctx.stroke();
  }
  const slot = (plotEnd - plotStart) / Math.max(rows.length, 1);
  const bodyWidth = Math.max(1.2, Math.min(5, slot * .68));
  rows.forEach((candle, index) => {
    const x = plotStart + slot * (index + .5);
    const color = candle.close >= candle.open ? "#55e08a" : "#ff657d";
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, y(candle.high));
    ctx.lineTo(x, y(candle.low));
    ctx.stroke();
    const bodyTop = y(Math.max(candle.open, candle.close));
    const bodyBottom = y(Math.min(candle.open, candle.close));
    ctx.fillRect(x - bodyWidth / 2, bodyTop, bodyWidth, Math.max(1.4, bodyBottom - bodyTop));
  });
  ctx.fillStyle = "rgba(177, 191, 222, .68)";
  ctx.font = "10px system-ui";
  ctx.fillText(`BINANCE FUTURES · ${interval.toUpperCase()} · ${rows.length} СВЕЧЕЙ`, plotStart, h - 5);
  if (row?.entry_price) drawLevel(ctx, w, safeY(row.entry_price), `ВХОД ${fmt(row.entry_price, 6)}`, "#43d7ff");
  if (row?.stop_price) drawLevel(ctx, w, safeY(row.stop_price), `СТОП ${fmt(row.stop_price, 6)}`, "#ff657d");
  if (row?.target_price) drawLevel(ctx, w, safeY(row.target_price), `ТЕЙК ${fmt(row.target_price, 6)}`, "#55e08a");
}

function drawMiniTrend(canvas, candles) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "rgba(3, 7, 15, .72)";
  ctx.fillRect(0, 0, w, h);

  const prices = (candles || []).map(c => Number(c.close)).filter(Number.isFinite);
  if (prices.length < 2) {
    ctx.strokeStyle = "rgba(154, 166, 188, .34)";
    ctx.beginPath();
    ctx.moveTo(10, h / 2);
    ctx.lineTo(w - 10, h / 2);
    ctx.stroke();
    return { label: "нет данных", change: 0, className: "warn" };
  }

  const first = prices[0];
  const last = prices[prices.length - 1];
  const change = ((last - first) / Math.max(first, 0.000001)) * 100;
  const max = Math.max(...prices);
  const min = Math.min(...prices);
  const color = change > 0.08 ? "#55e08a" : change < -0.08 ? "#ff657d" : "#ffc45c";
  const label = change > 0.08 ? "тренд вверх" : change < -0.08 ? "тренд вниз" : "флет";
  const className = change > 0.08 ? "positive" : change < -0.08 ? "negative" : "warn";
  const y = price => h - ((price - min) / Math.max(max - min, 0.000001)) * (h - 22) - 11;

  ctx.strokeStyle = "rgba(177, 191, 222, .10)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const gy = (h / 4) * i;
    ctx.beginPath();
    ctx.moveTo(8, gy);
    ctx.lineTo(w - 8, gy);
    ctx.stroke();
  }

  ctx.strokeStyle = color;
  ctx.lineWidth = 2.4;
  ctx.beginPath();
  const plotEnd = w * 0.64;
  prices.forEach((price, index) => {
    const x = 9 + index * ((plotEnd - 9) / Math.max(prices.length - 1, 1));
    if (index === 0) ctx.moveTo(x, y(price));
    else ctx.lineTo(x, y(price));
  });
  ctx.stroke();

  const startY = y(first);
  const endY = y(last);
  ctx.fillStyle = "rgba(246, 247, 251, .74)";
  ctx.beginPath();
  ctx.arc(9, startY, 3, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(plotEnd, endY, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = "11px system-ui";
  ctx.fillText(chartIntervalLabel(), 9, 15);

  return { label, change, className };
}

function drawLevel(ctx, width, y, label, color) {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.setLineDash([6, 6]);
  ctx.beginPath();
  ctx.moveTo(0, y);
  ctx.lineTo(width, y);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = "bold 11px system-ui";
  const labelWidth = ctx.measureText(label).width + 12;
  const labelY = Math.max(2, Math.min(y - 17, ctx.canvas.height - 19));
  ctx.fillStyle = "rgba(4, 8, 18, .9)";
  ctx.fillRect(6, labelY, labelWidth, 17);
  ctx.strokeStyle = color;
  ctx.strokeRect(6, labelY, labelWidth, 17);
  ctx.fillStyle = color;
  ctx.fillText(label, 12, labelY + 12);
}

function chartIntervalLabel(interval = chartInterval) {
  return { "1d": "1 день", "4h": "4 часа", "1h": "1 час", "15m": "15 мин", "5m": "5 мин", "1m": "1 мин" }[interval] || interval;
}

async function loadJournal() {
  const data = await api("/api/journal");
  currentJournal = data.items;
  renderJournal();
}

function renderJournal() {
  const symbolFilter = cleanSymbol(document.getElementById("journalSymbol").value);
  const dateFrom = document.getElementById("journalFrom").value;
  const dateTo = document.getElementById("journalTo").value;
  const search = document.getElementById("journalSearch").value.trim().toLowerCase();
  const rows = currentJournal.filter(row => {
    const created = String(row.created_at || "").slice(0, 10);
    const symbolOk = !symbolFilter || cleanSymbol(row.symbol).includes(symbolFilter);
    const fromOk = !dateFrom || created >= dateFrom;
    const toOk = !dateTo || created <= dateTo;
    const text = `${row.symbol} ${row.description} ${row.theory} ${row.outcome}`.toLowerCase();
    const searchOk = !search || text.includes(search);
    return symbolOk && fromOk && toOk && searchOk;
  });
  document.getElementById("journalList").innerHTML = rows.map(row => {
    const result = journalResult(row);
    const entryId = safeId(row.id);
    return `
    <article class="journal-card ${result.className}">
      <div>
        <strong>${escapeHtml(cleanSymbol(row.symbol) || "-")}<small>${escapeHtml(row.outcome)} · ${escapeHtml(row.created_at)}</small></strong>
        <p>${escapeHtml(row.description || "-")}</p>
        <div class="chips"><small class="chip">coin:${cleanSymbol(row.symbol).replace("USDT", "")}</small>${row.session_id ? `<small class="chip">session:${escapeHtml(sessionName(row.session_id))}</small>` : ""}</div>
        <small>${escapeHtml(row.theory || "")}</small>
      </div>
      <div class="journal-visuals">
        <div class="journal-result ${result.className}" data-journal-entry-id="${entryId}"><i>${result.icon}</i><strong>${result.label}</strong><span>${result.amount}</span></div>
        <canvas id="journal-chart-${entryId}" class="journal-trend-chart" width="560" height="190"></canvas>
        <em id="journal-trend-${entryId}" class="trend-caption">загрузка ${chartIntervalLabel()}</em>
        <div class="media-strip">${mediaImages(row.screenshot_file_id)}</div>
      </div>
    </article>
  `;
  }).join("") || emptyRow("Дневник пуст");
  hydrateProtectedImages(document.getElementById("journalList"));
  rows.forEach(row => {
    if (cleanSymbol(row.symbol)) loadJournalHistory(row);
  });
}

function journalResult(row) {
  if (!row.linked_trade_id) return { className: "is-idea", icon: "○", label: "ИДЕЯ", amount: "без результата" };
  const trade = [...currentOpenTrades, ...currentTrades].find(item => Number(item.id) === Number(row.linked_trade_id));
  const status = row.trade_status || trade?.status;
  let pnl = row.trade_pnl == null ? null : Number(row.trade_pnl);
  if (status === "open" && trade) {
    const mark = priceState.get(cleanSymbol(trade.symbol));
    if (mark) pnl = calcPnl(trade, mark);
  }
  if (status === "open") {
    const className = pnl == null ? "is-open" : pnl >= 0 ? "is-profit is-open" : "is-loss is-open";
    return { className, icon: "●", label: "ОТКРЫТА", amount: pnl == null ? "ожидаю цену" : `${signed(pnl)} USDT сейчас` };
  }
  if (pnl == null) return { className: "is-idea", icon: "○", label: "НЕТ ИТОГА", amount: "сделка не связана" };
  if (pnl > 0) return { className: "is-profit", icon: "▲", label: "ПРИБЫЛЬ", amount: `${signed(pnl)} USDT` };
  if (pnl < 0) return { className: "is-loss", icon: "▼", label: "УБЫТОК", amount: `${signed(pnl)} USDT` };
  return { className: "is-even", icon: "◆", label: "БЕЗУБЫТОК", amount: "0 USDT" };
}

function updateJournalResults() {
  currentJournal.forEach(row => {
    const node = document.querySelector(`[data-journal-entry-id="${row.id}"]`);
    if (!node) return;
    const result = journalResult(row);
    node.className = `journal-result ${result.className}`;
    setJournalResultContent(node, result);
    node.closest(".journal-card")?.classList.remove("is-profit", "is-loss", "is-even", "is-open", "is-idea");
    result.className.split(" ").forEach(className => node.closest(".journal-card")?.classList.add(className));
  });
}

async function loadJournalHistory(row) {
  const entryId = safeId(row.id);
  const canvas = document.getElementById(`journal-chart-${entryId}`);
  const caption = document.getElementById(`journal-trend-${entryId}`);
  if (!canvas) return;
  try {
    const data = await api(`/api/journal/${row.id}/chart?interval=${chartInterval}`);
    const chartTrade = data.trade || {};
    if (data.items.length > 2) {
      clearInterval(chartAnimations.get(`journal-${entryId}`));
      let count = 2;
      const step = Math.max(1, Math.ceil(data.items.length / 70));
      const timer = setInterval(() => {
        count += step;
        if (count >= data.items.length) count = 2;
        drawTradeChart(canvas, data.items.slice(0, count), chartTrade, chartInterval);
      }, 220);
      chartAnimations.set(`journal-${entryId}`, timer);
      caption.textContent = `${data.market === "spot" ? "Binance Spot" : "Binance Futures"} · ${chartIntervalLabel()} · история на ${data.anchor_time}`;
    } else {
      drawTradeChart(canvas, data.items, chartTrade, chartInterval);
      caption.textContent = `${chartIntervalLabel()} · исторических свечей нет`;
    }
  } catch {
    drawMiniTrend(canvas, []);
    caption.textContent = "график сделки недоступен";
  }
}

function mediaImages(value) {
  return String(value || "").split(",").filter(Boolean).map(fileId => `
    <img class="journal-shot" data-protected-src="${escapeHtml(`/api/media/${encodeURIComponent(fileId)}`)}" alt="Скрин сделки" loading="lazy" />
  `).join("");
}

async function loadSessions() {
  const data = await api("/api/sessions");
  currentSessions = data.items || [];
  const active = currentSessions.find(item => item.status === "active");
  const badge = document.getElementById("activeSessionBadge");
  badge.textContent = active ? `active · ${active.name}` : "нет активной";
  badge.className = `live-status ${active ? "is-live" : "is-offline"}`;
  document.getElementById("sessionList").innerHTML = currentSessions.map(item => {
    const pnl = Number(item.realized_pnl || 0);
    const balance = Number(item.start_balance) + pnl;
    const progress = item.target_balance ? ((balance - item.start_balance) / Math.max(item.target_balance - item.start_balance, 0.000001)) * 100 : 0;
    const closed = Number(item.closed_count || 0);
    const winrate = closed ? Number(item.wins || 0) / closed * 100 : 0;
    const status = safeSessionStatus(item.status);
    const sessionId = safeId(item.id);
    return `<article class="session-card ${status}">
      <div><span class="session-status">${status === "active" ? "АКТИВНА" : "АРХИВ"}</span><h3>${escapeHtml(item.name)}</h3><small>${escapeHtml(item.started_at)}</small></div>
      <div class="session-money"><span>Старт <b>${fmt(item.start_balance)} USDT</b></span><span>Баланс <b class="${pnl >= 0 ? "positive" : "negative"}">${fmt(balance)} USDT</b></span><span>PnL <b class="${pnl >= 0 ? "positive" : "negative"}">${signed(pnl)} USDT</b></span></div>
      <progress class="session-progress" max="100" value="${Math.max(0, Math.min(100, progress))}"></progress>
      <div class="session-meta"><span>${item.trade_count || 0} сделок</span><span>Winrate ${fmt(winrate)}%</span><span>Цель ${item.target_balance ? fmt(item.target_balance) : "-"}</span></div>
      <div class="session-actions">${status === "active" ? `<button class="mini-action" data-action="archive-session" data-id="${sessionId}">В архив</button>` : `<button class="mini-action" data-action="activate-session" data-id="${sessionId}">Продолжить</button>`}</div>
    </article>`;
  }).join("") || emptyRow("Создай первую торговую сессию");
}

async function createSession(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = {
    name: form.get("name"),
    start_balance: Number(form.get("start_balance")),
  };
  if (form.get("target_balance")) payload.target_balance = Number(form.get("target_balance"));
  const response = await apiFetch("/api/sessions", jsonRequest("POST", payload));
  if (!response.ok) return alert("Не удалось создать сессию");
  event.currentTarget.reset();
  await Promise.all([loadSessions(), loadDashboard()]);
}

async function archiveSession(id) {
  await apiFetch(`/api/sessions/${id}/archive`, { method: "POST" });
  await Promise.all([loadSessions(), loadDashboard()]);
}

async function activateSession(id) {
  await apiFetch(`/api/sessions/${id}/activate`, { method: "POST" });
  await Promise.all([loadSessions(), loadDashboard()]);
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function safeId(value) {
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? String(id) : "0";
}

function safeTradeSide(value) {
  return value === "short" ? "short" : "long";
}

function safeTradeStatus(value) {
  return ["open", "closed", "cancelled"].includes(value) ? value : "closed";
}

function safeSessionStatus(value) {
  return value === "active" ? "active" : "archived";
}

function numberAttr(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? String(number) : "";
}

function setJournalResultContent(node, result) {
  node.replaceChildren();
  const icon = document.createElement("i");
  icon.textContent = result.icon;
  const label = document.createElement("strong");
  label.textContent = result.label;
  const amount = document.createElement("span");
  amount.textContent = result.amount;
  node.append(icon, label, amount);
}

function sessionName(id) {
  return currentSessions.find(item => Number(item.id) === Number(id))?.name || `#${id}`;
}

function renderAnalytics() {
  const closed = currentTrades.filter(row => row.status === "closed");
  const open = currentOpenTrades.length;
  const best = closed.reduce((acc, row) => Math.max(acc, Number(row.pnl || 0)), 0);
  const worst = closed.reduce((acc, row) => Math.min(acc, Number(row.pnl || 0)), 0);
  const bySymbol = {};
  [...currentTrades, ...currentOpenTrades].forEach(row => {
    const symbol = cleanSymbol(row.symbol) || "UNKNOWN";
    bySymbol[symbol] = (bySymbol[symbol] || 0) + 1;
  });
  document.getElementById("analyticsPanel").innerHTML = `
    <div class="metric"><span>Открытых</span><strong>${open}</strong></div>
    <div class="metric"><span>Закрытых</span><strong>${closed.length}</strong></div>
    <div class="metric"><span>Лучший PnL</span><strong class="positive">${signed(best)}</strong></div>
    <div class="metric"><span>Худший PnL</span><strong class="negative">${signed(worst)}</strong></div>
    <div class="band analytics-wide"><h2>Активность по монетам</h2>${Object.entries(bySymbol).map(([symbol, count]) => `<span class="chip">${escapeHtml(symbol)}: ${count}</span>`).join("") || "<span class='chip'>Нет данных</span>"}</div>
    <div class="band analytics-wide">
      <div class="section-head">
        <div>
          <h2>Obsidian Export</h2>
          <p class="section-copy">Скачай готовый vault: Dashboard, Sessions, Trades, Journal, Coins и Canvas-карта связей.</p>
        </div>
        <span id="obsidianExportStatus" class="live-status">готов</span>
      </div>
      <div class="toolbar">
        <button class="primary-action compact" type="button" data-action="download-obsidian">Скачать всё</button>
        ${activeSession ? `<button class="primary-action compact" type="button" data-action="download-session-obsidian" data-id="${Number(activeSession.id)}">Активная сессия</button>` : ""}
      </div>
    </div>
  `;
}

async function downloadObsidianExport(sessionId = 0) {
  const status = document.getElementById("obsidianExportStatus");
  if (status) {
    status.textContent = "собираю";
    status.className = "live-status";
  }
  const path = sessionId ? `/api/export/obsidian.zip?session_id=${Number(sessionId)}` : "/api/export/obsidian.zip";
  try {
    const response = await apiFetch(path);
    if (!response.ok) throw new Error(await response.text());
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : "trading-assistant-obsidian.zip";
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    if (status) {
      status.textContent = "скачано";
      status.className = "live-status is-live";
    }
  } catch (error) {
    console.error("Obsidian export failed", error);
    if (status) {
      status.textContent = "ошибка";
      status.className = "live-status is-offline";
    }
    alert("Не удалось скачать Obsidian export");
  }
}

function showModelPlaceholder() {
  const status = document.getElementById("modelSetupStatus");
  if (status) {
    status.textContent = "нужен backend";
    status.className = "live-status is-offline";
  }
  alert("UI вкладки готов. Реальное сохранение ключей включу после backend-миграции и безопасного хранилища секретов.");
}

async function calculateRisk() {
  const form = new FormData(document.getElementById("riskForm"));
  const query = new URLSearchParams(form);
  try {
    const data = await api(`/api/risk?${query.toString()}`);
    const r = data.result;
    const stopDistance = (r.stop_price - r.entry_price) / r.entry_price * 100;
    const targetDistance = r.target_price ? (r.target_price - r.entry_price) / r.entry_price * 100 : 0;
    const fundingLabel = r.funding_payment > 0 ? "расход" : r.funding_payment < 0 ? "получение" : "нет";
    const liquidation = r.liquidation_price == null
      ? "не рассчитывается для Cross без данных аккаунта"
      : `${fmt(r.liquidation_price, 6)} (${fmt(r.liquidation_distance_percent)}% от входа)`;
    document.getElementById("riskResult").textContent =
      `Количество: ${fmt(r.quantity, 6)} ${cleanSymbol(r.symbol).replace("USDT", "")}\n` +
      `Размер позиции: ${fmt(r.notional)} USDT\nМаржа: ${fmt(r.margin)} USDT\n` +
      `Минимальное плечо по депозиту: ${fmt(Math.max(1, r.minimum_leverage), 2)}x\n` +
      `Маржи хватает: ${r.margin_sufficient ? "да" : "нет — увеличь плечо или уменьши риск"}\n\n` +
      `Лимит риска: ${fmt(r.risk_amount)} USDT\nЧистый убыток по стопу: ${fmt(r.net_loss_at_stop)} USDT\n` +
      `  движение цены: ${fmt(r.gross_loss_at_stop)}\n  комиссии: ${fmt(r.entry_fee + r.stop_exit_fee)}\n` +
      `  проскальзывание: ${fmt(r.stop_slippage)}\n  funding: ${fmt(r.funding_payment)} (${fundingLabel})\n` +
      `Чистая прибыль по тейку: ${r.net_profit_at_target == null ? "-" : fmt(r.net_profit_at_target)} USDT\n` +
      `R/R после издержек: ${r.reward_to_risk == null ? "-" : fmt(r.reward_to_risk)}\n` +
      `До стопа: ${fmt(stopDistance)}% · до тейка: ${fmt(targetDistance)}%\n` +
      `Оценка ликвидации: ${liquidation}`;
  } catch {
    document.getElementById("riskResult").textContent = "Проверь параметры расчета";
  }
}

async function reviewTrade() {
  const form = new FormData(document.getElementById("riskForm"));
  const query = new URLSearchParams(form);
  document.getElementById("reviewResult").textContent = "Проверяю сделку...";
  try {
    const data = await api(`/api/review?${query.toString()}`);
    const r = data.review;
    const issues = r.issues.map(item => `- ${item.severity.toUpperCase()}: ${item.title}. ${item.detail}`).join("\n");
    document.getElementById("reviewResult").textContent =
      `Оценка по правилам: ${fmt(r.rule_score, 0)}/100 (не вероятность)\nSeverity: ${r.severity.toUpperCase()}\n${r.summary}\n\n${issues || "Критичных замечаний нет"}`;
  } catch {
    document.getElementById("reviewResult").textContent = "Не удалось проверить сделку";
  }
}

async function suggestTrade() {
  const form = new FormData(document.getElementById("riskForm"));
  const symbol = form.get("symbol");
  const timeframe = form.get("timeframe") || "5m";
  const target = document.getElementById("suggestResult");
  target.textContent = "Сверяю 1D / 1H / 15M / 5M...";
  try {
    const data = await api(`/api/setup?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`);
    const contexts = Object.entries(data.contexts).map(([tf, item]) => `${tf.toUpperCase()}: ${item.bias} · RSI ${item.rsi}`).join("\n");
    target.textContent = `${data.symbol} · рабочий ТФ ${data.timeframe}\nСценарий: ${data.side.toUpperCase()}\nВход: ${fmt(data.entry, 6)}\nСтоп: ${fmt(data.stop, 6)}\nТейк: ${fmt(data.target, 6)}\nЭвристическая оценка: ${data.rule_score}/100 (не вероятность)\n\n${contexts}\n\n${data.note}`;
    if (data.side !== "neutral") document.querySelector('#riskForm select[name="side"]').value = data.side;
    document.querySelector('#riskForm input[name="entry"]').value = data.entry.toFixed(6);
    document.querySelector('#riskForm input[name="stop"]').value = data.stop.toFixed(6);
    document.querySelector('#riskForm input[name="target"]').value = data.target.toFixed(6);
    calculateRisk();
  } catch {
    target.textContent = "Не удалось получить мультитаймфреймовый сценарий";
  }
}

async function closeTrade(id) {
  const exitPrice = prompt("Цена закрытия:");
  if (!exitPrice) return;
  const response = await apiFetch(`/api/trades/${id}/close`, jsonRequest("POST", { exit_price: Number(exitPrice), note: "miniapp" }));
  const data = await response.json();
  if (!data.ok) alert("Не удалось закрыть сделку");
  await loadAll();
}

function toggleEditTrade(id) {
  const numericId = Number(id);
  expandedTrades.add(numericId);
  if (editingTrades.has(numericId)) editingTrades.delete(numericId);
  else editingTrades.add(numericId);
  renderTrades("openTrades", currentOpenTrades, true);
  if (currentTrades.length) renderTrades("tradesTable", currentTrades, false);
}

async function saveTradeEdit(id) {
  const value = suffix => document.getElementById(`edit-${suffix}-${id}`)?.value;
  const payload = {
    entry_price: Number(value("entry")),
    stop_price: Number(value("stop")),
    quantity: Number(value("qty")),
    timeframe: value("timeframe") || "5m",
    note: value("note") || "",
  };
  if (value("target")) payload.target_price = Number(value("target"));
  const response = await apiFetch(`/api/trades/${id}/update`, jsonRequest("POST", payload));
  const result = await response.json();
  if (!result.ok) return alert("Не удалось изменить сделку");

  const files = [...(document.getElementById(`edit-photo-${id}`)?.files || [])];
  for (const file of files) {
    const upload = await apiFetch(`/api/trades/${id}/attachment?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    if (!upload.ok) alert(`Не загрузилось фото: ${file.name}`);
  }
  editingTrades.delete(Number(id));
  [...candleCache.keys()].filter(key => key.startsWith(`trade:${id}:`)).forEach(key => candleCache.delete(key));
  await loadAll();
}

function tradeAttachmentImages(items) {
  return items.map(item => {
    const src = item.local_path ? `/api/trade-attachment/${safeId(item.id)}` : `/api/media/${encodeURIComponent(item.telegram_file_id || "")}`;
    return `<img class="trade-shot" data-protected-src="${escapeHtml(src)}" alt="Фото сделки" loading="lazy">`;
  }).join("");
}

function hydrateProtectedImages(root = document) {
  root.querySelectorAll("img[data-protected-src]").forEach(async image => {
    const path = image.dataset.protectedSrc;
    if (!path || image.dataset.loading === "true") return;
    image.dataset.loading = "true";
    try {
      const response = await apiFetch(path);
      if (!response.ok) throw new Error("image unavailable");
      image.src = URL.createObjectURL(await response.blob());
      image.addEventListener("load", () => URL.revokeObjectURL(image.src), { once: true });
    } catch {
      image.alt = "Изображение недоступно";
    }
  });
}

async function cancelTrade(id) {
  if (!confirm("Отменить открытую сделку?")) return;
  const response = await apiFetch(`/api/trades/${id}/cancel`, { method: "POST" });
  const data = await response.json();
  if (!data.ok) alert("Не удалось отменить сделку");
  await loadAll();
}

function fillSymbol(symbol) {
  const input = document.querySelector('#riskForm input[name="symbol"]');
  input.value = cleanSymbol(symbol).replace("USDT", "");
  calculateRisk();
}

function cleanSymbol(symbol) {
  return String(symbol || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function calcPnl(row, price) {
  const direction = row.side === "long" ? 1 : -1;
  return (price - row.entry_price) * row.quantity * direction;
}

function pnlPercent(row, price) {
  const direction = row.side === "long" ? 1 : -1;
  return ((price - row.entry_price) / row.entry_price) * 100 * direction;
}

function distanceTo(level, price) {
  if (!level || !price) return "-";
  return `${signed((level - price) / price * 100)}%`;
}

function tradeProgress(row, price) {
  if (!price || !row.target_price) return 50;
  const min = Math.min(row.stop_price, row.target_price);
  const max = Math.max(row.stop_price, row.target_price);
  return Math.max(0, Math.min(100, ((price - min) / Math.max(max - min, 0.000001)) * 100));
}

function rrText(row) {
  if (!row.target_price) return "-";
  const risk = Math.abs(row.entry_price - row.stop_price);
  const reward = Math.abs(row.target_price - row.entry_price);
  return risk ? fmt(reward / risk, 2) : "-";
}

function closeReasonText(reason) {
  const normalized = String(reason || "").replace(/[^a-z_]/g, "");
  return { stop_loss: "стоп", take_profit: "тейк", manual: "вручную" }[normalized] || normalized.replace(/_/g, " ");
}

function emptyRow(text) {
  return `<div class="row"><strong>${escapeHtml(text)}</strong><span></span><span></span><span></span><span></span></div>`;
}

async function loadAll() {
  await Promise.all([loadDashboard(), loadTrades(), loadJournal(), loadMarketTop(), loadSessions()]);
  renderAnalytics();
  await calculateRisk();
}

if (telegramInitData) {
  loadAll();
  priceTimer = setInterval(() => loadPrices(), 3000);
  marketTimer = setInterval(loadMarketTop, 20000);
} else {
  document.querySelector("main").textContent = "Откройте Mini App через Telegram.";
}
