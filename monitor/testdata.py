"""ВЫМЫШЛЕННАЯ история для проверки дашборда по ссылке ?test=1.

Цифры придуманы и не совпадают с настоящими; настоящая история (data/) не читается.
В репозиторий тестовые данные не коммитятся: build_site.py генерирует их при сборке
и публикует ЗАШИФРОВАННЫМИ тем же паролем (testdata/*.json.enc).
"""
from __future__ import annotations

import copy

from diff import compare

T = lambda d, o, b: [{"days": d, "orders": o, "bonus": b}]  # noqa: E731

NAMES = {"tashkent": "Ташкент", "samarkand": "Самарканд", "namangan": "Наманган",
         "andijan": "Андижан", "fergana": "Фергана", "bukhara": "Бухара"}
BASE = {
    "tashkent":  {"moto": T(30, 100, 1000000), "car": T(30, 100, 1000000), "ebike": T(30, 100, 800000), "bike": T(30, 100, 800000), "walking": []},
    "samarkand": {"moto": T(30, 100, 950000),  "car": T(30, 100, 950000),  "ebike": T(30, 100, 750000), "bike": T(30, 100, 750000), "walking": []},
    "andijan":   {"moto": T(30, 70, 900000),   "car": T(30, 70, 900000),   "ebike": T(30, 70, 700000),  "bike": T(30, 70, 700000),  "walking": []},
    "namangan":  {"moto": T(30, 80, 400000),   "car": T(30, 80, 400000),   "ebike": [],                 "bike": T(30, 80, 400000),  "walking": []},
    "fergana":   {"moto": T(30, 80, 450000),   "car": T(30, 80, 450000),   "ebike": [],                 "bike": T(30, 80, 450000),  "walking": []},
    "bukhara":   {"moto": T(30, 80, 420000),   "car": T(30, 80, 420000),   "ebike": [],                 "bike": T(30, 80, 420000),  "walking": []},
}
OFFICES = {s: {"address": f"{NAMES[s]}, ул. Тестовая, 1 (вымышленный адрес)", "days": "Пн–Пт", "hours": "09:00–18:00", "map": None}
           for s in ("tashkent", "samarkand", "namangan")}


def build() -> tuple[list, list, dict]:
    """→ (history, changes, last_check) — полностью вымышленные."""
    base = {"cities": {s: {"name_ru": NAMES[s], "name_uz": None, "transports": copy.deepcopy(t)} for s, t in BASE.items()},
            "offices": copy.deepcopy(OFFICES), "hero_text": None, "notes": None, "city_list": list(BASE), "transport_types": None,
            "date": "2026-06-01", "checked_at": "2026-06-01T05:00:00+00:00", "bundle_file": "/assets/index-TEST0001.js", "method": "bundle"}
    steps = [
        ("2026-07-15", lambda c: c["namangan"]["transports"]["moto"][0].update(bonus=650000)),
        ("2026-08-20", lambda c: c["bukhara"]["transports"]["car"][0].update(orders=100)),
        ("2026-09-10", lambda c: c["namangan"]["transports"].update(ebike=[{"days": 30, "orders": 80, "bonus": 600000}])),
        ("2026-10-05", lambda c: (c["tashkent"]["transports"]["moto"][0].update(bonus=1300000),
                                  c["samarkand"]["transports"]["moto"][0].update(bonus=1100000))),
        ("2026-11-12", lambda c: (c["fergana"]["transports"]["bike"].clear(),
                                  c["andijan"]["transports"]["moto"].append({"days": 60, "orders": 200, "bonus": 1800000}))),
    ]
    history, changes = [base], []
    for date, fn in steps:
        snap = copy.deepcopy(history[-1])
        fn(snap["cities"])
        snap.update(date=date, checked_at=f"{date}T05:00:00+00:00", bundle_file=f"/assets/index-TEST{date.replace('-', '')}.js")
        changes += compare(history[-1], snap, date)
        history.append(snap)
    last = {"checked_at": "2026-11-16T05:00:00+00:00", "status": "no_change", "method": "bundle",
            "bundle_file": history[-1]["bundle_file"], "run_id": "test", "last_change_date": "2026-11-12"}
    return history, changes, last
