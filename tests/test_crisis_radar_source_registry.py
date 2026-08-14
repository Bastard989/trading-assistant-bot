from trading_bot.crisis_radar.source_registry import SOURCE_POLICIES, source_registry_payload


def test_source_registry_is_unique_licensed_and_fail_explicit() -> None:
    assert len({item.code for item in SOURCE_POLICIES}) == len(SOURCE_POLICIES)
    assert all(item.license_or_terms_url.startswith("https://") for item in SOURCE_POLICIES)
    payload = source_registry_payload()
    assert payload["version"] == "2026-08-14.2"
    assert payload["rules"]["missing_data_is_never_forward_filled_silently"] is True
    assert next(item for item in payload["sources"] if item["code"] == "world_bank")[
        "operational_role"
    ].startswith("structural")
    bis = next(item for item in payload["sources"] if item["code"] == "bis")
    assert bis["tier"] == "A"
    assert "debt-service" in bis["operational_role"]
    assert "three allowlisted" in bis["rate_limit_policy"]
    nbs = next(item for item in payload["sources"] if item["code"] == "nbs_news")
    assert nbs["tier"] == "A"
    assert "6 MB" in nbs["rate_limit_policy"]
    assert "China" in nbs["operational_role"]
    bok = next(item for item in payload["sources"] if item["code"] == "bok_news")
    assert bok["tier"] == "A"
    assert "Korea" in bok["operational_role"]
    new_york_fed = next(
        item for item in payload["sources"] if item["code"] == "new_york_fed"
    )
    assert new_york_fed["tier"] == "A"
    assert new_york_fed["production_status"] == "candidate"
    assert "disabled from live scoring" in new_york_fed["operational_role"]
    binance = next(
        item for item in payload["sources"] if item["code"] == "binance_market"
    )
    assert binance["tier"] == "B"
    assert binance["production_status"] == "candidate"
    assert binance["fallback_code"] == "bybit"
    assert "disabled from live scoring" in binance["operational_role"]
    bybit_research = next(
        item
        for item in payload["sources"]
        if item["code"] == "bybit_stablecoin_research"
    )
    assert bybit_research["production_status"] == "candidate"
    assert bybit_research["fallback_code"] == "binance_market"
    assert "isolated health" in bybit_research["operational_role"]
