"""Фаза 2: после коммита и публикации дашборда отправляет сообщения из out/pending.json.

Перед отправкой ждёт (до 3 минут), пока живой data/last_check.json.enc на GitHub Pages
(расшифровывается паролем DASHBOARD_PASSWORD) покажет текущий запуск — чтобы кнопка «Подробнее» уже открывала свежие данные.

--test-group : отправить ОДНО тестовое сообщение (с пометкой ТЕСТ) в группу.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from common import DATA_DIR, ROOT, dashboard_url, http_get, log, read_json
import telegram
import testdata
from vault import Vault, VaultError, decrypt_obj, password_from_env

OUT = Path(os.environ.get("OUT_DIR", ROOT / "out")) / "pending.json"


def wait_for_pages(max_wait: int = 180) -> bool:
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        return True
    password = password_from_env()
    if not password:
        log("DASHBOARD_PASSWORD не задан — не могу проверить свежесть дашборда, не жду.")
        return False
    url = dashboard_url() + "data/last_check.json.enc"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            live = decrypt_obj(json.loads(http_get(f"{url}?t={int(time.time())}", retries=1)), "last_check.json", password)
            if str(live.get("run_id")) == str(run_id):
                log(f"Дашборд обновлён: {url} показывает запуск {run_id}")
                return True
            log(f"Дашборд пока показывает запуск {live.get('run_id')}, жду…")
        except Exception as e:  # noqa: BLE001
            log(f"Дашборд недоступен ({e}), жду…")
        time.sleep(15)
    log("За 3 минуты дашборд не обновился — отправляю сообщение всё равно.")
    return False


TEST_CITY, TEST_DATE = "namangan", "2026-09-10"


def build_test_group_message() -> tuple[str, str]:
    """ТЕСТ-сообщение в реальном формате на ВЫМЫШЛЕННОМ изменении из testdata.py (data/ не читается и не меняется)."""
    changes = testdata.build()[1]
    sample = [c for c in changes if c.get("city") == TEST_CITY and c.get("date") == TEST_DATE]
    if not sample:
        raise RuntimeError("В вымышленных тестовых данных (monitor/testdata.py) нет тестового изменения")
    text = telegram.build_change_message(sample, TEST_DATE, {TEST_CITY: "Наманган"}, test=True)
    text += "\n\n<i>Кнопка «📊 Подробнее» откроет дашборд с тестовыми данными.</i>"
    url = telegram.details_url(dashboard_url(), TEST_DATE, sample, test=True)
    return text, url


def send_test_group(s: dict) -> None:
    text, url = build_test_group_message()
    log("ТЕСТ-сообщение в группу:\n" + text + f"\nКнопка: {url}")
    telegram.send(s["token"], s["group"], text, url)


def mark_hello_sent() -> None:
    """Отметить в data/last_check.json.enc, что приветствие администратору доставлено."""
    try:
        vault = Vault.from_env(DATA_DIR)
        last = vault.load("last_check.json", {})
        last["baseline_notified"] = True
        vault.save("last_check.json", last)
    except VaultError as e:
        log(f"Отметку baseline_notified сохранить не удалось: {e}")
        return
    log("Отметка baseline_notified=true сохранена.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-group", action="store_true")
    args = ap.parse_args(argv)
    s = telegram.secrets()
    if not s:
        log(telegram.SKIP_MSG)
        return 0
    if args.test_group:
        send_test_group(s)
        return 0
    pending = read_json(OUT, {"group": None, "admin": []})
    if not pending.get("group") and not pending.get("admin"):
        log("Отправлять нечего.")
        return 0
    if pending.get("group") or any(m.get("url") for m in pending.get("admin", [])):
        wait_for_pages()
    ok = True
    for m in pending.get("admin", []):
        try:
            telegram.send(s["token"], s["admin"], m["text"], m.get("url"))
            if m.get("kind") == "hello":
                mark_hello_sent()
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"Ошибка отправки администратору: {e}")
            if "chat not found" in str(e):
                log("Подсказка: ADMIN_CHAT_ID должен быть вашим личным ID, и вы должны написать боту /start в личном чате.")
    if pending.get("group"):
        try:
            telegram.send(s["token"], s["group"], pending["group"]["text"], pending["group"]["url"])
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"Ошибка отправки в группу: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
