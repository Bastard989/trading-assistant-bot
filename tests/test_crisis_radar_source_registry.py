from trading_bot.crisis_radar.source_registry import SOURCE_POLICIES, source_registry_payload


def test_source_registry_is_unique_licensed_and_fail_explicit() -> None:
    assert len({item.code for item in SOURCE_POLICIES}) == len(SOURCE_POLICIES)
    assert all(item.license_or_terms_url.startswith("https://") for item in SOURCE_POLICIES)
    payload = source_registry_payload()
    assert payload["rules"]["missing_data_is_never_forward_filled_silently"] is True
    assert next(item for item in payload["sources"] if item["code"] == "world_bank")[
        "operational_role"
    ].startswith("structural")
