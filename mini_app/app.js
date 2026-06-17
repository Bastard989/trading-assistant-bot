const params = new URLSearchParams(location.search);
const tgUser = window.Telegram?.WebApp?.initDataUnsafe?.user?.id;
const userId = params.get("user_id") || tgUser || "1";
window.Telegram?.WebApp?.ready();

const fmt = (value, digits = 2) => Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: digits });
const signed = value => `${Number(value || 0) > 0 ? "+" : ""}${fmt(value)}`;

document.querySelectorAll(".tab").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(item => item.classList.remove("active"));
    document.querySelectorAll(".view").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.view).classList.add("active");
  });
});

document.querySelectorAll(".seg").forEach(button => {
  button.addEventListener("click", async () => {
    document.querySelectorAll(".seg").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    await loadTrades(button.dataset.status);
  });
});

document.getElementById("refreshBtn").addEventListener("click", loadAll);
document.getElementById("riskForm").addEventListener("input", calculateRisk);

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async function loadDashboard() {
  const data = await api(`/api/dashboard?user_id=${userId}`);
  document.getElementById("netPnl").textContent = `${signed(data.stats.net_pnl)} USDT`;
  document.getElementById("netPnl").className = data.stats.net_pnl >= 0 ? "positive" : "negative";
  document.getElementById("winrate").textContent = `${fmt(data.stats.winrate)}%`;
  document.getElementById("openRisk").textContent = `${fmt(data.open_risk)} USDT`;
  document.getElementById("alertCount").textContent = data.active_alerts.length;
  document.getElementById("watchlist").innerHTML = data.watchlist.map(symbol => `<span class="chip">${symbol}</span>`).join("") || "<span class='chip'>Пусто</span>";
  document.getElementById("planText").textContent = data.plan ? `${data.plan.allowed_symbols || "без монет"} | риск ${data.plan.max_daily_risk_percent}% | стоп ${fmt(data.plan.max_daily_loss)} USDT` : "План дня не задан";
  renderTrades("openTrades", data.open_trades);
}

async function loadTrades(status = "") {
  const query = status ? `&status=${status}` : "";
  const data = await api(`/api/trades?user_id=${userId}${query}`);
  renderTrades("tradesTable", data.items);
}

async function loadContexts() {
  const data = await api(`/api/contexts?user_id=${userId}`);
  document.getElementById("contextsList").innerHTML = data.items.map(row => `
    <div class="row">
      <strong>${row.symbol}<small>${row.timeframe} ${row.bias.toUpperCase()}</small></strong>
      <span>${row.structure || "-"}</span>
      <span>${row.levels || "-"}</span>
      <span>${row.confidence}%</span>
      <span>${row.note || "-"}</span>
    </div>
  `).join("") || emptyRow("Контекста пока нет");
}

async function loadJournal() {
  const data = await api(`/api/journal?user_id=${userId}`);
  document.getElementById("journalList").innerHTML = data.items.map(row => `
    <div class="row">
      <strong>${row.symbol || "-"}<small>${row.outcome}</small></strong>
      <span>${row.description || "-"}</span>
      <span>${row.theory || "-"}</span>
      <span>${row.screenshot_file_id ? "screenshot" : "-"}</span>
      <span>${row.created_at}</span>
    </div>
  `).join("") || emptyRow("Дневник пуст");
}

function renderTrades(targetId, rows) {
  document.getElementById(targetId).innerHTML = rows.map(row => `
    <div class="row">
      <strong>#${row.id} ${row.symbol}<small>${row.side.toUpperCase()} ${row.status}</small></strong>
      <span>Entry ${fmt(row.entry_price, 6)}<small>Stop ${fmt(row.stop_price, 6)}</small></span>
      <span>Target ${row.target_price ? fmt(row.target_price, 6) : "-"}</span>
      <span>Risk ${fmt(row.risk_amount)}<small>x${fmt(row.leverage, 1)}</small></span>
      <span class="${Number(row.pnl || 0) >= 0 ? "positive" : "negative"}">${row.pnl == null ? "-" : signed(row.pnl)}</span>
    </div>
  `).join("") || emptyRow("Нет данных");
}

function emptyRow(text) {
  return `<div class="row"><strong>${text}</strong><span></span><span></span><span></span><span></span></div>`;
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
      `Qty: ${fmt(r.quantity, 6)}\n` +
      `Risk: ${fmt(r.risk_amount)} USDT\n` +
      `Margin: ${fmt(r.margin)} USDT\n` +
      `Potential profit: ${r.profit_at_target == null ? "-" : fmt(r.profit_at_target)} USDT\n` +
      `R/R: ${r.reward_to_risk == null ? "-" : fmt(r.reward_to_risk)}\n` +
      `До стопа: ${fmt(stopDistance)}%\n` +
      `До тейка: ${fmt(targetDistance)}%`;
  } catch (error) {
    document.getElementById("riskResult").textContent = "Проверь параметры расчета";
  }
}

async function loadAll() {
  await Promise.all([loadDashboard(), loadTrades(), loadContexts(), loadJournal()]);
  await calculateRisk();
}

loadAll();
