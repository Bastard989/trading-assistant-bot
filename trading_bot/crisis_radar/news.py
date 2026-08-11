from __future__ import annotations

import hashlib
import html
import json
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
    publisher: str = ""
    original_language: str = "en"
    normalized_title: str = ""
    dedup_hash: str = ""
    source_tier: str = "A"
    evidence_excerpt: str = ""
    raw_payload_hash: str = ""

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
        if self.source_tier not in {"A", "B", "C"}:
            raise ValueError("unsupported news source tier")


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
        raise SourcePayloadError("invalid HTML in official news text") from exc
    normalized = re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()
    return normalized[:limit]


def _canonical_url(value: str, *, allowed_hosts: tuple[str, ...]) -> str:
    parts = urlsplit(value.strip())
    if (
        parts.scheme.lower() not in {"http", "https"}
        or parts.hostname not in allowed_hosts
        or parts.username
        or parts.password
        or parts.port is not None
    ):
        raise SourcePayloadError("official news item has an untrusted URL")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit(("https", parts.hostname, path, "", ""))


class RssAdapter:
    _HOSTS = {
        "fed_news": ("www.federalreserve.gov",),
        "ecb_news": ("www.ecb.europa.eu",),
        "sec_news": ("www.sec.gov",),
        "cftc_news": ("www.cftc.gov",),
        "bis_news": ("www.bis.org",),
        "boj_news": ("www.boj.or.jp",),
        "rbi_news": ("rbi.org.in", "www.rbi.org.in"),
        "boe_news": ("www.bankofengland.co.uk",),
        "boc_news": ("www.bankofcanada.ca",),
        "rba_news": ("www.rba.gov.au",),
        "fdic_news": (
            "www.fdic.gov", "fdic.gov", "public.govdelivery.com",
            "content.govdelivery.com",
        ),
        "ofac_news": (
            "ofac.treasury.gov",
            "public.govdelivery.com",
            "content.govdelivery.com",
        ),
    }

    _PUBLISHERS = {
        "fed_news": "Federal Reserve Board",
        "ecb_news": "European Central Bank",
        "sec_news": "US Securities and Exchange Commission",
        "cftc_news": "US Commodity Futures Trading Commission",
        "bis_news": "Bank for International Settlements",
        "boj_news": "Bank of Japan",
        "rbi_news": "Reserve Bank of India",
        "boe_news": "Bank of England",
        "boc_news": "Bank of Canada",
        "rba_news": "Reserve Bank of Australia",
        "fdic_news": "Federal Deposit Insurance Corporation",
        "ofac_news": "U.S. Treasury Office of Foreign Assets Control",
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
        is_rss2 = root.tag == "rss"
        is_rss1 = root.tag == "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"
        channel = root.find("channel") if is_rss2 else root if is_rss1 else None
        if channel is None:
            raise SourcePayloadError("official feed is not supported RSS")
        nodes = (
            channel.findall("item")
            if is_rss2
            else root.findall("{http://purl.org/rss/1.0/}item")
        )
        items = []
        for node in nodes[:100]:
            item = self._normalize_item(node, fetched_at=fetched_at, rss1=is_rss1)
            if item is not None:
                items.append(item)
        if not items:
            raise SourcePayloadError("official RSS contains no valid items")
        return sorted(items, key=lambda item: item.published_at)

    def _normalize_item(
        self, node: ElementTree.Element, *, fetched_at: datetime, rss1: bool = False
    ) -> NewsItem | None:
        prefix = "{http://purl.org/rss/1.0/}" if rss1 else ""
        title_raw = node.findtext(f"{prefix}title") or ""
        link_raw = node.findtext(f"{prefix}link") or ""
        guid_raw = (node.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") if rss1 else node.findtext("guid")) or link_raw
        published_raw = (
            node.findtext("{http://purl.org/dc/elements/1.1/}date")
            if rss1
            else node.findtext("pubDate")
        ) or ""
        if not title_raw or not link_raw or not guid_raw or not published_raw:
            raise SourcePayloadError("official RSS item is missing required fields")
        try:
            published_at = (
                datetime.fromisoformat(published_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
                if rss1
                else parsedate_to_datetime(published_raw).astimezone(timezone.utc)
            )
        except (TypeError, ValueError) as exc:
            raise SourcePayloadError("invalid official RSS publication date") from exc
        if published_at > fetched_at + timedelta(minutes=5):
            return None
        title = _plain_text(title_raw, limit=300)
        summary = _plain_text(node.findtext(f"{prefix}description") or "", limit=1200)
        if summary.casefold() == title.casefold():
            summary = ""
        category = _plain_text(node.findtext(f"{prefix}category") or "", limit=120)
        url = _canonical_url(link_raw, allowed_hosts=self._HOSTS[self.source_code])
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
        normalized_title = " ".join(re.findall(r"[a-z0-9]{2,}", title.casefold()))
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
            publisher=self._PUBLISHERS[self.source_code],
            original_language="en",
            normalized_title=normalized_title,
            dedup_hash=hashlib.sha256(normalized_title.encode()).hexdigest(),
            source_tier="A",
            evidence_excerpt=(summary or title)[:600],
            raw_payload_hash=content_hash,
        )


class HkmaNewsAdapter:
    """Strict adapter for the official HKMA press-release JSON API."""

    source_code = "hkma_news"
    _HOSTS = ("www.hkma.gov.hk",)
    _PUBLISHER = "Hong Kong Monetary Authority"
    _HIGH_IMPORTANCE_TERMS = (
        "bank failure",
        "credit conditions",
        "emergency",
        "financial stability",
        "liquidity",
        "negative equity",
        "resolution",
        "reserve assets",
        "systemic",
    )

    def normalize(self, payload: bytes, *, fetched_at: datetime) -> list[NewsItem]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourcePayloadError("invalid HKMA official API JSON") from exc
        if not isinstance(document, dict):
            raise SourcePayloadError("HKMA official API root must be an object")
        header = document.get("header")
        result = document.get("result")
        if not isinstance(header, dict) or not isinstance(result, dict):
            raise SourcePayloadError("HKMA official API response is missing header or result")
        if header.get("success") is not True or header.get("err_code") != "0000":
            raise SourcePayloadError("HKMA official API reported an unsuccessful response")
        records = result.get("records")
        if not isinstance(records, list) or not records or len(records) > 100:
            raise SourcePayloadError("HKMA official API records must contain 1 to 100 items")
        declared_size = result.get("datasize")
        if not isinstance(declared_size, int) or declared_size < len(records):
            raise SourcePayloadError("HKMA official API datasize is inconsistent")

        items: list[NewsItem] = []
        seen_ids: set[str] = set()
        for record in records:
            item = self._normalize_record(record, fetched_at=fetched_at)
            if item is None:
                continue
            if item.provider_item_id in seen_ids:
                raise SourcePayloadError("HKMA official API contains duplicate records")
            seen_ids.add(item.provider_item_id)
            items.append(item)
        if not items:
            raise SourcePayloadError("HKMA official API contains no current valid items")
        return sorted(items, key=lambda item: item.published_at)

    def _normalize_record(
        self, record: object, *, fetched_at: datetime
    ) -> NewsItem | None:
        if not isinstance(record, dict):
            raise SourcePayloadError("HKMA official API record must be an object")
        title_raw = record.get("title")
        link_raw = record.get("link")
        date_raw = record.get("date")
        if not all(isinstance(value, str) and value.strip() for value in (
            title_raw,
            link_raw,
            date_raw,
        )):
            raise SourcePayloadError("HKMA official API record is missing required fields")
        try:
            published_at = datetime.strptime(date_raw, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise SourcePayloadError("invalid HKMA official API publication date") from exc
        if published_at > fetched_at + timedelta(minutes=5):
            return None

        title = _plain_text(title_raw, limit=300)
        if not title:
            raise SourcePayloadError("HKMA official API record has an empty title")
        url = _canonical_url(link_raw, allowed_hosts=self._HOSTS)
        provider_item_id = hashlib.sha256(url.encode()).hexdigest()
        normalized_title = " ".join(re.findall(r"[a-z0-9]{2,}", title.casefold()))
        source_text = title.casefold()
        importance = (
            "high"
            if any(term in source_text for term in self._HIGH_IMPORTANCE_TERMS)
            else "medium"
        )
        content_hash = hashlib.sha256(
            "\n".join((title, url, published_at.isoformat())).encode()
        ).hexdigest()
        raw_payload_hash = hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        return NewsItem(
            source_code=self.source_code,
            provider_item_id=provider_item_id,
            published_at=published_at,
            fetched_at=fetched_at,
            title=title,
            summary="",
            url=url,
            category="press_release",
            language="en",
            importance=importance,
            content_hash=content_hash,
            publisher=self._PUBLISHER,
            original_language="en",
            normalized_title=normalized_title,
            dedup_hash=hashlib.sha256(normalized_title.encode()).hexdigest(),
            source_tier="A",
            evidence_excerpt=title[:600],
            raw_payload_hash=raw_payload_hash,
        )


def normalize_official_news(
    source_code: str, payload: bytes, *, fetched_at: datetime
) -> list[NewsItem]:
    if source_code == HkmaNewsAdapter.source_code:
        return HkmaNewsAdapter().normalize(payload, fetched_at=fetched_at)
    return RssAdapter(source_code).normalize(payload, fetched_at=fetched_at)


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
        "china": (
            "china",
            "chinese economy",
            "pboc",
            "people's bank of china",
            "renminbi",
            "yuan",
        ),
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
