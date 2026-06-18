const params = new URLSearchParams(location.search);
const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user?.id;
const userId = params.get("user_id") || tgUser || "1";
window.Telegram?.WebApp?.ready();

const fmt = (value, digits = 2) => Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: digits });
const signed = value => `${Number(value || 0) > 0 ? "+" : ""}${fmt(value)}`;
const priceState = new Map();
const candleCache = new Map();
const candleUpdatedAt = new Map();
const expandedTrades = new Set();
const chartAnimations = new Map();
const expandedMarkets = new Set();
const editingTrades = new Set();
const chartIntervals = ["1h", "15m", "5m", "1m"];
let chartInterval = "1m";
let currentOpenTrades = [];
let currentTrades = [];
let currentJournal = [];
let currentPriceItems = [];
let priceTimer = null;
let marketTimer = null;
let currentSessions = [];

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
["journalSymbol", "journalFrom", "journalTo", "journalSearch"].forEach(id => {
  document.getElementById(id).addEventListener("input", renderJournal);
});

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function switchView(view) {
  const button = document.querySelector(`.tab[data-view="${view}"]`);
  button?.click();
}

async function loadDashboard() {
  const data = await api(`/api/dashboard?user_id=${userId}`);
  currentOpenTrades = data.open_trades;
  document.getElementById("netPnl").textContent = `${signed(data.stats.net_pnl)} USDT`;
  document.getElementById("netPnl").className = data.stats.net_pnl >= 0 ? "positive" : "negative";
  document.getElementById("winrate").textContent = `${fmt(data.stats.winrate)}%`;
  document.getElementById("openRisk").textContent = `${fmt(data.open_risk)} USDT`;
  document.getElementById("alertCount").textContent = data.active_alerts.length;
  document.getElementById("watchlist").innerHTML = data.watchlist.map(symbol => `<button class="chip favorite-chip" onclick="fillSymbol('${symbol}')">★ ${symbol}</button>`).join("") || "<span class='chip'>Пусто</span>";
  document.getElementById("planText").textContent = data.plan ? `${data.plan.allowed_symbols || "без монет"} | риск ${data.plan.max_daily_risk_percent}% | стоп ${fmt(data.plan.max_daily_loss)} USDT` : "План дня не задан";
  document.querySelector(".eyebrow").textContent = `Кабинет трейдера · ${data.session?.name || "без активной сессии"}`;
  renderTrades("openTrades", currentOpenTrades, true);
  await loadPrices(data.watchlist, data.open_trades);
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
    const data = await api(`/api/prices?user_id=${userId}${query}`);
    const items = Array.isArray(data.items) ? data.items : [];
    const now = new Date();
    status.textContent = `live ${now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    status.className = "live-status is-live";
    try {
      renderPrices(items);
      if (!editingTrades.size && currentOpenTrades.length) renderTrades("openTrades", currentOpenTrades, true);
      if (!editingTrades.size && currentTrades.length) renderTrades("tradesTable", currentTrades, false);
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
      <button class="price-card ${direction}" type="button" onclick="fillSymbol('${symbol}')">
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
      return `<article class="market-card ${expandedMarkets.has(marketSymbol) ? "expanded" : ""}" onclick="toggleMarketCard(this, '${marketSymbol}')">
        <div class="market-title"><span class="market-rank">${index + 1}</span><strong>${marketSymbol.replace("USDT", "")}<small>/USDT</small></strong></div>
        <span class="scanner-score">${scannerScore}<small>/100</small></span>
        <b>${fmt(item.price, item.price > 10 ? 2 : 6)}</b>
        <small class="market-change ${item.price_change_percent >= 0 ? "positive" : "negative"}">${item.price_change_percent >= 0 ? "↑" : "↓"} ${signed(item.price_change_percent)}% · 24ч</small>
        <span class="direction-pill ${item.price_change_percent >= 0 ? "positive" : "negative"}">${direction}</span>
        <small>VOL ${fmt(item.quote_volume / 1000000, 0)}M · RANGE ${fmt(item.intraday_range_percent)}%</small>
        <div class="range-meter market-range"><i style="width:${Math.max(0, Math.min(100, position))}%"></i><span style="left:${Math.max(2, Math.min(98, position))}%"></span></div>
        <small class="range-labels"><i>${fmt(item.low_price, 4)}</i><i>${item.exchange}</i><i>${fmt(item.high_price, 4)}</i></small>
        <div class="market-detail">
          <small>Цена на ${fmt(position, 0)}% суточного диапазона</small>
          <canvas id="market-chart-${cleanSymbol(item.symbol)}" class="mini-trend-chart" width="360" height="118"></canvas>
          <em id="market-trend-${cleanSymbol(item.symbol)}" class="trend-caption">Нажми для графика</em>
          <button class="mini-action" onclick="event.stopPropagation(); fillSymbol('${cleanSymbol(item.symbol)}'); switchView('calculator')">Разобрать вход</button>
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
    loadMiniTrend(symbol, `market-chart-${symbol}`, `market-trend-${symbol}`);
  } else {
    expandedMarkets.delete(symbol);
  }
}

async function loadTrades(status = "") {
  const query = status ? `&status=${status}` : "";
  const data = await api(`/api/trades?user_id=${userId}${query}`);
  currentTrades = data.items;
  renderTrades("tradesTable", currentTrades, false);
}

function renderTrades(targetId, rows, compact = false) {
  document.getElementById(targetId).innerHTML = rows.map(row => renderTradeCard(row, compact)).join("") || emptyRow("Нет данных");
  rows.forEach(row => {
    if (row.status === "open" || expandedTrades.has(Number(row.id))) loadTradeChart(row);
  });
}

function renderTradeCard(row, compact) {
  const symbol = cleanSymbol(row.symbol);
  const livePrice = priceState.get(symbol);
  const isOpen = row.status === "open";
  const markPrice = isOpen ? livePrice : row.exit_price;
  const pnl = markPrice ? calcPnl(row, markPrice) : Number(row.pnl || 0);
  const pnlPct = markPrice ? pnlPercent(row, markPrice) : 0;
  const progress = tradeProgress(row, markPrice);
  const pnlClass = pnl >= 0 ? "positive" : "negative";
  const attachmentStrip = tradeAttachmentImages(row.attachments || []);
  const editPanel = isOpen ? `
    <div class="trade-edit" onclick="event.stopPropagation()">
      <div class="edit-grid">
        <label>Вход<input id="edit-entry-${row.id}" type="number" step="any" value="${row.entry_price}"></label>
        <label>Стоп<input id="edit-stop-${row.id}" type="number" step="any" value="${row.stop_price}"></label>
        <label>Тейк<input id="edit-target-${row.id}" type="number" step="any" value="${row.target_price || ""}"></label>
        <label>Количество<input id="edit-qty-${row.id}" type="number" step="any" value="${row.quantity}"></label>
        <label>Таймфрейм<select id="edit-timeframe-${row.id}">${["1m","5m","15m","1h","4h","1d"].map(tf => `<option ${row.timeframe === tf ? "selected" : ""}>${tf}</option>`).join("")}</select></label>
        <label>Комментарий<input id="edit-note-${row.id}" placeholder="Почему перенес стоп или тейк"></label>
        <label class="photo-picker">Добавить фото<input id="edit-photo-${row.id}" type="file" accept="image/jpeg,image/png,image/webp" multiple></label>
      </div>
      <div class="edit-actions"><button class="primary-action compact" onclick="saveTradeEdit(${row.id})">Сохранить</button><button class="mini-action" onclick="toggleEditTrade(${row.id})">Отмена</button></div>
    </div>` : "";
  const details = `
    <div class="trade-details">
      <canvas id="chart-${row.id}" class="trade-chart" width="620" height="180"></canvas>
      <div class="trade-stats">
        <span>Цена сейчас <b>${markPrice ? fmt(markPrice, markPrice > 10 ? 2 : 6) : "-"}</b></span>
        <span>До стопа <b>${distanceTo(row.stop_price, markPrice)}</b></span>
        <span>До тейка <b>${row.target_price ? distanceTo(row.target_price, markPrice) : "-"}</b></span>
        <span>R/R <b>${rrText(row)}</b></span>
        <span>Таймфрейм <b>${chartIntervalLabel()}</b></span>
        <span>Количество <b>${fmt(row.quantity, 8)} ${symbol.replace("USDT", "")}</b></span>
        <span>Теги <b>${escapeHtml(row.tags || `coin:${symbol.replace("USDT", "")}`)}</b></span>
      </div>
      ${attachmentStrip ? `<div class="trade-media">${attachmentStrip}</div>` : ""}
    </div>
    ${editPanel}
  `;
  return `
    <article class="trade-card ${compact ? "compact-trade" : ""} ${expandedTrades.has(Number(row.id)) ? "expanded" : ""} ${editingTrades.has(Number(row.id)) ? "editing" : ""}" data-trade-id="${row.id}" onclick="toggleTrade(${row.id}, event)">
      <div class="trade-main">
        <strong>#${row.id} ${symbol}<small>${row.side.toUpperCase()} ${row.status}${row.close_reason ? ` · ${closeReasonText(row.close_reason)}` : ""}</small></strong>
        <span>Entry ${fmt(row.entry_price, 6)}<small>Stop ${fmt(row.stop_price, 6)}</small></span>
        <span>Target ${row.target_price ? fmt(row.target_price, 6) : "-"}</span>
        <span class="${pnlClass}">${markPrice ? signed(pnl) : (row.pnl == null ? "-" : signed(row.pnl))} USDT<small>${markPrice ? signed(pnlPct) : "0"}%</small></span>
        <span class="trade-actions" onclick="event.stopPropagation()">
          ${isOpen ? `<button class="mini-action" onclick="toggleEditTrade(${row.id})">Изменить</button><button class="mini-action" onclick="closeTrade(${row.id})">Закрыть</button><button class="mini-action" onclick="cancelTrade(${row.id})">Отменить</button>` : ""}
        </span>
      </div>
      <div class="progress-rail"><span style="width:${progress}%"></span></div>
      ${details}
    </article>
  `;
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
  try {
    const cacheKey = `trade:${row.id}:${chartInterval}`;
    const stale = Date.now() - (candleUpdatedAt.get(cacheKey) || 0) > 10000;
    if (force || stale || !candleCache.has(cacheKey)) {
      const data = await api(`/api/trades/${row.id}/chart?user_id=${userId}&interval=${chartInterval}`);
      candleCache.set(cacheKey, data.items);
      candleUpdatedAt.set(cacheKey, Date.now());
    }
    canvases.forEach((canvas, index) => {
      if (row.status === "closed" && candleCache.get(cacheKey)?.length > 2) animateTradeChart(canvas, candleCache.get(cacheKey), row, `${row.id}-${index}`);
      else drawTradeChart(canvas, candleCache.get(cacheKey), row);
    });
  } catch {
    canvases.forEach(canvas => drawTradeChart(canvas, [], row));
  }
}

function animateTradeChart(canvas, candles, row, animationKey = row.id) {
  clearInterval(chartAnimations.get(animationKey));
  let count = 2;
  const step = Math.max(1, Math.ceil(candles.length / 45));
  const timer = setInterval(() => {
    count += step;
    if (count >= candles.length) count = 2;
    drawTradeChart(canvas, candles.slice(0, count), row);
  }, 170);
  chartAnimations.set(animationKey, timer);
}

async function loadMiniTrend(symbol, canvasId, captionId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !symbol) return;
  const caption = document.getElementById(captionId);
  try {
    const cacheKey = candlesKey(symbol);
    const stale = Date.now() - (candleUpdatedAt.get(cacheKey) || 0) > 10000;
    if (stale || !candleCache.has(cacheKey)) {
      const data = await api(`/api/klines?symbol=${symbol}&interval=${chartInterval}&limit=80`);
      candleCache.set(cacheKey, data.items);
      candleUpdatedAt.set(cacheKey, Date.now());
    }
    const trend = drawMiniTrend(canvas, candleCache.get(cacheKey));
    if (caption) caption.textContent = `${chartIntervalLabel()} · ${trend.label} · ${signed(trend.change)}%`;
    if (caption) caption.className = `trend-caption ${trend.className}`;
  } catch {
    drawMiniTrend(canvas, []);
    if (caption) caption.textContent = `${chartIntervalLabel()} · нет данных`;
  }
}

function candlesKey(symbol) {
  return `${cleanSymbol(symbol)}:${chartInterval}`;
}

function drawTradeChart(canvas, candles, row) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "rgba(5, 8, 16, .7)";
  ctx.fillRect(0, 0, w, h);
  const prices = (candles || []).map(c => Number(c.close)).filter(Number.isFinite);
  if (prices.length < 2) {
    ctx.fillStyle = "rgba(177, 191, 222, .72)";
    ctx.font = "13px system-ui";
    ctx.fillText("Загружаю свечи...", 16, h / 2);
    return;
  }
  const levels = [row.entry_price, row.stop_price, row.target_price].map(Number).filter(Number.isFinite);
  const scaledPrices = [...prices, ...levels];
  const rawMax = Math.max(...scaledPrices);
  const rawMin = Math.min(...scaledPrices);
  const padding = Math.max((rawMax - rawMin) * .18, prices[prices.length - 1] * .0012);
  const max = rawMax + padding;
  const min = rawMin - padding;
  const y = price => h - ((price - min) / Math.max(max - min, 0.000001)) * (h - 22) - 11;
  const safeY = price => Math.max(11, Math.min(h - 11, y(price)));
  const plotEnd = w * 0.64;
  ctx.fillStyle = "rgba(168, 85, 247, .055)";
  ctx.fillRect(plotEnd, 0, w - plotEnd, h);
  ctx.fillStyle = "rgba(177, 191, 222, .35)";
  ctx.font = "10px system-ui";
  ctx.fillText("ПРОСТРАНСТВО ЦЕНЫ", plotEnd + 10, 16);
  ctx.strokeStyle = "rgba(67, 215, 255, .8)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  prices.forEach((price, index) => {
    const x = 10 + index * ((plotEnd - 10) / Math.max(prices.length - 1, 1));
    if (index === 0) ctx.moveTo(x, y(price));
    else ctx.lineTo(x, y(price));
  });
  ctx.stroke();
  drawLevel(ctx, w, safeY(row.entry_price), `ВХОД ${fmt(row.entry_price, 6)}`, "#43d7ff");
  drawLevel(ctx, w, safeY(row.stop_price), `СТОП ${fmt(row.stop_price, 6)}`, "#ff657d");
  if (row.target_price) drawLevel(ctx, w, safeY(row.target_price), `ТЕЙК ${fmt(row.target_price, 6)}`, "#55e08a");
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

function chartIntervalLabel() {
  return { "1h": "1 час", "15m": "15 мин", "5m": "5 мин", "1m": "1 мин" }[chartInterval] || chartInterval;
}

async function loadJournal() {
  const data = await api(`/api/journal?user_id=${userId}`);
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
  document.getElementById("journalList").innerHTML = rows.map(row => `
    <article class="journal-card">
      <div>
        <strong>${row.symbol || "-"}<small>${row.outcome} · ${row.created_at}</small></strong>
        <p>${row.description || "-"}</p>
        <div class="chips"><small class="chip">coin:${cleanSymbol(row.symbol).replace("USDT", "")}</small>${row.session_id ? `<small class="chip">session:${escapeHtml(sessionName(row.session_id))}</small>` : ""}</div>
        <small>${row.theory || ""}</small>
      </div>
      <div class="journal-visuals">
        <canvas id="journal-chart-${row.id}" class="journal-trend-chart" width="360" height="118"></canvas>
        <em id="journal-trend-${row.id}" class="trend-caption">загрузка ${chartIntervalLabel()}</em>
        <div class="media-strip">${mediaImages(row.screenshot_file_id)}</div>
      </div>
    </article>
  `).join("") || emptyRow("Дневник пуст");
  rows.forEach(row => {
    const symbol = cleanSymbol(row.symbol);
    if (row.linked_trade_id) loadJournalTradeTrend(row);
    else if (symbol) loadMiniTrend(symbol, `journal-chart-${row.id}`, `journal-trend-${row.id}`);
  });
}

async function loadJournalTradeTrend(row) {
  const canvas = document.getElementById(`journal-chart-${row.id}`);
  const caption = document.getElementById(`journal-trend-${row.id}`);
  if (!canvas) return;
  try {
    const data = await api(`/api/trades/${row.linked_trade_id}/chart?user_id=${userId}&interval=${chartInterval}`);
    if (data.historical && data.items.length > 2) {
      clearInterval(chartAnimations.get(`journal-${row.id}`));
      let count = 2;
      const step = Math.max(1, Math.ceil(data.items.length / 45));
      const timer = setInterval(() => {
        count += step;
        if (count >= data.items.length) count = 2;
        drawTradeChart(canvas, data.items.slice(0, count), data.trade);
      }, 180);
      chartAnimations.set(`journal-${row.id}`, timer);
      caption.textContent = "история сделки · повтор";
    } else {
      drawTradeChart(canvas, data.items, data.trade);
      caption.textContent = `${chartIntervalLabel()} · уровни входа, стопа и тейка${data.fallback ? " · архив недоступен" : ""}`;
    }
  } catch {
    drawMiniTrend(canvas, []);
    caption.textContent = "график сделки недоступен";
  }
}

function mediaImages(value) {
  return String(value || "").split(",").filter(Boolean).map(fileId => `
    <img class="journal-shot" src="/api/media/${encodeURIComponent(fileId)}" alt="Скрин сделки" loading="lazy" />
  `).join("");
}

async function loadSessions() {
  const data = await api(`/api/sessions?user_id=${userId}`);
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
    return `<article class="session-card ${item.status}">
      <div><span class="session-status">${item.status === "active" ? "АКТИВНА" : "АРХИВ"}</span><h3>${escapeHtml(item.name)}</h3><small>${item.started_at}</small></div>
      <div class="session-money"><span>Старт <b>${fmt(item.start_balance)} USDT</b></span><span>Баланс <b class="${pnl >= 0 ? "positive" : "negative"}">${fmt(balance)} USDT</b></span><span>PnL <b class="${pnl >= 0 ? "positive" : "negative"}">${signed(pnl)} USDT</b></span></div>
      <div class="session-progress"><i style="width:${Math.max(0, Math.min(100, progress))}%"></i></div>
      <div class="session-meta"><span>${item.trade_count || 0} сделок</span><span>Winrate ${fmt(winrate)}%</span><span>Цель ${item.target_balance ? fmt(item.target_balance) : "-"}</span></div>
      <div class="session-actions">${item.status === "active" ? `<button class="mini-action" onclick="archiveSession(${item.id})">В архив</button>` : `<button class="mini-action" onclick="activateSession(${item.id})">Продолжить</button>`}</div>
    </article>`;
  }).join("") || emptyRow("Создай первую торговую сессию");
}

async function createSession(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const query = new URLSearchParams({ user_id: userId, name: form.get("name"), start_balance: form.get("start_balance") });
  if (form.get("target_balance")) query.set("target_balance", form.get("target_balance"));
  const response = await fetch(`/api/sessions?${query}`, { method: "POST" });
  if (!response.ok) return alert("Не удалось создать сессию");
  event.currentTarget.reset();
  await Promise.all([loadSessions(), loadDashboard()]);
}

async function archiveSession(id) {
  await fetch(`/api/sessions/${id}/archive?user_id=${userId}`, { method: "POST" });
  await Promise.all([loadSessions(), loadDashboard()]);
}

async function activateSession(id) {
  await fetch(`/api/sessions/${id}/activate?user_id=${userId}`, { method: "POST" });
  await Promise.all([loadSessions(), loadDashboard()]);
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
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
    bySymbol[row.symbol] = (bySymbol[row.symbol] || 0) + 1;
  });
  document.getElementById("analyticsPanel").innerHTML = `
    <div class="metric"><span>Открытых</span><strong>${open}</strong></div>
    <div class="metric"><span>Закрытых</span><strong>${closed.length}</strong></div>
    <div class="metric"><span>Лучший PnL</span><strong class="positive">${signed(best)}</strong></div>
    <div class="metric"><span>Худший PnL</span><strong class="negative">${signed(worst)}</strong></div>
    <div class="band analytics-wide"><h2>Активность по монетам</h2>${Object.entries(bySymbol).map(([symbol, count]) => `<span class="chip">${symbol}: ${count}</span>`).join("") || "<span class='chip'>Нет данных</span>"}</div>
  `;
}

async function calculateRisk() {
  const form = new FormData(document.getElementById("riskForm"));
  const query = new URLSearchParams(form);
  try {
    const data = await api(`/api/risk?${query.toString()}`);
    const r = data.result;
    const stopDistance = (r.stop_price - r.entry_price) / r.entry_price * 100;
    const targetDistance = r.target_price ? (r.target_price - r.entry_price) / r.entry_price * 100 : 0;
    document.getElementById("riskResult").textContent =
      `Qty: ${fmt(r.quantity, 6)}\nRisk: ${fmt(r.risk_amount)} USDT\nMargin: ${fmt(r.margin)} USDT\nPotential profit: ${r.profit_at_target == null ? "-" : fmt(r.profit_at_target)} USDT\nR/R: ${r.reward_to_risk == null ? "-" : fmt(r.reward_to_risk)}\nДо стопа: ${fmt(stopDistance)}%\nДо тейка: ${fmt(targetDistance)}%`;
  } catch {
    document.getElementById("riskResult").textContent = "Проверь параметры расчета";
  }
}

async function reviewTrade() {
  const form = new FormData(document.getElementById("riskForm"));
  const query = new URLSearchParams(form);
  query.set("user_id", userId);
  document.getElementById("reviewResult").textContent = "Проверяю сделку...";
  try {
    const data = await api(`/api/review?${query.toString()}`);
    const r = data.review;
    const issues = r.issues.map(item => `- ${item.severity.toUpperCase()}: ${item.title}. ${item.detail}`).join("\n");
    document.getElementById("reviewResult").textContent =
      `Score: ${fmt(r.score, 0)}/100\nВероятно зайдет: ${fmt(r.win_probability, 0)}%\nВероятно не зайдет: ${fmt(r.loss_probability, 0)}%\nSeverity: ${r.severity.toUpperCase()}\n${r.summary}\n\n${issues || "Критичных замечаний нет"}`;
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
    target.textContent = `${data.symbol} · рабочий ТФ ${data.timeframe}\nСценарий: ${data.side.toUpperCase()}\nВход: ${fmt(data.entry, 6)}\nСтоп: ${fmt(data.stop, 6)}\nТейк: ${fmt(data.target, 6)}\nКачество сетапа: ${data.score}/100\nОценка успеха: ${data.win_probability}%\nОценка неуспеха: ${data.loss_probability}%\n\n${contexts}\n\n${data.note}`;
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
  const response = await fetch(`/api/trades/${id}/close?user_id=${userId}&exit_price=${encodeURIComponent(exitPrice)}&note=miniapp`, { method: "POST" });
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
  const query = new URLSearchParams({
    user_id: userId,
    entry_price: value("entry"),
    stop_price: value("stop"),
    quantity: value("qty"),
    timeframe: value("timeframe") || "5m",
    note: value("note") || "",
  });
  if (value("target")) query.set("target_price", value("target"));
  const response = await fetch(`/api/trades/${id}/update?${query}`, { method: "POST" });
  const result = await response.json();
  if (!result.ok) return alert("Не удалось изменить сделку");

  const files = [...(document.getElementById(`edit-photo-${id}`)?.files || [])];
  for (const file of files) {
    const upload = await fetch(`/api/trades/${id}/attachment?user_id=${userId}&filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    if (!upload.ok) alert(`Не загрузилось фото: ${file.name}`);
  }
  editingTrades.delete(Number(id));
  candleCache.delete(`trade:${id}:${chartInterval}`);
  await loadAll();
}

function tradeAttachmentImages(items) {
  return items.map(item => {
    const src = item.local_path ? `/api/trade-attachment/${item.id}` : `/api/media/${encodeURIComponent(item.telegram_file_id)}`;
    return `<img class="trade-shot" src="${src}" alt="Фото сделки" loading="lazy">`;
  }).join("");
}

async function cancelTrade(id) {
  if (!confirm("Отменить открытую сделку?")) return;
  const response = await fetch(`/api/trades/${id}/cancel?user_id=${userId}`, { method: "POST" });
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
  return { stop_loss: "стоп", take_profit: "тейк", manual: "вручную" }[reason] || String(reason).replace(/_/g, " ");
}

function emptyRow(text) {
  return `<div class="row"><strong>${text}</strong><span></span><span></span><span></span><span></span></div>`;
}

async function loadAll() {
  await Promise.all([loadDashboard(), loadTrades(), loadJournal(), loadMarketTop(), loadSessions()]);
  renderAnalytics();
  await calculateRisk();
}

loadAll();
priceTimer = setInterval(() => loadPrices(), 3000);
marketTimer = setInterval(loadMarketTop, 20000);
