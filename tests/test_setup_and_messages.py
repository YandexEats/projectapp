"""Тесты настройки (get_chat_id.py, set_secrets.ps1) и служебных сообщений."""
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT
from test_monitor import run

PS = "powershell.exe"


# ------------------------------------------------ set_secrets.ps1: проверка ID
@pytest.mark.parametrize("fn,value,ok", [
    ("Test-GroupId", "-1001234567890", True),
    ("Test-GroupId", "-123456789", False),       # обычная группа без -100
    ("Test-GroupId", "123456789", False),        # личный ID
    ("Test-AdminId", "123456789", True),
    ("Test-AdminId", "-1001234567890", False),   # ID группы — отказ
    ("Test-AdminId", "-123", False),
    ("Test-AdminId", "", False),
    ("Test-Token", "123456789:AAH" + "x" * 32, True),
    ("Test-Token", "не токен", False),
])
def test_set_secrets_validation(fn, value, ok):
    cmd = f"$env:SET_SECRETS_SELFTEST='1'; . '{ROOT / 'set_secrets.ps1'}'; {fn} '{value}'"
    r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd], capture_output=True, text=True)
    print(f"{fn} '{value}' → {r.stdout.strip()}")
    assert r.stdout.strip().splitlines()[-1] == ("True" if ok else "False")


# ------------------------------------------------ get_chat_id.py: вывод
def _load_get_chat_id():
    spec = importlib.util.spec_from_file_location("get_chat_id", ROOT / "get_chat_id.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fake(updates):
    def call(token, method):
        if method == "getMe":
            return {"ok": True, "result": {"username": "tezkor_test_bot"}}
        return {"ok": True, "result": updates}
    return call


GROUP_UPD = {"message": {"chat": {"id": -1009876543210, "type": "supergroup", "title": "Курьеры Tezkor"}, "text": "/start"}}
PRIV_UPD = {"message": {"chat": {"id": 555000111, "type": "private", "first_name": "Иван"}, "text": "/start"}}


@pytest.mark.parametrize("updates,code,expect", [
    ([GROUP_UPD, PRIV_UPD], 0, ["ГРУППА: Курьеры Tezkor → -1009876543210", "ЛИЧНЫЙ: Иван → 555000111"]),
    ([], 1, ["Ничего не найдено", "/start", "/start@tezkor_test_bot"]),
    ([PRIV_UPD], 1, ["ЛИЧНЫЙ: Иван → 555000111", "Группа не видна", "режима приватности", "/start@tezkor_test_bot"]),
])
def test_get_chat_id_output(monkeypatch, capsys, updates, code, expect):
    m = _load_get_chat_id()
    monkeypatch.setattr(m.getpass, "getpass", lambda prompt="": "123:TOKEN")
    monkeypatch.setattr(m, "call", _fake(updates))
    assert m.main() == code
    out = capsys.readouterr().out
    print(out)
    for e in expect:
        assert e in out


# ------------------------------------------------ сообщения администратору
@pytest.mark.parametrize("with_history,expect", [
    (False, "Базовый снимок сохранён"),
    (True, "Проверка: изменений нет, бот подключён"),
])
def test_first_run_with_secrets_admin_only(tmp_path, fake_server, live, with_history, expect):
    base, handler = fake_server
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    data_dir.mkdir()
    if with_history:
        from check import build_snapshot
        (data_dir / "history.json").write_text(json.dumps([build_snapshot(live, None, "2026-11-16")], ensure_ascii=False), encoding="utf-8")
        (data_dir / "last_check.json").write_text(json.dumps({"baseline_notified": False}), encoding="utf-8")
    env = {"DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "CONFIRM_DELAY": "0", "BOT_TOKEN": "123:TEST",
           "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base,
           "DASHBOARD_URL": "https://yandexeats.github.io/tezkor-referral-monitor/"}
    assert run("check.py", ["--no-fallback"], env).returncode == 0
    assert run("notify.py", [], env).returncode == 0
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["555"], "только админу, в группу ничего"
    p = handler.calls[0]["payload"]
    print("===== Админу =====\n" + p["text"] + "\nКнопка: " + p["reply_markup"]["inline_keyboard"][0][0]["url"])
    assert expect in p["text"]
    assert json.loads((data_dir / "last_check.json").read_text(encoding="utf-8"))["baseline_notified"] is True
    # повторный запуск — больше не шлёт
    handler.calls.clear()
    run("check.py", ["--no-fallback"], env)
    run("notify.py", [], env)
    assert handler.calls == []


# ------------------------------------------------ ТЕСТ-сообщение в группу
def _digest(folder: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in sorted(folder.glob("*.json"))}


def test_test_group_message_does_not_touch_data(fake_server):
    base, handler = fake_server
    before = _digest(ROOT / "data")
    env = {"BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base,
           "DASHBOARD_URL": "https://yandexeats.github.io/tezkor-referral-monitor/"}
    assert run("notify.py", ["--test-group"], env).returncode == 0
    assert _digest(ROOT / "data") == before, "data/ не должна меняться"
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["-1001"]
    p = handler.calls[0]["payload"]
    url = p["reply_markup"]["inline_keyboard"][0][0]["url"]
    print("===== ТЕСТ в группу =====\n" + p["text"] + "\nКнопка: " + url)
    assert p["text"].startswith("🧪 <b>ТЕСТ, не реальное изменение</b>")
    assert "🚲 Электровело: появился, 600 000 сум, 80 заказов за 30 дней" in p["text"]
    assert url == "https://yandexeats.github.io/tezkor-referral-monitor/?date=2026-09-10&city=namangan&test=1"
