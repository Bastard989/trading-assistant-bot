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
    "忽略之前的指令",
    "忽略所有指令",
    "系统提示",
)

_TAXONOMY = {
    "bankruptcy": ("bankruptcy", "insolvency", "chapter 11", "банкрот", "破产"),
    "default": (
        "sovereign default", "debt default", "missed payment", "дефолт",
        "债务违约", "主权违约",
    ),
    "bank_run": (
        "bank run", "deposit flight", "withdrawal limits", "набег на банк",
        "银行挤兑", "存款外流", "提现限制",
    ),
    "emergency_liquidity": (
        "emergency liquidity", "emergency lending", "liquidity assistance",
        "экстренная ликвидность", "紧急流动性", "紧急贷款",
    ),
    "sanctions": (
        "sanction", "export control", "asset freeze", "санкц",
        "制裁", "出口管制", "资产冻结",
    ),
    "armed_conflict": (
        "war ", "armed conflict", "military strike", "invasion", "войн", "удар",
        "战争", "武装冲突", "军事打击", "入侵",
    ),
    "supply_disruption": (
        "supply disruption", "shipping disruption", "port closure", "blockade",
        "перебои поставок", "供应中断", "航运中断", "港口关闭", "封锁",
    ),
    "commodity_shock": (
        "oil shock", "gas supply", "food shock", "commodity shock", "сырьевой шок",
        "油价冲击", "天然气供应", "食品价格冲击", "大宗商品冲击",
    ),
    "regulatory_restriction": (
        "trading suspension", "regulatory ban", "withdrawal ban", "prohibition",
        "запрет торгов", "暂停交易", "监管禁令", "禁止提款",
    ),
    "cyber_exchange_failure": (
        "exchange hack", "cyberattack", "trading outage", "exchange failure", "взлом биржи",
        "交易所黑客", "网络攻击", "交易中断", "交易所故障",
    ),
    "stablecoin_failure": (
        "stablecoin depeg", "stablecoin collapse", "reserve shortfall", "депег",
        "稳定币脱锚", "稳定币崩溃", "储备不足",
    ),
    "recession_signal": (
        "recession", "sharp contraction", "economic crisis", "рецесс", "экономический кризис",
        "经济衰退", "经济大幅收缩", "经济危机",
    ),
}

_REGIONS = {
    "US": ("united states", "u.s.", "federal reserve", "sec ", "cftc"),
    "EU": ("euro area", "eurozone", "european central bank", "ecb"),
    "GBR": ("united kingdom", "bank of england", "britain"),
    "CHN": (
        "china", "chinese", "beijing", "hong kong", "中国", "全国",
        "北京", "香港", "国家统计局", "中国人民银行",
    ),
    "JPN": ("japan", "japanese", "bank of japan"),
    "KOR": ("south korea", "korean"),
    "IND": ("india", "indian", "reserve bank of india"),
    "BRA": ("brazil", "brazilian"),
    "MEX": ("mexico", "mexican"),
    "GLOBAL": ("global", "world economy", "international monetary fund", "bis"),
    "CRYPTO": ("crypto", "bitcoin", "ethereum", "stablecoin", "digital asset"),
}

_SOURCE_REGIONS = {
    "nbs_news": "CHN",
    "bok_news": "KOR",
    "hkma_news": "HKG",
}

# Statistical/central-bank releases often mention external shocks as background.
# For these feeds the event itself must be explicit in the headline; otherwise the
# release remains scenario context and cannot create an event candidate.
_TITLE_GROUNDED_EVENT_SOURCES = frozenset({"nbs_news", "bok_news"})

_ASSETS = {
    "BTC": ("bitcoin", "btc", "比特币"),
    "ETH": ("ethereum", "ether", "eth", "以太坊"),
    "OIL": ("oil", "brent", "wti", "石油", "原油"),
    "GOLD": ("gold", "黄金"),
    "USD": ("dollar", "usd", "美元"),
    "BANKS": ("bank", "deposit", "lender", "银行", "存款"),
    "EQUITIES": ("stock market", "equities", "shares", "股市", "股票"),
    "CREDIT": ("bond", "credit", "debt", "债券", "信贷", "债务"),
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
    if item.source_code in _TITLE_GROUNDED_EVENT_SOURCES:
        title_text = item.title.casefold()
        if not any(phrase in title_text for phrase in _TAXONOMY[taxonomy]):
            return None
    region_codes = {
        code for code, phrases in _REGIONS.items() if any(p in text for p in phrases)
    }
    if item.source_code in _SOURCE_REGIONS:
        region_codes.add(_SOURCE_REGIONS[item.source_code])
    regions = tuple(sorted(region_codes))
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
