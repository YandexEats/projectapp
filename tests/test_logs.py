"""Логи публичного репозитория не должны содержать суммы бонусов и текст изменений.

Запуск: python -m pytest tests/test_logs.py -v -s
"""
import types

import pytest

from common import redact
import scraper  # noqa: E402

AMOUNT = str(12 * 10**5)            # сумма, собранная в рантайме, чтобы не хранить её в коде
GROUPED = "1 200 000"


def assert_no_amounts(text: str) -> None:
    """В тексте нет ничего похожего на сумму (redact ничего не меняет) и нет стрелок «было → стало»."""
    assert redact(text) == text, "в логе есть похожее на сумму: " + "".join(
        a for a, b in zip(text.splitlines(), redact(text).splitlines()) if a != b)
    assert "→" not in text and " сум" not in text


@pytest.mark.parametrize("raw,expected", [
    (f"бонус {GROUPED} сум", "бонус ••• сум"),
    (f"bonus={AMOUNT}", "bonus=•••"),
    ("bonus:12e5,orders:80", "bonus:•••,orders:80"),
    ("1 200 000 so'mgacha", "••• so'mgacha"),
])
def test_redact_masks_amounts(raw, expected):
    assert redact(raw) == expected


@pytest.mark.parametrize("keep", [
    "Запуск Actions: https://github.com/YandexEats/tezkor-referral-monitor/actions/runs/43925956761",
    "Дашборд обновлён: показывает запуск 43925956761",
    "data: проверка 2026-11-16 — no_change",
    "бандл=/assets/index-CEGaT5xQ.js, метод=bundle",
    "Обнаружено изменений: 3. Перепроверка через 120 с…",
    "Telegram: сообщение отправлено (chat …4567, message_id=42)",
])
def test_redact_keeps_service_info(keep):
    assert redact(keep) == keep


def test_validate_message_has_no_value():
    data = {"cities": {"tashkent": {"transports": {"moto": [{"days": 30, "orders": 80, "bonus": AMOUNT}]}}}}
    probs = scraper.validate(data, None)
    print("\n", probs)
    assert probs and AMOUNT not in " ".join(probs) and "bonus" in probs[0]


def test_node_error_does_not_leak_bundle_source(monkeypatch):
    stderr = (f"/tmp/x/bundle.js:1\nconst c={{tashkent:{{moto:[{{days:30,orders:120,bonus:{AMOUNT}}}]}}}}\n"
              "                       ^\n\nSyntaxError: Unexpected token '}'\n    at wrapSafe (node:internal)\n")
    monkeypatch.setattr(scraper.shutil, "which", lambda n: "node")
    monkeypatch.setattr(scraper.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stderr=stderr, stdout=""))
    with pytest.raises(scraper.ScrapeError) as ei:
        scraper.parse_bundle_text("x" * 2000)
    print("\n", ei.value)
    assert str(ei.value) == "Ошибка extract_bundle.js (код 1): SyntaxError: Unexpected token '}'"


def test_change_run_logs_only_counts(tmp_path, fake_server, live, live_bundle):
    """Полный путь с реальным изменением (как тест 2): в выводе check.py и notify.py только количества."""
    from test_monitor import DASH, _modified_bundle, run
    from conftest import write_enc
    from check import build_snapshot
    base, handler = fake_server
    html, path, js = live_bundle
    handler.routes["/"] = html.replace(path, "/assets/index-TEST123.js")
    handler.routes["/assets/index-TEST123.js"] = _modified_bundle(js)
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    write_enc(data_dir, "history.json", [build_snapshot(live, None, "2026-11-01")])
    write_enc(data_dir, "last_check.json", {"baseline_notified": True})
    env = {"SITE_URL": base + "/", "DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "CONFIRM_DELAY": "0",
           "BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "-1002", "TELEGRAM_API": base, "DASHBOARD_URL": DASH}
    r1 = run("check.py", ["--no-fallback"], env)
    r2 = run("notify.py", [], env)
    assert r1.returncode == 0 and r2.returncode == 0
    assert "Изменения записаны: изменений: 3, городов: 3." in r1.stdout
    group = [c for c in handler.calls if c["payload"]["chat_id"] == "-1001"]
    assert "500 000 → 700 000" in group[0]["payload"]["text"], "в Telegram сообщение по-прежнему полное"
    for out in (r1.stdout + r1.stderr, r2.stdout + r2.stderr):
        assert_no_amounts(out)
        assert "Мото" not in out and "Наманган" not in out


def test_test_group_message_logs_only_counts(fake_server):
    from test_monitor import run
    base, handler = fake_server
    env = {"BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base,
           "DASHBOARD_URL": "https://yandexeats.github.io/tezkor-referral-monitor/"}
    r = run("notify.py", ["--test-group"], env)
    assert r.returncode == 0 and "строк в тексте:" in r.stdout
    assert "600 000" in handler.calls[0]["payload"]["text"]
    assert_no_amounts(r.stdout + r.stderr)


@pytest.fixture(scope="module")
def live_bundle():
    from common import http_get
    html = http_get(scraper.SITE_URL)
    path = scraper.find_bundle_path(html)
    return html, path, http_get(scraper.SITE_URL.rstrip("/") + path)
