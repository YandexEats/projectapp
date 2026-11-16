"""Тесты мониторинга. Запуск: python -m pytest tests -v -s"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import BASELINE_SHA256, HERO_SHA256, HERO_UZ_SHA256, ROOT, T, TEST_PASSWORD, read_enc, sha, transports_only, write_enc

import scraper  # noqa: E402
from common import http_get  # noqa: E402
from diff import compare  # noqa: E402
import telegram  # noqa: E402

PY = sys.executable
DASH = "https://example.github.io/tezkor-referral-monitor/"


def run(script, args, env_extra, cwd=ROOT):
    """Запуск скрипта из monitor/. По умолчанию DASHBOARD_PASSWORD = тестовый пароль; None в env_extra — убрать переменную."""
    drop = ("BOT_TOKEN", "GROUP_CHAT_ID", "ADMIN_CHAT_ID", "GITHUB_RUN_ID", "GITHUB_OUTPUT", "DASHBOARD_PASSWORD", "DASHBOARD_PASSWORD_OLD")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["DASHBOARD_PASSWORD"] = TEST_PASSWORD
    env.update(env_extra)
    env = {k: v for k, v in env.items() if v is not None}
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([PY, str(ROOT / "monitor" / script), *args], env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    print(f"\n----- {script} {' '.join(args)} → код выхода {r.returncode} -----\n{r.stdout}{r.stderr}")
    return r


@pytest.fixture(scope="module")
def live_bundle():
    html = http_get(scraper.SITE_URL)
    path = scraper.find_bundle_path(html)
    return html, path, http_get(scraper.SITE_URL.rstrip("/") + path)


# ---------------------------------------------------------------- ТЕСТ 1
def test1_live_bundle_matches_baseline(live):
    print("\nБандл:", live["bundle_file"], "| метод:", live["method"])
    for slug, tr in transports_only(live).items():
        print(f"  {slug:10s}", "  ".join(f"{t}:{'/'.join(str(x[k]) for x in v for k in ('days', 'orders', 'bonus')) or '[]'}" for t, v in tr.items()))
    print("Заголовок:", live["hero_text"])
    print("Примечание UZ:", live["notes"]["tashkent_uz"][:80], "…")
    print("Офисы:", {s: o["hours"] + " " + o["days"] for s, o in live["offices"].items()})
    got = sha(transports_only(live))
    print("SHA-256 бонусов:", got)
    assert got == BASELINE_SHA256, "бонусы на сайте отличаются от эталона (если изменение официальное — обновите BASELINE_SHA256 в conftest.py)"
    assert sha(live["hero_text"]) == HERO_SHA256
    assert live["notes"]["tashkent_uz"].startswith("Toshkentda avval ro'yxatdan o'tgan kuryerlar ham hisobga olinadi")
    assert set(live["offices"]) == set(live["cities"])
    assert scraper.validate(live, None) == []


# ---------------------------------------------------------------- ТЕСТ 2
def _modified_bundle(js: str) -> str:
    i = js.index('namangan:{name:')
    j = js.index("bonus:5e5", i)
    js = js[:j] + "bonus:7e5" + js[j + len("bonus:5e5"):]                  # Наманган, мото: 500 000 → 700 000
    i = js.index('bukhara:{name:')
    j = js.index("car:[{days:30,orders:80", i)
    js = js[:j] + "car:[{days:30,orders:100" + js[j + len("car:[{days:30,orders:80"):]  # Бухара, авто: 80 → 100 заказов
    old = ",bike:[{days:30,orders:120,bonus:9e5}]"                            # Ташкент (через переменную-ссылку), вело убран
    j = js.index(old)
    return js[:j] + ",bike:[]" + js[j + len(old):]


def test2_simulated_change_full_pipeline(tmp_path, fake_server, live, live_bundle):
    base, handler = fake_server
    html, _path, js = live_bundle
    handler.routes["/"] = html.replace(_path, "/assets/index-TEST123.js")
    handler.routes["/assets/index-TEST123.js"] = _modified_bundle(js)

    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    data_dir.mkdir()
    # история = базовый снимок с живого сайта
    from check import build_snapshot
    base_snap = build_snapshot(live, None, "2026-11-01")
    write_enc(data_dir, "history.json", [base_snap])
    write_enc(data_dir, "last_check.json", {"baseline_notified": True})

    env = {"SITE_URL": base + "/", "DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "CONFIRM_DELAY": "0",
           "BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "-1002",
           "TELEGRAM_API": base, "DASHBOARD_URL": DASH}
    r = run("check.py", ["--no-fallback"], env)
    assert r.returncode == 0
    assert sorted(p.name for p in data_dir.iterdir()) == ["changes.json.enc", "history.json.enc", "last_check.json.enc"], "только шифртекст"
    changes = read_enc(data_dir, "changes.json")
    print("===== changes.json (расшифровано) =====\n" + json.dumps(changes, ensure_ascii=False, indent=1))
    hist = read_enc(data_dir, "history.json")
    assert len(hist) == 2 and hist[1]["bundle_file"] == "/assets/index-TEST123.js"
    kinds = {(c["city"], c["transport"], c["field"], c["old"], c["new"]) for c in changes}
    assert kinds == {("namangan", "moto", "bonus", 500000, 700000),
                     ("bukhara", "car", "orders", 80, 100),
                     ("tashkent", "bike", "transport_removed", 900000, None)}

    r = run("notify.py", [], env)
    assert r.returncode == 0
    group = [c for c in handler.calls if c["payload"]["chat_id"] == "-1001"]
    assert len(group) == 1
    p = group[0]["payload"]
    print("===== Текст сообщения в группу (HTML) =====\n" + p["text"])
    print("===== Кнопка =====\n", json.dumps(p["reply_markup"], ensure_ascii=False))
    assert "🔔 <b>Uzum Tezkor изменил реферальные бонусы</b>" in p["text"]
    assert "🏍 Мото: 500 000 → 700 000 сум (+200 000), 80 заказов за 30 дней" in p["text"]
    assert "🚗 Авто: условие 80 → 100 заказов за 30 дней" in p["text"]
    btn = p["reply_markup"]["inline_keyboard"][0][0]
    assert btn["text"] == "📊 Подробнее" and btn["url"].startswith(DASH + "?date=")
    assert "city=" not in btn["url"]  # изменились 3 города → без фильтра города


def test2b_single_city_button_has_city():
    tr = {"moto": T(30, 80, 400000), "car": T(30, 80, 400000), "ebike": [], "bike": T(30, 80, 400000), "walking": []}  # вымышленные
    old = {"cities": {"namangan": {"name_ru": "Наманган", "transports": tr}}}
    new = copy.deepcopy(old)
    new["cities"]["namangan"]["transports"]["ebike"] = [{"days": 30, "orders": 80, "bonus": 600000}]
    ch = compare(old, new, "2026-11-16")
    text = telegram.build_change_message(ch, "2026-11-16", {"namangan": "Наманган"})
    print("\n" + text)
    assert "🚲 Электровело: появился, 600 000 сум, 80 заказов за 30 дней" in text
    assert telegram.details_url(DASH, "2026-11-16", ch) == DASH + "?date=2026-11-16&city=namangan"


# ---------------------------------------------------------------- ТЕСТ 3
@pytest.mark.parametrize("case", ["empty_bundle", "unreachable"])
def test3_broken_site_alerts_admin_only(tmp_path, fake_server, live, case):
    base, handler = fake_server
    handler.routes["/"] = '<html><script type="module" src="/assets/index-BROKEN.js"></script><div id="root"></div></html>'
    handler.routes["/assets/index-BROKEN.js"] = ""
    site = base + "/" if case == "empty_bundle" else "http://127.0.0.1:9/"
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    data_dir.mkdir()
    from check import build_snapshot
    write_enc(data_dir, "history.json", [build_snapshot(live, None, "2026-11-01")])
    hist_bytes = (data_dir / "history.json.enc").read_bytes()
    env = {"SITE_URL": site, "DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "CONFIRM_DELAY": "0", "PW_TIMEOUT": "8000",
           "BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "-1002", "TELEGRAM_API": base,
           "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "owner/tezkor-referral-monitor", "GITHUB_RUN_ID": "777"}
    r = run("check.py", [], env)
    assert r.returncode != 0, "должен завершиться с ошибкой"
    assert (data_dir / "history.json.enc").read_bytes() == hist_bytes, "история не должна меняться"
    assert not (data_dir / "changes.json.enc").exists() and not list(data_dir.glob("*.json"))
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["-1002"], "только админ, без группы"
    alert = handler.calls[0]["payload"]["text"]
    print("===== Сообщение администратору =====\n" + alert)
    assert "Не удалось прочитать сайт, возможно изменилась структура" in alert
    assert "https://github.com/owner/tezkor-referral-monitor/actions/runs/777" in alert
    last = read_enc(data_dir, "last_check.json")
    assert last["status"] == "error"


# ---------------------------------------------------------------- ТЕСТ 4
def test4_playwright_fallback_matches_baseline():
    d = scraper.fetch_fallback()
    print("\nЗапасной способ (Playwright):", d["method"], d["bundle_file"], d["hero_text"])
    for slug, tr in transports_only(d).items():
        print(f"  {slug:10s}", tr)
    assert sha(transports_only(d)) == BASELINE_SHA256
    assert sha(d["hero_text"]["uz"]) == HERO_UZ_SHA256


def test4b_force_fallback_via_check(tmp_path):
    """Основной способ отключён флагом --force-fallback → check.py работает через Playwright."""
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    r = run("check.py", ["--force-fallback"], {"DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "CONFIRM_DELAY": "0"})
    assert r.returncode == 0
    hist = read_enc(data_dir, "history.json")
    assert hist[0]["method"] == "playwright"
    assert sha(transports_only(hist[0])) == BASELINE_SHA256
    assert "секреты не заданы — Telegram пропущен" in run("notify.py", [], {"OUT_DIR": str(out_dir)}).stdout
