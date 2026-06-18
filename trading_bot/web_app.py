from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trading_bot.db import Database
from trading_bot.evaluator import review_trade
from trading_bot.market import MarketClient, normalize_symbol
from trading_bot.models import TradeDraft
from trading_bot.repositories import (
    AlertRepository,
    DailyPlanRepository,
    JournalRepository,
    MarketContextRepository,
    TemplateRepository,
    TradeRepository,
    TradingSessionRepository,
    UserRepository,
    WatchlistRepository,
)
from trading_bot.risk import calculate_risk


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "mini_app"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3")).expanduser()
MEDIA_CACHE_DIR = BASE_DIR / "data" / "media_cache"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

db = Database(DATABASE_PATH)
users = UserRepository(db)
trades = TradeRepository(db)
alerts = AlertRepository(db)
journal = JournalRepository(db)
contexts = MarketContextRepository(db)
watchlist = WatchlistRepository(db)
daily_plans = DailyPlanRepository(db)
templates = TemplateRepository(db)
sessions = TradingSessionRepository(db)
market = MarketClient(os.getenv("MARKET", "futures").strip().lower())

app = FastAPI(title="Trading Assistant Mini App")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/dashboard")
def dashboard(user_id: int = Query(...)) -> dict:
    users.ensure_user(user_id)
    active_session = sessions.active(user_id)
    if active_session:
        stats = trades.stats_for_session(user_id, int(active_session["id"]))
        open_trades = trades.list_for_session(user_id, int(active_session["id"]), status="open", limit=100)
    else:
        stats = trades.stats(user_id)
        open_trades = trades.list_for_user(user_id, status="open", limit=100)
    active_alerts = alerts.list_for_user(user_id)
    symbols = watchlist.list_symbols(user_id)
    plan = daily_plans.latest(user_id)
    closed = int(stats["closed"] or 0)
    wins = int(stats["wins"] or 0)
    winrate = wins / closed * 100 if closed else 0
    return {
        "stats": {
            "total": int(stats["total"] or 0),
            "closed": closed,
            "wins": wins,
            "losses": int(stats["losses"] or 0),
            "winrate": round(winrate, 2),
            "net_pnl": float(stats["net_pnl"] or 0),
            "avg_pnl": float(stats["avg_pnl"] or 0),
            "best_pnl": float(stats["best_pnl"] or 0),
            "worst_pnl": float(stats["worst_pnl"] or 0),
        },
        "open_risk": sum(float(row["risk_amount"] or 0) for row in open_trades),
        "open_trades": [row_to_dict(row) for row in open_trades],
        "active_alerts": [row_to_dict(row) for row in active_alerts],
        "watchlist": symbols,
        "plan": row_to_dict(plan) if plan else None,
        "session": row_to_dict(active_session) if active_session else None,
    }


@app.get("/api/sessions")
def sessions_api(user_id: int = Query(...)) -> dict:
    return {"items": [row_to_dict(row) for row in sessions.list_for_user(user_id)]}


@app.post("/api/sessions")
def create_session_api(user_id: int = Query(...), name: str = Query(..., min_length=1, max_length=80), start_balance: float = Query(..., gt=0), target_balance: float | None = Query(None, gt=0), note: str = "") -> dict:
    return {"ok": True, "id": sessions.create(user_id, name, start_balance, target_balance, note)}


@app.post("/api/sessions/{session_id}/activate")
def activate_session_api(session_id: int, user_id: int = Query(...)) -> dict:
    return {"ok": sessions.activate(user_id, session_id)}


@app.post("/api/sessions/{session_id}/archive")
def archive_session_api(session_id: int, user_id: int = Query(...)) -> dict:
    return {"ok": sessions.archive(user_id, session_id)}


@app.get("/api/trades")
def trades_api(user_id: int = Query(...), status: str | None = None) -> dict:
    status = status if status in {"open", "closed", "cancelled"} else None
    return {"items": [row_to_dict(row) for row in trades.list_for_user(user_id, status=status, limit=100)]}


@app.post("/api/trades/{trade_id}/close")
def close_trade_api(
    trade_id: int,
    user_id: int = Query(...),
    exit_price: float = Query(...),
    fees: float = 0,
    note: str = "",
) -> dict:
    row = trades.close(user_id, trade_id, exit_price, fees, note or "closed from mini app", close_reason="manual")
    return {"ok": row is not None, "trade": row_to_dict(row) if row else None}


@app.post("/api/trades/{trade_id}/cancel")
def cancel_trade_api(trade_id: int, user_id: int = Query(...)) -> dict:
    return {"ok": trades.cancel(user_id, trade_id)}


@app.get("/api/contexts")
def contexts_api(user_id: int = Query(...), symbol: str = "") -> dict:
    return {"items": [row_to_dict(row) for row in contexts.list_for_user(user_id, symbol=symbol, limit=100)]}


@app.get("/api/alerts")
def alerts_api(user_id: int = Query(...)) -> dict:
    return {"items": [row_to_dict(row) for row in alerts.list_for_user(user_id)]}


@app.get("/api/journal")
def journal_api(user_id: int = Query(...), symbol: str = "") -> dict:
    return {"items": [row_to_dict(row) for row in journal.list_for_user(user_id, symbol=symbol, limit=50)]}


@app.get("/api/templates")
def templates_api(user_id: int = Query(...)) -> dict:
    return {"items": templates.list_for_user(user_id)}


@app.get("/api/prices")
async def prices_api(user_id: int = Query(...), symbols: str = "") -> dict:
    requested = {item.strip() for item in symbols.split(",") if item.strip()}
    if not requested:
        requested.update(watchlist.list_symbols(user_id))
        requested.update(str(row["symbol"]) for row in trades.list_for_user(user_id, status="open", limit=100))
    if not requested:
        requested = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

    tickers = await market.get_tickers(requested)
    return {
        "items": [
            {
                "symbol": ticker.symbol,
                "price": ticker.price,
                "price_change_percent": ticker.price_change_percent,
                "intraday_range_percent": ticker.intraday_range_percent,
                "high_price": ticker.high_price,
                "low_price": ticker.low_price,
                "quote_volume": ticker.quote_volume,
            }
            for ticker in tickers
        ]
    }


@app.get("/api/market/top")
async def market_top_api(limit: int = Query(30, ge=1, le=50)) -> dict:
    tickers = await market.top_by_activity(limit)
    return {
        "items": [
            {
                "symbol": ticker.symbol,
                "exchange": "Binance Futures" if market.market == "futures" else "Binance Spot",
                "price": ticker.price,
                "price_change_percent": ticker.price_change_percent,
                "intraday_range_percent": ticker.intraday_range_percent,
                "quote_volume": ticker.quote_volume,
                "activity_score": ticker.activity_score,
                "high_price": ticker.high_price,
                "low_price": ticker.low_price,
            }
            for ticker in tickers
        ]
    }


@app.get("/api/klines")
async def klines_api(symbol: str, interval: str = "1m", limit: int = Query(80, ge=10, le=240)) -> dict:
    allowed = {"1m", "5m", "15m", "1h", "4h", "1d"}
    interval = interval if interval in allowed else "1m"
    rows = await market.get_klines(symbol, interval, limit=limit)
    return {"items": rows}


@app.get("/api/trades/{trade_id}/chart")
async def trade_chart_api(trade_id: int, user_id: int = Query(...), interval: str = "1m") -> dict:
    trade = trades.get(user_id, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    stored = [row_to_dict(row) for row in trades.candles(trade_id, interval)]
    if trade["status"] == "open":
        live = await market.get_klines(trade["symbol"], interval, limit=80)
        if interval == "1m":
            trades.save_candles(trade_id, live, interval)
        return {"items": live, "historical": False}
    return {"items": stored, "historical": True}


@app.get("/api/media/{file_id:path}")
async def media_api(file_id: str) -> FileResponse:
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not configured")
    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
    target = MEDIA_CACHE_DIR / f"{digest}.jpg"
    if not target.exists():
        async with httpx.AsyncClient(timeout=30) as client:
            meta = await client.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile", params={"file_id": file_id})
            meta.raise_for_status()
            payload = meta.json()
            file_path = payload["result"]["file_path"]
            data = await client.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}")
            data.raise_for_status()
            target.write_bytes(data.content)
    return FileResponse(target)


@app.get("/api/risk")
def risk_api(
    symbol: str,
    side: str,
    entry: float,
    stop: float,
    target: float | None = None,
    account: float = 0,
    risk_percent: float = 1,
    leverage: float = 1,
) -> dict:
    calc = calculate_risk(symbol, side, entry, stop, account, risk_percent, target, leverage)
    return {"result": calc.__dict__}


@app.get("/api/review")
async def review_api(
    user_id: int,
    symbol: str,
    side: str,
    entry: float,
    stop: float,
    target: float | None = None,
    account: float = 0,
    risk_percent: float = 1,
    leverage: float = 1,
) -> dict:
    calc = calculate_risk(symbol, side, entry, stop, account, risk_percent, target, leverage)
    draft = TradeDraft(
        symbol=normalize_symbol(symbol),
        side=side.lower(),
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        quantity=calc.quantity,
        leverage=leverage,
        risk_amount=calc.risk_amount,
    )
    sentiment = None
    current_price = None
    try:
        sentiment = await market.get_sentiment(draft.symbol)
    except Exception:
        sentiment = None
    try:
        current_price = await market.get_price(draft.symbol)
    except Exception:
        current_price = None
    defaults = users.get_defaults(user_id)
    account_size = float(account or defaults["default_account_size"] or 0)
    review = review_trade(
        draft=draft,
        contexts=contexts.latest_for_symbol(user_id, draft.symbol),
        watchlist_symbols=watchlist.list_symbols(user_id),
        daily_plan=daily_plans.latest(user_id),
        account_size=account_size,
        open_risk_total=trades.open_risk_total(user_id),
        today_pnl=0,
        sentiment=sentiment,
        current_price=current_price,
    )
    return {
        "review": {
            "score": review.score,
            "win_probability": review.win_probability,
            "loss_probability": review.loss_probability,
            "severity": review.severity,
            "summary": review.summary,
            "issues": [issue.__dict__ for issue in review.issues],
            "distances": [distance.__dict__ for distance in review.distances],
        }
    }


@app.get("/api/setup")
async def setup_api(symbol: str, timeframe: str = "5m", risk_reward: float = Query(2, ge=1, le=5)) -> dict:
    symbol = normalize_symbol(symbol)
    timeframe = timeframe if timeframe in {"1m", "5m", "15m", "1h"} else "5m"
    intervals = ["1d", "1h", "15m", "5m"]
    analyses = {}
    candle_sets = {}
    for interval in intervals:
        candles = await market.get_klines(symbol, interval, limit=120)
        candle_sets[interval] = candles
        closes = [float(item["close"]) for item in candles]
        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)
        rsi_value = rsi(closes)
        bias = "long" if closes[-1] > ema20 > ema50 else "short" if closes[-1] < ema20 < ema50 else "neutral"
        analyses[interval] = {"bias": bias, "rsi": round(rsi_value, 1), "ema20": ema20, "ema50": ema50, "price": closes[-1]}

    votes = sum(1 if item["bias"] == "long" else -1 if item["bias"] == "short" else 0 for item in analyses.values())
    side = "long" if votes > 0 else "short" if votes < 0 else "neutral"
    working = candle_sets.get(timeframe) or candle_sets["5m"]
    price = float(working[-1]["close"])
    atr_value = atr(working)
    stop_distance = max(atr_value * 1.5, price * 0.0025)
    if side == "short":
        stop = price + stop_distance
        target = price - stop_distance * risk_reward
    else:
        stop = price - stop_distance
        target = price + stop_distance * risk_reward
    alignment = abs(votes) / len(analyses)
    score = min(82, round(48 + alignment * 28 + (6 if analyses["1d"]["bias"] == side else 0)))
    return {
        "symbol": symbol, "timeframe": timeframe, "side": side, "entry": price,
        "stop": stop, "target": target, "score": score,
        "win_probability": score, "loss_probability": 100 - score,
        "contexts": analyses,
        "note": "Оценка качества сетапа по тренду и волатильности, не статистическая гарантия исхода.",
    }


def ema(values: list[float], period: int) -> float:
    result = values[0]
    multiplier = 2 / (period + 1)
    for value in values[1:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


def rsi(values: list[float], period: int = 14) -> float:
    changes = [values[index] - values[index - 1] for index in range(1, len(values))][-period:]
    gains = sum(max(change, 0) for change in changes) / max(len(changes), 1)
    losses = sum(max(-change, 0) for change in changes) / max(len(changes), 1)
    if losses == 0:
        return 100
    return 100 - 100 / (1 + gains / losses)


def atr(candles: list[dict[str, float]], period: int = 14) -> float:
    recent = candles[-period:]
    ranges = []
    previous = float(candles[-period - 1]["close"]) if len(candles) > period else float(recent[0]["open"])
    for candle in recent:
        high, low = float(candle["high"]), float(candle["low"])
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
        previous = float(candle["close"])
    return sum(ranges) / max(len(ranges), 1)


def row_to_dict(row) -> dict:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}
