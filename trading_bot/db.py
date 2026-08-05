from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


CURRENT_SCHEMA_VERSION = 20


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cr_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    terms_url TEXT NOT NULL DEFAULT '',
    access_type TEXT NOT NULL DEFAULT 'api',
    expected_frequency TEXT NOT NULL DEFAULT '',
    max_staleness_seconds INTEGER NOT NULL DEFAULT 86400 CHECK(max_staleness_seconds > 0),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cr_methodology_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    version TEXT NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'retired')),
    effective_from TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, version)
);

CREATE TABLE IF NOT EXISTS cr_indicator_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    group_code TEXT NOT NULL,
    region_code TEXT NOT NULL DEFAULT 'GLOBAL',
    unit TEXT NOT NULL,
    frequency TEXT NOT NULL,
    risk_direction TEXT NOT NULL CHECK(
        risk_direction IN ('higher_is_worse', 'lower_is_worse', 'two_sided')
    ),
    source_id INTEGER NOT NULL,
    provider_series_id TEXT NOT NULL DEFAULT '',
    transform TEXT NOT NULL DEFAULT 'identity',
    max_staleness_seconds INTEGER NOT NULL DEFAULT 86400 CHECK(max_staleness_seconds > 0),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(source_id) REFERENCES cr_sources(id)
);

CREATE TABLE IF NOT EXISTS cr_threshold_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id INTEGER NOT NULL,
    methodology_id INTEGER NOT NULL,
    scope TEXT NOT NULL DEFAULT 'system' CHECK(scope IN ('system', 'personal')),
    owner_user_id INTEGER NOT NULL DEFAULT 0,
    warning_value TEXT NOT NULL,
    danger_value TEXT NOT NULL,
    critical_value TEXT NOT NULL,
    reference_value TEXT NOT NULL DEFAULT '0',
    momentum_rule TEXT NOT NULL DEFAULT '{}',
    persistence_rule TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(indicator_id, methodology_id, scope, owner_user_id),
    FOREIGN KEY(indicator_id) REFERENCES cr_indicator_definitions(id),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'partial', 'failed')),
    cursor TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '',
    rows_fetched INTEGER NOT NULL DEFAULT 0 CHECK(rows_fetched >= 0),
    rows_written INTEGER NOT NULL DEFAULT 0 CHECK(rows_written >= 0),
    error_code TEXT NOT NULL DEFAULT '',
    error_detail TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(source_id) REFERENCES cr_sources(id)
);

CREATE TABLE IF NOT EXISTS cr_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    released_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    value_text TEXT NOT NULL,
    unit TEXT NOT NULL,
    vintage TEXT NOT NULL DEFAULT '',
    quality_flags TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL DEFAULT '',
    sync_run_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(indicator_id, source_id, observed_at, vintage),
    FOREIGN KEY(indicator_id) REFERENCES cr_indicator_definitions(id),
    FOREIGN KEY(source_id) REFERENCES cr_sources(id),
    FOREIGN KEY(sync_run_id) REFERENCES cr_sync_runs(id)
);

CREATE TABLE IF NOT EXISTS cr_observation_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    previous_observation_id INTEGER NOT NULL,
    revised_observation_id INTEGER NOT NULL,
    detected_at TEXT NOT NULL,
    UNIQUE(previous_observation_id, revised_observation_id),
    FOREIGN KEY(previous_observation_id) REFERENCES cr_observations(id),
    FOREIGN KEY(revised_observation_id) REFERENCES cr_observations(id)
);

CREATE TABLE IF NOT EXISTS cr_indicator_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id INTEGER NOT NULL,
    methodology_id INTEGER NOT NULL,
    observation_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    value_text TEXT NOT NULL,
    band TEXT NOT NULL CHECK(band IN ('normal', 'warning', 'danger', 'critical')),
    distance_to_next_text TEXT,
    historical_percentile_text TEXT,
    momentum_text TEXT,
    persistence_count INTEGER NOT NULL DEFAULT 0 CHECK(persistence_count >= 0),
    stress_score_text TEXT NOT NULL,
    freshness TEXT NOT NULL CHECK(freshness IN ('fresh', 'delayed', 'stale', 'missing')),
    quality_score_text TEXT NOT NULL,
    explanation_payload TEXT NOT NULL DEFAULT '{}',
    raw_band TEXT NOT NULL DEFAULT 'normal' CHECK(raw_band IN ('normal', 'warning', 'danger', 'critical')),
    held_by_hysteresis INTEGER NOT NULL DEFAULT 0 CHECK(held_by_hysteresis IN (0, 1)),
    confirmation_required INTEGER NOT NULL DEFAULT 1 CHECK(confirmation_required >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(indicator_id, methodology_id, snapshot_at),
    FOREIGN KEY(indicator_id) REFERENCES cr_indicator_definitions(id),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id),
    FOREIGN KEY(observation_id) REFERENCES cr_observations(id)
);

CREATE TABLE IF NOT EXISTS cr_group_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_code TEXT NOT NULL,
    methodology_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    band TEXT NOT NULL CHECK(band IN ('normal', 'warning', 'danger', 'critical')),
    stress_score_text TEXT NOT NULL,
    indicator_count INTEGER NOT NULL CHECK(indicator_count >= 0),
    worsening_count INTEGER NOT NULL CHECK(worsening_count >= 0),
    contributors_payload TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_code, methodology_id, snapshot_at),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    methodology_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('insufficient_data', 'stable', 'tension', 'warning', 'confirmation', 'crisis')),
    calculated_stage TEXT NOT NULL DEFAULT 'stable' CHECK(calculated_stage IN ('stable', 'tension', 'warning', 'confirmation', 'crisis')),
    coverage_status TEXT NOT NULL DEFAULT 'not_evaluated' CHECK(coverage_status IN ('not_evaluated', 'healthy', 'degraded', 'insufficient_data')),
    coverage_ratio_text TEXT,
    coverage_payload TEXT NOT NULL DEFAULT '{}',
    dominant_window TEXT NOT NULL DEFAULT '30d',
    active_group_count INTEGER NOT NULL DEFAULT 0 CHECK(active_group_count >= 0),
    warning_group_count INTEGER NOT NULL DEFAULT 0 CHECK(warning_group_count >= 0),
    danger_group_count INTEGER NOT NULL DEFAULT 0 CHECK(danger_group_count >= 0),
    critical_group_count INTEGER NOT NULL DEFAULT 0 CHECK(critical_group_count >= 0),
    explanation_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(methodology_id, snapshot_at),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_scenario_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    methodology_id INTEGER NOT NULL,
    name_payload TEXT NOT NULL DEFAULT '{}',
    horizon TEXT NOT NULL,
    group_codes_payload TEXT NOT NULL DEFAULT '[]',
    anchor_groups_payload TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, methodology_id),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_scenario_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    methodology_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('unknown', 'inactive', 'watch', 'elevated', 'confirmed')),
    confidence TEXT NOT NULL CHECK(confidence IN ('low', 'medium', 'high')),
    active_group_count INTEGER NOT NULL DEFAULT 0 CHECK(active_group_count >= 0),
    evidence_payload TEXT NOT NULL DEFAULT '[]',
    explanation_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scenario_id, methodology_id, snapshot_at),
    FOREIGN KEY(scenario_id) REFERENCES cr_scenario_definitions(id),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL CHECK(event_type IN ('scenario_escalation', 'scenario_recovery')),
    severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'critical')),
    scenario_id INTEGER NOT NULL,
    methodology_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(scenario_id) REFERENCES cr_scenario_definitions(id),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_alert_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT,
    sent_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, user_id),
    FOREIGN KEY(event_id) REFERENCES cr_alert_events(id)
);

CREATE TABLE IF NOT EXISTS cr_data_health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    methodology_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL CHECK(to_status IN ('healthy', 'degraded', 'insufficient_data')),
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_data_health_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT,
    sent_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, user_id),
    FOREIGN KEY(event_id) REFERENCES cr_data_health_events(id)
);

CREATE TABLE IF NOT EXISTS cr_release_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    provider_event_id TEXT NOT NULL,
    release_name TEXT NOT NULL,
    release_date TEXT NOT NULL,
    scheduled_at TEXT,
    time_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(time_confirmed IN (0, 1)),
    region_code TEXT NOT NULL DEFAULT 'US',
    importance TEXT NOT NULL CHECK(importance IN ('medium', 'high')),
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'released', 'cancelled')),
    source_url TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, provider_event_id, release_date),
    FOREIGN KEY(source_id) REFERENCES cr_sources(id)
);

CREATE TABLE IF NOT EXISTS cr_report_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_key TEXT NOT NULL,
    report_type TEXT NOT NULL CHECK(report_type IN ('midweek', 'weekend')),
    report_date TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT,
    sent_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_key, user_id)
);

CREATE TABLE IF NOT EXISTS cr_news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    provider_item_id TEXT NOT NULL,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 300),
    summary TEXT NOT NULL DEFAULT '' CHECK(length(summary) <= 1200),
    url TEXT NOT NULL CHECK(length(url) BETWEEN 1 AND 1000),
    category TEXT NOT NULL DEFAULT '' CHECK(length(category) <= 120),
    language TEXT NOT NULL DEFAULT 'en' CHECK(language IN ('en', 'ru')),
    importance TEXT NOT NULL CHECK(importance IN ('medium', 'high')),
    content_hash TEXT NOT NULL,
    sync_run_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, provider_item_id),
    FOREIGN KEY(source_id) REFERENCES cr_sources(id),
    FOREIGN KEY(sync_run_id) REFERENCES cr_sync_runs(id)
);

CREATE TABLE IF NOT EXISTS cr_news_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_item_id INTEGER NOT NULL,
    scenario_id INTEGER NOT NULL,
    methodology_id INTEGER NOT NULL,
    relevance_score_text TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('context', 'watch', 'urgent')),
    rule_version TEXT NOT NULL,
    rule_codes_payload TEXT NOT NULL DEFAULT '[]',
    explanation_payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(news_item_id, scenario_id, methodology_id, rule_version),
    FOREIGN KEY(news_item_id) REFERENCES cr_news_items(id),
    FOREIGN KEY(scenario_id) REFERENCES cr_scenario_definitions(id),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_event_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    taxonomy TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('discovery', 'watch', 'corroborated', 'official')),
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 300),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    regions_payload TEXT NOT NULL DEFAULT '[]',
    entities_payload TEXT NOT NULL DEFAULT '[]',
    assets_payload TEXT NOT NULL DEFAULT '[]',
    impact_direction TEXT NOT NULL DEFAULT 'mixed' CHECK(impact_direction IN ('negative', 'positive', 'mixed')),
    horizon TEXT NOT NULL DEFAULT 'unknown',
    severity_score_text TEXT NOT NULL,
    confidence_score_text TEXT NOT NULL,
    event_score_text TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 0 CHECK(source_count >= 0),
    official_source_count INTEGER NOT NULL DEFAULT 0 CHECK(official_source_count >= 0),
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cr_event_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    news_item_id INTEGER NOT NULL,
    source_tier TEXT NOT NULL CHECK(source_tier IN ('A', 'B', 'C')),
    evidence_excerpt TEXT NOT NULL CHECK(length(evidence_excerpt) <= 600),
    injection_detected INTEGER NOT NULL DEFAULT 0 CHECK(injection_detected IN (0, 1)),
    relation TEXT NOT NULL DEFAULT 'supports' CHECK(relation IN ('supports', 'contradicts', 'mentions')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, news_item_id),
    FOREIGN KEY(event_id) REFERENCES cr_event_clusters(id),
    FOREIGN KEY(news_item_id) REFERENCES cr_news_items(id)
);

CREATE TABLE IF NOT EXISTS cr_indicator_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id INTEGER NOT NULL,
    methodology_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    features_payload TEXT NOT NULL,
    lineage_payload TEXT NOT NULL,
    input_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(indicator_id, methodology_id, snapshot_at, feature_version),
    FOREIGN KEY(indicator_id) REFERENCES cr_indicator_definitions(id),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_contagion_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    methodology_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    breadth_score_text TEXT NOT NULL,
    stress_correlation_text TEXT,
    payload TEXT NOT NULL,
    input_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(methodology_id, snapshot_at, feature_version),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_scenario_fusion_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    methodology_id INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL,
    fusion_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('unknown', 'inactive', 'watch', 'elevated', 'confirmed')),
    strength_score_text TEXT NOT NULL,
    reliability_score_text TEXT NOT NULL,
    independent_cluster_count INTEGER NOT NULL DEFAULT 0 CHECK(independent_cluster_count >= 0),
    anchor_active INTEGER NOT NULL DEFAULT 0 CHECK(anchor_active IN (0, 1)),
    components_payload TEXT NOT NULL,
    explanation_payload TEXT NOT NULL,
    input_checksum TEXT NOT NULL CHECK(length(input_checksum) = 64),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scenario_id, methodology_id, snapshot_at, fusion_version),
    FOREIGN KEY(scenario_id) REFERENCES cr_scenario_definitions(id),
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_agent_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    locale TEXT NOT NULL DEFAULT 'ru' CHECK(locale IN ('ru', 'en')),
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS cr_agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 8000),
    evidence_payload TEXT NOT NULL DEFAULT '[]' CHECK(length(evidence_payload) <= 20000),
    limitations_payload TEXT NOT NULL DEFAULT '[]' CHECK(length(limitations_payload) <= 12000),
    grounded INTEGER NOT NULL DEFAULT 0 CHECK(grounded IN (0, 1)),
    grounding_payload TEXT NOT NULL DEFAULT '[]' CHECK(length(grounding_payload) <= 12000),
    model TEXT NOT NULL DEFAULT '' CHECK(length(model) <= 120),
    latency_ms INTEGER CHECK(latency_ms IS NULL OR latency_ms >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(thread_id) REFERENCES cr_agent_threads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cr_backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    methodology_id INTEGER NOT NULL,
    scenario_code TEXT NOT NULL CHECK(length(scenario_code) BETWEEN 1 AND 80),
    horizon_seconds INTEGER NOT NULL CHECK(horizon_seconds > 0),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
    parameters_payload TEXT NOT NULL DEFAULT '{}' CHECK(length(parameters_payload) <= 20000),
    metrics_payload TEXT NOT NULL DEFAULT '{}' CHECK(length(metrics_payload) <= 20000),
    sample_count INTEGER NOT NULL DEFAULT 0 CHECK(sample_count >= 0),
    scored_count INTEGER NOT NULL DEFAULT 0 CHECK(scored_count >= 0),
    error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code) <= 120),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_backtest_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    predicted_at TEXT NOT NULL,
    horizon_end TEXT NOT NULL,
    event_at TEXT,
    signal_score_text TEXT NOT NULL,
    outcome INTEGER NOT NULL CHECK(outcome IN (0, 1)),
    calibrated_probability_text TEXT,
    baseline_probability_text TEXT,
    confidence TEXT NOT NULL CHECK(confidence IN ('insufficient', 'low', 'medium', 'high')),
    training_sample_count INTEGER NOT NULL DEFAULT 0 CHECK(training_sample_count >= 0),
    calibration_bin INTEGER NOT NULL CHECK(calibration_bin >= 0),
    latest_training_horizon_end TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, predicted_at),
    FOREIGN KEY(run_id) REFERENCES cr_backtest_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cr_calibration_bins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    bin_index INTEGER NOT NULL CHECK(bin_index >= 0),
    lower_text TEXT NOT NULL,
    upper_text TEXT NOT NULL,
    prediction_count INTEGER NOT NULL CHECK(prediction_count > 0),
    average_probability_text TEXT NOT NULL,
    observed_rate_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, bin_index),
    FOREIGN KEY(run_id) REFERENCES cr_backtest_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cr_event_catalog_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_code TEXT NOT NULL CHECK(length(scenario_code) BETWEEN 1 AND 80),
    version TEXT NOT NULL CHECK(length(version) BETWEEN 1 AND 80),
    checksum TEXT NOT NULL CHECK(length(checksum) = 64),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'retired')),
    source_name TEXT NOT NULL CHECK(length(source_name) BETWEEN 1 AND 240),
    source_url TEXT NOT NULL CHECK(length(source_url) BETWEEN 1 AND 1000),
    definition_payload TEXT NOT NULL DEFAULT '{}' CHECK(length(definition_payload) <= 30000),
    limitations_payload TEXT NOT NULL DEFAULT '[]' CHECK(length(limitations_payload) <= 20000),
    effective_from TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scenario_code, version)
);

CREATE TABLE IF NOT EXISTS cr_event_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER NOT NULL,
    event_code TEXT NOT NULL CHECK(length(event_code) BETWEEN 1 AND 120),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    start_precision TEXT NOT NULL CHECK(start_precision IN ('instant', 'day', 'month', 'year')),
    end_precision TEXT CHECK(end_precision IS NULL OR end_precision IN ('instant', 'day', 'month', 'year')),
    region_code TEXT NOT NULL DEFAULT 'GLOBAL' CHECK(length(region_code) BETWEEN 1 AND 32),
    source_url TEXT NOT NULL CHECK(length(source_url) BETWEEN 1 AND 1000),
    source_note TEXT NOT NULL DEFAULT '' CHECK(length(source_note) <= 2000),
    label_status TEXT NOT NULL DEFAULT 'confirmed' CHECK(label_status IN ('confirmed', 'derived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(catalog_id, event_code),
    CHECK(ended_at IS NULL OR ended_at >= started_at),
    FOREIGN KEY(catalog_id) REFERENCES cr_event_catalog_versions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cr_replay_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    methodology_id INTEGER NOT NULL,
    scenario_code TEXT NOT NULL CHECK(length(scenario_code) BETWEEN 1 AND 80),
    engine_version TEXT NOT NULL CHECK(length(engine_version) BETWEEN 1 AND 80),
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    step_seconds INTEGER NOT NULL CHECK(step_seconds > 0),
    status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
    parameters_payload TEXT NOT NULL DEFAULT '{}' CHECK(length(parameters_payload) <= 20000),
    signal_count INTEGER NOT NULL DEFAULT 0 CHECK(signal_count >= 0),
    result_checksum TEXT NOT NULL DEFAULT '' CHECK(length(result_checksum) IN (0, 64)),
    error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code) <= 120),
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
);

CREATE TABLE IF NOT EXISTS cr_replay_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    signal_at TEXT NOT NULL,
    signal_score_text TEXT NOT NULL,
    scenario_status TEXT NOT NULL CHECK(scenario_status IN ('inactive', 'watch', 'elevated', 'confirmed')),
    data_confidence TEXT NOT NULL CHECK(data_confidence IN ('low', 'medium', 'high')),
    coverage_text TEXT NOT NULL,
    input_count INTEGER NOT NULL CHECK(input_count >= 0),
    backtest_eligible INTEGER NOT NULL DEFAULT 0 CHECK(backtest_eligible IN (0, 1)),
    eligibility_reason TEXT NOT NULL DEFAULT '' CHECK(length(eligibility_reason) <= 120),
    latest_released_at TEXT,
    observation_ids_payload TEXT NOT NULL DEFAULT '[]' CHECK(length(observation_ids_payload) <= 20000),
    input_checksum TEXT NOT NULL CHECK(length(input_checksum) = 64),
    evidence_payload TEXT NOT NULL DEFAULT '[]' CHECK(length(evidence_payload) <= 20000),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, signal_at),
    FOREIGN KEY(run_id) REFERENCES cr_replay_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cr_backtest_provenance (
    run_id INTEGER PRIMARY KEY,
    replay_run_id INTEGER NOT NULL,
    event_catalog_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(run_id) REFERENCES cr_backtest_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(replay_run_id) REFERENCES cr_replay_runs(id),
    FOREIGN KEY(event_catalog_id) REFERENCES cr_event_catalog_versions(id)
);

CREATE INDEX IF NOT EXISTS idx_cr_sync_runs_source_started
    ON cr_sync_runs(source_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_cr_observations_indicator_observed
    ON cr_observations(indicator_id, observed_at DESC, released_at DESC);
CREATE INDEX IF NOT EXISTS idx_cr_observations_source_fetched
    ON cr_observations(source_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_cr_indicator_states_snapshot
    ON cr_indicator_states(snapshot_at DESC, indicator_id);
CREATE INDEX IF NOT EXISTS idx_cr_group_states_snapshot
    ON cr_group_states(snapshot_at DESC, group_code);
CREATE INDEX IF NOT EXISTS idx_cr_market_snapshots_snapshot
    ON cr_market_snapshots(snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_cr_scenario_states_snapshot
    ON cr_scenario_states(snapshot_at DESC, scenario_id);
CREATE INDEX IF NOT EXISTS idx_cr_alert_deliveries_retry
    ON cr_alert_deliveries(status, next_attempt_at, attempts);
CREATE INDEX IF NOT EXISTS idx_cr_data_health_deliveries_retry
    ON cr_data_health_deliveries(status, next_attempt_at, attempts);
CREATE INDEX IF NOT EXISTS idx_cr_release_events_upcoming
    ON cr_release_events(status, release_date, importance);
CREATE INDEX IF NOT EXISTS idx_cr_report_deliveries_retry
    ON cr_report_deliveries(status, next_attempt_at, attempts);
CREATE INDEX IF NOT EXISTS idx_cr_news_items_published
    ON cr_news_items(published_at DESC, source_id);
CREATE INDEX IF NOT EXISTS idx_cr_news_evidence_scenario
    ON cr_news_evidence(methodology_id, scenario_id, severity, news_item_id);
CREATE INDEX IF NOT EXISTS idx_cr_event_clusters_last_seen
    ON cr_event_clusters(last_seen_at DESC, taxonomy, status);
CREATE INDEX IF NOT EXISTS idx_cr_event_evidence_event
    ON cr_event_evidence(event_id, source_tier, news_item_id);
CREATE INDEX IF NOT EXISTS idx_cr_indicator_features_snapshot
    ON cr_indicator_features(snapshot_at DESC, indicator_id, feature_version);
CREATE INDEX IF NOT EXISTS idx_cr_contagion_features_snapshot
    ON cr_contagion_features(snapshot_at DESC, feature_version);
CREATE INDEX IF NOT EXISTS idx_cr_scenario_fusion_snapshot
    ON cr_scenario_fusion_states(snapshot_at DESC, scenario_id, fusion_version);
CREATE INDEX IF NOT EXISTS idx_cr_agent_threads_user_updated
    ON cr_agent_threads(user_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_cr_agent_messages_thread_created
    ON cr_agent_messages(thread_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_cr_backtest_runs_scenario_completed
    ON cr_backtest_runs(scenario_code, completed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_cr_backtest_predictions_run_time
    ON cr_backtest_predictions(run_id, predicted_at);
CREATE INDEX IF NOT EXISTS idx_cr_event_catalog_scenario_status
    ON cr_event_catalog_versions(scenario_code, status, id DESC);
CREATE INDEX IF NOT EXISTS idx_cr_event_labels_catalog_start
    ON cr_event_labels(catalog_id, started_at);
CREATE INDEX IF NOT EXISTS idx_cr_replay_runs_scenario_completed
    ON cr_replay_runs(scenario_code, completed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_cr_replay_signals_run_time
    ON cr_replay_signals(run_id, signal_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    user_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('in_progress', 'completed')),
    response_status INTEGER,
    response_body TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    PRIMARY KEY(user_id, scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS trade_level_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_price REAL NOT NULL,
    level_price REAL,
    matched_level TEXT NOT NULL,
    candle_high REAL,
    candle_low REAL,
    ambiguity TEXT NOT NULL DEFAULT '',
    notification_status TEXT NOT NULL DEFAULT 'pending' CHECK(notification_status IN ('pending', 'sent')),
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notified_at TEXT,
    UNIQUE(trade_id, matched_level),
    FOREIGN KEY(trade_id) REFERENCES trades(id),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    trading_profile TEXT NOT NULL DEFAULT '',
    default_account_size REAL NOT NULL DEFAULT 0,
    default_risk_percent REAL NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trading_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    start_balance REAL NOT NULL,
    target_balance REAL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
    note TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('above', 'below')),
    target_price REAL NOT NULL,
    last_price REAL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'triggered', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    triggered_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('long', 'short')),
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL,
    quantity REAL NOT NULL,
    leverage REAL NOT NULL DEFAULT 1,
    risk_amount REAL NOT NULL DEFAULT 0,
    setup TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    review_score REAL,
    ignored_warnings INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed', 'cancelled')),
    exit_price REAL,
    pnl REAL,
    fees REAL NOT NULL DEFAULT 0,
    close_reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS pending_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('long', 'short')),
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL,
    quantity REAL NOT NULL,
    leverage REAL NOT NULL DEFAULT 1,
    risk_amount REAL NOT NULL DEFAULT 0,
    setup TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    review_score REAL NOT NULL DEFAULT 0,
    review_payload TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'idea' CHECK(outcome IN ('win', 'loss', 'breakeven', 'idea')),
    theory TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    screenshot_file_id TEXT NOT NULL DEFAULT '',
    linked_trade_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id),
    FOREIGN KEY(linked_trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS market_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bias TEXT NOT NULL CHECK(bias IN ('long', 'short', 'neutral')),
    structure TEXT NOT NULL DEFAULT '',
    levels TEXT NOT NULL DEFAULT '',
    invalidation_level REAL,
    note TEXT NOT NULL DEFAULT '',
    screenshot_file_id TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 70,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, symbol),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_date TEXT NOT NULL,
    allowed_symbols TEXT NOT NULL DEFAULT '',
    max_daily_risk_percent REAL NOT NULL DEFAULT 3,
    max_daily_loss REAL NOT NULL DEFAULT 0,
    plan_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, plan_date),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS trade_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    trade_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    score REAL NOT NULL,
    rule_score REAL,
    win_probability REAL NOT NULL,
    loss_probability REAL NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id),
    FOREIGN KEY(trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS note_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS trade_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    open_time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    interval TEXT NOT NULL DEFAULT '1m',
    UNIQUE(trade_id, open_time, interval),
    FOREIGN KEY(trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS trade_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    telegram_file_id TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(trade_id) REFERENCES trades(id),
    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_user_status_opened
    ON trades(user_id, status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_user_created
    ON journal_entries(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_status_symbol
    ON alerts(status, symbol);
CREATE INDEX IF NOT EXISTS idx_contexts_user_symbol_created
    ON market_contexts(user_id, symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_attachments_user_trade
    ON trade_attachments(user_id, trade_id);
"""


class Database:
    def __init__(self, path: Path, *, auto_migrate: bool = True) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if auto_migrate:
            self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            self._add_column(connection, "trades", "risk_amount", "REAL NOT NULL DEFAULT 0")
            self._add_column(connection, "trades", "setup", "TEXT NOT NULL DEFAULT ''")
            self._add_column(connection, "trades", "tags", "TEXT NOT NULL DEFAULT ''")
            self._add_column(connection, "trades", "review_score", "REAL")
            self._add_column(connection, "trades", "ignored_warnings", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(connection, "trades", "close_reason", "TEXT NOT NULL DEFAULT ''")
            self._add_column(connection, "trades", "session_id", "INTEGER")
            self._add_column(connection, "trades", "timeframe", "TEXT NOT NULL DEFAULT '5m'")
            self._add_column(connection, "journal_entries", "session_id", "INTEGER")
            self._add_column(connection, "trade_reviews", "rule_score", "REAL")
            self._add_column(
                connection,
                "cr_indicator_definitions",
                "provider_series_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._add_column(
                connection,
                "cr_indicator_definitions",
                "max_staleness_seconds",
                "INTEGER NOT NULL DEFAULT 86400",
            )
            self._add_column(
                connection,
                "cr_indicator_states",
                "raw_band",
                "TEXT NOT NULL DEFAULT 'normal'",
            )
            self._add_column(
                connection,
                "cr_indicator_states",
                "held_by_hysteresis",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._add_column(
                connection,
                "cr_indicator_states",
                "confirmation_required",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._add_column(
                connection,
                "cr_agent_messages",
                "grounded",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._add_column(
                connection,
                "cr_agent_messages",
                "grounding_payload",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._migrate_crisis_coverage_v15(connection)
            self._add_column(
                connection,
                "cr_threshold_sets",
                "basis",
                "TEXT NOT NULL DEFAULT 'legacy'",
            )
            self._add_column(
                connection,
                "cr_threshold_sets",
                "promotion_status",
                "TEXT NOT NULL DEFAULT 'active'",
            )
            self._add_column(
                connection,
                "cr_threshold_sets",
                "rationale_payload",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            for column, definition in (
                ("publisher", "TEXT NOT NULL DEFAULT ''"),
                ("original_language", "TEXT NOT NULL DEFAULT 'en'"),
                ("normalized_title", "TEXT NOT NULL DEFAULT ''"),
                ("dedup_hash", "TEXT NOT NULL DEFAULT ''"),
                ("source_tier", "TEXT NOT NULL DEFAULT 'A'"),
                ("evidence_excerpt", "TEXT NOT NULL DEFAULT ''"),
                ("raw_payload_hash", "TEXT NOT NULL DEFAULT ''"),
            ):
                self._add_column(connection, "cr_news_items", column, definition)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_user_session_status "
                "ON trades(user_id, session_id, status)"
            )
            self._record_migration(connection, 1, "baseline-schema-v1")
            self._record_migration(connection, 2, "idempotency-keys-v2")
            self._record_migration(connection, 3, "trade-review-rule-score-v3")
            self._record_migration(connection, 4, "trade-level-observations-notify-v4")
            self._record_migration(connection, 5, "crisis-radar-foundation-v5")
            self._record_migration(connection, 6, "crisis-radar-market-stage-v6")
            self._record_migration(connection, 7, "crisis-radar-scenarios-alerts-v7")
            self._record_migration(connection, 8, "crisis-radar-calendar-reports-v8")
            self._record_migration(connection, 9, "crisis-radar-stability-audit-v9")
            self._record_migration(connection, 10, "crisis-radar-news-evidence-v10")
            self._record_migration(connection, 11, "crisis-radar-local-agent-v11")
            self._record_migration(connection, 12, "crisis-radar-agent-grounding-v12")
            self._record_migration(connection, 13, "crisis-radar-walk-forward-backtest-v13")
            self._record_migration(connection, 14, "crisis-radar-event-catalog-replay-v14")
            self._record_migration(connection, 15, "crisis-radar-coverage-gate-v15")
            self._record_migration(connection, 16, "crisis-radar-threshold-metadata-v16")
            self._record_migration(connection, 17, "crisis-radar-global-events-v17")
            self._record_migration(connection, 18, "crisis-radar-trend-regime-v18")
            self._record_migration(connection, 19, "crisis-radar-scenario-fusion-v19")
            self._record_migration(connection, 20, "crisis-radar-data-health-alerts-v20")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _add_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_crisis_coverage_v15(self, connection: sqlite3.Connection) -> None:
        market_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cr_market_snapshots'"
        ).fetchone()
        market_sql = "" if market_sql_row is None else str(market_sql_row[0] or "")
        if "coverage_status" not in market_sql or "insufficient_data" not in market_sql:
            connection.execute("ALTER TABLE cr_market_snapshots RENAME TO cr_market_snapshots_v14")
            connection.execute(
                """
                CREATE TABLE cr_market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    methodology_id INTEGER NOT NULL,
                    snapshot_at TEXT NOT NULL,
                    stage TEXT NOT NULL CHECK(stage IN (
                        'insufficient_data', 'stable', 'tension', 'warning',
                        'confirmation', 'crisis'
                    )),
                    calculated_stage TEXT NOT NULL DEFAULT 'stable' CHECK(calculated_stage IN (
                        'stable', 'tension', 'warning', 'confirmation', 'crisis'
                    )),
                    coverage_status TEXT NOT NULL DEFAULT 'not_evaluated' CHECK(coverage_status IN (
                        'not_evaluated', 'healthy', 'degraded', 'insufficient_data'
                    )),
                    coverage_ratio_text TEXT,
                    coverage_payload TEXT NOT NULL DEFAULT '{}',
                    dominant_window TEXT NOT NULL DEFAULT '30d',
                    active_group_count INTEGER NOT NULL DEFAULT 0 CHECK(active_group_count >= 0),
                    warning_group_count INTEGER NOT NULL DEFAULT 0 CHECK(warning_group_count >= 0),
                    danger_group_count INTEGER NOT NULL DEFAULT 0 CHECK(danger_group_count >= 0),
                    critical_group_count INTEGER NOT NULL DEFAULT 0 CHECK(critical_group_count >= 0),
                    explanation_payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(methodology_id, snapshot_at),
                    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO cr_market_snapshots(
                    id, methodology_id, snapshot_at, stage, calculated_stage,
                    coverage_status, coverage_ratio_text, coverage_payload,
                    dominant_window, active_group_count, warning_group_count,
                    danger_group_count, critical_group_count, explanation_payload, created_at
                )
                SELECT id, methodology_id, snapshot_at, stage, stage,
                       'not_evaluated', NULL, '{}', dominant_window,
                       active_group_count, warning_group_count, danger_group_count,
                       critical_group_count, explanation_payload, created_at
                FROM cr_market_snapshots_v14
                """
            )
            connection.execute("DROP TABLE cr_market_snapshots_v14")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_cr_market_snapshots_snapshot "
                "ON cr_market_snapshots(snapshot_at DESC)"
            )

        scenario_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cr_scenario_states'"
        ).fetchone()
        scenario_sql = "" if scenario_sql_row is None else str(scenario_sql_row[0] or "")
        if "'unknown'" not in scenario_sql:
            connection.execute("ALTER TABLE cr_scenario_states RENAME TO cr_scenario_states_v14")
            connection.execute(
                """
                CREATE TABLE cr_scenario_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id INTEGER NOT NULL,
                    methodology_id INTEGER NOT NULL,
                    snapshot_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'unknown', 'inactive', 'watch', 'elevated', 'confirmed'
                    )),
                    confidence TEXT NOT NULL CHECK(confidence IN ('low', 'medium', 'high')),
                    active_group_count INTEGER NOT NULL DEFAULT 0 CHECK(active_group_count >= 0),
                    evidence_payload TEXT NOT NULL DEFAULT '[]',
                    explanation_payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(scenario_id, methodology_id, snapshot_at),
                    FOREIGN KEY(scenario_id) REFERENCES cr_scenario_definitions(id),
                    FOREIGN KEY(methodology_id) REFERENCES cr_methodology_versions(id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO cr_scenario_states(
                    id, scenario_id, methodology_id, snapshot_at, status, confidence,
                    active_group_count, evidence_payload, explanation_payload, created_at
                )
                SELECT id, scenario_id, methodology_id, snapshot_at, status, confidence,
                       active_group_count, evidence_payload, explanation_payload, created_at
                FROM cr_scenario_states_v14
                """
            )
            connection.execute("DROP TABLE cr_scenario_states_v14")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_cr_scenario_states_snapshot "
                "ON cr_scenario_states(snapshot_at DESC, scenario_id)"
            )

    def _record_migration(self, connection: sqlite3.Connection, version: int, checksum: str) -> None:
        existing = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        if existing and existing[0] != checksum:
            raise RuntimeError(f"Migration checksum mismatch for version {version}")
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, checksum) VALUES (?, ?)",
            (version, checksum),
        )
