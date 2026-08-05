import sqlite3

import pytest

from trading_bot.db import CURRENT_SCHEMA_VERSION, Database


EXPECTED_TABLES = {
    "cr_sources",
    "cr_methodology_versions",
    "cr_indicator_definitions",
    "cr_threshold_sets",
    "cr_sync_runs",
    "cr_observations",
    "cr_observation_revisions",
    "cr_indicator_states",
    "cr_group_states",
    "cr_market_snapshots",
    "cr_scenario_definitions",
    "cr_scenario_states",
    "cr_alert_events",
    "cr_alert_deliveries",
    "cr_release_events",
    "cr_report_deliveries",
    "cr_news_items",
    "cr_news_evidence",
    "cr_agent_threads",
    "cr_agent_messages",
    "cr_backtest_runs",
    "cr_backtest_predictions",
    "cr_calibration_bins",
    "cr_event_catalog_versions",
    "cr_event_labels",
    "cr_replay_runs",
    "cr_replay_signals",
    "cr_backtest_provenance",
    "cr_event_clusters",
    "cr_event_evidence",
    "cr_indicator_features",
    "cr_contagion_features",
    "cr_scenario_fusion_states",
    "cr_data_health_events",
    "cr_data_health_deliveries",
}


def _seed_indicator(connection: sqlite3.Connection) -> tuple[int, int, int]:
    source_id = connection.execute(
        "INSERT INTO cr_sources(code, name) VALUES ('fred', 'FRED') RETURNING id"
    ).fetchone()[0]
    methodology_id = connection.execute(
        """
        INSERT INTO cr_methodology_versions(code, version, checksum, effective_from)
        VALUES ('starter', 'v1', 'checksum', '2026-07-20T00:00:00+00:00')
        RETURNING id
        """
    ).fetchone()[0]
    indicator_id = connection.execute(
        """
        INSERT INTO cr_indicator_definitions(
            code, name, group_code, unit, frequency, risk_direction, source_id
        ) VALUES ('us_hy_oas', 'US HY OAS', 'credit', 'percent', 'daily',
                  'higher_is_worse', ?)
        RETURNING id
        """,
        (source_id,),
    ).fetchone()[0]
    return source_id, methodology_id, indicator_id


def test_crisis_radar_foundation_schema_is_created_and_versioned(tmp_path) -> None:
    database = Database(tmp_path / "crisis-radar.sqlite3")
    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cr_%'"
            )
        }
        version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]

    assert tables == EXPECTED_TABLES
    assert version == CURRENT_SCHEMA_VERSION
    with database.connect() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(cr_indicator_states)")
        }
    assert {"raw_band", "held_by_hysteresis", "confirmation_required"} <= columns
    with database.connect() as connection:
        agent_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(cr_agent_messages)")
        }
    assert {"grounded", "grounding_payload"} <= agent_columns


def test_observation_ingest_is_idempotent_and_vintages_are_preserved(tmp_path) -> None:
    database = Database(tmp_path / "observations.sqlite3")
    with database.connect() as connection:
        source_id, _, indicator_id = _seed_indicator(connection)
        values = (
            indicator_id,
            source_id,
            "2026-06-01",
            "2026-07-01T12:00:00+00:00",
            "2026-07-01T12:01:00+00:00",
            "2.71",
            "percent",
            "2026-07-01",
        )
        connection.execute(
            """
            INSERT INTO cr_observations(
                indicator_id, source_id, observed_at, released_at, fetched_at,
                value_text, unit, vintage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO cr_observations(
                    indicator_id, source_id, observed_at, released_at, fetched_at,
                    value_text, unit, vintage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        connection.execute(
            """
            INSERT INTO cr_observations(
                indicator_id, source_id, observed_at, released_at, fetched_at,
                value_text, unit, vintage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*values[:5], "2.95", values[6], "2026-07-15"),
        )
        count = connection.execute("SELECT count(*) FROM cr_observations").fetchone()[0]

    assert count == 2


def test_system_and_personal_thresholds_can_coexist(tmp_path) -> None:
    database = Database(tmp_path / "thresholds.sqlite3")
    with database.connect() as connection:
        _, methodology_id, indicator_id = _seed_indicator(connection)
        base_values = (indicator_id, methodology_id, "4.5", "6", "8")
        connection.execute(
            """
            INSERT INTO cr_threshold_sets(
                indicator_id, methodology_id, warning_value, danger_value, critical_value
            ) VALUES (?, ?, ?, ?, ?)
            """,
            base_values,
        )
        connection.execute(
            """
            INSERT INTO cr_threshold_sets(
                indicator_id, methodology_id, scope, owner_user_id,
                warning_value, danger_value, critical_value
            ) VALUES (?, ?, 'personal', 42, ?, ?, ?)
            """,
            base_values,
        )
        rows = connection.execute(
            "SELECT scope, owner_user_id FROM cr_threshold_sets ORDER BY owner_user_id"
        ).fetchall()

    assert [tuple(row) for row in rows] == [("system", 0), ("personal", 42)]
