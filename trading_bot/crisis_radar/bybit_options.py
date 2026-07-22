from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from trading_bot.crisis_radar.opportunities import AssetClass, MarketQuote
from trading_bot.crisis_radar.sources.base import SourcePayloadError


OPTION_SYMBOL = re.compile(
    r"^(?P<base>BTC|ETH)-(?P<expiry>\d{1,2}[A-Z]{3}\d{2})-(?P<strike>\d+(?:\.\d+)?)-P$"
)


@dataclass(frozen=True)
class _Put:
    symbol: str
    expiry: datetime
    strike: Decimal
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    open_interest: Decimal
    turnover: Decimal
    underlying: Decimal


def _positive(row: dict, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        value = Decimal(str(row[field]))
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise SourcePayloadError(f"invalid Bybit option {field}") from exc
    if not value.is_finite() or (value < 0 if allow_zero else value <= 0):
        raise SourcePayloadError(f"invalid Bybit option {field}")
    return value


def _nonnegative_or_zero(row: dict, field: str) -> Decimal:
    raw = row.get(field)
    if raw is None or raw == "":
        return Decimal("0")
    return _positive(row, field, allow_zero=True)


def _parse_put(row: object, *, base_coin: str) -> _Put | None:
    if not isinstance(row, dict):
        raise SourcePayloadError("invalid Bybit option ticker row")
    symbol = str(row.get("symbol") or "")
    match = OPTION_SYMBOL.fullmatch(symbol)
    if match is None or match.group("base") != base_coin:
        return None
    try:
        expiry = datetime.strptime(match.group("expiry"), "%d%b%y").replace(
            hour=8, tzinfo=timezone.utc
        )
        strike = Decimal(match.group("strike"))
    except (ValueError, InvalidOperation) as exc:
        raise SourcePayloadError("invalid Bybit option symbol") from exc
    bid = _nonnegative_or_zero(row, "bid1Price")
    ask = _nonnegative_or_zero(row, "ask1Price")
    bid_size = _nonnegative_or_zero(row, "bid1Size")
    ask_size = _nonnegative_or_zero(row, "ask1Size")
    underlying = _nonnegative_or_zero(row, "underlyingPrice")
    if min(bid, ask, bid_size, ask_size, underlying) <= 0:
        return None
    return _Put(
        symbol=symbol,
        expiry=expiry,
        strike=strike,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        open_interest=_nonnegative_or_zero(row, "openInterest"),
        turnover=_nonnegative_or_zero(row, "turnover24h"),
        underlying=underlying,
    )


def build_defined_risk_put_spread(
    payload: bytes,
    *,
    base_coin: str,
    fetched_at: datetime,
) -> MarketQuote | None:
    """Select one quoted Bybit put spread without inventing missing legs or prices."""
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")
    fetched_at = fetched_at.astimezone(timezone.utc)
    base_coin = base_coin.strip().upper()
    if base_coin not in {"BTC", "ETH"}:
        raise ValueError("base_coin must be BTC or ETH")
    try:
        document = json.loads(payload)
        if document.get("retCode") != 0 or document["result"].get("category") != "option":
            raise SourcePayloadError("Bybit returned an option API error")
        rows = document["result"]["list"]
        server_time = datetime.fromtimestamp(int(document["time"]) / 1000, timezone.utc)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SourcePayloadError("invalid Bybit option ticker payload") from exc
    if not isinstance(rows, list) or len(rows) > 5000:
        raise SourcePayloadError("invalid Bybit option ticker list")
    if server_time > fetched_at + timedelta(minutes=5):
        raise SourcePayloadError("Bybit option response is dated in the future")
    as_of = min(server_time, fetched_at)
    puts = [item for row in rows if (item := _parse_put(row, base_coin=base_coin)) is not None]
    eligible = [
        item
        for item in puts
        if timedelta(days=7) <= item.expiry - as_of <= timedelta(days=60)
        and Decimal("0.70") <= item.strike / item.underlying <= Decimal("1.00")
    ]
    if len(eligible) < 2:
        return None
    expiries = sorted({item.expiry for item in eligible})
    for expiry in expiries:
        chain = [item for item in eligible if item.expiry == expiry]
        underlying = sum((item.underlying for item in chain), Decimal("0")) / len(chain)
        long_candidates = [item for item in chain if item.strike <= underlying]
        if not long_candidates:
            continue
        long_put = min(
            long_candidates,
            key=lambda item: (abs(item.strike / underlying - Decimal("0.90")), -item.strike),
        )
        short_candidates = [item for item in chain if item.strike < long_put.strike]
        if not short_candidates:
            continue
        short_put = min(
            short_candidates,
            key=lambda item: (abs(item.strike / underlying - Decimal("0.80")), -item.strike),
        )
        debit = long_put.ask - short_put.bid
        width = long_put.strike - short_put.strike
        max_gain = width - debit
        if debit <= 0 or max_gain <= 0:
            continue
        max_gain_pct = (max_gain / debit * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        quoted_size = min(long_put.ask_size, short_put.bid_size)
        quoted_oi = min(long_put.open_interest, short_put.open_interest)
        quoted_turnover = min(long_put.turnover, short_put.turnover)
        liquidity = min(
            Decimal("1"),
            Decimal("0.50")
            + min(Decimal("0.20"), quoted_size / Decimal("10"))
            + min(Decimal("0.15"), quoted_oi / Decimal("100"))
            + min(Decimal("0.15"), quoted_turnover / Decimal("100000")),
        )
        return MarketQuote(
            symbol=f"{long_put.symbol}/{short_put.symbol}",
            asset_class=AssetClass.OPTIONS,
            price=debit.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN),
            as_of=as_of,
            exposures=frozenset({"crypto"}),
            liquidity_score=liquidity.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN),
            data_quality_score=Decimal("0.85"),
            risk_score=min(Decimal("1"), debit / width).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_EVEN
            ),
            expected_move_pct=max_gain_pct,
            adverse_move_pct=Decimal("100"),
            max_age_seconds=120,
            option_risk_profile="defined_risk",
            max_loss_pct=Decimal("100"),
            max_gain_pct=max_gain_pct,
        )
    return None
