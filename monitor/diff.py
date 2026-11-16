"""Сравнение двух снимков и построение записей журнала изменений."""
from __future__ import annotations

from common import TRANSPORT_ORDER


def _tsort(keys):
    return sorted(keys, key=lambda t: (TRANSPORT_ORDER.index(t) if t in TRANSPORT_ORDER else 99, t))


def _max_tier(tiers):
    return max(tiers, key=lambda x: x["bonus"]) if tiers else None


def compare(old: dict, new: dict, date: str) -> list[dict]:
    """Возвращает список изменений {date, city, transport, field, old, new, diff, ...}."""
    changes: list[dict] = []

    def add(city, transport, field, o, n, diff=None, **extra):
        rec = {"date": date, "city": city, "transport": transport, "field": field, "old": o, "new": n, "diff": diff}
        rec.update(extra)
        changes.append(rec)

    oc, nc = old.get("cities", {}), new.get("cities", {})
    for slug in nc:
        if slug not in oc:
            add(slug, None, "city_added", None, nc[slug].get("name_ru"), transports=nc[slug].get("transports"))
    for slug in oc:
        if slug not in nc:
            add(slug, None, "city_removed", oc[slug].get("name_ru"), None)

    for slug in [s for s in nc if s in oc]:
        ot, nt = oc[slug].get("transports", {}), nc[slug].get("transports", {})
        for t in _tsort(set(ot) | set(nt)):
            a, b = ot.get(t) or [], nt.get(t) or []
            if a == b:
                continue
            if not a and b:
                top = _max_tier(b)
                add(slug, t, "transport_added", None, top["bonus"], top["bonus"], orders=top["orders"], days=top["days"], tiers=b)
                continue
            if a and not b:
                top = _max_tier(a)
                add(slug, t, "transport_removed", top["bonus"], None, -top["bonus"], orders=top["orders"], days=top["days"], tiers=a)
                continue
            for i in range(min(len(a), len(b))):
                x, y = a[i], b[i]
                ctx = {"tier": i + 1, "tiers_count": len(b)}
                if x["bonus"] != y["bonus"]:
                    add(slug, t, "bonus", x["bonus"], y["bonus"], y["bonus"] - x["bonus"], orders=y["orders"], days=y["days"], **ctx)
                if x["orders"] != y["orders"]:
                    add(slug, t, "orders", x["orders"], y["orders"], y["orders"] - x["orders"], days=y["days"], bonus=y["bonus"], **ctx)
                if x["days"] != y["days"]:
                    add(slug, t, "days", x["days"], y["days"], y["days"] - x["days"], orders=y["orders"], bonus=y["bonus"], **ctx)
            for i in range(len(a), len(b)):
                y = b[i]
                add(slug, t, "tier_added", None, y["bonus"], y["bonus"], orders=y["orders"], days=y["days"], tier=i + 1)
            for i in range(len(b), len(a)):
                x = a[i]
                add(slug, t, "tier_removed", x["bonus"], None, -x["bonus"], orders=x["orders"], days=x["days"], tier=i + 1)

    # --- вторичные тексты (сравниваем, только если известны в обоих снимках) ---
    def text_cmp(label, city, o, n):
        if o is not None and n is not None and o != n:
            add(city, None, "text", o, n, None, label=label)

    oh, nh = old.get("hero_text") or {}, new.get("hero_text") or {}
    text_cmp("Заголовок (RU)", None, oh.get("ru"), nh.get("ru"))
    text_cmp("Заголовок (UZ)", None, oh.get("uz"), nh.get("uz"))
    on, nn = old.get("notes") or {}, new.get("notes") or {}
    if old.get("notes") is not None and new.get("notes") is not None:
        text_cmp("Примечание по Ташкенту (RU)", "tashkent", on.get("tashkent_ru"), nn.get("tashkent_ru"))
        text_cmp("Примечание по Ташкенту (UZ)", "tashkent", on.get("tashkent_uz"), nn.get("tashkent_uz"))
    oo, no = old.get("offices"), new.get("offices")
    if oo is not None and no is not None:
        for slug in sorted(set(oo) | set(no)):
            a, b = oo.get(slug) or {}, no.get(slug) or {}
            for key, label in (("address", "Адрес офиса"), ("hours", "Часы работы офиса"), ("days", "Дни работы офиса")):
                if a.get(key) != b.get(key):
                    add(slug, None, "text", a.get(key), b.get(key), None, label=label)
    ol, nl = old.get("city_list"), new.get("city_list")
    if ol is not None and nl is not None and ol != nl and set(ol) != set(nl):
        add(None, None, "text", ", ".join(ol), ", ".join(nl), None, label="Список городов")
    return changes


def comparable(snapshot: dict) -> dict:
    """Часть снимка, по которой определяется «изменилось ли что-то»."""
    return {k: snapshot.get(k) for k in ("cities", "hero_text", "notes", "offices", "city_list")}
