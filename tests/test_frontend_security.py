from pathlib import Path


APP_JS = (Path(__file__).parents[1] / "mini_app" / "app.js").read_text(encoding="utf-8")


def test_frontend_has_no_url_identity_or_fallback_user() -> None:
    assert "user_id" not in APP_JS
    assert '|| "1"' not in APP_JS
    assert "initDataUnsafe" not in APP_JS
    assert "Authorization" in APP_JS


def test_stored_journal_fields_are_escaped() -> None:
    assert "${escapeHtml(row.description || \"-\")}" in APP_JS
    assert "${escapeHtml(row.theory || \"\")}" in APP_JS
    assert "setJournalResultContent(node, result)" in APP_JS
    assert "node.innerHTML = `${result.icon}" not in APP_JS


def test_stored_session_and_trade_fields_are_sanitized_before_html_templates() -> None:
    assert "function safeId(value)" in APP_JS
    assert "function safeTradeStatus(value)" in APP_JS
    assert "function safeSessionStatus(value)" in APP_JS
    assert "${escapeHtml(item.name)}" in APP_JS
    assert "${escapeHtml(item.started_at)}" in APP_JS
    assert 'class="session-card ${status}"' in APP_JS
    assert 'data-trade-id="${tradeId}"' in APP_JS
    assert "${side.toUpperCase()} ${status}" in APP_JS
    assert "numberAttr(row.entry_price)" in APP_JS


def test_generated_media_urls_are_attribute_escaped() -> None:
    assert 'data-protected-src="${escapeHtml(`/api/media/${encodeURIComponent(fileId)}`)}"' in APP_JS
    assert 'data-protected-src="${escapeHtml(src)}"' in APP_JS


def test_inline_event_handlers_are_not_used() -> None:
    assert "onclick=" not in APP_JS
    assert "onchange=" not in APP_JS
    assert "data-action=" in APP_JS
    assert "style=" not in APP_JS
    assert ".style." not in APP_JS
