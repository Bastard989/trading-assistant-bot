from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from trading_bot.crisis_radar.event_pipeline import normalize_title
from trading_bot.crisis_radar.news import NewsItem
from trading_bot.crisis_radar.sources.base import SourcePayloadError


class GdeltDiscoveryAdapter:
    source_code = "gdelt_discovery"

    def normalize(self, payload: bytes, *, fetched_at: datetime) -> list[NewsItem]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            document = json.loads(payload.decode("utf-8-sig"))
            rows = document["articles"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourcePayloadError("invalid GDELT discovery payload") from exc
        if not isinstance(rows, list):
            raise SourcePayloadError("invalid GDELT discovery articles")
        raw_hash = hashlib.sha256(payload).hexdigest()
        result = []
        for row in rows[:250]:
            try:
                title = str(row["title"]).strip()[:300]
                url = self._safe_url(str(row["url"]))
                published_at = datetime.strptime(str(row["seendate"]), "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=timezone.utc
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid GDELT discovery article") from exc
            if not title or published_at > fetched_at:
                continue
            normalized = normalize_title(title)
            provider_id = hashlib.sha256(url.encode()).hexdigest()
            content_hash = hashlib.sha256(f"{title}\n{url}\n{published_at.isoformat()}".encode()).hexdigest()
            result.append(
                NewsItem(
                    source_code=self.source_code,
                    provider_item_id=provider_id,
                    published_at=published_at,
                    fetched_at=fetched_at,
                    title=title,
                    summary="",
                    url=url,
                    category="global discovery",
                    language="en",
                    importance="medium",
                    content_hash=content_hash,
                    publisher=str(row.get("domain") or "GDELT discovery")[:200],
                    original_language=str(row.get("language") or "unknown")[:32],
                    normalized_title=normalized,
                    dedup_hash=hashlib.sha256(normalized.encode()).hexdigest(),
                    source_tier="C",
                    evidence_excerpt=title,
                    raw_payload_hash=raw_hash,
                )
            )
        if not result:
            raise SourcePayloadError("GDELT discovery contains no valid articles")
        return sorted(result, key=lambda item: item.published_at)

    @staticmethod
    def _safe_url(value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise SourcePayloadError("GDELT article URL is not safe")
        return urlunsplit(("https", parts.netloc, parts.path or "/", parts.query, ""))[:1000]
