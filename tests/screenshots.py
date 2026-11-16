"""Скриншоты живого дашборда в мобильном окне (iPhone-размер). Запуск: python tests/screenshots.py [URL]"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "https://yandexeats.github.io/tezkor-referral-monitor/"
OUT = Path(__file__).resolve().parent.parent / "test-evidence"
OUT.mkdir(exist_ok=True)
BLOCKS = ["block-table", "block-history", "block-bars", "block-log", "block-offices"]

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(**p.devices["iPhone 13"])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    for tag, q in [("real", ""), ("test", "?test=1"), ("deeplink", "?test=1&city=namangan&date=2026-09-10")]:
        page.goto(URL + q + ("&" if q else "?") + "nocache=1", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / f"{tag}_00_viewport.png"))
        if tag == "deeplink":
            continue
        page.locator("header").screenshot(path=str(OUT / f"{tag}_00_header.png"))
        for i, bid in enumerate(BLOCKS, 1):
            page.locator(f"#{bid}").screenshot(path=str(OUT / f"{tag}_{i}_{bid}.png"))
        sw = page.evaluate("document.documentElement.scrollWidth")
        print(f"{tag}: ширина страницы {sw}px (окно {page.viewport_size['width']}px)")
    print("Ошибки JS:", errors or "нет")
    print("Скриншоты:", OUT)
    b.close()
