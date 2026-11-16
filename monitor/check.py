"""Фаза 1: загрузить → разобрать → сравнить → дописать историю.

Данные хранятся ЗАШИФРОВАННЫМИ (data/*.json.enc, см. vault.py, пароль — секрет DASHBOARD_PASSWORD).
Без пароля или с неверным паролем: ошибка, предупреждение администратору, данные не трогаются.
--reencrypt: только перешифровать данные (после смены пароля), сайт не проверяется.

Пишет data/*.json.enc и out/pending.json (сообщения, которые notify.py отправит
ПОСЛЕ коммита и публикации дашборда). При поломке сайта сразу шлёт
предупреждение администратору и завершается с кодом 1.
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
import time
from pathlib import Path

from common import (DATA_DIR, ROOT, dashboard_url, fmt_sum, log, now_utc_iso,
                    run_url, today_tashkent, write_json)
from diff import compare
from scraper import fetch_with_fallback, validate
import telegram
from vault import DATA_NAMES, Vault, VaultError

OUT_DIR = Path(os.environ.get("OUT_DIR", ROOT / "out"))
SECONDARY = ("offices", "notes", "transport_types")


def build_snapshot(data: dict, prev: dict | None, date: str) -> dict:
    snap = {
        "date": date,
        "checked_at": now_utc_iso(),
        "bundle_file": data.get("bundle_file"),
        "method": data.get("method"),
        "cities": data.get("cities") or {},
        "hero_text": data.get("hero_text"),
        "notes": data.get("notes"),
        "offices": data.get("offices"),
        "city_list": data.get("city_list"),
        "transport_types": data.get("transport_types"),
    }
    # Запасной способ не видит часть вторичных данных — переносим их из прошлого снимка
    if prev:
        for k in SECONDARY:
            if snap.get(k) is None:
                snap[k] = copy.deepcopy(prev.get(k))
        if snap["hero_text"] and prev.get("hero_text"):
            for lang in ("ru", "uz"):
                if snap["hero_text"].get(lang) is None:
                    snap["hero_text"][lang] = prev["hero_text"].get(lang)
        for slug, c in snap["cities"].items():
            pc = prev.get("cities", {}).get(slug)
            if pc and data.get("method") != "bundle":
                c["name_ru"] = pc.get("name_ru") or c.get("name_ru")
                c["name_uz"] = pc.get("name_uz") or c.get("name_uz")
    return snap


def gh_output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def alert_admin_broken(errors: list[str]) -> None:
    telegram.alert_admin("⚠️ <b>Не удалось прочитать сайт, возможно изменилась структура</b>\n\n"
                         "Ошибка:\n<code>" + telegram._e("\n".join(errors))[:3000] + "</code>")


def open_vault() -> tuple[Vault | None, tuple | None]:
    """Открыть зашифрованные данные. При любой проблеме с паролем — ошибка и предупреждение админу, данные не трогаем."""
    try:
        vault = Vault.from_env(DATA_DIR)
        history = vault.load("history.json", [])
        change_log = vault.load("changes.json", [])
        last = vault.load("last_check.json", {})
    except VaultError as e:
        log(f"::error title=Шифрование данных::{e}")
        log("ОШИБКА: " + str(e))
        telegram.alert_admin("⛔ <b>Дашборд: проблема с паролем данных</b>\n\n" + telegram._e(str(e)))
        gh_output(status="error", has_pending="false")
        return None, None
    return vault, (history, change_log, last)


def reencrypt(vault: Vault, history, change_log, last, emit: bool = True) -> int:
    """Перешифровать все файлы: новый IV, а при смене пароля — новая соль. Содержимое не меняется."""
    if vault.used_old_password or vault.migrated:
        vault.new_salt()
    for name, obj in (("history.json", history), ("changes.json", change_log), ("last_check.json", last)):
        vault.save(name, obj)
    how = "старым паролем (DASHBOARD_PASSWORD_OLD)" if vault.used_old_password else "текущим паролем"
    log(f"Перешифровано: {', '.join(DATA_NAMES)} (прочитано {how}"
        + (f"; из открытого формата: {', '.join(vault.migrated)}" if vault.migrated else "") + ").")
    if emit:
        gh_output(status="reencrypted", has_pending="false")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm-delay", type=int, default=int(os.environ.get("CONFIRM_DELAY", "120")))
    ap.add_argument("--force-fallback", action="store_true", help="сразу использовать Playwright")
    ap.add_argument("--no-fallback", action="store_true", help="не использовать Playwright")
    ap.add_argument("--allow-city-removal", action="store_true")
    ap.add_argument("--reencrypt", action="store_true", help="только перешифровать данные (после смены пароля)")
    args = ap.parse_args(argv)

    vault, loaded = open_vault()
    if vault is None:
        return 2
    history, change_log, last = loaded
    if args.reencrypt:
        return reencrypt(vault, history, change_log, last)
    if vault.migrated or vault.used_old_password:
        # Старые открытые *.json (или данные под старым паролем) — сразу шифруем всё, до любых других шагов
        reencrypt(vault, history, change_log, last, emit=False)
    prev = history[-1] if history else None
    date = today_tashkent()
    pending = {"group": None, "admin": []}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(OUT_DIR / "pending.json", pending)

    last_info = {
        "checked_at": now_utc_iso(), "run_id": os.environ.get("GITHUB_RUN_ID"), "run_url": run_url(),
        "baseline_notified": bool(last.get("baseline_notified")),
        "last_change_date": last.get("last_change_date"),
        "history_start": history[0]["date"] if history else None,
    }

    def fetch_ok():
        d, errs = fetch_with_fallback(force_fallback=args.force_fallback, disable_fallback=args.no_fallback)
        probs = validate(d, prev, allow_city_removal=args.allow_city_removal) if d else ["данные не получены"]
        return d, errs, probs

    data, errors, problems = fetch_ok()
    if problems:
        all_err = errors + problems
        log("ОШИБКА: сайт не прочитан или данные выглядят сломанными: " + "; ".join(all_err))
        alert_admin_broken(all_err)
        last_info.update(status="error", method=(data or {}).get("method"), bundle_file=(data or {}).get("bundle_file"), error="; ".join(all_err))
        vault.save("last_check.json", last_info)
        gh_output(status="error", has_pending="false")
        log("История НЕ изменена, сообщение в группу НЕ отправлялось.")
        return 1

    if data["method"] != "bundle":
        pending["admin"].append({"text": "⚠️ Основной способ (разбор бандла) не сработал, данные получены запасным способом (Playwright).\n"
                                         + telegram._e("; ".join(errors)) + (f"\n🔗 {run_url()}" if run_url() else ""), "url": None})

    snapshot = build_snapshot(data, prev, date)
    status = "no_change"
    changes: list[dict] = []
    if prev is None:
        history.append(snapshot)
        status = "baseline"
        log(f"Сохранён базовый снимок ({len(snapshot['cities'])} городов, метод {snapshot['method']}).")
    else:
        changes = compare(prev, snapshot, date)
        attempts = 0
        while changes and attempts < 3:
            attempts += 1
            log(f"Обнаружено изменений: {len(changes)}. Перепроверка через {args.confirm_delay} с…")
            time.sleep(args.confirm_delay)
            d2, e2, p2 = fetch_ok()
            if p2:
                log("Перепроверка не удалась: " + "; ".join(e2 + p2) + " — считаю изменения неподтверждёнными.")
                changes = []
                status = "unconfirmed"
                break
            snap2 = build_snapshot(d2, prev, date)
            if compare(snapshot, snap2, date) == []:
                log("Изменения подтверждены повторной загрузкой.")
                break
            log("Повторная загрузка дала другой результат — сайт, вероятно, обновляется. Пробую ещё раз.")
            snapshot, data = snap2, d2
            changes = compare(prev, snapshot, date)
        else:
            if changes:
                log("Сайт нестабилен 3 раза подряд — изменения не записаны.")
                changes, status = [], "unconfirmed"
        if changes:
            status = "changed"
            history.append(snapshot)
            change_log.extend(changes)
            last_info["last_change_date"] = date
            city_names = {s: c.get("name_ru") for s, c in snapshot["cities"].items()}
            for s, c in prev.get("cities", {}).items():
                city_names.setdefault(s, c.get("name_ru"))
            text = telegram.build_change_message(changes, date, city_names)
            pending["group"] = {"text": text, "url": telegram.details_url(dashboard_url(), date, changes)}
            # В публичный лог — только количество; суммы и текст уходят лишь в Telegram и в зашифрованную историю
            log(f"Изменения записаны: изменений: {len(changes)}, городов: {len({c.get('city') for c in changes})}.")
        elif status == "no_change":
            log("Изменений нет.")

    if telegram.secrets() and not last_info["baseline_notified"]:
        # Первый запуск с секретами: служебное сообщение ТОЛЬКО администратору (в личку)
        first = history[0]
        top = fmt_sum(max((t['bonus'] for c in first['cities'].values() for ts in c['transports'].values() for t in ts), default=0))
        if status == "baseline":
            head = "✅ <b>Базовый снимок сохранён</b>"
        elif status == "changed":
            head = "✅ <b>Проверка: бот подключён.</b> Найдены изменения — сообщение отправлено в группу."
        else:
            head = "✅ <b>Проверка: изменений нет, бот подключён</b>"
        pending["admin"].append({
            "text": f"{head}\nИстория начинается с {first['date']} (городов: {len(first['cities'])}, "
                    f"максимальный бонус: {top} сум).\n"
                    "Дальше: проверка каждый день в 10:00 (Ташкент). В группу пишу только при изменениях, "
                    "сюда — только служебные сообщения и предупреждения.",
            "url": dashboard_url(),
            "kind": "hello",  # отметку baseline_notified ставит notify.py только после успешной отправки
        })

    last_info.update(status=status, method=snapshot["method"], bundle_file=snapshot["bundle_file"], error=None,
                     history_start=history[0]["date"] if history else None, snapshots=len(history))
    vault.save("history.json", history)
    vault.save("changes.json", change_log)
    vault.save("last_check.json", last_info)
    write_json(OUT_DIR / "pending.json", pending)
    has_pending = bool(pending["group"] or pending["admin"])
    gh_output(status=status, has_pending=str(has_pending).lower())
    log(f"Готово: статус={status}, метод={snapshot['method']}, бандл={snapshot['bundle_file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
