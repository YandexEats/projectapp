"""Тесты настройки (get_chat_id.py, set_secrets.ps1) и служебных сообщений."""
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT, read_enc, write_enc
from test_monitor import run
from common import dashboard_url, github_repo

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
        write_enc(data_dir, "history.json", [build_snapshot(live, None, "2026-11-16")])
        write_enc(data_dir, "last_check.json", {"baseline_notified": False})
    env = {"DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "CONFIRM_DELAY": "0", "BOT_TOKEN": "123:TEST",
           "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base}  # адрес дашборда — из config.json
    assert run("check.py", ["--no-fallback"], env).returncode == 0
    assert run("notify.py", [], env).returncode == 0
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["555"], "только админу, в группу ничего"
    p = handler.calls[0]["payload"]
    print("===== Админу =====\n" + p["text"] + "\nКнопка: " + p["reply_markup"]["inline_keyboard"][0][0]["url"])
    assert expect in p["text"]
    assert read_enc(data_dir, "last_check.json")["baseline_notified"] is True
    assert not list(data_dir.glob("*.json")), "открытых файлов быть не должно"
    # повторный запуск — больше не шлёт
    handler.calls.clear()
    run("check.py", ["--no-fallback"], env)
    run("notify.py", [], env)
    assert handler.calls == []


def test_hello_not_marked_when_telegram_refuses(tmp_path, fake_server, live):
    """Telegram ответил «chat not found» → отметка не ставится, при следующем запуске приветствие повторится."""
    base, handler = fake_server
    handler.fail_chats.add("555")
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    data_dir.mkdir()
    from check import build_snapshot
    write_enc(data_dir, "history.json", [build_snapshot(live, None, "2026-11-16")])
    env = {"DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "CONFIRM_DELAY": "0", "BOT_TOKEN": "123:TEST",
           "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base}
    assert run("check.py", ["--no-fallback"], env).returncode == 0
    r = run("notify.py", [], env)
    assert r.returncode == 1 and "chat not found" in r.stdout
    assert read_enc(data_dir, "last_check.json")["baseline_notified"] is False
    handler.fail_chats.clear(); handler.calls.clear()
    run("check.py", ["--no-fallback"], env)
    assert run("notify.py", [], env).returncode == 0
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["555"]
    assert read_enc(data_dir, "last_check.json")["baseline_notified"] is True


# ------------------------------------------------ ТЕСТ-сообщение в группу
def _digest(folder: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in sorted(folder.glob("*")) if p.is_file()}


def test_test_group_message_does_not_touch_data(fake_server):
    base, handler = fake_server
    before = _digest(ROOT / "data")
    env = {"BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base}
    assert run("notify.py", ["--test-group"], env).returncode == 0
    assert _digest(ROOT / "data") == before, "data/ не должна меняться"
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["-1001"]
    p = handler.calls[0]["payload"]
    url = p["reply_markup"]["inline_keyboard"][0][0]["url"]
    print("===== ТЕСТ в группу =====\n" + p["text"] + "\nКнопка: " + url)
    assert p["text"].startswith("🧪 <b>ТЕСТ, не реальное изменение</b>")
    assert "🚲 Электровело: появился, 600 000 сум, 80 заказов за 30 дней" in p["text"]
    assert url == dashboard_url() + "?date=2026-09-10&city=namangan&test=1"


# ------------------------------------------------ адрес дашборда: одно место (config.json)
def test_dashboard_url_comes_from_config(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    repo = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["github_repo"]
    owner, name = repo.split("/")
    print(f"\nconfig.json: {repo} → {dashboard_url()}")
    assert dashboard_url() == f"https://{owner.lower()}.github.io/{name}/"
    assert github_repo() == repo
    # ps1-скрипты читают то же значение
    for script in ("set_secrets.ps1", "set_dashboard_password.ps1"):
        cmd = f"$env:SET_SECRETS_SELFTEST='1'; . '{ROOT / script}'; $repo"
        r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd], capture_output=True, text=True)
        print(f"{script}: $repo = {r.stdout.strip().splitlines()[-1]}")
        assert r.stdout.strip().splitlines()[-1] == repo
