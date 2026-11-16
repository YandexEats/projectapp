"""Получение данных с сайта: основной способ (разбор JS-бандла) и запасной (Playwright)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urljoin

from common import CITY_RU_FALLBACK, SITE_URL, http_get, log

HERE = Path(__file__).resolve().parent
BUNDLE_RE = re.compile(r'<script[^>]+src="(/assets/index-[^"]+\.js)"')


class ScrapeError(Exception):
    pass


def find_bundle_path(html: str) -> str:
    m = BUNDLE_RE.search(html)
    if not m:
        raise ScrapeError("В HTML не найден <script src=\"/assets/index-*.js\">")
    return m.group(1)


def parse_bundle_text(js: str) -> dict:
    """Запускает Node-извлекатель на тексте бандла."""
    node = shutil.which("node")
    if not node:
        raise ScrapeError("Node.js не найден")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bundle.js"
        p.write_text(js, encoding="utf-8")
        r = subprocess.run([node, str(HERE / "extract_bundle.js"), str(p)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        # stderr Node может содержать строку исходника бандла с суммами — берём только «XxxError: сообщение»
        err = next((l.strip() for l in r.stderr.splitlines() if re.match(r"^\s*\w*Error\b", l)), "без описания")
        raise ScrapeError(f"Ошибка extract_bundle.js (код {r.returncode}): {err[:200]}")
    return json.loads(r.stdout)


def fetch_primary() -> dict:
    html = http_get(SITE_URL)
    bundle_path = find_bundle_path(html)
    js = http_get(urljoin(SITE_URL, bundle_path))
    if len(js) < 1000:
        raise ScrapeError(f"Бандл подозрительно мал ({len(js)} байт)")
    data = parse_bundle_text(js)
    data["bundle_file"] = bundle_path
    data["method"] = "bundle"
    return data


# --------------------------- Запасной способ: Playwright ---------------------------
AMOUNT_RE = re.compile(r"([\d\s  ]+)\s*so'm", re.I)


def _int(s: str) -> int:
    return int(re.sub(r"\D", "", s))


def _ensure_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        log("Playwright не установлен — устанавливаю (только для запасного способа)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "playwright"], check=True)
    # браузер: на GitHub-раннере нужны системные зависимости
    args = [sys.executable, "-m", "playwright", "install", "chromium"]
    if os.environ.get("GITHUB_ACTIONS") == "true":
        args.insert(-1, "--with-deps")
    subprocess.run(args, check=True, capture_output=True)


def fetch_fallback(slugs: list[str] | None = None) -> dict:
    """Открывает каждую страницу ?city=<slug>, кликает вкладки транспорта и читает таблицы."""
    _ensure_playwright()
    from playwright.sync_api import sync_playwright

    slugs = slugs or list(CITY_RU_FALLBACK.keys())
    cities: dict = {}
    hero_uz = None
    bundle_file = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, user_agent=os.environ.get("UA_OVERRIDE") or None)
        page = ctx.new_page()
        page.set_default_timeout(int(os.environ.get("PW_TIMEOUT", "30000")))
        for slug in slugs:
            page.goto(f"{SITE_URL}?city={slug}", wait_until="networkidle")
            if bundle_file is None:
                src = page.evaluate("() => (document.querySelector('script[src*=\"/assets/index-\"]')||{}).getAttribute?.('src')")
                bundle_file = src
            page.wait_for_selector("[role=tab][aria-controls*='-content-']")
            if hero_uz is None:
                body = page.inner_text("body")
                m = re.search(r"([\d\s ]{5,})\s*\n?\s*so'mgacha", body)
                hero_uz = (re.sub(r"\s+", " ", m.group(1)).strip() + " so'mgacha") if m else None
            # имя города — активная кнопка выбора города
            tabs = page.query_selector_all("[role=tab][aria-controls*='-content-']")
            transports: dict = {t: [] for t in ["moto", "car", "ebike", "bike", "walking"]}
            for tab in tabs:
                ctrl = tab.get_attribute("aria-controls") or ""
                tid = ctrl.rsplit("-content-", 1)[-1]
                tab.click()
                panel = page.locator(f"[id='{ctrl}']")
                panel.wait_for(state="visible")
                text = panel.inner_text()
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                tiers = []
                for i, line in enumerate(lines):
                    m = re.fullmatch(r"(\d+)\s*kun", line)
                    if m and i + 2 < len(lines):
                        mo = re.fullmatch(r"(\d+)\s*ta", lines[i + 1])
                        mb = AMOUNT_RE.search(lines[i + 2])
                        if mo and mb:
                            tiers.append({"days": int(m.group(1)), "orders": int(mo.group(1)), "bonus": _int(mb.group(1))})
                transports[tid] = tiers
            name_uz = page.evaluate(
                "(s)=>{const b=[...document.querySelectorAll('section button')].filter(x=>!x.getAttribute('role'));"
                "const a=b.find(x=>/bg-primary|active/.test(x.className)); return a?a.textContent.trim():null}", slug)
            cities[slug] = {"name_ru": CITY_RU_FALLBACK.get(slug, slug), "name_uz": name_uz, "transports": transports}
        browser.close()
    return {
        "cities": cities, "offices": None, "transport_types": None, "city_list": slugs,
        "hero_text": {"ru": None, "uz": hero_uz}, "notes": None,
        "bundle_file": bundle_file, "method": "playwright",
    }


def validate(data: dict, prev_snapshot: dict | None, allow_city_removal: bool = False) -> list[str]:
    """Возвращает список проблем; пустой список = данные выглядят нормально."""
    problems = []
    cities = (data or {}).get("cities") or {}
    if not cities:
        problems.append("не найдено ни одного города")
        return problems
    total_tiers = 0
    for slug, c in cities.items():
        tr = c.get("transports")
        if not isinstance(tr, dict) or not tr:
            problems.append(f"{slug}: нет данных о транспорте")
            continue
        for t, tiers in tr.items():
            for tier in tiers:
                total_tiers += 1
                for k in ("days", "orders", "bonus"):
                    v = tier.get(k)
                    if not isinstance(v, int) or v <= 0:
                        # само значение не пишем: сообщение попадает в публичный лог Actions
                        problems.append(f"{slug}/{t}: некорректное поле {k} (тип {type(v).__name__}, {'≤ 0' if isinstance(v, int) else 'не число'})")
    if total_tiers == 0:
        problems.append("у всех городов пустые бонусы")
    if prev_snapshot and not allow_city_removal:
        gone = set(prev_snapshot.get("cities", {})) - set(cities)
        if gone:
            problems.append("пропали города: " + ", ".join(sorted(gone)))
    return problems


def fetch_with_fallback(force_fallback: bool = False, disable_fallback: bool = False) -> tuple[dict | None, list[str]]:
    """Пробует основной способ, при неудаче — запасной. Возвращает (данные, ошибки)."""
    errors = []
    if not force_fallback:
        try:
            d = fetch_primary()
            if d.get("cities"):
                return d, errors
            errors.append("основной способ: города не найдены в бандле")
        except Exception as e:  # noqa: BLE001
            errors.append(f"основной способ: {e}")
        log(errors[-1])
    if disable_fallback:
        return None, errors
    try:
        log("Пробую запасной способ (Playwright)…")
        return fetch_fallback(), errors
    except Exception as e:  # noqa: BLE001
        errors.append(f"запасной способ: {e}")
        log(errors[-1])
        return None, errors
