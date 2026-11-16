"""Фаза 2: после коммита и публикации дашборда отправляет сообщения из out/pending.json.

Перед отправкой ждёт (до 3 минут), пока живой data/last_check.json на GitHub Pages
покажет текущий запуск — чтобы кнопка «Подробнее» уже открывала свежие данные.

--test-group : отправить ОДНО тестовое сообщение (с пометкой ТЕСТ) в группу.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from common import DATA_DIR, ROOT, dashboard_url, http_get, log, read_json, write_json
import telegram

OUT = Path(os.environ.get("OUT_DIR", ROOT / "out")) / "pending.json"


def wait_for_pages(max_wait: int = 180) -> bool:
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        return True
    url = dashboard_url() + "data/last_check.json"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            live = json.loads(http_get(f"{url}?t={int(time.time())}", retries=1))
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
    """ТЕСТ-сообщение в реальном формате на ВЫМЫШЛЕННОМ изменении из site/testdata (data/ не читается и не меняется)."""
    changes = read_json(ROOT / "site" / "testdata" / "changes.json", [])
    sample = [c for c in changes if c.get("city") == TEST_CITY and c.get("date") == TEST_DATE]
    if not sample:
        raise RuntimeError("В site/testdata/changes.json нет тестового изменения")
    text = telegram.build_change_message(sample, TEST_DATE, {TEST_CITY: "Наманган"}, test=True)
    text += "\n\n<i>Кнопка «📊 Подробнее» откроет дашборд с тестовыми данными.</i>"
    url = telegram.details_url(dashboard_url(), TEST_DATE, sample, test=True)
    return text, url


def send_test_group(s: dict) -> None:
    text, url = build_test_group_message()
    log("ТЕСТ-сообщение в группу:\n" + text + f"\nКнопка: {url}")
    telegram.send(s["token"], s["group"], text, url)


def mark_hello_sent() -> None:
    """Отметить в data/last_check.json, что приветствие администратору доставлено."""
    path = DATA_DIR / "last_check.json"
    last = read_json(path, {})
    last["baseline_notified"] = True
    write_json(path, last)
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
