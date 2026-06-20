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


def test_inline_event_handlers_are_not_used() -> None:
    assert "onclick=" not in APP_JS
    assert "onchange=" not in APP_JS
    assert "data-action=" in APP_JS
    assert "style=" not in APP_JS
    assert ".style." not in APP_JS
