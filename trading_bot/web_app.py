from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trading_bot.db import Database
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

db = Database(DATABASE_PATH)
users = UserRepository(db)
trades = TradeRepository(db)
alerts = AlertRepository(db)
journal = JournalRepository(db)
contexts = MarketContextRepository(db)
watchlist = WatchlistRepository(db)
daily_plans = DailyPlanRepository(db)
templates = TemplateRepository(db)

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


def row_to_dict(row) -> dict:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}
