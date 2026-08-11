from __future__ import annotations


def asset_classes_for_symbol(symbol: str) -> frozenset[str]:
    normalized = symbol.upper().replace("/", "").replace("-", "")
    classes = {"CRYPTO"}
    if normalized.startswith("BTC"):
        classes.update({"BTC", "MAJOR_SPOT_ASSETS"})
    elif normalized.startswith("ETH"):
        classes.update({"ETH", "MAJOR_SPOT_ASSETS"})
    else:
        classes.add("ALTCOINS")
    return frozenset(classes)


def build_exposure_overlay(
    trades: tuple[dict, ...],
    scenarios: tuple[dict, ...],
) -> dict:
    active = tuple(
        scenario
        for scenario in scenarios
        if scenario.get("status") in {
            "watch", "elevated", "confirmed", "recovery_watch", "recovery_confirmed"
        }
    )
    items = []
    directional_counts: dict[str, int] = {}
    for trade in trades:
        classes = asset_classes_for_symbol(str(trade["symbol"]))
        conflicts = []
        aligned = []
        for scenario in active:
            vulnerable = classes & set(scenario.get("vulnerable_assets") or ())
            beneficiaries = classes & set(scenario.get("possible_beneficiaries") or ())
            side = str(trade["side"])
            if (side == "long" and vulnerable) or (side == "short" and beneficiaries):
                conflicts.append(str(scenario["code"]))
            elif (side == "short" and vulnerable) or (side == "long" and beneficiaries):
                aligned.append(str(scenario["code"]))
        key = f"{trade['side']}:{next(iter(sorted(classes)))}"
        directional_counts[key] = directional_counts.get(key, 0) + 1
        leverage = float(trade.get("leverage") or 1)
        items.append(
            {
                "trade_id": int(trade["id"]),
                "symbol": trade["symbol"],
                "side": trade["side"],
                "leverage": leverage,
                "asset_classes": sorted(classes),
                "conflicting_scenarios": conflicts,
                "aligned_scenarios": aligned,
                "leverage_vulnerability": (
                    "high" if leverage >= 10 and conflicts
                    else "elevated" if leverage >= 3 and conflicts
                    else "normal"
                ),
                "assessment": (
                    "conflict" if conflicts else "aligned" if aligned else "unclassified"
                ),
            }
        )
    concentration = [
        {"direction_class": key, "open_trade_count": count}
        for key, count in sorted(directional_counts.items())
        if count >= 2
    ]
    return {
        "read_only": True,
        "trade_mutations": False,
        "items": items,
        "concentration": concentration,
        "limitations": (
            "Class mapping is deterministic and coarse; it does not estimate portfolio VaR or promise a hedge result."
        ),
    }
