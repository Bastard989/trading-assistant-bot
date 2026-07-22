const telegramInitData = window.Telegram?.WebApp?.initData || "";
window.Telegram?.WebApp?.ready();
let appLocale = window.AppI18n?.getLocale() === "en" ? "en" : "ru";
const uiText = (ru, en) => appLocale === "en" ? en : ru;

const fmt = (value, digits = 2) => Number(value || 0).toLocaleString(
  appLocale === "en" ? "en-US" : "ru-RU",
  { maximumFractionDigits: digits }
);
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
const expandedCrisisHistory = new Set();
const crisisHistoryCache = new Map();
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
let crisisLocale = appLocale;
let crisisDetailed = false;
let crisisSnapshot = null;
let crisisWorld = null;
let crisisSourceHealth = null;
let crisisOpportunities = null;
let crisisAgentThreadId = null;
let crisisAgentState = null;
let crisisAgentMessages = [];
let crisisAgentBusy = false;

document.querySelectorAll(".tab").forEach(button => {
  button.addEventListener("click", async () => {
    document.querySelectorAll(".tab").forEach(item => item.classList.remove("active"));
    document.querySelectorAll(".view").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.view).classList.add("active");
    if (button.dataset.view === "market") await loadMarketTop();
    if (button.dataset.view === "sessions") await loadSessions();
    if (button.dataset.view === "analytics") renderAnalytics();
    if (button.dataset.view === "crisis-radar") await loadCrisisRadar();
    if (button.dataset.view === "models") await loadModelStatus();
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
document.getElementById("crisisModeBtn").addEventListener("click", () => {
  crisisDetailed = !crisisDetailed;
  renderCrisisRadar();
});
document.getElementById("crisisAgentForm").addEventListener("submit", askCrisisAgent);
document.querySelectorAll(".app-locale-btn").forEach(button => {
  button.addEventListener("click", async () => {
    appLocale = button.dataset.appLocale === "en" ? "en" : "ru";
    crisisLocale = appLocale;
    window.AppI18n?.setLocale(appLocale);
    await loadAll();
    if (document.getElementById("crisis-radar").classList.contains("active")) await loadCrisisRadar();
    if (document.getElementById("models").classList.contains("active")) await loadModelStatus();
  });
});
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
    case "refresh-local-model": event.stopPropagation(); await loadModelStatus(); break;
    case "toggle-crisis-history":
      event.stopPropagation(); await toggleCrisisHistory(target.dataset.indicatorCode || ""); break;
    case "open-crisis-indicator":
      event.stopPropagation(); openCrisisIndicator(target.dataset.indicatorCode || ""); break;
    case "crisis-help":
      event.stopPropagation(); openCrisisHelp(target.dataset.help || "stage"); break;
    case "opportunity-calculator":
      event.stopPropagation(); transferOpportunityToCalculator(target); break;
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

const crisisCopy = {
  ru: {
    title: "Что происходит с мировым рынком",
    signalsEyebrow: "Главное сегодня",
    signalsTitle: "Три сигнала, за которыми следим",
    signalsCopy: "Сработавшие или ближайшие к опасному порогу показатели.",
    nextEyebrow: "Дальше",
    nextTitle: "Следующая важная публикация",
    scenarioEyebrow: "Сценарий",
    leadScenarioTitle: "Что сейчас развивается",
    actionEyebrow: "Действие",
    bestOpportunityTitle: "Лучшая аналитическая возможность",
    openDetails: "Открыть точные пороги и историю",
    normalDistance: "Пока норма; расстояние до первого тревожного порога",
    nextDistance: "До следующего уровня риска",
    exactThresholds: "Точные пороги",
    noUpcoming: "Подтверждённых важных публикаций пока нет",
    noActiveScenario: "Ни один кризисный сценарий пока не активен",
    noOpportunity: "Недостаточно данных для направленной идеи — наблюдаем",
    snapshot: "Последний расчёт",
    active: "Групп ухудшается",
    danger: "Опасных групп",
    critical: "Критических групп",
    groups: "Каналы риска",
    groupsCopy: "Независимые группы сигналов. Несколько похожих показателей не считаются отдельными подтверждениями.",
    scenarios: "Сценарии",
    scenariosCopy: "Статус сценария меняется только при совместном подтверждении независимыми каналами.",
    opportunities: "Возможности",
    opportunitiesCopy: "Условные long / short / hedge / wait идеи без открытия сделки.",
    opportunityCoverage: "классов данных",
    optionHealthy: "Bybit-опционы доступны",
    optionDegraded: "Bybit-опционы: нет ликвидного спреда",
    opportunityWait: "Ждать подтверждения",
    opportunityScore: "оценка идеи",
    opportunityTrigger: "Условие активации",
    opportunityInvalidation: "Отмена идеи",
    opportunityRange: "Сценарный диапазон",
    opportunityLoss: "Диапазон риска",
    opportunityCalculator: "Перенести в калькулятор",
    opportunityDisclaimer: "Диапазоны — сценарная оценка, а не обещание прибыли. Сделки автоматически не создаются.",
    world: "Мировой контур",
    worldCopy: "США, Европа, Китай и глобальные каналы с честными пропусками.",
    available: "доступно",
    fresh: "свежие",
    stale: "устарели",
    missing: "нет данных",
    sourcesHealthy: "источников без ошибки",
    news: "Официальный контекст",
    newsCopy: "Публикации центральных банков, связанные со сценариями. Новость сама по себе не меняет стадию рынка.",
    newsEmpty: "За последние 14 дней релевантных официальных публикаций нет",
    contextSeverity: "контекст",
    watchSeverity: "наблюдение",
    urgentSeverity: "срочно проверить",
    calendar: "Ближайшие публикации",
    calendarCopy: "Подтверждённые даты важных макроэкономических данных. FRED не публикует здесь точное время выхода.",
    timePending: "время не опубликовано",
    highImportance: "высокая важность",
    mediumImportance: "средняя важность",
    calendarEmpty: "В ближайшие 30 дней подтверждённых публикаций из выбранного списка нет",
    horizon: "горизонт",
    confidence: "полнота данных",
    evidence: "активных каналов",
    indicators: "Индикаторы и пороги",
    indicatorsCopy: "Текущее значение, расстояние до следующего порога и свежесть данных.",
    technical: "Техническая проверка",
    technicalCopy: "Версия методики, внутренние scores и здоровье источников.",
    detailed: "Все данные и методика",
    simple: "Вернуться к главному",
    noData: "Данные ещё не загружены",
    loading: "Загружаю состояние рынка…",
    unavailable: "Crisis Radar пока недоступен. Проверьте миграцию, feature flag и синхронизацию.",
    threshold: "пороги",
    next: "до следующего",
    source: "источник",
    score: "технический score",
    method: "методика",
    changes: "Что изменилось",
    insufficientHistory: "Недостаточно сохранённой истории",
    activeDelta: "изменение активных групп",
    stageChange: "стадия",
    history: "История и пороги",
    hideHistory: "Скрыть график",
    chartLoading: "Загружаю историю…",
    chartEmpty: "Для графика нужно минимум две точки",
    current: "значение",
    eventZones: "исторические эпизоды",
    rawSignal: "сырой сигнал",
    confirmations: "наблюдений",
    pendingConfirmation: "ожидает подтверждения",
    hysteresisHold: "уровень удерживается запасом восстановления",
    agentTitle: "Спросить аналитика",
    agentCopy: "Подключённая модель объясняет уже рассчитанные данные и не меняет сигналы, пороги или сделки.",
    agentMode: "Режим",
    agentQuestion: "Вопрос",
    agentPlaceholder: "Что сейчас ухудшается и почему это важно?",
    agentSend: "Спросить",
    agentSending: "Анализирую…",
    agentFast: "Быстрый",
    agentDeep: "Глубокий",
    agentReady: "модель готова",
    agentReadyWarm: "в памяти",
    agentReadyCold: "готова · холодный запуск",
    agentCooldown: "пауза после таймаута",
    agentMissing: "модель не найдена",
    agentOffline: "провайдер недоступен",
    agentDisabled: "аналитик выключен",
    agentEmpty: "Задай вопрос о текущей стадии, сценариях, порогах или изменениях индикаторов.",
    agentError: "Аналитик сейчас не ответил. Проверь выбранный провайдер и повтори запрос.",
    agentEvidence: "Опирается на",
    agentLimitations: "Ограничения",
    agentGrounded: "проверено по данным",
    agentUngrounded: "требует ручной проверки",
    agentDisclaimer: "Это объяснение данных, а не финансовый совет и не команда на сделку.",
  },
  en: {
    title: "What is happening in global markets",
    signalsEyebrow: "Today at a glance",
    signalsTitle: "Three signals to watch",
    signalsCopy: "Triggered indicators or those nearest to a more dangerous threshold.",
    nextEyebrow: "Next",
    nextTitle: "Next important release",
    scenarioEyebrow: "Scenario",
    leadScenarioTitle: "What may be developing now",
    actionEyebrow: "Action",
    bestOpportunityTitle: "Best analytical opportunity",
    openDetails: "Open exact thresholds and history",
    normalDistance: "Still normal; distance to the first warning threshold",
    nextDistance: "Distance to the next risk level",
    exactThresholds: "Exact thresholds",
    noUpcoming: "No confirmed important releases are currently available",
    noActiveScenario: "No crisis scenario is currently active",
    noOpportunity: "Not enough evidence for a directional idea — keep watching",
    snapshot: "Last calculation",
    active: "Groups deteriorating",
    danger: "Danger groups",
    critical: "Critical groups",
    groups: "Risk channels",
    groupsCopy: "Independent signal groups. Correlated indicators are not treated as separate confirmation.",
    scenarios: "Scenarios",
    scenariosCopy: "A scenario changes status only when independent channels confirm it together.",
    opportunities: "Opportunities",
    opportunitiesCopy: "Conditional long / short / hedge / wait ideas without opening a trade.",
    opportunityCoverage: "data classes",
    optionHealthy: "Bybit options available",
    optionDegraded: "Bybit options: no liquid spread",
    opportunityWait: "Wait for confirmation",
    opportunityScore: "idea score",
    opportunityTrigger: "Activation condition",
    opportunityInvalidation: "Idea invalidation",
    opportunityRange: "Scenario range",
    opportunityLoss: "Risk range",
    opportunityCalculator: "Send to calculator",
    opportunityDisclaimer: "Ranges are scenario estimates, not promised returns. No trade is opened automatically.",
    world: "World contour",
    worldCopy: "United States, Europe, China and global channels with honest missing-data states.",
    available: "available",
    fresh: "fresh",
    stale: "stale",
    missing: "missing",
    sourcesHealthy: "sources without an error",
    news: "Official context",
    newsCopy: "Central-bank releases linked to scenarios. A news item never changes the market stage by itself.",
    newsEmpty: "No relevant official releases in the past 14 days",
    contextSeverity: "context",
    watchSeverity: "watch",
    urgentSeverity: "review urgently",
    calendar: "Upcoming releases",
    calendarCopy: "Confirmed dates for important macro data. FRED does not provide an exact release time here.",
    timePending: "time not published",
    highImportance: "high importance",
    mediumImportance: "medium importance",
    calendarEmpty: "No confirmed selected releases in the next 30 days",
    horizon: "horizon",
    confidence: "data coverage",
    evidence: "active channels",
    indicators: "Indicators and thresholds",
    indicatorsCopy: "Current value, distance to the next threshold and data freshness.",
    technical: "Technical audit",
    technicalCopy: "Methodology version, internal scores and source health.",
    detailed: "All data and methodology",
    simple: "Back to overview",
    noData: "Data has not been loaded yet",
    loading: "Loading market state…",
    unavailable: "Crisis Radar is unavailable. Check the migration, feature flag and synchronization.",
    threshold: "thresholds",
    next: "to next",
    source: "source",
    score: "technical score",
    method: "methodology",
    changes: "What changed",
    insufficientHistory: "Not enough saved history",
    activeDelta: "active-group change",
    stageChange: "stage",
    history: "History and thresholds",
    hideHistory: "Hide chart",
    chartLoading: "Loading history…",
    chartEmpty: "At least two points are required for a chart",
    current: "value",
    eventZones: "historical episodes",
    rawSignal: "raw signal",
    confirmations: "observations",
    pendingConfirmation: "awaiting confirmation",
    hysteresisHold: "level held by recovery margin",
    agentTitle: "Ask the analyst",
    agentCopy: "The connected model explains calculated data and cannot change signals, thresholds or trades.",
    agentMode: "Mode",
    agentQuestion: "Question",
    agentPlaceholder: "What is deteriorating now and why does it matter?",
    agentSend: "Ask",
    agentSending: "Analyzing…",
    agentFast: "Fast",
    agentDeep: "Deep",
    agentReady: "model ready",
    agentReadyWarm: "in memory",
    agentReadyCold: "ready · cold start",
    agentCooldown: "timeout cooldown",
    agentMissing: "model missing",
    agentOffline: "provider unavailable",
    agentDisabled: "analyst disabled",
    agentEmpty: "Ask about the current stage, scenarios, thresholds or indicator changes.",
    agentError: "The analyst did not answer. Check the selected provider and try again.",
    agentEvidence: "Evidence",
    agentLimitations: "Limitations",
    agentGrounded: "grounded in data",
    agentUngrounded: "manual verification required",
    agentDisclaimer: "This explains data; it is not financial advice or a trade instruction.",
  },
};

const crisisHelpCopy = {
  ru: {
    stage: ["Стадия мирового рынка", "Показывает, насколько широко и тяжело уже ухудшаются независимые части экономики и рынков. Это не вероятность и не обещание кризиса.", ["Напряжение означает отдельные ранние проблемы.", "Предупреждение требует нескольких ухудшающихся каналов.", "Подтверждение и кризис требуют одновременного сильного ухудшения независимых групп."]],
    signals: ["Главные сигналы", "Радар выбирает три наиболее тревожных или ближайших к следующему порогу индикатора, чтобы не заставлять тебя просматривать весь список.", ["Нажми карточку, чтобы открыть точные пороги и историю.", "Один сигнал не подтверждает кризис.", "Важнее одновременное ухудшение нескольких независимых каналов."]],
    calendar: ["Публикации данных", "Здесь показано, когда выйдет информация, способная подтвердить или ослабить текущую тревогу.", ["Дата берётся из официального календаря.", "Отсутствующее время не выдумывается.", "Сам выход данных ещё не означает кризис — важно новое значение."]],
    groups: ["Каналы риска", "Похожие показатели объединяются в группы, чтобы один и тот же процесс не считался несколько раз.", ["Сильнейший индикатор даёт 70% оценки группы.", "Среднее состояние группы даёт ещё 30%.", "Общая стадия считает группы, а не количество всех сырых рядов."]],
    scenarios: ["Кризисные сценарии", "Сценарий объединяет несколько независимых каналов и конкретный временной горизонт.", ["Наблюдение — раннее сочетание.", "Повышен — ухудшение стало сильнее.", "Подтверждён — несколько необходимых условий сработали совместно."]],
    opportunities: ["Аналитические возможности", "Это условные варианты long, short, hedge или wait, а не готовые сделки и не обещание доходности.", ["Идея появляется только при достаточном подтверждении и качестве данных.", "Диапазон прибыли является сценарием.", "При слабых данных честный результат — ждать."]],
    world: ["Мировой контур", "Показывает покрытие США, Европы, Китая, глобальной экономики и крипторынка.", ["Свежие данные участвуют в расчёте.", "Устаревшие и отсутствующие данные обозначаются отдельно.", "Пропуск не заменяется выдуманным значением."]],
    agent: ["Локальный аналитик", "Модель объясняет уже рассчитанные сигналы простыми словами, но не имеет права менять числа, пороги или стадию.", ["Ответ проходит проверку опоры на данные.", "Модель не открывает сделки.", "При сбое остаётся детерминированное объяснение системы."]],
    news: ["Официальный контекст", "Новости центральных банков помогают понять причины и возможные последствия, но не меняют числовой сигнал сами по себе.", ["Используются официальные источники.", "Новость связывается со сценариями.", "Числовая стадия считается только по данным."]],
    indicators: ["Индикаторы и точные пороги", "Здесь находится полный технический уровень: значение, warning, danger, critical, направление ухудшения, свежесть и источник.", ["Пороги обязательны и версионируются.", "Сырой порог — это число в исходной единице.", "Персональный порог должен отображаться отдельно от системного."]],
    methodology: ["Данные и методика", "Раздел нужен для аудита: версия расчётов, качество источников, внутренние оценки и причины деградации.", ["Изменение порогов требует новой версии.", "Вероятность показывается только после успешной исторической проверки.", "Недостаточно данных — нормальный и честный результат."]],
  },
  en: {
    stage: ["Global market stage", "Shows how broad and severe the observed deterioration is across independent parts of the economy and markets. It is not a probability or a promise of a crisis.", ["Tension means isolated early weakness.", "Warning requires several deteriorating channels.", "Confirmation and crisis require broad simultaneous stress."]],
    signals: ["Top signals", "The radar selects the three most stressed indicators or those nearest to the next threshold.", ["Open a card for exact thresholds and history.", "One signal does not confirm a crisis.", "Independent confirmation matters more than raw count."]],
    calendar: ["Data releases", "Shows when information that could confirm or weaken the current concern is expected.", ["Dates come from official calendars.", "Missing times are not invented.", "A release matters through its value, not merely because it happened."]],
    groups: ["Risk channels", "Related indicators are grouped so the same process is not counted several times.", ["The strongest indicator contributes 70%.", "The group mean contributes 30%.", "Market stage counts groups, not every raw series."]],
    scenarios: ["Crisis scenarios", "A scenario combines independent channels with a defined horizon.", ["Watch is an early combination.", "Elevated means broader weakness.", "Confirmed means required conditions aligned."]],
    opportunities: ["Analytical opportunities", "Conditional long, short, hedge or wait views — not orders or promised returns.", ["Ideas require evidence and data quality.", "Return ranges are scenarios.", "Weak data correctly produces wait."]],
    world: ["World coverage", "Shows coverage across the US, Europe, China, the global economy and crypto.", ["Fresh data is used.", "Stale and missing data are explicit.", "Missing values are never fabricated."]],
    agent: ["Local analyst", "The model explains calculated signals but cannot change numbers, thresholds or stage.", ["Answers are checked against evidence.", "It cannot open trades.", "A deterministic fallback remains available."]],
    news: ["Official context", "Central-bank news explains context but cannot change the numerical stage on its own.", ["Official feeds are used.", "News is linked to scenarios.", "Only data drives the calculated stage."]],
    indicators: ["Indicators and exact thresholds", "The complete technical layer: current value, warning, danger, critical, risk direction, freshness and source.", ["Thresholds are mandatory and versioned.", "Raw means the original measurement unit.", "Personal thresholds must remain separate from system thresholds."]],
    methodology: ["Data and methodology", "The audit layer: calculation version, source quality, internal scores and degradation reasons.", ["Threshold changes require a new version.", "Probability requires validated historical calibration.", "Insufficient data is an honest result."]],
  },
};

const crisisStageLabels = {
  ru: { stable: "стабильность", tension: "напряжение", warning: "предупреждение", confirmation: "подтверждение", crisis: "кризис", unknown: "нет данных" },
  en: { stable: "stability", tension: "tension", warning: "warning", confirmation: "confirmation", crisis: "crisis", unknown: "no data" },
};

const crisisBandLabels = {
  ru: { normal: "норма", warning: "предупреждение", danger: "опасно", critical: "критично" },
  en: { normal: "normal", warning: "warning", danger: "danger", critical: "critical" },
};

const crisisFreshnessLabels = {
  ru: { fresh: "свежие", delayed: "задержка", stale: "устарели", missing: "нет данных" },
  en: { fresh: "fresh", delayed: "delayed", stale: "stale", missing: "missing" },
};

const crisisScenarioLabels = {
  ru: { inactive: "не активен", watch: "наблюдение", elevated: "повышен", confirmed: "подтверждён" },
  en: { inactive: "inactive", watch: "watch", elevated: "elevated", confirmed: "confirmed" },
};

const crisisConfidenceLabels = {
  ru: { low: "низкая", medium: "средняя", high: "высокая" },
  en: { low: "low", medium: "medium", high: "high" },
};

const crisisNames = {
  ru: {
    sahm_rule: "Индикатор рецессии Sahm Rule",
    us_hy_oas: "Спред высокодоходных облигаций США",
    vix: "Индекс волатильности VIX",
    labor: "Рынок труда",
    credit: "Кредит",
    market_stress: "Рыночный стресс",
    us_real_gdp_qoq: "Рост реального ВВП США",
    wti_90d_change: "Изменение WTI за 90 дней",
    real_economy: "Реальная экономика",
    inflation_commodities: "Инфляция и сырьё",
    euro_ciss: "Системный финансовый стресс еврозоны",
    euro_real_gdp_qoq: "Рост реального ВВП еврозоны",
    euro_financial_stress: "Финансовый стресс еврозоны",
    euro_growth: "Экономический рост еврозоны",
    global_recession: "Глобальное замедление / рецессия",
    financial_stress: "Системный финансовый стресс",
    oil_stagflation: "Нефтяной инфляционный шок",
    btc_funding_rate: "Funding BTC",
    eth_funding_rate: "Funding ETH",
    btc_oi_7d_abs_change: "Изменение OI BTC за 7 дней",
    eth_oi_7d_abs_change: "Изменение OI ETH за 7 дней",
    btc_30d_drawdown: "Просадка BTC за 30 дней",
    eth_30d_drawdown: "Просадка ETH за 30 дней",
    crypto_leverage: "Криптовалютные плечи",
    crypto_price_stress: "Ценовой стресс крипторынка",
    crypto_leverage_unwind: "Криптовалютный сброс плечей",
    sp500_30d_drawdown: "Просадка S&P 500 за 30 дней",
    us_10y2y_spread: "Спред Treasury 10Y–2Y",
    us_nfci: "Финансовые условия США NFCI",
    fed_assets_90d_change: "Изменение активов ФРС за 90 дней",
    equity_market_stress: "Стресс рынка акций США",
    rates_liquidity: "Ставки и ликвидность США",
    us_financial_conditions: "Финансовые условия США",
    china_real_gdp_yoy: "Рост реального ВВП Китая",
    world_real_gdp_yoy: "Рост мирового реального ВВП",
    us_credit_to_gdp_gap: "Кредитный разрыв США",
    china_credit_to_gdp_gap: "Кредитный разрыв Китая",
    china_growth: "Экономический рост Китая",
    global_growth: "Мировой экономический рост",
    global_credit_cycle: "Глобальный кредитный цикл",
    g20_cli_6m_change: "Динамика опережающего индикатора G20 за 6 месяцев",
    china_cli_6m_change: "Динамика опережающего индикатора Китая за 6 месяцев",
    global_leading_cycle: "Опережающий цикл G20",
    china_leading_cycle: "Опережающий цикл Китая",
    china_hard_landing: "Резкое замедление экономики Китая",
  },
  en: {
    sahm_rule: "Sahm Rule recession indicator",
    us_hy_oas: "US high-yield bond spread",
    vix: "VIX volatility index",
    us_real_gdp_qoq: "US real GDP growth",
    wti_90d_change: "WTI 90-day change",
    labor: "Labor market",
    credit: "Credit",
    market_stress: "Market stress",
    real_economy: "Real economy",
    inflation_commodities: "Inflation and commodities",
    euro_ciss: "Euro-area systemic financial stress",
    euro_real_gdp_qoq: "Euro-area real GDP growth",
    euro_financial_stress: "Euro-area financial stress",
    euro_growth: "Euro-area economic growth",
    global_recession: "Global slowdown / recession",
    financial_stress: "Systemic financial stress",
    oil_stagflation: "Oil-driven inflation shock",
    btc_funding_rate: "BTC funding",
    eth_funding_rate: "ETH funding",
    btc_oi_7d_abs_change: "BTC OI 7-day change",
    eth_oi_7d_abs_change: "ETH OI 7-day change",
    btc_30d_drawdown: "BTC 30-day drawdown",
    eth_30d_drawdown: "ETH 30-day drawdown",
    crypto_leverage: "Crypto leverage",
    crypto_price_stress: "Crypto price stress",
    crypto_leverage_unwind: "Crypto leverage unwind",
    sp500_30d_drawdown: "S&P 500 30-day drawdown",
    us_10y2y_spread: "Treasury 10Y–2Y spread",
    us_nfci: "US financial conditions NFCI",
    fed_assets_90d_change: "Fed assets 90-day change",
    equity_market_stress: "US equity-market stress",
    rates_liquidity: "US rates and liquidity",
    us_financial_conditions: "US financial conditions",
    china_real_gdp_yoy: "China real GDP growth",
    world_real_gdp_yoy: "World real GDP growth",
    us_credit_to_gdp_gap: "US credit-to-GDP gap",
    china_credit_to_gdp_gap: "China credit-to-GDP gap",
    china_growth: "China economic growth",
    global_growth: "Global economic growth",
    global_credit_cycle: "Global credit cycle",
    g20_cli_6m_change: "G20 leading indicator 6-month momentum",
    china_cli_6m_change: "China leading indicator 6-month momentum",
    global_leading_cycle: "G20 leading cycle",
    china_leading_cycle: "China leading cycle",
    china_hard_landing: "China hard landing",
  },
};

function safeCrisisState(value, allowed, fallback) {
  const normalized = String(value || "").toLowerCase();
  return allowed.includes(normalized) ? normalized : fallback;
}

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" ? url.href : "#";
  } catch {
    return "#";
  }
}

function crisisNumber(value, digits = 2) {
  const locale = crisisLocale === "en" ? "en-US" : "ru-RU";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(locale, { maximumFractionDigits: digits }) : "—";
}

function safeCrisisCode(value) {
  const code = String(value || "").toLowerCase();
  return /^[a-z0-9_]{1,64}$/.test(code) ? code : "";
}

function localizeCrisisExplanation(value) {
  let result = String(value || "");
  const names = crisisNames[crisisLocale] || {};
  Object.entries(names)
    .sort(([left], [right]) => right.length - left.length)
    .forEach(([code, label]) => {
      result = result.replace(new RegExp(`\\b${code}\\b`, "g"), label);
    });
  return result;
}

function localizeCrisisHorizon(value) {
  const result = String(value || "—");
  if (crisisLocale !== "ru") return result;
  return result.replace(/(\d+)h\b/g, "$1 ч").replace(/(\d+)d\b/g, "$1 дн.").replace(/(\d+)m\b/g, "$1 мес.").replace(/(\d+)y\b/g, "$1 г.");
}

function crisisSigned(value, digits = 0) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number > 0 ? "+" : ""}${crisisNumber(number, digits)}`;
}

function openCrisisHelp(topic) {
  const fallback = crisisHelpCopy[crisisLocale]?.stage;
  const [title, body, points] = crisisHelpCopy[crisisLocale]?.[topic] || fallback;
  document.getElementById("crisisHelpEyebrow").textContent = crisisLocale === "en" ? "Guide" : "Подсказка";
  document.getElementById("crisisHelpTitle").textContent = title;
  document.getElementById("crisisHelpBody").textContent = body;
  document.getElementById("crisisHelpPoints").innerHTML = points.map(point => `<div>${escapeHtml(point)}</div>`).join("");
  const dialog = document.getElementById("crisisHelpDialog");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function openCrisisIndicator(code) {
  const safeCode = safeCrisisCode(code);
  if (!safeCode) return;
  crisisDetailed = true;
  renderCrisisRadar();
  requestAnimationFrame(() => {
    const card = document.getElementById(`crisis-indicator-${safeCode}`);
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
    card?.classList.add("is-highlighted");
    window.setTimeout(() => card?.classList.remove("is-highlighted"), 1600);
  });
}

async function loadCrisisRadar() {
  const copy = crisisCopy[crisisLocale];
  document.getElementById("crisisExplanation").textContent = copy.loading;
  try {
    crisisSnapshot = await api(`/api/crisis-radar/overview?locale=${crisisLocale}`);
    const requests = await Promise.allSettled([
      api(`/api/crisis-radar/calendar?locale=${crisisLocale}&days=30`),
      api(`/api/crisis-radar/news?locale=${crisisLocale}&days=14&limit=20`),
      api(`/api/crisis-radar/world?locale=${crisisLocale}`),
      api(`/api/crisis-radar/sources/health?locale=${crisisLocale}`),
      api(`/api/crisis-radar/opportunities?locale=${crisisLocale}&limit=10`),
    ]);
    crisisSnapshot.calendar = requests[0].status === "fulfilled" ? requests[0].value : { ready: false, events: [] };
    crisisSnapshot.news = requests[1].status === "fulfilled" ? requests[1].value : { ready: false, items: [] };
    crisisWorld = requests[2].status === "fulfilled" ? requests[2].value : { ready: false, regions: [] };
    crisisSourceHealth = requests[3].status === "fulfilled" ? requests[3].value : { ready: false, sources: [], summary: {} };
    crisisOpportunities = requests[4].status === "fulfilled" ? requests[4].value : { ready: false, ideas: [], available_asset_classes: [] };
  } catch {
    crisisSnapshot = { ready: false, stage: "unknown", explanation: copy.unavailable, groups: [], indicators: [], sources: [] };
    crisisWorld = { ready: false, regions: [] };
    crisisSourceHealth = { ready: false, sources: [], summary: {} };
    crisisOpportunities = { ready: false, ideas: [], available_asset_classes: [] };
  }
  await loadCrisisAgent();
  renderCrisisRadar();
}

function renderCrisisToday(snapshot, opportunityPayload, copy) {
  const bandRank = { normal: 0, warning: 1, danger: 2, critical: 3 };
  const indicators = (snapshot.indicators || [])
    .filter(item => safeCrisisCode(item.code) && item.freshness !== "missing")
    .sort((left, right) => {
      const bandDelta = (bandRank[right.band] || 0) - (bandRank[left.band] || 0);
      if (bandDelta) return bandDelta;
      const leftDistance = Number(left.distance_to_next_text);
      const rightDistance = Number(right.distance_to_next_text);
      if (Number.isFinite(leftDistance) && Number.isFinite(rightDistance)) return leftDistance - rightDistance;
      return Number.isFinite(leftDistance) ? -1 : 1;
    })
    .slice(0, 3);

  document.getElementById("crisisTopSignals").innerHTML = indicators.map(item => {
    const code = safeCrisisCode(item.code);
    const band = safeCrisisState(item.band, ["normal", "warning", "danger", "critical"], "normal");
    const name = crisisNames[crisisLocale]?.[item.code] || item.name || item.code;
    const distance = Number(item.distance_to_next_text);
    const distanceText = Number.isFinite(distance)
      ? `${band === "normal" ? copy.normalDistance : copy.nextDistance}: ${crisisNumber(distance, 4)} ${escapeHtml(item.unit || "")}`
      : (band === "critical" ? crisisBandLabels[crisisLocale].critical : copy.noData);
    return `<button class="crisis-top-signal band-${band}" type="button" data-action="open-crisis-indicator" data-indicator-code="${code}"><div class="crisis-top-signal-head"><span>${escapeHtml(crisisBandLabels[crisisLocale][band])}</span><small>${crisisNumber(item.value_text, 4)} ${escapeHtml(item.unit || "")}</small></div><strong>${escapeHtml(name)}</strong><p>${distanceText}</p><small class="crisis-today-action">${escapeHtml(copy.openDetails)} →</small></button>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.noData)}</div>`;

  const nextEvent = (snapshot.calendar?.events || [])[0];
  if (nextEvent) {
    const dateValue = /^\d{4}-\d{2}-\d{2}$/.test(nextEvent.release_date || "")
      ? new Date(`${nextEvent.release_date}T00:00:00`) : null;
    const dateLabel = dateValue && !Number.isNaN(dateValue.getTime())
      ? dateValue.toLocaleDateString(crisisLocale === "en" ? "en-US" : "ru-RU", { day: "numeric", month: "long", weekday: "short" }) : "—";
    document.getElementById("crisisNextEvent").innerHTML = `<div class="crisis-today-result-card ${nextEvent.importance === "high" ? "is-critical" : "is-wait"}"><div class="crisis-today-result-head"><span>${escapeHtml(nextEvent.importance === "high" ? copy.highImportance : copy.mediumImportance)}</span><small>${escapeHtml(dateLabel)}</small></div><strong>${escapeHtml(nextEvent.release_name || nextEvent.official_name || "—")}</strong><p>${escapeHtml(nextEvent.time_confirmed && nextEvent.scheduled_at ? nextEvent.scheduled_at : copy.timePending)}</p></div>`;
  } else {
    document.getElementById("crisisNextEvent").innerHTML = `<div class="crisis-today-result-card"><strong>${escapeHtml(copy.noUpcoming)}</strong></div>`;
  }

  const scenarioRank = { inactive: 0, watch: 1, elevated: 2, confirmed: 3 };
  const leadScenario = [...(snapshot.scenarios || [])]
    .sort((left, right) => (scenarioRank[right.status] || 0) - (scenarioRank[left.status] || 0))[0];
  if (leadScenario && scenarioRank[leadScenario.status] > 0) {
    const status = safeCrisisState(leadScenario.status, ["watch", "elevated", "confirmed"], "watch");
    const cardClass = status === "confirmed" ? "is-critical" : "is-alert";
    document.getElementById("crisisLeadScenario").innerHTML = `<div class="crisis-today-result-card ${cardClass}"><div class="crisis-today-result-head"><span>${escapeHtml(crisisScenarioLabels[crisisLocale][status])}</span><small>${escapeHtml(localizeCrisisHorizon(leadScenario.horizon))}</small></div><strong>${escapeHtml(leadScenario.name || crisisNames[crisisLocale]?.[leadScenario.code] || leadScenario.code)}</strong><p>${escapeHtml(localizeCrisisExplanation(leadScenario.explanation || ""))}</p><small>${escapeHtml(copy.evidence)}: ${Number(leadScenario.active_group_count || 0)}</small></div>`;
  } else {
    document.getElementById("crisisLeadScenario").innerHTML = `<div class="crisis-today-result-card"><strong>${escapeHtml(copy.noActiveScenario)}</strong><p>${escapeHtml(crisisLocale === "en" ? "Keep monitoring independent channels." : "Продолжаем наблюдать за независимыми каналами.")}</p></div>`;
  }

  const bestIdea = (opportunityPayload.ideas || [])[0];
  if (bestIdea) {
    const side = safeCrisisState(bestIdea.side, ["long", "short", "hedge", "wait"], "wait");
    const label = side === "wait" ? copy.opportunityWait : side.toUpperCase();
    document.getElementById("crisisBestOpportunity").innerHTML = `<div class="crisis-today-result-card ${side === "wait" ? "is-wait" : "is-alert"}"><div class="crisis-today-result-head"><span>${escapeHtml(label)}</span><small>${escapeHtml(copy.opportunityScore)} ${crisisNumber(Number(bestIdea.score) * 100, 0)}/100</small></div><strong>${escapeHtml(bestIdea.symbol || "MARKET")}</strong><p>${escapeHtml(bestIdea.rationale || copy.noOpportunity)}</p></div>`;
  } else {
    document.getElementById("crisisBestOpportunity").innerHTML = `<div class="crisis-today-result-card is-wait"><strong>${escapeHtml(copy.noOpportunity)}</strong></div>`;
  }
}

function renderCrisisRadar() {
  const copy = crisisCopy[crisisLocale];
  const snapshot = crisisSnapshot || { ready: false, stage: "unknown", groups: [], indicators: [], sources: [] };
  const stage = safeCrisisState(snapshot.stage, ["stable", "tension", "warning", "confirmation", "crisis"], "unknown");
  document.getElementById("crisisTitle").textContent = copy.title;
  document.getElementById("crisisSignalsEyebrow").textContent = copy.signalsEyebrow;
  document.getElementById("crisisSignalsTitle").textContent = copy.signalsTitle;
  document.getElementById("crisisSignalsCopy").textContent = copy.signalsCopy;
  document.getElementById("crisisNextEyebrow").textContent = copy.nextEyebrow;
  document.getElementById("crisisNextTitle").textContent = copy.nextTitle;
  document.getElementById("crisisScenarioEyebrow").textContent = copy.scenarioEyebrow;
  document.getElementById("crisisLeadScenarioTitle").textContent = copy.leadScenarioTitle;
  document.getElementById("crisisActionEyebrow").textContent = copy.actionEyebrow;
  document.getElementById("crisisBestOpportunityTitle").textContent = copy.bestOpportunityTitle;
  document.getElementById("crisisAsOfLabel").textContent = copy.snapshot;
  document.getElementById("crisisActiveLabel").textContent = copy.active;
  document.getElementById("crisisDangerLabel").textContent = copy.danger;
  document.getElementById("crisisCriticalLabel").textContent = copy.critical;
  document.getElementById("crisisGroupsTitle").textContent = copy.groups;
  document.getElementById("crisisGroupsCopy").textContent = copy.groupsCopy;
  document.getElementById("crisisScenariosTitle").textContent = copy.scenarios;
  document.getElementById("crisisScenariosCopy").textContent = copy.scenariosCopy;
  document.getElementById("crisisOpportunitiesTitle").textContent = copy.opportunities;
  document.getElementById("crisisOpportunitiesCopy").textContent = copy.opportunitiesCopy;
  document.getElementById("crisisOpportunityDisclaimer").textContent = copy.opportunityDisclaimer;
  document.getElementById("crisisWorldTitle").textContent = copy.world;
  document.getElementById("crisisWorldCopy").textContent = copy.worldCopy;
  document.getElementById("crisisNewsTitle").textContent = copy.news;
  document.getElementById("crisisNewsCopy").textContent = copy.newsCopy;
  document.getElementById("crisisCalendarTitle").textContent = copy.calendar;
  document.getElementById("crisisCalendarCopy").textContent = copy.calendarCopy;
  document.getElementById("crisisIndicatorsTitle").textContent = copy.indicators;
  document.getElementById("crisisIndicatorsCopy").textContent = copy.indicatorsCopy;
  document.getElementById("crisisTechnicalTitle").textContent = copy.technical;
  document.getElementById("crisisTechnicalCopy").textContent = copy.technicalCopy;
  document.getElementById("crisisAgentTitle").textContent = copy.agentTitle;
  document.getElementById("crisisAgentCopy").textContent = copy.agentCopy;
  document.getElementById("crisisAgentModeLabel").textContent = copy.agentMode;
  document.getElementById("crisisAgentQuestionLabel").textContent = copy.agentQuestion;
  document.getElementById("crisisAgentQuestion").placeholder = copy.agentPlaceholder;
  document.getElementById("crisisAgentSend").textContent = copy.agentSend;
  document.getElementById("crisisAgentDisclaimer").textContent = copy.agentDisclaimer;
  document.querySelector('#crisisAgentMode option[value="fast"]').textContent = copy.agentFast;
  document.querySelector('#crisisAgentMode option[value="deep"]').textContent = copy.agentDeep;
  document.getElementById("crisisModeBtn").textContent = crisisDetailed ? copy.simple : copy.detailed;
  const stageNode = document.getElementById("crisisStageBadge");
  stageNode.textContent = crisisStageLabels[crisisLocale][stage];
  stageNode.className = `crisis-stage stage-${stage}`;
  document.getElementById("crisisExplanation").textContent = localizeCrisisExplanation(snapshot.explanation || copy.noData);
  const timestamp = snapshot.as_of ? new Date(snapshot.as_of) : null;
  document.getElementById("crisisAsOf").textContent = timestamp && !Number.isNaN(timestamp.getTime())
    ? timestamp.toLocaleString(crisisLocale === "en" ? "en-US" : "ru-RU") : "—";
  document.getElementById("crisisActiveGroups").textContent = snapshot.breadth?.active || 0;
  document.getElementById("crisisDangerGroups").textContent = snapshot.breadth?.danger_or_worse || 0;
  document.getElementById("crisisCriticalGroups").textContent = snapshot.breadth?.critical || 0;
  document.querySelectorAll("[data-crisis-detail]").forEach(section => { section.hidden = !crisisDetailed; });

  document.getElementById("crisisChanges").innerHTML = ["24h", "7d"].map(windowKey => {
    const change = snapshot.changes?.[windowKey];
    const label = windowKey === "24h" ? "24h" : "7d";
    if (!change?.available) {
      return `<article class="crisis-change unavailable"><span>${label} · ${escapeHtml(copy.changes)}</span><strong>—</strong><small>${escapeHtml(copy.insufficientHistory)}</small></article>`;
    }
    const fromStage = safeCrisisState(change.stage_from, ["stable", "tension", "warning", "confirmation", "crisis"], "unknown");
    const toStage = safeCrisisState(change.stage_to, ["stable", "tension", "warning", "confirmation", "crisis"], "unknown");
    return `<article class="crisis-change"><span>${label} · ${escapeHtml(copy.changes)}</span><strong>${escapeHtml(crisisStageLabels[crisisLocale][fromStage])} → ${escapeHtml(crisisStageLabels[crisisLocale][toStage])}</strong><small>${escapeHtml(copy.activeDelta)}: ${crisisSigned(change.active_groups_delta)}</small></article>`;
  }).join("");

  const calendarEvents = snapshot.calendar?.events || [];
  document.getElementById("crisisCalendar").innerHTML = calendarEvents.map(item => {
    const importance = item.importance === "high" ? "high" : "medium";
    const dateValue = /^\d{4}-\d{2}-\d{2}$/.test(item.release_date || "")
      ? new Date(`${item.release_date}T00:00:00`) : null;
    const dateLabel = dateValue && !Number.isNaN(dateValue.getTime())
      ? dateValue.toLocaleDateString(crisisLocale === "en" ? "en-US" : "ru-RU", { day: "numeric", month: "short", weekday: "short" })
      : "—";
    const sourceUrl = safeExternalUrl(item.source_url);
    const name = escapeHtml(item.release_name || item.official_name || "—");
    const linkedName = sourceUrl === "#" ? name : `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${name}</a>`;
    const importanceLabel = importance === "high" ? copy.highImportance : copy.mediumImportance;
    const timeLabel = item.time_confirmed && item.scheduled_at ? item.scheduled_at : copy.timePending;
    return `<article class="crisis-calendar-event importance-${importance}"><time>${escapeHtml(dateLabel)}</time><div><strong>${linkedName}</strong><small>${escapeHtml(timeLabel)}</small></div><span>${escapeHtml(importanceLabel)}</span></article>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.calendarEmpty)}</div>`;

  document.getElementById("crisisGroups").innerHTML = (snapshot.groups || []).map(group => {
    const band = safeCrisisState(group.band, ["normal", "warning", "danger", "critical"], "normal");
    const name = crisisNames[crisisLocale]?.[group.code] || group.code;
    const score = crisisDetailed ? `<small>${copy.score}: ${crisisNumber(Number(group.stress_score) * 100, 0)}/100</small>` : "";
    return `<article class="crisis-group band-${band}"><span>${escapeHtml(crisisBandLabels[crisisLocale][band])}</span><strong>${escapeHtml(name)}</strong><small>${Number(group.worsening_count || 0)} / ${Number(group.indicator_count || 0)}</small>${score}</article>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.noData)}</div>`;

  document.getElementById("crisisScenarios").innerHTML = (snapshot.scenarios || []).map(item => {
    const status = safeCrisisState(item.status, ["inactive", "watch", "elevated", "confirmed"], "inactive");
    const confidence = safeCrisisState(item.confidence, ["low", "medium", "high"], "low");
    const name = item.name || crisisNames[crisisLocale]?.[item.code] || item.code;
    const explanation = localizeCrisisExplanation(item.explanation || copy.noData);
    return `<article class="crisis-scenario scenario-${status}"><div class="crisis-scenario-head"><span>${escapeHtml(crisisScenarioLabels[crisisLocale][status])}</span><small>${escapeHtml(copy.horizon)} · ${escapeHtml(localizeCrisisHorizon(item.horizon))}</small></div><strong>${escapeHtml(name)}</strong><p>${escapeHtml(explanation)}</p><div class="crisis-scenario-meta"><span>${escapeHtml(copy.evidence)}: ${Number(item.active_group_count || 0)}</span><span>${escapeHtml(copy.confidence)}: ${escapeHtml(crisisConfidenceLabels[crisisLocale][confidence])}</span></div></article>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.noData)}</div>`;

  const opportunityPayload = crisisOpportunities || { ideas: [], available_asset_classes: [] };
  renderCrisisToday(snapshot, opportunityPayload, copy);
  const opportunityClasses = opportunityPayload.available_asset_classes || [];
  const coverage = document.getElementById("crisisOpportunityCoverage");
  const optionHealthy = opportunityPayload.market_data_status?.bybit_options?.status === "healthy";
  coverage.textContent = `${opportunityClasses.length} ${copy.opportunityCoverage} · ${optionHealthy ? copy.optionHealthy : copy.optionDegraded}`;
  coverage.className = `live-status ${opportunityClasses.length ? "is-live" : "is-offline"}`;
  document.getElementById("crisisOpportunities").innerHTML = (opportunityPayload.ideas || []).map(item => {
    const side = safeCrisisState(item.side, ["long", "short", "hedge", "wait"], "wait");
    const expectedMin = Number(item.expected_range_pct?.minimum);
    const expectedMax = Number(item.expected_range_pct?.maximum);
    const lossMin = Number(item.loss_range_pct?.minimum);
    const lossMax = Number(item.loss_range_pct?.maximum);
    const hasCalculator = ["long", "short"].includes(side)
      && Number.isFinite(Number(item.reference_price)) && Number(item.reference_price) > 0
      && Number.isFinite(expectedMin) && Number.isFinite(lossMin);
    const calculator = hasCalculator
      ? `<button class="mini-action" type="button" data-action="opportunity-calculator" data-symbol="${escapeHtml(item.symbol || "")}" data-side="${side}" data-price="${escapeHtml(item.reference_price)}" data-expected="${expectedMin}" data-loss="${Math.abs(lossMin)}">${escapeHtml(copy.opportunityCalculator)}</button>`
      : "";
    const evidence = (item.evidence || []).slice(0, 3).map(value => `<li>${escapeHtml(value)}</li>`).join("");
    return `<article class="crisis-opportunity side-${side}"><div class="crisis-opportunity-head"><span>${escapeHtml(side === "wait" ? copy.opportunityWait : side.toUpperCase())}</span><small>#${Number(item.rank || 0)} · ${escapeHtml(copy.opportunityScore)} ${crisisNumber(Number(item.score) * 100, 0)}/100</small></div><strong>${escapeHtml(item.symbol || "MARKET")}</strong><p>${escapeHtml(item.rationale || "")}</p><div class="crisis-opportunity-ranges"><span>${escapeHtml(copy.opportunityRange)}: ${crisisNumber(expectedMin)}%…${crisisNumber(expectedMax)}%</span><span>${escapeHtml(copy.opportunityLoss)}: ${crisisNumber(lossMin)}%…${crisisNumber(lossMax)}%</span></div><details><summary>${escapeHtml(copy.opportunityTrigger)}</summary><p>${escapeHtml(item.trigger || "—")}</p><b>${escapeHtml(copy.opportunityInvalidation)}</b><p>${escapeHtml(item.invalidation || "—")}</p>${evidence ? `<ul>${evidence}</ul>` : ""}</details>${calculator}</article>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.noData)}</div>`;

  const worldPayload = crisisWorld || { regions: [] };
  document.getElementById("crisisWorld").innerHTML = (worldPayload.regions || []).map(region => {
    const activeGroups = (region.groups || []).filter(group => ["warning", "danger", "critical"].includes(group.band));
    const groupNames = activeGroups.slice(0, 3).map(group => crisisNames[crisisLocale]?.[group.code] || group.name || group.code);
    const freshness = region.freshness || {};
    return `<article class="crisis-world-card ${region.ready ? "is-ready" : "is-missing"}"><div><strong>${escapeHtml(region.name || region.code || "—")}</strong><span>${Number(region.available_count || 0)}/${Number(region.indicator_count || 0)} ${escapeHtml(copy.available)}</span></div><div class="crisis-world-meter"><span class="freshness-fresh">${Number(freshness.fresh || 0)} ${escapeHtml(copy.fresh)}</span><span class="freshness-stale">${Number(freshness.stale || 0)} ${escapeHtml(copy.stale)}</span><span class="freshness-missing">${Number(freshness.missing || 0)} ${escapeHtml(copy.missing)}</span></div><small>${escapeHtml(groupNames.join(" · ") || copy.noData)}</small></article>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.noData)}</div>`;

  const severityLabels = {
    context: copy.contextSeverity,
    watch: copy.watchSeverity,
    urgent: copy.urgentSeverity,
  };
  document.getElementById("crisisNews").innerHTML = (snapshot.news?.items || []).map(item => {
    const severity = (item.scenarios || []).some(entry => entry.severity === "urgent")
      ? "urgent"
      : (item.scenarios || []).some(entry => entry.severity === "watch") ? "watch" : "context";
    const published = item.published_at ? new Date(item.published_at) : null;
    const publishedLabel = published && !Number.isNaN(published.getTime())
      ? published.toLocaleString(crisisLocale === "en" ? "en-US" : "ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
      : "—";
    const sourceUrl = safeExternalUrl(item.url);
    const title = escapeHtml(item.title || "—");
    const linkedTitle = sourceUrl === "#" ? title : `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${title}</a>`;
    const scenarios = (item.scenarios || []).map(entry => `<span>${escapeHtml(entry.name || entry.code || "—")}</span>`).join("");
    const explanation = [...new Set(
      (item.scenarios || []).map(entry => entry.explanation).filter(Boolean),
    )].join(" ");
    return `<article class="crisis-news-item severity-${severity}"><div class="crisis-news-head"><span>${escapeHtml(severityLabels[severity])}</span><time>${escapeHtml(publishedLabel)}</time></div><strong>${linkedTitle}</strong><small>${escapeHtml(item.source?.name || item.source?.code || "—")}</small>${explanation ? `<p>${escapeHtml(explanation)}</p>` : ""}<div class="crisis-news-scenarios">${scenarios}</div></article>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.newsEmpty)}</div>`;

  document.getElementById("crisisIndicators").innerHTML = (snapshot.indicators || []).map(item => {
    const code = safeCrisisCode(item.code);
    if (!code) return "";
    const band = safeCrisisState(item.band, ["normal", "warning", "danger", "critical"], "normal");
    const freshness = safeCrisisState(item.freshness, ["fresh", "delayed", "stale", "missing"], "missing");
    const name = crisisNames[crisisLocale]?.[item.code] || item.name || item.code;
    const distance = item.distance_to_next_text == null ? "—" : crisisNumber(item.distance_to_next_text);
    const sourceUrl = safeExternalUrl(item.source_url);
    const source = sourceUrl === "#"
      ? escapeHtml(item.source_name || item.source_code || "—")
      : `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.source_name || item.source_code)}</a>`;
    const rawBand = safeCrisisState(item.raw_band, ["normal", "warning", "danger", "critical"], band);
    const persistence = Math.max(0, Number(item.persistence_count || 0));
    const required = Math.max(1, Number(item.confirmation_required || 1));
    const confirmationContext = rawBand === "normal"
      ? ""
      : `<span>${escapeHtml(copy.confirmations)}: ${Math.min(persistence, required)}/${required}</span>`;
    const stabilityNote = item.held_by_hysteresis
      ? copy.hysteresisHold
      : rawBand !== band ? copy.pendingConfirmation : "";
    const technical = crisisDetailed
      ? `<div class="crisis-tech-row"><span>${copy.score}: ${crisisNumber(Number(item.stress_score_text) * 100, 0)}/100</span><span>quality: ${crisisNumber(Number(item.quality_score_text) * 100, 0)}/100</span><span>${escapeHtml(copy.rawSignal)}: ${escapeHtml(crisisBandLabels[crisisLocale][rawBand])}</span>${confirmationContext}${stabilityNote ? `<span class="crisis-stability-note">${escapeHtml(stabilityNote)}</span>` : ""}<span>${escapeHtml(item.released_at || "—")}</span></div>` : "";
    const expanded = expandedCrisisHistory.has(code);
    const chart = expanded
      ? `<div class="crisis-history"><div class="crisis-chart-legend"><span class="legend-current">${escapeHtml(copy.current)}</span><span class="legend-events">${escapeHtml(copy.eventZones)}</span><span class="legend-warning">warning</span><span class="legend-danger">danger</span><span class="legend-critical">critical</span></div><svg id="crisis-chart-${code}" class="crisis-history-chart" viewBox="0 0 720 260" role="img" aria-label="${escapeHtml(name)}"></svg><small id="crisis-chart-status-${code}">${escapeHtml(copy.chartLoading)}</small></div>` : "";
    return `<article id="crisis-indicator-${code}" class="crisis-indicator band-${band}"><div class="crisis-indicator-head"><div><span class="crisis-band-label">${escapeHtml(crisisBandLabels[crisisLocale][band])}</span><strong>${escapeHtml(name)}</strong></div><em class="freshness-${freshness}">${escapeHtml(crisisFreshnessLabels[crisisLocale][freshness])}</em></div><div class="crisis-value"><b>${crisisNumber(item.value_text, 4)}</b><small>${escapeHtml(item.unit || "")}</small></div><div class="crisis-thresholds"><span>${copy.threshold}: ${crisisNumber(item.thresholds?.warning)} / ${crisisNumber(item.thresholds?.danger)} / ${crisisNumber(item.thresholds?.critical)}</span><span>${copy.next}: ${distance}</span><span>${copy.source}: ${source}</span></div>${technical}<button class="crisis-history-toggle" type="button" data-action="toggle-crisis-history" data-indicator-code="${code}">${escapeHtml(expanded ? copy.hideHistory : copy.history)}</button>${chart}</article>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.noData)}</div>`;

  expandedCrisisHistory.forEach(code => { void loadCrisisHistory(code); });

  const method = snapshot.methodology || {};
  document.getElementById("crisisMethodology").textContent = `${copy.method}: ${method.code || "—"} / ${method.version || "—"}`;
  const healthSources = crisisSourceHealth?.sources || snapshot.sources || [];
  document.getElementById("crisisSources").innerHTML = healthSources.map(source => {
    const status = safeCrisisState(source.status, ["healthy", "degraded", "failed", "stale", "running", "never_synced", "disabled", "succeeded", "partial"], "unknown");
    const observed = Number(source.coverage?.observed_count || 0);
    const total = Number(source.coverage?.indicator_count || 0);
    const last = source.latest_run?.finished_at || source.finished_at || source.latest_run?.started_at || "—";
    return `<div class="crisis-source"><strong>${escapeHtml(source.name || source.code || "—")}</strong><span class="source-${status}">${escapeHtml(source.status_label || status)}</span><small>${observed}/${total} · ${escapeHtml(last)}</small></div>`;
  }).join("") || `<div class="crisis-empty">${escapeHtml(copy.noData)}</div>`;
  renderCrisisAgent();
}

function transferOpportunityToCalculator(target) {
  const side = target.dataset.side === "short" ? "short" : "long";
  const price = Number(target.dataset.price);
  const expected = Math.abs(Number(target.dataset.expected));
  const loss = Math.abs(Number(target.dataset.loss));
  if (![price, expected, loss].every(Number.isFinite) || price <= 0 || expected <= 0 || loss <= 0) return;
  const form = document.getElementById("riskForm");
  form.querySelector('input[name="symbol"]').value = cleanSymbol(target.dataset.symbol).replace("USDT", "");
  form.querySelector('select[name="side"]').value = side;
  form.querySelector('input[name="entry"]').value = price.toFixed(8);
  form.querySelector('input[name="stop"]').value = (price * (side === "long" ? 1 - loss / 100 : 1 + loss / 100)).toFixed(8);
  form.querySelector('input[name="target"]').value = (price * (side === "long" ? 1 + expected / 100 : 1 - expected / 100)).toFixed(8);
  switchView("calculator");
  void calculateRisk();
}

async function loadCrisisAgent() {
  try {
    crisisAgentState = await api("/api/crisis-radar/agent/status");
    if (crisisAgentState.enabled && crisisAgentState.available && crisisAgentState.model_installed
      && crisisAgentThreadId == null && crisisAgentMessages.length === 0) {
      const threads = await api("/api/crisis-radar/agent/threads");
      const latest = threads.items?.[0];
      if (latest) {
        const thread = await api(`/api/crisis-radar/agent/threads/${Number(latest.id)}`);
        crisisAgentThreadId = Number(thread.thread_id);
        crisisAgentMessages = Array.isArray(thread.messages) ? thread.messages : [];
      }
    }
  } catch {
    crisisAgentState = { enabled: true, available: false, model_installed: false, model_loaded: false };
  }
}

function crisisAgentIsReady() {
  return Boolean(
    crisisAgentState?.enabled
    && crisisAgentState?.available
    && crisisAgentState?.model_installed
    && !Number(crisisAgentState?.cooldown_remaining_seconds || 0)
  );
}

function renderCrisisAgent() {
  const copy = crisisCopy[crisisLocale];
  const status = document.getElementById("crisisAgentStatus");
  const ready = crisisAgentIsReady();
  const cooldown = Number(crisisAgentState?.cooldown_remaining_seconds || 0);
  const modelName = String(crisisAgentState?.model || copy.agentReady);
  status.textContent = !crisisAgentState?.enabled
    ? copy.agentDisabled : !crisisAgentState?.available
      ? copy.agentOffline : !crisisAgentState?.model_installed ? copy.agentMissing
        : cooldown ? `${copy.agentCooldown} · ${cooldown}s`
          : `${modelName} · ${crisisAgentState?.model_loaded ? copy.agentReadyWarm : copy.agentReadyCold}`;
  status.className = `live-status ${ready ? "is-live" : "is-offline"}`;
  document.getElementById("crisisAgentSend").disabled = !ready || crisisAgentBusy;
  const container = document.getElementById("crisisAgentMessages");
  container.replaceChildren();
  if (!crisisAgentMessages.length) {
    const empty = document.createElement("div");
    empty.className = "crisis-empty";
    empty.textContent = copy.agentEmpty;
    container.append(empty);
    return;
  }
  crisisAgentMessages.forEach(message => {
    const article = document.createElement("article");
    article.className = `crisis-agent-message role-${message.role === "user" ? "user" : "assistant"}`;
    if (message.role === "assistant") {
      const grounding = document.createElement("small");
      grounding.className = `crisis-agent-grounding ${message.grounded ? "is-grounded" : "is-ungrounded"}`;
      grounding.textContent = message.grounded ? copy.agentGrounded : copy.agentUngrounded;
      article.append(grounding);
    }
    const content = document.createElement("p");
    content.textContent = String(message.content || "");
    article.append(content);
    const evidence = Array.isArray(message.evidence) ? message.evidence : [];
    if (evidence.length) {
      const block = document.createElement("div");
      block.className = "crisis-agent-evidence";
      const label = document.createElement("small");
      label.textContent = `${copy.agentEvidence}:`;
      block.append(label);
      evidence.forEach(item => {
        const url = safeExternalUrl(item.url);
        const node = document.createElement(url === "#" ? "span" : "a");
        node.textContent = String(item.label || item.code || "—");
        if (url !== "#") {
          node.href = url;
          node.target = "_blank";
          node.rel = "noopener noreferrer";
        }
        block.append(node);
      });
      article.append(block);
    }
    const limitations = Array.isArray(message.limitations) ? message.limitations : [];
    if (limitations.length) {
      const note = document.createElement("small");
      note.className = "crisis-agent-limitations";
      note.textContent = `${copy.agentLimitations}: ${limitations.join(" ")}`;
      article.append(note);
    }
    container.append(article);
  });
  container.scrollTop = container.scrollHeight;
}

async function askCrisisAgent(event) {
  event.preventDefault();
  const copy = crisisCopy[crisisLocale];
  const input = document.getElementById("crisisAgentQuestion");
  const question = input.value.trim();
  if (!question) return;
  const button = document.getElementById("crisisAgentSend");
  crisisAgentBusy = true;
  button.disabled = true;
  button.textContent = copy.agentSending;
  crisisAgentMessages.push({ role: "user", content: question, evidence: [], limitations: [] });
  input.value = "";
  renderCrisisAgent();
  try {
    const result = await api("/api/crisis-radar/agent/chat", jsonRequest("POST", {
      question,
      locale: crisisLocale,
      mode: document.getElementById("crisisAgentMode").value === "deep" ? "deep" : "fast",
      thread_id: crisisAgentThreadId,
    }));
    crisisAgentThreadId = Number(result.thread_id);
    if (result.runtime) crisisAgentState = { ...crisisAgentState, ...result.runtime };
    const assistant = [...(result.messages || [])].reverse().find(item => item.role === "assistant");
    if (assistant) crisisAgentMessages.push(assistant);
  } catch {
    crisisAgentMessages.push({ role: "assistant", content: copy.agentError, evidence: [], limitations: [] });
  } finally {
    crisisAgentBusy = false;
    button.textContent = copy.agentSend;
    button.disabled = !crisisAgentIsReady();
    renderCrisisAgent();
  }
}

async function toggleCrisisHistory(rawCode) {
  const code = safeCrisisCode(rawCode);
  if (!code) return;
  if (expandedCrisisHistory.has(code)) expandedCrisisHistory.delete(code);
  else expandedCrisisHistory.add(code);
  renderCrisisRadar();
}

async function loadCrisisHistory(code) {
  if (!expandedCrisisHistory.has(code)) return;
  const status = document.getElementById(`crisis-chart-status-${code}`);
  try {
    if (!crisisHistoryCache.has(code)) {
      crisisHistoryCache.set(code, await api(`/api/crisis-radar/indicators/${encodeURIComponent(code)}/history?limit=500`));
    }
    drawCrisisHistory(code, crisisHistoryCache.get(code));
  } catch {
    if (status) status.textContent = crisisCopy[crisisLocale].unavailable;
  }
}

function drawCrisisHistory(code, payload) {
  const svg = document.getElementById(`crisis-chart-${code}`);
  const status = document.getElementById(`crisis-chart-status-${code}`);
  if (!svg) return;
  const points = (payload?.points || []).map(point => ({
    date: new Date(point.observed_at),
    value: Number(point.value_text),
  })).filter(point => !Number.isNaN(point.date.getTime()) && Number.isFinite(point.value));
  if (points.length < 2) {
    if (status) status.textContent = crisisCopy[crisisLocale].chartEmpty;
    return;
  }
  const thresholds = ["warning", "danger", "critical"].map(key => ({ key, value: Number(payload.thresholds?.[key]) })).filter(item => Number.isFinite(item.value));
  const values = [...points.map(point => point.value), ...thresholds.map(item => item.value)];
  let min = Math.min(...values);
  let max = Math.max(...values);
  const spread = Math.max(max - min, Math.max(Math.abs(max), 1) * 0.1);
  min -= spread * 0.12;
  max += spread * 0.12;
  const width = 720;
  const height = 260;
  const left = 62;
  const right = 18;
  const top = 18;
  const bottom = 42;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const y = value => top + ((max - value) / (max - min)) * plotHeight;
  const firstTime = points[0].date.getTime();
  const lastTime = points[points.length - 1].date.getTime();
  const xTime = value => left + ((Math.min(Math.max(value, firstTime), lastTime) - firstTime) / Math.max(lastTime - firstTime, 1)) * plotWidth;
  const x = index => xTime(points[index].date.getTime());
  const namespace = "http://www.w3.org/2000/svg";
  const add = (tag, attributes, text = "") => {
    const node = document.createElementNS(namespace, tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text) node.textContent = text;
    svg.appendChild(node);
    return node;
  };
  svg.replaceChildren();
  add("rect", { x: left, y: top, width: plotWidth, height: plotHeight, fill: "rgba(3,5,10,.42)" });
  const thresholdColors = { warning: "#f5c451", danger: "#ff8b4d", critical: "#ff5370" };
  const thresholdMap = Object.fromEntries(thresholds.map(item => [item.key, item.value]));
  if ([thresholdMap.warning, thresholdMap.danger, thresholdMap.critical].every(Number.isFinite)) {
    if (payload.risk_direction === "lower_is_worse") {
      add("rect", { x: left, y: y(thresholdMap.critical), width: plotWidth, height: top + plotHeight - y(thresholdMap.critical), fill: "rgba(255,83,112,.09)" });
      add("rect", { x: left, y: y(thresholdMap.danger), width: plotWidth, height: y(thresholdMap.critical) - y(thresholdMap.danger), fill: "rgba(255,139,77,.07)" });
      add("rect", { x: left, y: y(thresholdMap.warning), width: plotWidth, height: y(thresholdMap.danger) - y(thresholdMap.warning), fill: "rgba(245,196,81,.06)" });
    } else {
      add("rect", { x: left, y: top, width: plotWidth, height: y(thresholdMap.critical) - top, fill: "rgba(255,83,112,.09)" });
      add("rect", { x: left, y: y(thresholdMap.critical), width: plotWidth, height: y(thresholdMap.danger) - y(thresholdMap.critical), fill: "rgba(255,139,77,.07)" });
      add("rect", { x: left, y: y(thresholdMap.danger), width: plotWidth, height: y(thresholdMap.warning) - y(thresholdMap.danger), fill: "rgba(245,196,81,.06)" });
    }
  }
  (payload.event_windows || []).forEach(window => {
    const started = new Date(window.started_at).getTime();
    const ended = new Date(window.ended_at || window.started_at).getTime();
    if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < firstTime || started > lastTime) return;
    const startX = xTime(started);
    const endX = xTime(Math.max(ended, started));
    add("rect", {
      x: startX,
      y: top,
      width: Math.max(endX - startX, 3),
      height: plotHeight,
      fill: window.label_status === "derived" ? "rgba(169,116,255,.12)" : "rgba(109,229,255,.10)",
      class: "crisis-event-window",
    });
  });
  thresholds.forEach(item => {
    add("line", { x1: left, y1: y(item.value), x2: width - right, y2: y(item.value), stroke: thresholdColors[item.key], "stroke-width": 1, "stroke-dasharray": "6 5" });
    add("text", { x: left + 6, y: y(item.value) - 4, fill: thresholdColors[item.key], "font-size": 11 }, `${item.key} ${crisisNumber(item.value, 2)}`);
  });
  add("polyline", { points: points.map((point, index) => `${x(index)},${y(point.value)}`).join(" "), fill: "none", stroke: "#6de5ff", "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" });
  add("text", { x: 8, y: top + 4, fill: "#9ca9c8", "font-size": 11 }, crisisNumber(max, 2));
  add("text", { x: 8, y: top + plotHeight, fill: "#9ca9c8", "font-size": 11 }, crisisNumber(min, 2));
  const dateLocale = crisisLocale === "en" ? "en-US" : "ru-RU";
  add("text", { x: left, y: height - 14, fill: "#9ca9c8", "font-size": 11 }, points[0].date.toLocaleDateString(dateLocale));
  const lastLabel = points[points.length - 1].date.toLocaleDateString(dateLocale);
  add("text", { x: width - right, y: height - 14, fill: "#9ca9c8", "font-size": 11, "text-anchor": "end" }, lastLabel);
  if (status) status.textContent = `${points.length} · ${payload.unit || ""}`;
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
  document.getElementById("planText").textContent = data.plan
    ? `${data.plan.allowed_symbols || uiText("без монет", "no markets")} | ${uiText("риск", "risk")} ${data.plan.max_daily_risk_percent}% | ${uiText("стоп", "stop")} ${fmt(data.plan.max_daily_loss)} USDT`
    : uiText("План дня не задан", "Daily plan not set");
  document.querySelector(".eyebrow").textContent = `${uiText("Кабинет трейдера", "Trader workspace")} · ${data.session?.name || uiText("без активной сессии", "no active session")}`;
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
    alert(uiText("Не удалось добавить монету", "Could not add the market"));
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
    alert(uiText("Не удалось убрать монету", "Could not remove the market"));
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
    status.textContent = `live ${now.toLocaleTimeString(appLocale === "en" ? "en-US" : "ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    status.className = "live-status is-live";
    try {
      renderPrices(items);
      refreshTradeMetrics(currentOpenTrades);
    } catch (renderError) {
      console.error("Price render failed", renderError);
      status.textContent = uiText("live, ошибка UI", "live, UI error");
      status.className = "live-status is-offline";
    }
  } catch (error) {
    console.error("Price request failed", error);
    status.textContent = uiText("нет связи", "offline");
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
        <span class="${changeClass}">${signed(item.price_change_percent)}% ${uiText("за 24ч", "in 24h")}</span>
        <small>${uiText("диапазон", "range")} ${fmt(item.intraday_range_percent)}% · high ${fmt(item.high_price)} · low ${fmt(item.low_price)}</small>
        <canvas id="price-chart-${symbol}" class="mini-trend-chart" width="360" height="118"></canvas>
        <em id="price-trend-${symbol}" class="trend-caption">${uiText("загрузка", "loading")} ${chartIntervalLabel()}</em>
      </button>
    `;
  }).join("") || `<div class='price-card empty-price'>${uiText("Добавь монеты в список", "Add markets to the watchlist")}</div>`;
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
      const direction = item.price_change_percent > 1
        ? uiText("бычий импульс", "bullish momentum")
        : item.price_change_percent < -1 ? uiText("медвежий импульс", "bearish momentum") : uiText("нейтрально", "neutral");
      const marketSymbol = cleanSymbol(item.symbol);
      const scannerScore = Math.min(99, Math.round(44 + Math.min(item.intraday_range_percent, 15) * 2.2 + Math.min(Math.log10(Math.max(item.quote_volume / 1000000, 1)), 4) * 4));
      return `<article class="market-card ${expandedMarkets.has(marketSymbol) ? "expanded" : ""}" data-action="toggle-market" data-symbol="${marketSymbol}">
        <div class="market-title"><span class="market-rank">${index + 1}</span><strong>${marketSymbol.replace("USDT", "")}<small>/USDT</small></strong></div>
        <span class="scanner-score">${scannerScore}<small>/100</small></span>
        <b>${fmt(item.price, item.price > 10 ? 2 : 6)}</b>
        <small class="market-change ${item.price_change_percent >= 0 ? "positive" : "negative"}">${item.price_change_percent >= 0 ? "↑" : "↓"} ${signed(item.price_change_percent)}% · 24h</small>
        <span class="direction-pill ${item.price_change_percent >= 0 ? "positive" : "negative"}">${direction}</span>
        <small>VOL ${fmt(item.quote_volume / 1000000, 0)}M · RANGE ${fmt(item.intraday_range_percent)}%</small>
        <progress class="range-meter market-range" max="100" value="${Math.max(0, Math.min(100, position))}"></progress>
        <small class="range-labels"><i>${fmt(item.low_price, 4)}</i><i>${escapeHtml(item.exchange)}</i><i>${fmt(item.high_price, 4)}</i></small>
        <div class="market-detail">
          <small>${uiText("Цена на", "Price at")} ${fmt(position, 0)}% ${uiText("суточного диапазона", "of the daily range")}</small>
          <div class="market-timeframe-switch" data-action="stop-propagation" aria-label="${uiText("Таймфрейм графика монеты", "Market chart timeframe")}">
            ${chartIntervals.map(tf => `<button type="button" data-action="set-market-timeframe" data-symbol="${marketSymbol}" data-timeframe="${tf}" class="${marketChartInterval(marketSymbol) === tf ? "active" : ""}">${tf.toUpperCase()}</button>`).join("")}
          </div>
          <canvas id="market-chart-${cleanSymbol(item.symbol)}" class="mini-trend-chart" width="360" height="118"></canvas>
          <em id="market-trend-${cleanSymbol(item.symbol)}" class="trend-caption">${uiText("Нажми для графика", "Open to load chart")}</em>
          <button class="mini-action" data-action="analyze-market" data-symbol="${cleanSymbol(item.symbol)}">${uiText("Разобрать вход", "Analyze entry")}</button>
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
  if (row?.entry_price) drawLevel(ctx, w, safeY(row.entry_price), `${uiText("ВХОД", "ENTRY")} ${fmt(row.entry_price, 6)}`, "#43d7ff");
  if (row?.stop_price) drawLevel(ctx, w, safeY(row.stop_price), `${uiText("СТОП", "STOP")} ${fmt(row.stop_price, 6)}`, "#ff657d");
  if (row?.target_price) drawLevel(ctx, w, safeY(row.target_price), `${uiText("ТЕЙК", "TARGET")} ${fmt(row.target_price, 6)}`, "#55e08a");
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
    return { label: uiText("нет данных", "no data"), change: 0, className: "warn" };
  }

  const first = prices[0];
  const last = prices[prices.length - 1];
  const change = ((last - first) / Math.max(first, 0.000001)) * 100;
  const max = Math.max(...prices);
  const min = Math.min(...prices);
  const color = change > 0.08 ? "#55e08a" : change < -0.08 ? "#ff657d" : "#ffc45c";
  const label = change > 0.08
    ? uiText("тренд вверх", "uptrend")
    : change < -0.08 ? uiText("тренд вниз", "downtrend") : uiText("флет", "range");
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
  const labels = appLocale === "en"
    ? { "1d": "1 day", "4h": "4 hours", "1h": "1 hour", "15m": "15 min", "5m": "5 min", "1m": "1 min" }
    : { "1d": "1 день", "4h": "4 часа", "1h": "1 час", "15m": "15 мин", "5m": "5 мин", "1m": "1 мин" };
  return labels[interval] || interval;
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
        <em id="journal-trend-${entryId}" class="trend-caption">${uiText("загрузка", "loading")} ${chartIntervalLabel()}</em>
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
  if (!response.ok) return alert(uiText("Не удалось создать сессию", "Could not create the session"));
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
          <h2>${uiText("Экспорт в Obsidian", "Obsidian Export")}</h2>
          <p class="section-copy">${uiText("Скачай готовый vault: Dashboard, Sessions, Trades, Journal, Coins и Canvas-карта связей.", "Download a ready vault with Dashboard, Sessions, Trades, Journal, Coins, and a Canvas relationship map.")}</p>
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
    alert(uiText("Не удалось скачать Obsidian export", "Could not download the Obsidian export"));
  }
}

function showModelPlaceholder() {
  const status = document.getElementById("modelSetupStatus");
  if (status) {
    status.textContent = uiText("настройка через сервер", "server configuration");
    status.className = "live-status is-offline";
  }
  alert(uiText(
    "Облачный провайдер настраивается переменными окружения backend. Ключ не принимается и не хранится в Mini App.",
    "Configure the cloud provider through backend environment variables. The Mini App never accepts or stores the key."
  ));
}

async function loadModelStatus() {
  const hero = document.getElementById("modelSetupStatus");
  const local = document.getElementById("localModelStatus");
  if (hero) {
    hero.textContent = "проверка";
    hero.className = "live-status";
  }
  try {
    const state = await api("/api/crisis-radar/agent/status");
    const providerLabel = String(state.provider || "unknown").replaceAll("-", " ");
    const providerNode = document.getElementById("crisisModelProvider");
    const modelNode = document.getElementById("crisisModelName");
    const connectedProvider = document.getElementById("connectedModelProvider");
    const connectedDetails = document.getElementById("connectedModelDetails");
    if (providerNode) providerNode.textContent = providerLabel;
    if (modelNode) modelNode.textContent = state.model || "—";
    if (connectedProvider) connectedProvider.textContent = providerLabel;
    if (connectedDetails) connectedDetails.textContent = `${state.model || "—"} · ${state.read_only ? uiText("только чтение", "read only") : "—"}`;
    const bindingNodes = {
      vision_trade_extraction: "bindingVision",
      journal_summary: "bindingJournal",
      obsidian_report: "bindingObsidian",
      trade_review: "bindingTradeReview",
      crisis_analysis: "bindingCrisis",
    };
    Object.entries(bindingNodes).forEach(([task, id]) => {
      const node = document.getElementById(id);
      const binding = state.task_bindings?.[task];
      if (!node) return;
      node.textContent = binding
        ? `${binding.provider} · ${binding.model} · ${binding.status}`
        : uiText("не назначено", "not assigned");
    });
    const ready = Boolean(state.enabled && state.available && state.model_installed && !state.cooldown_remaining_seconds);
    const loaded = Boolean(state.model_loaded);
    const label = ready ? `${state.model} ${loaded ? uiText("в памяти", "in memory") : uiText("готова · холодный запуск", "ready · cold start")}` : !state.enabled
      ? uiText("аналитик выключен", "analyst disabled") : !state.available ? uiText("провайдер недоступен", "provider unavailable") : uiText("модель не найдена", "model not found");
    [hero, local].filter(Boolean).forEach(node => {
      node.textContent = label;
      node.className = `live-status ${ready ? "is-live" : "is-offline"}`;
    });
  } catch {
    [hero, local].filter(Boolean).forEach(node => {
      node.textContent = uiText("провайдер недоступен", "provider unavailable");
      node.className = "live-status is-offline";
    });
  }
}

async function calculateRisk() {
  const form = new FormData(document.getElementById("riskForm"));
  const query = new URLSearchParams(form);
  try {
    const data = await api(`/api/risk?${query.toString()}`);
    const r = data.result;
    const stopDistance = (r.stop_price - r.entry_price) / r.entry_price * 100;
    const targetDistance = r.target_price ? (r.target_price - r.entry_price) / r.entry_price * 100 : 0;
    const fundingLabel = r.funding_payment > 0 ? uiText("расход", "expense") : r.funding_payment < 0 ? uiText("получение", "credit") : uiText("нет", "none");
    const liquidation = r.liquidation_price == null
      ? uiText("не рассчитывается для Cross без данных аккаунта", "not calculated for Cross without account data")
      : `${fmt(r.liquidation_price, 6)} (${fmt(r.liquidation_distance_percent)}% ${uiText("от входа", "from entry")})`;
    document.getElementById("riskResult").textContent =
      `${uiText("Количество", "Quantity")}: ${fmt(r.quantity, 6)} ${cleanSymbol(r.symbol).replace("USDT", "")}\n` +
      `${uiText("Размер позиции", "Position size")}: ${fmt(r.notional)} USDT\n${uiText("Маржа", "Margin")}: ${fmt(r.margin)} USDT\n` +
      `${uiText("Минимальное плечо по депозиту", "Minimum leverage for balance")}: ${fmt(Math.max(1, r.minimum_leverage), 2)}x\n` +
      `${uiText("Маржи хватает", "Margin sufficient")}: ${r.margin_sufficient ? uiText("да", "yes") : uiText("нет — увеличь плечо или уменьши риск", "no — increase leverage or reduce risk")}\n\n` +
      `${uiText("Лимит риска", "Risk limit")}: ${fmt(r.risk_amount)} USDT\n${uiText("Чистый убыток по стопу", "Net loss at stop")}: ${fmt(r.net_loss_at_stop)} USDT\n` +
      `  ${uiText("движение цены", "price move")}: ${fmt(r.gross_loss_at_stop)}\n  ${uiText("комиссии", "fees")}: ${fmt(r.entry_fee + r.stop_exit_fee)}\n` +
      `  ${uiText("проскальзывание", "slippage")}: ${fmt(r.stop_slippage)}\n  funding: ${fmt(r.funding_payment)} (${fundingLabel})\n` +
      `${uiText("Чистая прибыль по тейку", "Net profit at target")}: ${r.net_profit_at_target == null ? "-" : fmt(r.net_profit_at_target)} USDT\n` +
      `${uiText("R/R после издержек", "R/R after costs")}: ${r.reward_to_risk == null ? "-" : fmt(r.reward_to_risk)}\n` +
      `${uiText("До стопа", "To stop")}: ${fmt(stopDistance)}% · ${uiText("до тейка", "to target")}: ${fmt(targetDistance)}%\n` +
      `${uiText("Оценка ликвидации", "Estimated liquidation")}: ${liquidation}`;
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
  if (!data.ok) alert(uiText("Не удалось закрыть сделку", "Could not close the trade"));
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
  if (!result.ok) return alert(uiText("Не удалось изменить сделку", "Could not update the trade"));

  const files = [...(document.getElementById(`edit-photo-${id}`)?.files || [])];
  for (const file of files) {
    const upload = await apiFetch(`/api/trades/${id}/attachment?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    if (!upload.ok) alert(uiText(`Не загрузилось фото: ${file.name}`, `Could not upload photo: ${file.name}`));
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
  if (!confirm(uiText("Отменить открытую сделку?", "Cancel the open trade?"))) return;
  const response = await apiFetch(`/api/trades/${id}/cancel`, { method: "POST" });
  const data = await response.json();
  if (!data.ok) alert(uiText("Не удалось отменить сделку", "Could not cancel the trade"));
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
