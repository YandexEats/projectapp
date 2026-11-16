"""Генерирует ВЫМЫШЛЕННУЮ историю для проверки дашборда (site/testdata/*.json).

Открывается только по ссылке ?test=1, настоящую историю (data/) не трогает.
Запуск: python tests/make_testdata.py
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "monitor"))
sys.path.insert(0, str(ROOT / "tests"))

from common import read_json, write_json  # noqa: E402
from diff import compare  # noqa: E402

OUT = ROOT / "site" / "testdata"


def main():
    real = read_json(ROOT / "data" / "history.json", [])
    if real:
        base = copy.deepcopy(real[0])
    else:
        from conftest import BASELINE
        names = {"tashkent": "Ташкент", "samarkand": "Самарканд", "namangan": "Наманган", "andijan": "Андижан", "fergana": "Фергана", "bukhara": "Бухара"}
        base = {"cities": {s: {"name_ru": names[s], "name_uz": None, "transports": copy.deepcopy(t)} for s, t in BASELINE.items()},
                "offices": None, "hero_text": None, "notes": None, "city_list": list(BASELINE)}
    base.update(date="2026-06-01", checked_at="2026-06-01T05:00:00+00:00", bundle_file="/assets/index-TEST0001.js", method="bundle")

    steps = [
        ("2026-07-15", lambda c: c["namangan"]["transports"]["moto"][0].update(bonus=700000)),
        ("2026-08-20", lambda c: c["bukhara"]["transports"]["car"][0].update(orders=100)),
        ("2026-09-10", lambda c: c["namangan"]["transports"].update(ebike=[{"days": 30, "orders": 80, "bonus": 600000}])),
        ("2026-10-05", lambda c: (c["tashkent"]["transports"]["moto"][0].update(bonus=1500000),
                                  c["samarkand"]["transports"]["moto"][0].update(bonus=1000000))),
        ("2026-11-12", lambda c: (c["fergana"]["transports"]["bike"].clear(),
                                  c["andijan"]["transports"]["moto"].append({"days": 60, "orders": 200, "bonus": 2000000}))),
    ]
    history, changes = [base], []
    for date, fn in steps:
        snap = copy.deepcopy(history[-1])
        fn(snap["cities"])
        snap.update(date=date, checked_at=f"{date}T05:00:00+00:00", bundle_file=f"/assets/index-TEST{date.replace('-', '')}.js")
        changes += compare(history[-1], snap, date)
        history.append(snap)
    write_json(OUT / "history.json", history)
    write_json(OUT / "changes.json", changes)
    write_json(OUT / "last_check.json", {"checked_at": "2026-11-16T05:00:00+00:00", "status": "no_change", "method": "bundle",
                                         "bundle_file": history[-1]["bundle_file"], "run_id": "test", "last_change_date": "2026-11-12"})
    print(f"Тестовые данные: {len(history)} снимков, {len(changes)} изменений → {OUT}")


if __name__ == "__main__":
    main()
