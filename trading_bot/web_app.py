from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
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
TRADE_UPLOAD_DIR = Path(os.getenv("TRADE_UPLOAD_DIR", str(BASE_DIR / "data" / "trade_uploads"))).expanduser()
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
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


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
        "open_trades": [trade_to_dict(row) for row in open_trades],
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
    return {"items": [trade_to_dict(row) for row in trades.list_for_user(user_id, status=status, limit=100)]}


@app.post("/api/trades")
async def create_trade_api(
    user_id: int = Query(...),
    symbol: str = Query(...),
    side: str = Query(...),
    entry_price: float = Query(..., gt=0),
    stop_price: float = Query(..., gt=0),
    target_price: float | None = Query(None, gt=0),
    quantity: float = Query(..., gt=0),
    leverage: float = Query(1, gt=0),
    timeframe: str = "1m",
    note: str = "",
) -> dict:
    symbol = normalize_symbol(symbol)
    side = side.lower()
    if side not in {"long", "short"}:
        raise HTTPException(status_code=400, detail="Side must be long or short")
    if side == "long" and stop_price >= entry_price:
        raise HTTPException(status_code=400, detail="Long stop must be below entry")
    if side == "short" and stop_price <= entry_price:
        raise HTTPException(status_code=400, detail="Short stop must be above entry")
    if target_price is not None and side == "long" and target_price <= entry_price:
        raise HTTPException(status_code=400, detail="Long target must be above entry")
    if target_price is not None and side == "short" and target_price >= entry_price:
        raise HTTPException(status_code=400, detail="Short target must be below entry")
    try:
        market_price = await market.get_price(symbol)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail="Монета не найдена на Binance Futures") from exc
    market_distance = abs(entry_price - market_price) / market_price * 100
    if market_distance > 15:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Цена входа отличается от текущей цены {symbol} на {market_distance:.1f}%. "
                "Проверь монету и цену"
            ),
        )
    timeframe = timeframe if timeframe in {"1m", "5m", "15m", "1h", "4h", "1d"} else "1m"
    trade_id = trades.create(
        user_id=user_id,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        quantity=quantity,
        leverage=leverage,
        risk_amount=abs(entry_price - stop_price) * quantity,
        note=note,
        timeframe=timeframe,
    )
    row = trades.get(user_id, trade_id)
    return {"ok": True, "trade": trade_to_dict(row)}


@app.post("/api/trades/{trade_id}/update")
def update_trade_api(
    trade_id: int,
    user_id: int = Query(...),
    entry_price: float = Query(..., gt=0),
    stop_price: float = Query(..., gt=0),
    target_price: float | None = Query(None, gt=0),
    quantity: float = Query(..., gt=0),
    timeframe: str = "5m",
    note: str = "",
) -> dict:
    timeframe = timeframe if timeframe in {"1m", "5m", "15m", "1h", "4h", "1d"} else "5m"
    row = trades.update(user_id, trade_id, entry_price, stop_price, target_price, quantity, timeframe, note)
    return {"ok": row is not None, "trade": trade_to_dict(row) if row else None}


@app.post("/api/trades/{trade_id}/leverage")
def update_trade_leverage_api(trade_id: int, user_id: int = Query(...), leverage: float = Query(..., gt=0)) -> dict:
    row = trades.set_leverage(user_id, trade_id, leverage)
    return {"ok": row is not None, "trade": trade_to_dict(row) if row else None}


@app.post("/api/trades/{trade_id}/attachment")
async def upload_trade_attachment(trade_id: int, request: Request, user_id: int = Query(...), filename: str = "screenshot.jpg") -> dict:
    if not trades.get(user_id, trade_id):
        raise HTTPException(status_code=404, detail="Trade not found")
    data = await request.body()
    if not data or len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image must be between 1 byte and 12 MB")
    suffix = Path(filename).suffix.lower() if Path(filename).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
    directory = TRADE_UPLOAD_DIR / str(user_id) / str(trade_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{hashlib.sha256(data).hexdigest()}{suffix}"
    target.write_bytes(data)
    attachment_id = trades.add_attachment(user_id, trade_id, local_path=str(target), caption=filename)
    return {"ok": True, "id": attachment_id}


@app.post("/api/trades/{trade_id}/link-journal")
def link_trade_journal_api(trade_id: int, user_id: int = Query(...), journal_id: int = Query(...)) -> dict:
    trade = trades.get(user_id, trade_id)
    entry = journal.get(user_id, journal_id)
    if not trade or not entry:
        raise HTTPException(status_code=404, detail="Trade or journal entry not found")
    existing_file_ids = {str(item["telegram_file_id"]) for item in trades.attachments(user_id, trade_id)}
    attached = 0
    for file_id in str(entry["screenshot_file_id"] or "").split(","):
        file_id = file_id.strip()
        if file_id and file_id not in existing_file_ids:
            trades.add_attachment(user_id, trade_id, telegram_file_id=file_id, caption=str(entry["description"] or ""))
            existing_file_ids.add(file_id)
            attached += 1
    journal.link_trade(user_id, journal_id, trade_id)
    return {"ok": True, "attached": attached, "trade": trade_to_dict(trades.get(user_id, trade_id))}


@app.get("/api/trade-attachment/{attachment_id}")
def trade_attachment_api(attachment_id: int) -> FileResponse:
    row = trades.attachment(attachment_id)
    if not row or not row["local_path"] or not Path(row["local_path"]).exists():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(row["local_path"])


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


@app.get("/api/watchlist")
def watchlist_api(user_id: int = Query(...)) -> dict:
    users.ensure_user(user_id)
    return {"items": watchlist.list_symbols(user_id)}


@app.post("/api/watchlist")
async def add_watchlist_api(user_id: int = Query(...), symbol: str = Query(..., min_length=1)) -> dict:
    users.ensure_user(user_id)
    normalized = normalize_symbol(symbol)
    try:
        await market.get_price(normalized)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Монета не найдена на Binance Futures") from exc
    watchlist.add(user_id, normalized)
    return {"ok": True, "symbol": normalized, "items": watchlist.list_symbols(user_id)}


@app.delete("/api/watchlist")
def remove_watchlist_api(user_id: int = Query(...), symbol: str = Query(..., min_length=1)) -> dict:
    normalized = normalize_symbol(symbol)
    removed = watchlist.remove(user_id, normalized)
    return {"ok": removed, "symbol": normalized, "items": watchlist.list_symbols(user_id)}


@app.get("/api/journal")
def journal_api(user_id: int = Query(...), symbol: str = "") -> dict:
    return {"items": [row_to_dict(row) for row in journal.list_for_user(user_id, symbol=symbol, limit=50)]}


@app.get("/api/journal/{journal_id}/chart")
async def journal_chart_api(journal_id: int, user_id: int = Query(...), interval: str = "1m") -> dict:
    interval = normalized_interval(interval)
    entry = journal.get(user_id, journal_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Journal entry not found")

    trade = trades.get(user_id, int(entry["linked_trade_id"])) if entry["linked_trade_id"] else None
    symbol = str(trade["symbol"] if trade else entry["symbol"] or "")
    if not symbol:
        return {"items": [], "historical": True, "journal": row_to_dict(entry), "trade": None}

    if trade:
        try:
            rows = await historical_trade_candles(trade, interval)
        except httpx.HTTPError:
            rows = [row_to_dict(row) for row in trades.candles(int(trade["id"]), interval)]
        chart_trade = trade_to_dict(trade)
        anchor = trade["closed_at"] or trade["opened_at"]
    else:
        anchor_ms = timestamp_ms(entry["created_at"])
        span = INTERVAL_MS[interval]
        rows = await market.get_klines(
            symbol,
            interval,
            limit=180,
            start_time=anchor_ms - span * 120,
            end_time=anchor_ms + span * 59,
        )
        chart_trade = None
        anchor = entry["created_at"]

    return {
        "items": rows,
        "historical": True,
        "journal": row_to_dict(entry),
        "trade": chart_trade,
        "anchor_time": anchor,
        "market": market.market,
    }


@app.post("/api/journal/{journal_id}/merge")
def merge_journal_api(journal_id: int, user_id: int = Query(...), remove_id: int = Query(...)) -> dict:
    return {"ok": journal.merge(user_id, journal_id, remove_id)}


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
    interval = normalized_interval(interval)
    trade = trades.get(user_id, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade["status"] == "open":
        live = await market.get_klines(trade["symbol"], interval, limit=180)
        if interval == "1m":
            trades.save_candles(trade_id, live, interval)
        return {"items": live, "historical": False, "trade": trade_to_dict(trade), "market": market.market}
    stored = [row_to_dict(row) for row in trades.candles(trade_id, interval)]
    try:
        historical = await historical_trade_candles(trade, interval)
    except httpx.HTTPError:
        historical = stored
    return {"items": historical, "historical": True, "trade": trade_to_dict(trade), "market": market.market}


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
    entry_fee_percent: float = 0.05,
    exit_fee_percent: float = 0.05,
    slippage_percent: float = 0.02,
    funding_rate_percent: float = 0,
    holding_hours: float = 0,
    funding_interval_hours: float = 8,
    maintenance_margin_percent: float = 0.5,
    margin_mode: str = "isolated",
) -> dict:
    calc = calculate_risk(
        symbol, side, entry, stop, account, risk_percent, target, leverage,
        entry_fee_percent, exit_fee_percent, slippage_percent,
        funding_rate_percent, holding_hours, funding_interval_hours,
        maintenance_margin_percent, margin_mode,
    )
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


INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def normalized_interval(interval: str) -> str:
    return interval if interval in INTERVAL_MS else "1m"


def timestamp_ms(value: str | None) -> int:
    if not value:
        return int(datetime.now(timezone.utc).timestamp() * 1000)
    parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


async def historical_trade_candles(trade, interval: str) -> list[dict[str, float]]:
    interval = normalized_interval(interval)
    span = INTERVAL_MS[interval]
    opened_ms = timestamp_ms(trade["opened_at"])
    closed_ms = timestamp_ms(trade["closed_at"] or trade["opened_at"])
    start_ms = opened_ms - span * 35
    end_ms = closed_ms + span * 25
    start_ms = max(start_ms, end_ms - span * 239)
    requested = max(80, min(240, int((end_ms - start_ms) / span) + 1))
    return await market.get_klines(
        trade["symbol"],
        interval,
        limit=requested,
        start_time=start_ms,
        end_time=end_ms,
    )


def row_to_dict(row) -> dict:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def trade_to_dict(row) -> dict:
    if row is None:
        return {}
    payload = row_to_dict(row)
    payload["attachments"] = [row_to_dict(item) for item in trades.attachments(int(row["user_id"]), int(row["id"]))]
    return payload
