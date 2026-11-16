"""Сборка дашборда для GitHub Pages: python monitor/build_site.py _site

До ввода пароля страница должна быть нейтральной: никаких упоминаний темы проекта.
- в открытом виде публикуются только site/index.html, site/app.js (форма входа) и site/style.css;
- разметка и код дашборда (site/dashboard.html, site/dashboard.js) публикуются ЗАШИФРОВАННЫМИ: ui.json.enc;
- данные data/*.json.enc перешифровываются в текущий формат (v2) с новым IV — открытые data/*.json
  не публикуются никогда;
- вымышленные тестовые данные шифруются тем же паролем (testdata/*.json.enc);
- в конце: в сборке нет открытых *.json, все *.enc — конверты v2, и в открытых файлах нет слов
  из PUBLIC_FORBIDDEN (иначе сборка падает).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA_DIR, ROOT, log  # noqa: E402
import testdata  # noqa: E402
from vault import (DATA_NAMES, VERSION, PasswordMissing, Vault, VaultError, check_envelope,  # noqa: E402
                   password_from_env)

SITE = ROOT / "site"
PRIVATE_FILES = ("dashboard.html", "dashboard.js")   # только внутри ui.json.enc
# Слова, которых не должно быть ни в одном открытом файле сайта (проверка без учёта регистра)
PUBLIC_FORBIDDEN = ["uzum", "tezkor", "реферал", "курьер", "бонус", "referral", "courier", "bonus"]


def forbidden_in(text: str) -> list[str]:
    low = text.lower()
    return [w for w in PUBLIC_FORBIDDEN if w in low]


def verify_site(out: Path) -> list[str]:
    problems = []
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out).as_posix()
        if p.suffix == ".json":
            problems.append(f"открытый JSON в сборке: {rel}")
        elif p.name.endswith(".enc"):
            try:
                env = json.loads(p.read_text(encoding="utf-8"))
                check_envelope(env)
                if env["v"] != VERSION:
                    problems.append(f"{rel}: формат v{env['v']}, а дашборд читает только v{VERSION}")
            except (ValueError, VaultError) as e:
                problems.append(f"{rel}: не похоже на зашифрованный конверт ({e})")
        else:
            words = forbidden_in(p.read_text(encoding="utf-8", errors="replace"))
            if words:
                problems.append(f"{rel}: в открытом файле есть слова {words}")
            if p.name in PRIVATE_FILES:
                problems.append(f"{rel}: должен публиковаться только внутри ui.json.enc")
    return problems


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0] if argv else ROOT / "_site")
    try:
        vault = Vault(DATA_DIR, password_from_env())  # только текущий пароль: старый для публикации не годится
    except PasswordMissing as e:
        log("ОШИБКА: " + str(e))
        print(f"::error title=Не задан DASHBOARD_PASSWORD::{e}")
        return 1
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE, out, ignore=shutil.ignore_patterns("testdata", "*.json", *PRIVATE_FILES))

    try:
        # Пароль должен подходить к данным, иначе дашборд не откроется
        data = {n: vault.load(n, None) for n in DATA_NAMES if vault.enc_path(n).exists()}
        salt = vault.salt()  # одна соль для данных, тестовых данных и интерфейса → один «запомненный» ключ
    except VaultError as e:
        log("ОШИБКА: " + str(e))
        return 1
    for n in DATA_NAMES:
        if n not in data and vault.plain_path(n).exists():
            log(f"ВНИМАНИЕ: {n} ещё в открытом виде — НЕ публикуется. Его зашифрует ближайшая проверка (monitor.yml).")

    pub = Vault(out, vault.password, salt=salt)
    pub.save("ui.json", {"html": (SITE / "dashboard.html").read_text(encoding="utf-8"),
                         "js": (SITE / "dashboard.js").read_text(encoding="utf-8")})
    dv = Vault(out / "data", vault.password, salt=salt)
    for n, obj in data.items():
        dv.save(n, obj)  # текущий формат, новый IV
    history, changes, last = testdata.build()
    tv = Vault(out / "testdata", vault.password, salt=salt)
    for n, obj in (("history.json", history), ("changes.json", changes), ("last_check.json", last)):
        tv.save(n, obj)

    problems = verify_site(out)
    if problems:
        for p in problems:
            log("ОШИБКА: " + p)
        return 1
    log(f"Сборка готова: {out} · интерфейс: ui.json.enc · данные: {', '.join(n + '.enc' for n in data) or 'нет'} "
        "· тестовые данные: 3 файла (зашифрованы) · открытые файлы проверены на нейтральность")
    return 0


if __name__ == "__main__":
    sys.exit(main())
