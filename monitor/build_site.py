"""Сборка дашборда для GitHub Pages: python monitor/build_site.py _site

- копирует site/ (код дашборда);
- копирует ТОЛЬКО зашифрованные data/*.json.enc (открытые data/*.json не публикуются никогда);
- генерирует вымышленные тестовые данные и шифрует их тем же паролем (testdata/*.json.enc);
- в конце проверяет, что в сборке нет ни одного открытого *.json и что все *.enc — шифртекст.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA_DIR, ROOT, log  # noqa: E402
import testdata  # noqa: E402
from vault import DATA_NAMES, PasswordMissing, Vault, VaultError, check_envelope, password_from_env  # noqa: E402


def verify_no_plaintext(out: Path) -> list[str]:
    problems = []
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out).as_posix()
        if p.suffix == ".json":
            problems.append(f"открытый JSON в сборке: {rel}")
        elif p.name.endswith(".enc"):
            try:
                check_envelope(json.loads(p.read_text(encoding="utf-8")))
            except (ValueError, VaultError) as e:
                problems.append(f"{rel}: не похоже на зашифрованный конверт ({e})")
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
    shutil.copytree(ROOT / "site", out, ignore=shutil.ignore_patterns("testdata", "*.json"))

    copied = []
    for name in DATA_NAMES:
        src = vault.enc_path(name)
        if src.exists():
            (out / "data").mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out / "data" / src.name)
            copied.append(src.name)
        elif vault.plain_path(name).exists():
            log(f"ВНИМАНИЕ: {name} ещё в открытом виде — НЕ публикуется. Его зашифрует ближайшая проверка (monitor.yml).")
    try:
        for name in DATA_NAMES:  # пароль должен подходить к публикуемым данным, иначе дашборд не откроется
            if vault.enc_path(name).exists():
                vault.load(name, None)
        salt = vault.salt()  # та же соль, что у настоящих данных → один «запомненный» ключ подходит к обоим
    except VaultError as e:
        log("ОШИБКА: " + str(e))
        return 1

    history, changes, last = testdata.build()
    tv = Vault(out / "testdata", vault.password, salt=salt)
    for name, obj in (("history.json", history), ("changes.json", changes), ("last_check.json", last)):
        tv.save(name, obj)

    problems = verify_no_plaintext(out)
    if problems:
        for p in problems:
            log("ОШИБКА: " + p)
        return 1
    log(f"Сборка готова: {out} · данные: {', '.join(copied) or 'нет'} · тестовые данные: 3 файла (зашифрованы)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
