(() => {
  const STORAGE_KEY = "trading-assistant-locale";
  const translations = new Map(Object.entries({
    "Кабинет трейдера": "Trader workspace",
    "План не загружен": "Plan not loaded",
    "План дня не задан": "Daily plan not set",
    "Обновить": "Refresh",
    "Обновить данные": "Refresh data",
    "Язык интерфейса": "Interface language",
    "Разделы": "Sections",
    "Пульт": "Dashboard",
    "Кризис-радар": "Crisis Radar",
    "Журнал": "Journal",
    "Разделы торгового журнала": "Trading journal sections",
    "Модели и инструменты": "Models and tools",
    "Открыть модели и инструменты": "Open models and tools",
    "Сделки": "Trades",
    "Сессии": "Sessions",
    "Монеты": "Markets",
    "Дневник": "Journal",
    "Аналитика": "Analytics",
    "Модели": "Models",
    "Калькулятор": "Calculator",
    "Баланс сессии": "Session balance",
    "Нет активной сессии": "No active session",
    "Риск в рынке": "Open risk",
    "Алерты": "Alerts",
    "Живые цены": "Live prices",
    "Таймфрейм графиков": "Chart timeframe",
    "ожидание": "waiting",
    "Избранные монеты": "Watchlist",
    "Добавить монету": "Add market",
    "Добавить": "Add",
    "SOL, солана или солик": "SOL, solana, or sol",
    "SOL: разгон 100 → 150": "SOL: 100 → 150 run",
    "Открытые сделки": "Open trades",
    "Открыть сделку": "Open trade",
    "Все": "All",
    "Торговые сессии": "Trading sessions",
    "Отдельный депозит, сделки и статистика для каждой попытки разгона.": "A separate balance, trades, and statistics for each trading run.",
    "нет активной": "none active",
    "Название": "Name",
    "Стартовый депозит": "Starting balance",
    "Цель": "Target",
    "Начать сессию": "Start session",
    "Монета": "Market",
    "Сторона": "Side",
    "Рабочий таймфрейм": "Trading timeframe",
    "1 мин": "1 minute",
    "5 мин": "5 minutes",
    "15 мин": "15 minutes",
    "1 час": "1 hour",
    "4 часа": "4 hours",
    "1 день": "1 day",
    "Депозит": "Account size",
    "Риск %": "Risk %",
    "Вход": "Entry",
    "Стоп": "Stop",
    "Тейк": "Target",
    "Плечо": "Leverage",
    "Режим маржи": "Margin mode",
    "Комиссия входа %": "Entry fee %",
    "Комиссия выхода %": "Exit fee %",
    "Проскальзывание % / ордер": "Slippage % / order",
    "Время в позиции, часов": "Time in position, hours",
    "Funding % / 8ч": "Funding % / 8h",
    "Расчет": "Calculation",
    "Заполни форму": "Complete the form",
    "Количество рассчитывается так, чтобы убыток по стопу вместе с комиссиями, funding и заданным проскальзыванием укладывался в риск. Ликвидация для Isolated — оценка; точное значение берется только из подключенного биржевого аккаунта.": "Position size keeps the stop loss, fees, funding, and configured slippage within the risk limit. Isolated liquidation is an estimate; an exact value requires a connected exchange account.",
    "Предложить сценарий входа": "Suggest entry scenario",
    "Разобрать по 1D / 1H / 15M / 5M": "Review 1D / 1H / 15M / 5M",
    "Лучшие монеты сейчас": "Top markets now",
    "Таймфрейм графиков дневника": "Journal chart timeframe",
    "Дата от": "Date from",
    "Дата до": "Date to",
    "Поиск": "Search",
    "уровень, ошибка, лонг...": "level, mistake, long...",
    "Визуализации": "Visualizations",
    "Модели и агенты": "Models and agents",
    "Настройка на backend": "Backend configuration",
    "Провайдер и модель выбираются приватными переменными окружения. Mini App не принимает API-ключ.": "The provider and model are selected through private backend environment variables. The Mini App does not accept API keys.",
    "Проверить backend": "Check backend",
    "Локальный / OpenAI-совместимый": "Local / OpenAI-compatible",
    "Офлайн / ручной режим": "Offline / manual mode",
    "Активный локальный или облачный провайдер объясняет Crisis Radar в read-only режиме.": "The active local or cloud provider explains Crisis Radar in read-only mode.",
    "Провайдер": "Provider",
    "проверка провайдера": "checking provider",
    "Активное подключение задаётся приватными переменными окружения backend; ключ никогда не возвращается в Mini App.": "The active connection is configured through private backend environment variables; its key is never returned to the Mini App.",
    "проверка Ollama": "checking Ollama",
    "не настроено": "not configured",
    "Права агента": "Agent permissions",
    "только чтение": "read only",
    "облако": "cloud",
    "Для распознавания скринов, анализа дневника и расширенных отчётов.": "For screenshot recognition, journal analysis, and extended reports.",
    "не подключено": "not connected",
    "Модель": "Model",
    "выбрать после проверки": "select after verification",
    "Проверить": "Test",
    "Сохранить": "Save",
    "Как подключить": "How to connect",
    "Создай API key в кабинете провайдера.": "Create an API key in the provider dashboard.",
    "Вставь ключ сюда и нажми “Проверить”.": "Paste the key here and select “Test”.",
    "После проверки backend покажет список моделей, а ключ будет виден только маской.": "After verification, the backend will list models and only show a masked key.",
    "локально": "local",
    "Для локальных runtimes с OpenAI-compatible API.": "For local runtimes with an OpenAI-compatible API.",
    "Проверить сейчас": "Check now",
    "Как подключить локальную модель": "How to connect a local model",
    "Ollama должна быть запущена на этом компьютере.": "Ollama must be running on this computer.",
    "Backend принимает только localhost и не отправляет данные наружу.": "The backend accepts localhost only and does not send data outside.",
    "Модель получает ограниченный снимок рассчитанных данных без прав на запись.": "The model receives a bounded calculated-data snapshot without write access.",
    "без ключей": "no keys",
    "Без модели: бот не фантазирует, а просит ручной шаблон и уточнения.": "Without a model, the bot requests a manual template and clarification instead of guessing.",
    "активен": "active",
    "Безопасный fallback включён.": "Safe fallback is enabled.",
    "Если модель не настроена, `/open` по фото вернёт ручной шаблон, а не будет выдумывать вход/стоп/тейк.": "If no model is configured, a photo sent to `/open` returns a manual template instead of invented entry, stop, or target values.",
    "Активные задачи": "Active tasks",
    "Текущие назначения читаются из приватной конфигурации backend.": "Current bindings are read from the private backend configuration.",
    "Фото → сделка": "Photo → trade",
    "Анализ дневника": "Journal analysis",
    "Obsidian отчёт": "Obsidian report",
    "Экспорт в Obsidian": "Obsidian Export",
    "Разбор сетапа": "Setup review",
    "Подключённые модели": "Connected models",
    "sk-...abcd · 3 модели": "sk-...abcd · 3 models",
    "Ключи будут отображаться только маской. Реальное сохранение появится после backend-миграции.": "Keys will only be shown as masks. Persistent storage will be added after the backend migration.",
    "Активировать": "Activate",
    "Состояние мирового рынка": "Global market state",
    "Загружаю состояние рынка…": "Loading market state…",
    "Подробный режим": "Detailed mode",
    "Простой режим": "Simple mode",
    "Последний расчёт": "Latest calculation",
    "Групп ухудшается": "Deteriorating groups",
    "Опасных групп": "Danger groups",
    "Критических групп": "Critical groups",
    "Ближайшие публикации": "Upcoming releases",
    "Подтверждённые даты важных макроэкономических данных.": "Confirmed dates for important macroeconomic releases.",
    "Каналы риска": "Risk channels",
    "Независимые группы сигналов.": "Independent signal groups.",
    "Сценарии": "Scenarios",
    "Совместное подтверждение независимыми каналами.": "Joint confirmation from independent channels.",
    "Спросить локального аналитика": "Ask the local analyst",
    "Спросить аналитика": "Ask the analyst",
    "Подключённая модель объясняет уже рассчитанные данные и не меняет сигналы или сделки.": "The connected model explains calculated data and cannot change signals or trades.",
    "проверка": "checking",
    "Режим": "Mode",
    "Быстрый": "Fast",
    "Глубокий": "Deep",
    "Вопрос": "Question",
    "Что сейчас ухудшается и почему это важно?": "What is deteriorating now, and why does it matter?",
    "Спросить": "Ask",
    "Это объяснение данных, а не финансовый совет и не команда на сделку.": "This explains data; it is not financial advice or a trade instruction.",
    "Официальный контекст": "Official context",
    "Публикации центральных банков, связанные со сценариями. Новость сама по себе не меняет стадию рынка.": "Central-bank publications linked to scenarios. News alone does not change the market stage.",
    "Индикаторы и пороги": "Indicators and thresholds",
    "Текущее значение, расстояние до следующего порога и свежесть.": "Current value, distance to the next threshold, and freshness.",
    "Техническая проверка": "Technical audit",
    "Версия методики, scores и здоровье источников.": "Methodology version, scores, and source health.",
    "Нет данных": "No data",
    "нет данных": "no data",
    "Данные ещё не загружены": "Data has not been loaded yet",
    "Дневник пуст": "Journal is empty",
    "Список пуст": "The list is empty",
    "Добавь монеты в watchlist": "Add markets to the watchlist",
    "Создай первую торговую сессию": "Create your first trading session",
    "АКТИВНА": "ACTIVE",
    "АРХИВ": "ARCHIVED",
    "ОТКРЫТА": "OPEN",
    "ПРИБЫЛЬ": "PROFIT",
    "УБЫТОК": "LOSS",
    "БЕЗУБЫТОК": "BREAKEVEN",
    "НЕТ ИТОГА": "NO RESULT",
    "ИДЕЯ": "IDEA",
    "Изменить": "Edit",
    "Закрыть": "Close",
    "Отменить": "Cancel",
    "Сохранить": "Save",
    "Добавить фото": "Add photo",
    "Фото сделки": "Trade photo",
    "Скрин сделки": "Trade screenshot",
    "Изображение недоступно": "Image unavailable",
    "Цена закрытия:": "Close price:",
    "Почему перенес стоп или тейк": "Why the stop or target changed",
    "Критичных замечаний нет": "No critical issues",
    "Проверяю сделку...": "Reviewing trade...",
    "Сверяю 1D / 1H / 15M / 5M...": "Checking 1D / 1H / 15M / 5M...",
    "Анализирую…": "Analyzing…",
    "Проверь параметры расчета": "Check calculation inputs",
    "Загружаю свечи...": "Loading candles...",
    "график сделки недоступен": "trade chart unavailable",
    "Лучший PnL": "Best PnL",
    "Худший PnL": "Worst PnL",
    "Открытых": "Open",
    "Закрытых": "Closed",
    "Активность по монетам": "Activity by market",
    "Скачать всё": "Download all",
    "Активная сессия": "Active session",
    "готов": "ready",
    "собираю": "building",
    "скачано": "downloaded",
    "ошибка": "error",
    "В архив": "Archive",
    "Продолжить": "Continue",
    "Старт": "Start",
    "Баланс": "Balance",
    "проверено по данным": "grounded in data",
    "требует ручной проверки": "manual verification required",
    "Опирается на": "Evidence",
    "Ограничения": "Limitations",
    "qwen готов": "qwen ready",
    "Ollama недоступна": "Ollama unavailable",
    "аналитик выключен": "analyst disabled",
    "модель не найдена": "model not found",
    "Локальный аналитик сейчас не ответил. Проверь Ollama и повтори запрос.": "The local analyst did not respond. Check Ollama and retry.",
    "Откройте Mini App через Telegram.": "Open the Mini App through Telegram.",
    "Не удалось добавить монету": "Could not add the market",
    "Не удалось убрать монету": "Could not remove the market",
    "Не удалось закрыть сделку": "Could not close the trade",
    "Не удалось отменить сделку": "Could not cancel the trade",
    "Не удалось изменить сделку": "Could not update the trade",
    "Не удалось создать сессию": "Could not create the session",
    "Не удалось проверить сделку": "Could not review the trade",
    "Не удалось скачать Obsidian export": "Could not download the Obsidian export",
    "Отменить открытую сделку?": "Cancel the open trade?",
    "тренд вверх": "uptrend",
    "тренд вниз": "downtrend",
    "флет": "range",
    "бычий импульс": "bullish momentum",
    "медвежий импульс": "bearish momentum",
    "нейтрально": "neutral",
    "да": "yes",
    "нет": "no"
  }));

  const textOrigins = new WeakMap();
  const translatedValues = new WeakMap();
  const attributeOrigins = new WeakMap();
  let locale = localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "ru";

  function translateDynamic(value) {
    const patterns = [
      [/^(\d+) сделок$/, "$1 trades"],
      [/^(\d+) наблюдений$/, "$1 observations"],
      [/^Убрать (.+)$/, "Remove $1"],
      [/^Цель (.+)$/, "Target $1"],
      [/^Кабинет трейдера · (.+)$/, "Trader workspace · $1"],
      [/^Цена закрытия:\s*(.+)$/, "Close price: $1"],
    ];
    for (const [pattern, replacement] of patterns) {
      if (pattern.test(value)) return value.replace(pattern, replacement);
    }
    return value;
  }

  function translate(value) {
    if (locale !== "en") return value;
    const leading = value.match(/^\s*/)?.[0] || "";
    const trailing = value.match(/\s*$/)?.[0] || "";
    const core = value.trim();
    if (!core) return value;
    return `${leading}${translations.get(core) || translateDynamic(core)}${trailing}`;
  }

  function localizeTextNode(node, refreshOrigin = false) {
    if (!node?.parentElement || ["SCRIPT", "STYLE"].includes(node.parentElement.tagName)) return;
    if (refreshOrigin || !textOrigins.has(node)) textOrigins.set(node, node.nodeValue || "");
    const next = translate(textOrigins.get(node) || "");
    translatedValues.set(node, next);
    if (node.nodeValue !== next) node.nodeValue = next;
  }

  function localizeAttributes(element) {
    if (!(element instanceof Element)) return;
    const saved = attributeOrigins.get(element) || {};
    for (const name of ["placeholder", "title", "aria-label"]) {
      if (element.hasAttribute(name) && saved[name] == null) saved[name] = element.getAttribute(name);
      if (saved[name] != null) element.setAttribute(name, translate(saved[name]));
    }
    attributeOrigins.set(element, saved);
  }

  function apply(root = document) {
    if (root.nodeType === Node.TEXT_NODE) {
      localizeTextNode(root);
      return;
    }
    if (!(root instanceof Element || root instanceof Document)) return;
    if (root instanceof Element) localizeAttributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let node = walker.nextNode();
    while (node) {
      if (node.nodeType === Node.TEXT_NODE) localizeTextNode(node);
      else localizeAttributes(node);
      node = walker.nextNode();
    }
  }

  function setLocale(nextLocale) {
    locale = nextLocale === "en" ? "en" : "ru";
    localStorage.setItem(STORAGE_KEY, locale);
    document.documentElement.lang = locale;
    document.querySelectorAll(".app-locale-btn").forEach(button => {
      button.classList.toggle("active", button.dataset.appLocale === locale);
      button.setAttribute("aria-pressed", String(button.dataset.appLocale === locale));
    });
    apply(document);
    window.dispatchEvent(new CustomEvent("app-locale-changed", { detail: { locale } }));
  }

  const observer = new MutationObserver(mutations => {
    for (const mutation of mutations) {
      if (mutation.type === "characterData") {
        if (translatedValues.get(mutation.target) === mutation.target.nodeValue) continue;
        localizeTextNode(mutation.target, true);
      } else {
        mutation.addedNodes.forEach(node => apply(node));
      }
    }
  });

  window.AppI18n = { apply, getLocale: () => locale, setLocale, translate };
  setLocale(locale);
  observer.observe(document.documentElement, { childList: true, characterData: true, subtree: true });
})();
