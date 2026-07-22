from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

from trading_bot.crisis_radar.sources.base import SourcePayloadError


NEWS_RULE_VERSION = "official-news-v1"


@dataclass(frozen=True)
class NewsItem:
    source_code: str
    provider_item_id: str
    published_at: datetime
    fetched_at: datetime
    title: str
    summary: str
    url: str
    category: str
    language: str
    importance: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.source_code or not self.provider_item_id or not self.title:
            raise ValueError("news item identifiers and title are required")
        if self.published_at.tzinfo is None or self.fetched_at.tzinfo is None:
            raise ValueError("news timestamps must be timezone-aware")
        if self.published_at > self.fetched_at + timedelta(minutes=5):
            raise ValueError("news publication time cannot be in the future")
        if self.language not in {"en", "ru"}:
            raise ValueError("unsupported news language")
        if self.importance not in {"medium", "high"}:
            raise ValueError("unsupported news importance")


@dataclass(frozen=True)
class NewsEvidence:
    scenario_code: str
    relevance_score: Decimal
    severity: str
    rule_codes: tuple[str, ...]
    explanation_ru: str
    explanation_en: str


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str, *, limit: int) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError) as exc:
        raise SourcePayloadError("invalid HTML in official RSS text") from exc
    normalized = re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()
    return normalized[:limit]


def _canonical_url(value: str, *, allowed_host: str) -> str:
    parts = urlsplit(value.strip())
    if (
        parts.scheme.lower() != "https"
        or parts.hostname != allowed_host
        or parts.username
        or parts.password
        or parts.port is not None
    ):
        raise SourcePayloadError("official RSS item has an untrusted URL")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit(("https", allowed_host, path, "", ""))


class RssAdapter:
    _HOSTS = {
        "fed_news": "www.federalreserve.gov",
        "ecb_news": "www.ecb.europa.eu",
    }

    def __init__(self, source_code: str) -> None:
        if source_code not in self._HOSTS:
            raise ValueError("unsupported official RSS source")
        self.source_code = source_code

    def normalize(self, payload: bytes, *, fetched_at: datetime) -> list[NewsItem]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        lowered = payload.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise SourcePayloadError("DTD and entities are forbidden in official RSS")
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise SourcePayloadError("invalid official RSS XML") from exc
        channel = root.find("channel") if root.tag == "rss" else None
        if channel is None:
            raise SourcePayloadError("official feed is not RSS 2.0")
        items = []
        for node in channel.findall("item")[:100]:
            item = self._normalize_item(node, fetched_at=fetched_at)
            if item is not None:
                items.append(item)
        if not items:
            raise SourcePayloadError("official RSS contains no valid items")
        return sorted(items, key=lambda item: item.published_at)

    def _normalize_item(
        self, node: ElementTree.Element, *, fetched_at: datetime
    ) -> NewsItem | None:
        title_raw = node.findtext("title") or ""
        link_raw = node.findtext("link") or ""
        guid_raw = node.findtext("guid") or link_raw
        published_raw = node.findtext("pubDate") or ""
        if not title_raw or not link_raw or not guid_raw or not published_raw:
            raise SourcePayloadError("official RSS item is missing required fields")
        try:
            published_at = parsedate_to_datetime(published_raw).astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise SourcePayloadError("invalid official RSS publication date") from exc
        if published_at > fetched_at + timedelta(minutes=5):
            return None
        title = _plain_text(title_raw, limit=300)
        summary = _plain_text(node.findtext("description") or "", limit=1200)
        if summary.casefold() == title.casefold():
            summary = ""
        category = _plain_text(node.findtext("category") or "", limit=120)
        url = _canonical_url(link_raw, allowed_host=self._HOSTS[self.source_code])
        provider_item_id = hashlib.sha256(guid_raw.strip().encode()).hexdigest()
        source_text = " ".join((title, summary, category)).casefold()
        importance = "high" if any(
            phrase in source_text
            for phrase in (
                "fomc statement",
                "monetary policy decision",
                "economic projections",
                "financial stability",
                "lending conditions tightened",
                "emergency",
                "systemic",
            )
        ) else "medium"
        content_hash = hashlib.sha256(
            "\n".join((title, summary, url, published_at.isoformat())).encode()
        ).hexdigest()
        return NewsItem(
            source_code=self.source_code,
            provider_item_id=provider_item_id,
            published_at=published_at,
            fetched_at=fetched_at,
            title=title,
            summary=summary,
            url=url,
            category=category,
            language="en",
            importance=importance,
            content_hash=content_hash,
        )


_RULES = {
    "global_recession": {
        "growth": ("economic growth", "growth and resilience", "recession", "demand"),
        "labor": ("employment", "labour market", "labor market", "unemployment"),
        "projections": ("economic projections", "forecast", "outlook"),
        "credit_conditions": ("access to finance", "lending conditions", "credit conditions"),
    },
    "financial_stress": {
        "monetary_policy": ("monetary policy", "fomc", "discount rate", "interest rate"),
        "liquidity": ("liquidity", "market functioning", "emergency lending"),
        "banking_stress": ("financial stability", "bank failure", "banking stress", "systemic"),
        "credit_conditions": ("access to finance", "lending conditions", "credit conditions"),
    },
    "oil_stagflation": {
        "energy_shock": ("energy supply", "oil supply", "oil price", "energy shock"),
        "inflation": ("inflation", "price pressures", "supply shock"),
    },
    "crypto_leverage_unwind": {
        "crypto_assets": ("crypto asset", "cryptoasset", "stablecoin", "digital asset"),
    },
    "china_hard_landing": {
        "china": ("china", "chinese economy", "renminbi", "yuan"),
    },
}

_REASONS = {
    "growth": ("рост и спрос", "growth and demand"),
    "labor": ("рынок труда", "labour market"),
    "projections": ("макроэкономический прогноз", "macroeconomic projections"),
    "credit_conditions": ("условия кредитования", "credit conditions"),
    "monetary_policy": ("денежно-кредитная политика", "monetary policy"),
    "liquidity": ("ликвидность и работа рынка", "liquidity and market functioning"),
    "banking_stress": ("устойчивость банковской системы", "banking-system resilience"),
    "energy_shock": ("энергетический шок", "energy shock"),
    "inflation": ("инфляционное давление", "inflation pressure"),
    "crypto_assets": ("криптоактивы", "crypto assets"),
    "china": ("экономика Китая", "China's economy"),
}


def classify_news(item: NewsItem) -> tuple[NewsEvidence, ...]:
    text = " ".join((item.title, item.summary, item.category)).casefold()
    urgent = any(term in text for term in ("emergency", "failure", "crisis", "severe", "systemic"))
    result = []
    for scenario_code, groups in _RULES.items():
        matched = tuple(
            rule_code
            for rule_code, phrases in groups.items()
            if any(phrase in text for phrase in phrases)
        )
        if not matched:
            continue
        relevance = min(Decimal("0.95"), Decimal("0.35") + Decimal("0.15") * len(matched))
        severity = "urgent" if urgent else "watch" if item.importance == "high" or len(matched) > 1 else "context"
        reasons_ru = ", ".join(_REASONS[code][0] for code in matched)
        reasons_en = ", ".join(_REASONS[code][1] for code in matched)
        result.append(
            NewsEvidence(
                scenario_code=scenario_code,
                relevance_score=relevance,
                severity=severity,
                rule_codes=matched,
                explanation_ru=f"Официальная публикация связана с каналами: {reasons_ru}.",
                explanation_en=f"The official release relates to: {reasons_en}.",
            )
        )
    return tuple(result)
