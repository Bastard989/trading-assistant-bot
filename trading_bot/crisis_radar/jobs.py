from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.bybit import BybitClient
from trading_bot.crisis_radar.sources.fred_client import FredClient
from trading_bot.crisis_radar.sources.europe_clients import EcbClient, EurostatClient
from trading_bot.crisis_radar.sources.global_clients import BisClient, OecdClient, WorldBankClient
from trading_bot.crisis_radar.sources.official_clients import BeaClient, EiaClient
from trading_bot.crisis_radar.sources.news_clients import (
    GdeltDiscoveryClient,
    news_client_for,
)
from trading_bot.crisis_radar.sources.new_york_fed import NewYorkFedClient
from trading_bot.crisis_radar.sources.stablecoins import BinanceMarketClient


logger = logging.getLogger(__name__)


class CrisisRadarJobs:
    def __init__(
        self,
        service: CrisisRadarService,
        *,
        fred_api_key: str,
        bea_api_key: str = "",
        eia_api_key: str = "",
        alert_user_ids: tuple[int, ...] = (),
        business_timezone: str = "Europe/Moscow",
    ) -> None:
        self.service = service
        self.fred_api_key = fred_api_key.strip()
        self.bea_api_key = bea_api_key.strip()
        self.eia_api_key = eia_api_key.strip()
        self.alert_user_ids = tuple(sorted({int(item) for item in alert_user_ids if int(item) > 0}))
        self.business_timezone = ZoneInfo(business_timezone)
        self._sync_lock = asyncio.Lock()

    def register(self, application, *, interval_seconds: int) -> bool:
        self.service.bootstrap()
        if interval_seconds < 300:
            raise ValueError("Crisis Radar sync interval must be at least 300 seconds")
        application.job_queue.run_repeating(
            self.sync,
            interval=interval_seconds,
            first=15,
            name="crisis-radar-official-sync",
        )
        application.job_queue.run_repeating(
            self.sync_news_feeds,
            interval=900,
            first=45,
            name="crisis-radar-news-sync",
        )
        application.job_queue.run_daily(
            self.sync_global,
            time=time(3, 20, tzinfo=self.business_timezone),
            days=tuple(range(7)),
            name="crisis-radar-global-daily-sync",
        )
        application.job_queue.run_daily(
            self.summary,
            time=time(22, 30, tzinfo=self.business_timezone),
            days=(3,),
            data={"report_type": "midweek"},
            name="crisis-radar-midweek-summary",
        )
        application.job_queue.run_daily(
            self.summary,
            time=time(12, 0, tzinfo=self.business_timezone),
            days=(6,),
            data={"report_type": "weekend"},
            name="crisis-radar-weekend-summary",
        )
        return True

    async def sync(self, context) -> None:
        if self._sync_lock.locked():
            logger.info("Crisis Radar sync skipped because the previous run is active")
            return
        async with self._sync_lock:
            try:
                results = {}
                if self.fred_api_key:
                    fred_client = FredClient(self.fred_api_key)
                    results["fred"] = await self.service.sync_fred(
                        fred_client, recompute_after=False
                    )
                    results["fred_calendar"] = await self.service.sync_fred_calendar(fred_client)
                if self.bea_api_key:
                    results["bea"] = await self.service.sync_bea(
                        BeaClient(self.bea_api_key), recompute_after=False
                    )
                if self.eia_api_key:
                    results["eia"] = await self.service.sync_eia(
                        EiaClient(self.eia_api_key), recompute_after=False
                    )
                results["ecb"] = await self.service.sync_ecb(EcbClient(), recompute_after=False)
                results["eurostat"] = await self.service.sync_eurostat(
                    EurostatClient(), recompute_after=False
                )
                results["bybit"] = await self.service.sync_bybit(
                    BybitClient(), recompute_after=False
                )
                if bool(
                    getattr(
                        getattr(self.service, "feature_flags", None),
                        "scoring_v11",
                        False,
                    )
                ):
                    results["bybit_stablecoin_research"] = (
                        await self.service.sync_bybit_stablecoin(
                            BybitClient(), recompute_after=False
                        )
                    )
                    results["binance_market"] = (
                        await self.service.sync_binance_stablecoin(
                            BinanceMarketClient(), recompute_after=False
                        )
                    )
                self.service.recompute()
                if self.alert_user_ids:
                    self.service.repository.enqueue_alert_deliveries(self.alert_user_ids)
                    self.service.repository.enqueue_data_health_deliveries(self.alert_user_ids)
                    await self._deliver_alerts(context)
                    await self._deliver_data_health_alerts(context)
                    await self._deliver_reports(context)
                logger.info("Crisis Radar official sync completed: sources=%s", sorted(results))
            except Exception:
                logger.exception("Crisis Radar scheduled sync failed")

    async def sync_global(self, context) -> None:
        if self._sync_lock.locked():
            logger.info("Crisis Radar global sync skipped because another run is active")
            return
        async with self._sync_lock:
            try:
                results = {
                    "world_bank": await self.service.sync_world_bank(
                        WorldBankClient(), recompute_after=False
                    ),
                    "bis": await self.service.sync_bis(BisClient(), recompute_after=False),
                    "oecd": await self.service.sync_oecd(OecdClient(), recompute_after=False),
                }
                if bool(
                    getattr(
                        getattr(self.service, "feature_flags", None),
                        "scoring_v11",
                        False,
                    )
                ):
                    results["new_york_fed"] = await self.service.sync_new_york_fed(
                        NewYorkFedClient(), recompute_after=False
                    )
                    results["oecd_labour_research"] = (
                        await self.service.sync_oecd_labour(
                            OecdClient(), recompute_after=False
                        )
                    )
                self.service.recompute()
                if self.alert_user_ids:
                    self.service.repository.enqueue_alert_deliveries(self.alert_user_ids)
                    self.service.repository.enqueue_data_health_deliveries(self.alert_user_ids)
                    await self._deliver_alerts(context)
                    await self._deliver_data_health_alerts(context)
                logger.info("Crisis Radar global sync completed: sources=%s", sorted(results))
            except Exception:
                logger.exception("Crisis Radar global scheduled sync failed")

    async def sync_news_feeds(self, context) -> None:
        if self._sync_lock.locked():
            logger.info("Crisis Radar news sync skipped because another run is active")
            return
        async with self._sync_lock:
            try:
                news_events_v2 = bool(
                    getattr(getattr(self.service, "feature_flags", None), "news_events_v2", False)
                )
                source_codes = (
                    (
                        "fed_news", "ecb_news", "sec_news", "cftc_news",
                        "bis_news", "boj_news", "rbi_news",
                        "boe_news", "boc_news", "fdic_news", "hkma_news",
                        "nbs_news", "bok_news", "ofac_news",
                    )
                    if news_events_v2
                    else ("fed_news", "ecb_news")
                )
                results = {
                    source_code: await self.service.sync_news(news_client_for(source_code))
                    for source_code in source_codes
                }
                if news_events_v2:
                    results["gdelt_discovery"] = await self.service.sync_gdelt_discovery(
                        GdeltDiscoveryClient()
                    )
                logger.info("Crisis Radar news sync completed: sources=%s", sorted(results))
            except Exception:
                logger.exception("Crisis Radar news scheduled sync failed")

    async def summary(self, context) -> None:
        report_type = getattr(getattr(context, "job", None), "data", {}).get(
            "report_type", "midweek"
        )
        if report_type not in {"midweek", "weekend"}:
            logger.error("Crisis Radar ignored unknown summary type: %s", report_type)
            return
        local_date = datetime.now(self.business_timezone).date()
        overview = self.service.overview(locale="ru")
        calendar = self.service.calendar(locale="ru", days=15, as_of=local_date)
        news = self.service.news(locale="ru", days=7, limit=5)
        payload = {
            "stage": overview.get("stage", "unknown"),
            "as_of": overview.get("as_of"),
            "explanation": overview.get("explanation", ""),
            "breadth": overview.get("breadth", {}),
            "scenarios": overview.get("scenarios", []),
            "calendar": calendar.get("events", [])[:5],
            "news": news.get("items", [])[:5],
        }
        self.service.repository.enqueue_report_deliveries(
            report_key=f"{report_type}:{local_date.isoformat()}",
            report_type=report_type,
            report_date=local_date,
            payload=payload,
            user_ids=self.alert_user_ids,
        )
        await self._deliver_reports(context)

    async def _deliver_alerts(self, context) -> None:
        if context is None or getattr(context, "bot", None) is None:
            logger.warning("Crisis Radar alerts are queued, but Telegram context is unavailable")
            return
        names = {
            "global_recession": "Глобальное замедление / рецессия",
            "financial_stress": "Системный финансовый стресс",
            "oil_stagflation": "Нефтяной инфляционный шок",
            "crypto_leverage_unwind": "Криптовалютный сброс плечей",
            "china_hard_landing": "Резкое замедление экономики Китая",
            "regional_recession": "Региональная рецессия",
            "banking_crisis": "Банковский кризис",
            "sovereign_currency_crisis": "Суверенный / валютный кризис",
            "commodity_supply_shock": "Сырьевой / логистический шок",
            "tech_ai_repricing": "Переоценка технологического / AI-сектора",
            "exchange_stablecoin_failure": "Крах биржи / стейблкоина",
        }
        for delivery in self.service.repository.pending_alert_deliveries():
            payload = delivery.payload
            explanation = payload.get("explanation", {}).get("ru", "")
            direction = "УСИЛЕНИЕ" if delivery.event_type == "scenario_escalation" else "ВОССТАНОВЛЕНИЕ"
            message = (
                f"Crisis Radar · {direction}\n"
                f"Сценарий: {names.get(delivery.scenario_code, delivery.scenario_code)}\n"
                f"Статус: {delivery.from_state} → {delivery.to_state}\n"
                f"Горизонт: {payload.get('horizon', '—')}\n"
                f"Изменившие статус каналы: "
                f"{', '.join(item.get('group_code', '—') for item in payload.get('evidence', [])) or '—'}\n\n"
                f"{explanation}\n\n"
                "Вывод отменяется, если сценарий вернётся ниже повышенного уровня "
                "или обязательные данные станут недоступны. Это аналитика, сделка не открывается."
            )
            try:
                await context.bot.send_message(chat_id=delivery.user_id, text=message)
                self.service.repository.mark_alert_sent(
                    delivery.delivery_id, sent_at=datetime.now(timezone.utc)
                )
            except Exception as exc:
                logger.warning("Crisis Radar Telegram delivery failed: %s", type(exc).__name__)
                self.service.repository.mark_alert_failed(
                    delivery.delivery_id,
                    error=type(exc).__name__,
                    retry_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                )

    async def _deliver_data_health_alerts(self, context) -> None:
        if context is None or getattr(context, "bot", None) is None:
            logger.warning("Crisis Radar data-health alerts are queued; Telegram is unavailable")
            return
        labels = {
            "healthy": "данные восстановлены",
            "degraded": "данные частично недоступны",
            "insufficient_data": "недостаточно данных для оценки рынка",
        }
        for delivery in self.service.repository.pending_data_health_deliveries():
            payload = delivery.payload
            missing = [*payload.get("missing_regions", []), *payload.get("missing_groups", [])]
            message = (
                "Crisis Radar · СОСТОЯНИЕ ДАННЫХ\n"
                f"{labels.get(delivery.from_status, delivery.from_status)} → "
                f"{labels.get(delivery.to_status, delivery.to_status)}\n"
                f"Покрытие: {payload.get('ratio', '—')}\n"
                f"Недоступно: {', '.join(missing) or '—'}\n"
                "Это техническое предупреждение, а не рыночный сигнал. "
                "При недостатке данных радар скрывает спокойную/кризисную стадию."
            )
            try:
                await context.bot.send_message(chat_id=delivery.user_id, text=message[:4096])
                self.service.repository.mark_data_health_sent(
                    delivery.delivery_id, sent_at=datetime.now(timezone.utc)
                )
            except Exception as exc:
                logger.warning("Crisis Radar data-health delivery failed: %s", type(exc).__name__)
                self.service.repository.mark_data_health_failed(
                    delivery.delivery_id,
                    error=type(exc).__name__,
                    retry_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                )

    async def _deliver_reports(self, context) -> None:
        if context is None or getattr(context, "bot", None) is None:
            logger.warning("Crisis Radar reports are queued, but Telegram context is unavailable")
            return
        stage_names = {
            "stable": "СТАБИЛЬНОСТЬ",
            "tension": "НАПРЯЖЕНИЕ",
            "warning": "ПРЕДУПРЕЖДЕНИЕ",
            "confirmation": "ПОДТВЕРЖДЕНИЕ",
            "crisis": "КРИЗИС",
            "unknown": "НЕТ ДАННЫХ",
        }
        report_names = {"midweek": "сводка за середину недели", "weekend": "итоги недели"}
        status_names = {
            "inactive": "не активен",
            "watch": "наблюдение",
            "elevated": "повышенный",
            "confirmed": "подтверждён",
        }
        for delivery in self.service.repository.pending_report_deliveries():
            payload = delivery.payload
            breadth = payload.get("breadth", {})
            scenario_lines = [
                f"• {item.get('name', item.get('code', '—'))}: "
                f"{status_names.get(item.get('status'), item.get('status', '—'))}"
                for item in payload.get("scenarios", [])
                if item.get("status") != "inactive"
            ] or ["• активных сценариев нет"]
            calendar_lines = [
                f"• {item.get('release_date', '—')} — {item.get('release_name', '—')}"
                for item in payload.get("calendar", [])[:3]
            ] or ["• подтверждённых дат пока нет"]
            news_lines = [
                f"• {item.get('source', {}).get('name', 'Источник')}: "
                f"{item.get('title', '—')}"
                for item in payload.get("news", [])[:3]
            ] or ["• новых официальных публикаций по сценариям нет"]
            message = (
                f"Crisis Radar · {report_names.get(delivery.report_type, delivery.report_type)}\n"
                f"Стадия рынка: {stage_names.get(payload.get('stage'), 'НЕТ ДАННЫХ')}\n"
                f"Ухудшается групп: {breadth.get('active', 0)} · "
                f"опасных: {breadth.get('danger_or_worse', 0)} · "
                f"критических: {breadth.get('critical', 0)}\n\n"
                f"{payload.get('explanation') or 'Недостаточно данных для объяснения.'}\n\n"
                f"Сценарии:\n{chr(10).join(scenario_lines)}\n\n"
                f"Ближайшие публикации (время уточняется источником):\n"
                f"{chr(10).join(calendar_lines)}\n\n"
                f"Официальный новостной контекст:\n{chr(10).join(news_lines)}"
            )
            try:
                await context.bot.send_message(chat_id=delivery.user_id, text=message[:4096])
                self.service.repository.mark_report_sent(
                    delivery.delivery_id, sent_at=datetime.now(timezone.utc)
                )
            except Exception as exc:
                logger.warning("Crisis Radar report delivery failed: %s", type(exc).__name__)
                self.service.repository.mark_report_failed(
                    delivery.delivery_id,
                    error=type(exc).__name__,
                    retry_at=datetime.now(timezone.utc) + timedelta(minutes=15),
                )
