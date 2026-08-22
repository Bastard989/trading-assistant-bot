from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any, Iterable

from trading_bot.db import Database
from trading_bot.market import MarketClient


MOMENTUM_VERSION = "crypto-momentum-v1"
TRACKED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = (
    ("15m", 110, 10),
    ("1h", 180, 25),
    ("4h", 190, 30),
    ("1d", 120, 35),
)
SIGNIFICANT_STATES = frozenset(
    {"early_uptrend", "confirmed_uptrend", "overheated", "trend_break"}
)


@dataclass(frozen=True)
class TimeframeMomentum:
    interval: str
    score: int
    bullish: bool
    price: float
    return_pct: float
    sma20: float
    sma50: float
    sma20_slope_pct: float
    volume_ratio: float
    support: float


@dataclass(frozen=True)
class CryptoMomentumResult:
    symbol: str
    state: str
    score: int
    confidence: str
    data_quality: int
    as_of: datetime
    price: float | None
    funding_rate_pct: float | None
    oi_7d_change_pct: float | None
    bullish_timeframes: tuple[str, ...]
    evidence: tuple[str, ...]
    next_confirmation: tuple[str, ...]
    invalidation: tuple[str, ...]
    limitations: tuple[str, ...]
    timeframes: tuple[TimeframeMomentum, ...]
    methodology: str = MOMENTUM_VERSION

    def payload(self, locale: str = "ru") -> dict[str, Any]:
        translated = _localized_copy(self, locale)
        return {
            "symbol": self.symbol,
            "state": self.state,
            "state_label": translated["state_label"],
            "score": self.score,
            "confidence": self.confidence,
            "data_quality": self.data_quality,
            "as_of": self.as_of.astimezone(timezone.utc).isoformat(),
            "price": self.price,
            "funding_rate_pct": self.funding_rate_pct,
            "oi_7d_change_pct": self.oi_7d_change_pct,
            "bullish_timeframes": list(self.bullish_timeframes),
            "explanation": translated["explanation"],
            "evidence": list(translated["evidence"]),
            "next_confirmation": list(translated["next_confirmation"]),
            "invalidation": list(translated["invalidation"]),
            "limitations": list(translated["limitations"]),
            "timeframes": [asdict(item) for item in self.timeframes],
            "methodology": self.methodology,
            "analysis_only": True,
            "execution_allowed": False,
        }


@dataclass(frozen=True)
class CryptoMomentumDelivery:
    delivery_id: int
    user_id: int
    symbol: str
    from_state: str
    to_state: str
    severity: str
    payload: dict[str, Any]


def _closed_rows(rows: Iterable[dict[str, float]], *, now_ms: float) -> list[dict[str, float]]:
    return [row for row in rows if float(row.get("close_time", 0)) <= now_ms]


def _change(current: float, previous: float) -> float:
    return 0.0 if previous == 0 else (current / previous - 1.0) * 100.0


def analyze_timeframe(interval: str, rows: Iterable[dict[str, float]]) -> TimeframeMomentum:
    values = list(rows)
    if len(values) < 55:
        raise ValueError(f"{interval} requires at least 55 completed candles")
    closes = [float(row["close"]) for row in values]
    volumes = [float(row["volume"]) for row in values]
    price = closes[-1]
    sma20 = fmean(closes[-20:])
    sma50 = fmean(closes[-50:])
    prior_sma20 = fmean(closes[-25:-5])
    slope = _change(sma20, prior_sma20)
    lookbacks = {"15m": 24, "1h": 24, "4h": 42, "1d": 30}
    lookback = min(lookbacks[interval], len(closes) - 1)
    return_pct = _change(price, closes[-lookback - 1])
    recent_volume = fmean(volumes[-4:])
    baseline_volume = fmean(volumes[-24:-4]) if len(volumes) >= 24 else fmean(volumes[:-4])
    volume_ratio = recent_volume / baseline_volume if baseline_volume > 0 else 1.0
    components = (
        price > sma20,
        sma20 > sma50,
        slope > 0,
        return_pct > 0,
    )
    score = int(round(sum(25 for value in components if value)))
    return TimeframeMomentum(
        interval=interval,
        score=score,
        bullish=score >= 75,
        price=price,
        return_pct=return_pct,
        sma20=sma20,
        sma50=sma50,
        sma20_slope_pct=slope,
        volume_ratio=volume_ratio,
        support=min(sma20, min(closes[-10:])),
    )


def classify_crypto_momentum(
    symbol: str,
    frames: Iterable[TimeframeMomentum],
    *,
    as_of: datetime,
    funding_rate_pct: float | None,
    oi_7d_change_pct: float | None,
    previous_state: str | None = None,
) -> CryptoMomentumResult:
    frame_map = {item.interval: item for item in frames}
    available = tuple(frame_map[key] for key, _limit, _weight in TIMEFRAMES if key in frame_map)
    price_quality = len(available) / len(TIMEFRAMES)
    derivatives_quality = (funding_rate_pct is not None) + (oi_7d_change_pct is not None)
    data_quality = int(round((price_quality * 0.8 + derivatives_quality / 2 * 0.2) * 100))
    limitations: list[str] = []
    if len(available) < 3:
        limitations.append("missing_price_timeframes")
    if funding_rate_pct is None:
        limitations.append("missing_funding")
    if oi_7d_change_pct is None:
        limitations.append("missing_open_interest")
    if not available:
        return CryptoMomentumResult(
            symbol=symbol,
            state="insufficient_data",
            score=0,
            confidence="low",
            data_quality=data_quality,
            as_of=as_of,
            price=None,
            funding_rate_pct=funding_rate_pct,
            oi_7d_change_pct=oi_7d_change_pct,
            bullish_timeframes=(),
            evidence=(),
            next_confirmation=("restore_market_data",),
            invalidation=(),
            limitations=tuple(limitations),
            timeframes=(),
        )

    weights = {interval: weight for interval, _limit, weight in TIMEFRAMES}
    weight_total = sum(weights[item.interval] for item in available)
    score = int(round(sum(item.score * weights[item.interval] for item in available) / weight_total))
    bullish = tuple(item.interval for item in available if item.bullish)
    long_term_confirmed = {"4h", "1d"}.issubset(bullish)
    derivatives_confirm = (
        funding_rate_pct is not None
        and oi_7d_change_pct is not None
        and funding_rate_pct > -0.03
        and oi_7d_change_pct > 0
    )
    daily = frame_map.get("1d")
    four_hour = frame_map.get("4h")
    seven_day_return = four_hour.return_pct if four_hour else 0.0
    thirty_day_return = daily.return_pct if daily else 0.0
    crowded = bool(
        (funding_rate_pct is not None and funding_rate_pct >= 0.08)
        or (
            oi_7d_change_pct is not None
            and oi_7d_change_pct >= 18
            and (seven_day_return >= 15 or thirty_day_return >= 35)
        )
    )
    broken = score < 48 or (
        four_hour is not None and daily is not None
        and four_hour.price < four_hour.sma20 and daily.price < daily.sma20
    )
    if previous_state in {"early_uptrend", "confirmed_uptrend", "overheated"} and broken:
        state = "trend_break"
    elif data_quality < 60:
        state = "insufficient_data"
    elif crowded and score >= 68 and long_term_confirmed:
        state = "overheated"
    elif score >= 68 and len(bullish) >= 3 and long_term_confirmed and derivatives_confirm:
        state = "confirmed_uptrend"
    elif score >= 55 and len(bullish) >= 2 and bool({"4h", "1d"}.intersection(bullish)):
        state = "early_uptrend"
    elif score <= 35:
        state = "bearish"
    else:
        state = "neutral"

    evidence: list[str] = [f"bullish:{item}" for item in bullish]
    if derivatives_confirm:
        evidence.append("derivatives_confirm")
    if crowded:
        evidence.append("crowded_leverage")
    next_confirmation: list[str] = []
    if "4h" not in bullish:
        next_confirmation.append("confirm_4h")
    if "1d" not in bullish:
        next_confirmation.append("confirm_1d")
    if not derivatives_confirm:
        next_confirmation.append("confirm_derivatives")
    if crowded:
        next_confirmation.append("cool_funding_or_oi")
    invalidation: list[str] = []
    if four_hour:
        invalidation.append(f"4h_support:{four_hour.sma20:.8f}")
    if daily:
        invalidation.append(f"1d_support:{daily.sma20:.8f}")
    confidence = "high" if data_quality >= 90 else "medium" if data_quality >= 70 else "low"
    return CryptoMomentumResult(
        symbol=symbol,
        state=state,
        score=score,
        confidence=confidence,
        data_quality=data_quality,
        as_of=as_of,
        price=frame_map.get("15m", available[0]).price,
        funding_rate_pct=funding_rate_pct,
        oi_7d_change_pct=oi_7d_change_pct,
        bullish_timeframes=bullish,
        evidence=tuple(evidence),
        next_confirmation=tuple(next_confirmation),
        invalidation=tuple(invalidation),
        limitations=tuple(limitations),
        timeframes=available,
    )


class CryptoMomentumMonitor:
    def __init__(self, market: MarketClient) -> None:
        self.market = market

    async def analyze(self, symbol: str, *, previous_state: str | None = None) -> CryptoMomentumResult:
        as_of = datetime.now(timezone.utc)
        now_ms = as_of.timestamp() * 1000
        frame_rows = await asyncio.gather(
            *(self.market.get_klines(symbol, interval, limit) for interval, limit, _weight in TIMEFRAMES),
            return_exceptions=True,
        )
        frames: list[TimeframeMomentum] = []
        for (interval, _limit, _weight), rows in zip(TIMEFRAMES, frame_rows):
            if isinstance(rows, Exception):
                continue
            try:
                frames.append(analyze_timeframe(interval, _closed_rows(rows, now_ms=now_ms)))
            except (KeyError, TypeError, ValueError):
                continue
        funding, oi_change = await asyncio.gather(
            self.market.get_funding_rate(symbol),
            self.market.get_open_interest_change(symbol, period="4h", limit=43),
            return_exceptions=True,
        )
        funding_value = None if isinstance(funding, Exception) else funding
        oi_value = None if isinstance(oi_change, Exception) else oi_change
        return classify_crypto_momentum(
            symbol,
            frames,
            as_of=as_of,
            funding_rate_pct=funding_value,
            oi_7d_change_pct=oi_value,
            previous_state=previous_state,
        )

    async def analyze_all(
        self, previous_states: dict[str, str] | None = None
    ) -> tuple[CryptoMomentumResult, ...]:
        previous_states = previous_states or {}
        results = await asyncio.gather(
            *(self.analyze(symbol, previous_state=previous_states.get(symbol)) for symbol in TRACKED_SYMBOLS),
            return_exceptions=True,
        )
        safe: list[CryptoMomentumResult] = []
        for symbol, result in zip(TRACKED_SYMBOLS, results):
            if isinstance(result, Exception):
                safe.append(
                    classify_crypto_momentum(
                        symbol, (), as_of=datetime.now(timezone.utc),
                        funding_rate_pct=None, oi_7d_change_pct=None,
                    )
                )
            else:
                safe.append(result)
        return tuple(safe)


class CryptoMomentumRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def latest_states(self) -> dict[str, str]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot.symbol, snapshot.state
                FROM cr_crypto_momentum_snapshots AS snapshot
                JOIN (
                    SELECT symbol, max(id) AS id FROM cr_crypto_momentum_snapshots GROUP BY symbol
                ) AS latest ON latest.id=snapshot.id
                """
            ).fetchall()
        return {str(row["symbol"]): str(row["state"]) for row in rows}

    def save(self, result: CryptoMomentumResult) -> bool:
        payload = result.payload("ru")
        snapshot_at = result.as_of.astimezone(timezone.utc).isoformat()
        with self.db.connect() as connection:
            previous = connection.execute(
                "SELECT state FROM cr_crypto_momentum_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1",
                (result.symbol,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO cr_crypto_momentum_snapshots(
                    symbol, methodology, snapshot_at, state, score, confidence,
                    data_quality, price_text, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.symbol, result.methodology, snapshot_at, result.state, result.score,
                    result.confidence, result.data_quality,
                    None if result.price is None else str(result.price),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            if previous is None or previous["state"] == result.state:
                return False
            if result.state not in SIGNIFICANT_STATES:
                return False
            cutoff = (result.as_of - timedelta(hours=6)).astimezone(timezone.utc).isoformat()
            duplicate = connection.execute(
                """
                SELECT 1 FROM cr_crypto_momentum_events
                WHERE symbol=? AND to_state=? AND occurred_at>=? LIMIT 1
                """,
                (result.symbol, result.state, cutoff),
            ).fetchone()
            if duplicate is not None:
                return False
            severity = "critical" if result.state == "trend_break" else "warning"
            key = f"crypto-momentum:{result.symbol}:{snapshot_at}:{previous['state']}:{result.state}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO cr_crypto_momentum_events(
                    event_key, symbol, occurred_at, from_state, to_state, severity, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key, result.symbol, snapshot_at, previous["state"], result.state, severity,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            return cursor.rowcount == 1

    def enqueue_deliveries(self, user_ids: tuple[int, ...]) -> int:
        inserted = 0
        clean = tuple(sorted({int(value) for value in user_ids if int(value) > 0}))
        with self.db.connect() as connection:
            events = connection.execute("SELECT id FROM cr_crypto_momentum_events ORDER BY id").fetchall()
            for event in events:
                for user_id in clean:
                    inserted += connection.execute(
                        "INSERT OR IGNORE INTO cr_crypto_momentum_deliveries(event_id,user_id) VALUES (?,?)",
                        (event["id"], user_id),
                    ).rowcount
        return inserted

    def pending_deliveries(self, *, limit: int = 20) -> list[CryptoMomentumDelivery]:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT delivery.id AS delivery_id, delivery.user_id, event.symbol,
                       event.from_state, event.to_state, event.severity, event.payload
                FROM cr_crypto_momentum_deliveries AS delivery
                JOIN cr_crypto_momentum_events AS event ON event.id=delivery.event_id
                WHERE delivery.status IN ('pending','failed') AND delivery.attempts<3
                  AND (delivery.next_attempt_at IS NULL OR delivery.next_attempt_at<=?)
                ORDER BY event.occurred_at, delivery.id LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [
            CryptoMomentumDelivery(
                delivery_id=int(row["delivery_id"]), user_id=int(row["user_id"]),
                symbol=str(row["symbol"]), from_state=str(row["from_state"]),
                to_state=str(row["to_state"]), severity=str(row["severity"]),
                payload=json.loads(row["payload"] or "{}"),
            )
            for row in rows
        ]

    def mark_sent(self, delivery_id: int, *, sent_at: datetime) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE cr_crypto_momentum_deliveries
                SET status='sent', attempts=attempts+1, sent_at=?, next_attempt_at=NULL, last_error=''
                WHERE id=?
                """,
                (sent_at.astimezone(timezone.utc).isoformat(), delivery_id),
            )

    def mark_failed(self, delivery_id: int, *, error: str, retry_at: datetime) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE cr_crypto_momentum_deliveries
                SET status='failed', attempts=attempts+1, next_attempt_at=?, last_error=? WHERE id=?
                """,
                (retry_at.astimezone(timezone.utc).isoformat(), error[:200], delivery_id),
            )


def _localized_copy(result: CryptoMomentumResult, locale: str) -> dict[str, Any]:
    ru = locale != "en"
    labels = {
        "insufficient_data": ("Недостаточно данных", "Insufficient data"),
        "bearish": ("Нисходящий режим", "Bearish regime"),
        "neutral": ("Нет подтверждённого направления", "No confirmed direction"),
        "early_uptrend": ("Ранний рост", "Early uptrend"),
        "confirmed_uptrend": ("Подтверждённый рост", "Confirmed uptrend"),
        "overheated": ("Рост перегрет", "Uptrend is overheated"),
        "trend_break": ("Рост сломался", "Uptrend has broken"),
    }
    explanations = {
        "insufficient_data": ("Радар не получил достаточно независимых рыночных данных и не делает вывод.", "The radar lacks enough independent market data and withholds a conclusion."),
        "bearish": ("Большинство временных горизонтов направлены вниз.", "Most time horizons point down."),
        "neutral": ("Рост и падение пока не подтверждены несколькими горизонтами одновременно.", "Neither an uptrend nor a downtrend is confirmed across multiple horizons."),
        "early_uptrend": ("Несколько горизонтов развернулись вверх, но полное подтверждение ещё не собрано.", "Several horizons turned upward, but full confirmation is still missing."),
        "confirmed_uptrend": ("Рост подтверждён 4-часовым и дневным трендом, а деривативы не противоречат движению.", "The 4-hour and daily trends confirm the move and derivatives do not contradict it."),
        "overheated": ("Тренд остаётся восходящим, но funding или открытый интерес указывают на перегрев плечей.", "The trend remains upward, but funding or open interest signals crowded leverage."),
        "trend_break": ("Ранее восходящая структура потеряла ключевые средние и подтверждение горизонтов.", "The prior uptrend lost key averages and multi-horizon confirmation."),
    }
    evidence_map = {
        "bullish:15m": ("15 минут: структура направлена вверх", "15m structure points up"),
        "bullish:1h": ("1 час: структура направлена вверх", "1h structure points up"),
        "bullish:4h": ("4 часа: структура направлена вверх", "4h structure points up"),
        "bullish:1d": ("1 день: структура направлена вверх", "1d structure points up"),
        "derivatives_confirm": ("Funding и изменение открытого интереса подтверждают спрос", "Funding and open-interest change confirm demand"),
        "crowded_leverage": ("Плечи перегреты: повышен риск резкого сброса", "Leverage is crowded: unwind risk is elevated"),
    }
    next_map = {
        "restore_market_data": ("Дождаться восстановления котировок", "Wait for market data to recover"),
        "confirm_4h": ("Нужно подтверждение восходящей структуры на 4 часах", "The 4h structure must confirm"),
        "confirm_1d": ("Нужно подтверждение восходящей структуры на дневном графике", "The daily structure must confirm"),
        "confirm_derivatives": ("Нужна поддержка funding и подписанного изменения OI", "Funding and signed OI change must confirm"),
        "cool_funding_or_oi": ("Funding или OI должны остыть без слома цены", "Funding or OI must cool without a price breakdown"),
    }
    limitation_map = {
        "missing_price_timeframes": ("Доступны не все ценовые горизонты", "Some price horizons are unavailable"),
        "missing_funding": ("Funding недоступен", "Funding is unavailable"),
        "missing_open_interest": ("Открытый интерес недоступен", "Open interest is unavailable"),
    }
    def pick(pair: tuple[str, str]) -> str:
        return pair[0] if ru else pair[1]
    invalidation = []
    for value in result.invalidation:
        key, raw = value.split(":", 1)
        label = "4-часовая SMA20" if key == "4h_support" and ru else "Дневная SMA20" if ru else "4h SMA20" if key == "4h_support" else "Daily SMA20"
        invalidation.append(f"{label}: {float(raw):,.4f}".replace(",", " ") if ru else f"{label}: {float(raw):,.4f}")
    return {
        "state_label": pick(labels[result.state]),
        "explanation": pick(explanations[result.state]),
        "evidence": tuple(pick(evidence_map[value]) for value in result.evidence if value in evidence_map),
        "next_confirmation": tuple(pick(next_map[value]) for value in result.next_confirmation if value in next_map),
        "invalidation": tuple(invalidation),
        "limitations": tuple(pick(limitation_map[value]) for value in result.limitations if value in limitation_map),
    }
