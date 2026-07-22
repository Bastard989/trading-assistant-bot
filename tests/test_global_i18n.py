from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "mini_app" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "mini_app" / "app.js").read_text(encoding="utf-8")
I18N = (ROOT / "mini_app" / "i18n.js").read_text(encoding="utf-8")


def test_global_locale_switch_replaces_crisis_only_control() -> None:
    assert 'data-app-locale="ru"' in INDEX
    assert 'data-app-locale="en"' in INDEX
    assert "data-crisis-locale" not in INDEX
    assert 'src="/static/i18n.js?' in INDEX
    assert "window.AppI18n?.setLocale(appLocale)" in APP
    assert "let crisisLocale = appLocale" in APP


def test_i18n_persists_locale_and_translates_dynamic_dom() -> None:
    assert 'localStorage.getItem(STORAGE_KEY)' in I18N
    assert 'localStorage.setItem(STORAGE_KEY, locale)' in I18N
    assert "new MutationObserver" in I18N
    assert 'document.documentElement.lang = locale' in I18N
    assert '"Кризис-радар": "Crisis Radar"' in I18N
    assert '"Сделки": "Trades"' in I18N
    assert '"Калькулятор": "Calculator"' in I18N
    assert '"Настройка на backend": "Backend configuration"' in I18N
    assert '"Проверить backend": "Check backend"' in I18N
