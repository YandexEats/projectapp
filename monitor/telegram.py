"""Формирование и отправка сообщений в Telegram."""
from __future__ import annotations

import html
import os
from urllib.parse import urlencode

from common import CITY_RU_FALLBACK, TRANSPORT_ICON, TRANSPORT_RU, fmt_diff, fmt_sum, http_post_json, log

SKIP_MSG = "секреты не заданы — Telegram пропущен"


def secrets() -> dict | None:
    token = os.environ.get("BOT_TOKEN", "").strip()
    group = os.environ.get("GROUP_CHAT_ID", "").strip()
    admin = os.environ.get("ADMIN_CHAT_ID", "").strip() or group
    if not token or not group:
        return None
    return {"token": token, "group": group, "admin": admin}


def _e(s) -> str:
    return html.escape(str(s), quote=False)


def _cond(orders, days) -> str:
    return f"{orders} заказов за {days} дней"


def _change_line(c: dict, city_names: dict) -> str:
    t = c.get("transport")
    icon = TRANSPORT_ICON.get(t, "•")
    tname = TRANSPORT_RU.get(t, t or "")
    tier = f" (уровень {c['tier']})" if c.get("tier") and (c.get("tiers_count", 1) > 1 or c["field"].startswith("tier_")) else ""
    f = c["field"]
    if f == "bonus":
        return f"{icon} {tname}{tier}: {fmt_sum(c['old'])} → {fmt_sum(c['new'])} сум ({fmt_diff(c['diff'])}), {_cond(c['orders'], c['days'])}"
    if f == "orders":
        return f"{icon} {tname}{tier}: условие {c['old']} → {c['new']} заказов за {c['days']} дней"
    if f == "days":
        return f"{icon} {tname}{tier}: срок {c['old']} → {c['new']} дней ({c['orders']} заказов)"
    if f == "transport_added":
        return f"{icon} {tname}: появился, {fmt_sum(c['new'])} сум, {_cond(c['orders'], c['days'])}"
    if f == "transport_removed":
        return f"{icon} {tname}: убран (было {fmt_sum(c['old'])} сум, {_cond(c['orders'], c['days'])})"
    if f == "tier_added":
        return f"{icon} {tname}: новый уровень {c['tier']} — {fmt_sum(c['new'])} сум, {_cond(c['orders'], c['days'])}"
    if f == "tier_removed":
        return f"{icon} {tname}: убран уровень {c['tier']} (было {fmt_sum(c['old'])} сум)"
    if f == "city_added":
        return "🆕 Город добавлен в программу"
    if f == "city_removed":
        return "❌ Город убран из программы"
    if f == "text":
        o, n = c.get("old"), c.get("new")
        short = lambda s: (s[:120] + "…") if s and len(s) > 120 else (s or "—")  # noqa: E731
        return f"📝 {c.get('label', 'Текст')}: «{short(o)}» → «{short(n)}»"
    return f"• {f}: {c.get('old')} → {c.get('new')}"


def build_change_message(changes: list[dict], date: str, city_names: dict, test: bool = False) -> str:
    lines = []
    if test:
        lines.append("🧪 <b>ТЕСТ — это проверочное сообщение, бонусы НЕ менялись</b>")
        lines.append("")
    lines.append("🔔 <b>Uzum Tezkor изменил реферальные бонусы</b>")
    lines.append(f"📅 {_e(date)}")
    order: list = []
    by_city: dict = {}
    for c in changes:
        key = c.get("city")
        if key not in by_city:
            by_city[key] = []
            order.append(key)
        by_city[key].append(c)
    for key in order:
        if key is None:
            lines.append("🌐 <b>Общее</b>")
        else:
            lines.append(f"🏙 <b>{_e(city_names.get(key) or CITY_RU_FALLBACK.get(key, key))}</b>")
        for c in by_city[key]:
            lines.append("    " + _e(_change_line(c, city_names)))
    return "\n".join(lines)


def details_url(base: str, date: str, changes: list[dict], test: bool = False) -> str:
    params = {"date": date}
    cities = {c["city"] for c in changes if c.get("city")}
    if len(cities) == 1:
        params["city"] = cities.pop()
    if test:
        params["test"] = "1"
    return base + "?" + urlencode(params)


def send(token: str, chat_id: str, text: str, button_url: str | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if button_url:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": "📊 Подробнее", "url": button_url}]]}
    api = os.environ.get("TELEGRAM_API", "https://api.telegram.org")  # переопределяется только в тестах
    res = http_post_json(f"{api}/bot{token}/sendMessage", payload)
    if not res.get("ok"):
        raise RuntimeError(f"Telegram ответил ошибкой: {res}")
    log(f"Telegram: сообщение отправлено (chat …{str(chat_id)[-4:]}, message_id={res['result']['message_id']})")
    return res
