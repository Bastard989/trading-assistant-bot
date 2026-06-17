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
market = MarketClient(os.getenv("MARKET", "futures").strip().lower())

app = FastAPI(title="Trading Assistant Mini App")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/dashboard")
def dashboard(user_id: int = Query(...)) -> dict:
    users.ensure_user(user_id)
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
    }


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


def row_to_dict(row) -> dict:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}
