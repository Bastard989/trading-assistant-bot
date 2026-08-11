from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal

from trading_bot.crisis_radar.news import NewsItem


EVENT_RULE_VERSION = "global-events-v1"

_WORDS = re.compile(r"[a-zа-яё一-鿿0-9]{2,}", re.IGNORECASE)
_PROMPT_INJECTION = (
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "developer message",
    "tool call",
    "игнорируй предыдущие инструкции",
)

_TAXONOMY = {
    "bankruptcy": ("bankruptcy", "insolvency", "chapter 11", "банкрот"),
    "default": ("sovereign default", "debt default", "missed payment", "дефолт"),
    "bank_run": ("bank run", "deposit flight", "withdrawal limits", "набег на банк"),
    "emergency_liquidity": (
        "emergency liquidity", "emergency lending", "liquidity assistance", "экстренная ликвидность"
    ),
    "sanctions": ("sanction", "export control", "asset freeze", "санкц"),
    "armed_conflict": ("war ", "armed conflict", "military strike", "invasion", "войн", "удар"),
    "supply_disruption": (
        "supply disruption", "shipping disruption", "port closure", "blockade", "перебои поставок"
    ),
    "commodity_shock": ("oil shock", "gas supply", "food shock", "commodity shock", "сырьевой шок"),
    "regulatory_restriction": (
        "trading suspension", "regulatory ban", "withdrawal ban", "prohibition", "запрет торгов"
    ),
    "cyber_exchange_failure": (
        "exchange hack", "cyberattack", "trading outage", "exchange failure", "взлом биржи"
    ),
    "stablecoin_failure": ("stablecoin depeg", "stablecoin collapse", "reserve shortfall", "депег"),
    "recession_signal": ("recession", "sharp contraction", "economic crisis", "рецесс", "экономический кризис"),
}

_REGIONS = {
    "US": ("united states", "u.s.", "federal reserve", "sec ", "cftc"),
    "EU": ("euro area", "eurozone", "european central bank", "ecb"),
    "GBR": ("united kingdom", "bank of england", "britain"),
    "CHN": ("china", "chinese", "beijing", "hong kong"),
    "JPN": ("japan", "japanese", "bank of japan"),
    "KOR": ("south korea", "korean"),
    "IND": ("india", "indian", "reserve bank of india"),
    "BRA": ("brazil", "brazilian"),
    "MEX": ("mexico", "mexican"),
    "GLOBAL": ("global", "world economy", "international monetary fund", "bis"),
    "CRYPTO": ("crypto", "bitcoin", "ethereum", "stablecoin", "digital asset"),
}

_ASSETS = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "OIL": ("oil", "brent", "wti"),
    "GOLD": ("gold",),
    "USD": ("dollar", "usd"),
    "BANKS": ("bank", "deposit", "lender"),
    "EQUITIES": ("stock market", "equities", "shares"),
    "CREDIT": ("bond", "credit", "debt"),
}


@dataclass(frozen=True)
class EventCandidate:
    taxonomy: str
    title: str
    regions: tuple[str, ...]
    entities: tuple[str, ...]
    assets: tuple[str, ...]
    impact_direction: str
    horizon: str
    severity_score: Decimal
    evidence_excerpt: str
    source_tier: str
    injection_detected: bool
    normalized_title: str
    dedup_hash: str
    event_key: str


def normalize_title(value: str) -> str:
    stopwords = {"a", "an", "the", "by", "after", "as", "of", "in", "on"}
    tokens = []
    for token in _WORDS.findall(value.casefold()):
        if token in stopwords:
            continue
        if len(token) > 5 and token.endswith("ed"):
            token = token[:-2]
        elif len(token) > 5 and token.endswith("s"):
            token = token[:-1]
        tokens.append(token)
    return " ".join(tokens)[:500]


def near_duplicate_score(left: str, right: str) -> Decimal:
    left_tokens = set(normalize_title(left).split())
    right_tokens = set(normalize_title(right).split())
    if not left_tokens or not right_tokens:
        return Decimal("0")
    return Decimal(len(left_tokens & right_tokens)) / Decimal(len(left_tokens | right_tokens))


def extract_event_candidate(item: NewsItem) -> EventCandidate | None:
    text = " ".join((item.title, item.summary, item.category)).casefold()
    matches = [
        (taxonomy, sum(text.count(phrase) for phrase in phrases))
        for taxonomy, phrases in _TAXONOMY.items()
        if any(phrase in text for phrase in phrases)
    ]
    if not matches:
        return None
    taxonomy, match_count = max(matches, key=lambda pair: (pair[1], pair[0]))
    regions = tuple(sorted(code for code, phrases in _REGIONS.items() if any(p in text for p in phrases)))
    assets = tuple(sorted(code for code, phrases in _ASSETS.items() if any(p in text for p in phrases)))
    entities = tuple(sorted(set(regions + assets)))
    normalized = normalize_title(item.title)
    dedup_hash = hashlib.sha256(normalized.encode()).hexdigest()
    day = item.published_at.astimezone(timezone.utc).date().isoformat()
    event_basis = "|".join((taxonomy, ",".join(regions), ",".join(assets), normalized, day))
    event_key = hashlib.sha256(event_basis.encode()).hexdigest()
    severity = Decimal("0.45") + min(Decimal("0.35"), Decimal(match_count) * Decimal("0.08"))
    if any(term in text for term in ("emergency", "systemic", "collapse", "severe", "экстрен")):
        severity = min(Decimal("1"), severity + Decimal("0.2"))
    excerpt = (item.summary or item.title).strip()[:600]
    injection = any(phrase in text for phrase in _PROMPT_INJECTION)
    return EventCandidate(
        taxonomy=taxonomy,
        title=item.title,
        regions=regions or ("GLOBAL",),
        entities=entities,
        assets=assets,
        impact_direction="negative",
        horizon="immediate" if severity >= Decimal("0.75") else "days_to_months",
        severity_score=severity,
        evidence_excerpt=excerpt,
        source_tier=item.source_tier,
        injection_detected=injection,
        normalized_title=normalized,
        dedup_hash=dedup_hash,
        event_key=event_key,
    )


def event_score(
    *,
    severity: Decimal,
    source_tier: str,
    source_count: int,
    official_source_count: int,
    age_hours: Decimal = Decimal("0"),
    half_life_hours: Decimal = Decimal("72"),
) -> Decimal:
    if half_life_hours <= 0:
        raise ValueError("event half-life must be positive")
    source_quality = {"A": Decimal("1"), "B": Decimal("0.8"), "C": Decimal("0.45")}[source_tier]
    corroboration = min(Decimal("1"), Decimal("0.45") + Decimal("0.25") * source_count)
    if official_source_count:
        corroboration = max(corroboration, Decimal("0.75"))
    time_decay = Decimal("1") / (
        Decimal("1") + max(age_hours, Decimal("0")) / half_life_hours
    )
    return (severity * source_quality * corroboration * time_decay).quantize(Decimal("0.0001"))
