"""Тесты шифрования данных и защиты дашборда паролем. Запуск: python -m pytest tests/test_encryption.py -v -s"""
import functools
import json
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import ROOT, TEST_PASSWORD, read_enc, write_enc
from test_monitor import PY, run

import testdata  # noqa: E402
import vault  # noqa: E402

PS = "powershell.exe"
EVIDENCE = ROOT / "test-evidence"


def _all_text(folder: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in folder.rglob("*") if p.is_file())


# ------------------------------------------------ формат и криптография
def test_roundtrip_fresh_iv_same_salt_wrong_password(tmp_path):
    history = testdata.build()[0]
    write_enc(tmp_path, "history.json", history)
    e1 = json.loads((tmp_path / "history.json.enc").read_text(encoding="utf-8"))
    write_enc(tmp_path, "history.json", history)
    e2 = json.loads((tmp_path / "history.json.enc").read_text(encoding="utf-8"))
    print("\nКонверт:", {k: (v[:24] + "…" if isinstance(v, str) and len(v) > 24 else v) for k, v in e2.items()})
    assert e1["alg"] == "AES-256-GCM" and e1["kdf"] == "PBKDF2-SHA256" and e1["iter"] >= 300_000
    assert len(vault._b64d(e1["salt"])) == 16 and len(vault._b64d(e1["iv"])) == 12
    assert e1["iv"] != e2["iv"] and e1["ct"] != e2["ct"], "IV новый при каждой записи"
    assert e1["salt"] == e2["salt"], "соль постоянная, пока пароль тот же"
    assert read_enc(tmp_path, "history.json") == history
    with pytest.raises(vault.WrongPassword):
        vault.Vault(tmp_path, "неверный пароль").load("history.json", None)
    text = (tmp_path / "history.json.enc").read_text(encoding="utf-8")
    assert "tashkent" not in text and "Ташкент" not in text and "1000000" not in text


def test_file_swap_is_detected(tmp_path):
    """AAD содержит имя файла: changes.json, подменённый файлом history, не расшифруется."""
    write_enc(tmp_path, "history.json", [1, 2, 3])
    (tmp_path / "changes.json.enc").write_bytes((tmp_path / "history.json.enc").read_bytes())
    with pytest.raises(vault.WrongPassword):
        read_enc(tmp_path, "changes.json")


def test_password_normalization():
    salt = b"\x01" * 16
    k = vault.derive_key("пароль-1234", salt, vault.ITERATIONS)
    assert vault.derive_key("пароль-1234\r\n", salt, vault.ITERATIONS) == k   # перевод строки из stdin
    assert vault.derive_key("﻿пароль-1234", salt, vault.ITERATIONS) == k  # BOM
    assert vault.derive_key("пароль-1234 ", salt, vault.ITERATIONS) != k       # пробел — часть пароля
    with pytest.raises(vault.VaultError):
        vault.derive_key("x", salt, 1000)


# ------------------------------------------------ нет пароля / неверный пароль
def test_missing_password_fails_and_alerts_admin(tmp_path, fake_server):
    base, handler = fake_server
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    data_dir.mkdir()
    (data_dir / "history.json").write_text('[{"date": "2026-11-01", "cities": {}}]', encoding="utf-8")
    before = (data_dir / "history.json").read_bytes()
    env = {"DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "DASHBOARD_PASSWORD": None, "SITE_URL": "http://127.0.0.1:9/",
           "BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base}
    r = run("check.py", ["--no-fallback"], env)
    assert r.returncode != 0
    assert "Секрет DASHBOARD_PASSWORD не задан" in r.stdout
    assert sorted(p.name for p in data_dir.iterdir()) == ["history.json"] and (data_dir / "history.json").read_bytes() == before
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["555"], "предупреждение только админу"
    print("===== Админу =====\n" + handler.calls[0]["payload"]["text"])

    handler.calls.clear()
    r = run("vault.py", ["require-password"], env)
    assert r.returncode == 1
    assert "::error title=Не задан DASHBOARD_PASSWORD::" in r.stdout
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["555"]

    r = run("build_site.py", [str(tmp_path / "_site")], env)
    assert r.returncode == 1 and not (tmp_path / "_site").exists()


def test_wrong_password_fails_without_touching_data(tmp_path, fake_server):
    base, handler = fake_server
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    history, changes, last = testdata.build()
    for n, o in (("history.json", history), ("changes.json", changes), ("last_check.json", last)):
        write_enc(data_dir, n, o)
    before = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    env = {"DATA_DIR": str(data_dir), "OUT_DIR": str(out_dir), "DASHBOARD_PASSWORD": "совсем-другой-пароль",
           "BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base}
    r = run("check.py", ["--no-fallback"], env)
    assert r.returncode != 0 and "не подходит" in r.stdout
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["555"]


# ------------------------------------------------ миграция и смена пароля
def test_migration_from_plaintext(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    history, changes, last = testdata.build()
    for n, o in (("history.json", history), ("changes.json", changes), ("last_check.json", last)):
        (data_dir / n).write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
    r = run("check.py", ["--reencrypt"], {"DATA_DIR": str(data_dir), "OUT_DIR": str(tmp_path / "out")})
    assert r.returncode == 0
    assert sorted(p.name for p in data_dir.iterdir()) == ["changes.json.enc", "history.json.enc", "last_check.json.enc"]
    assert read_enc(data_dir, "history.json") == history and read_enc(data_dir, "changes.json") == changes
    text = _all_text(data_dir)
    assert "tashkent" not in text and "1000000" not in text and "Ташкент" not in text


def test_password_change_with_old_password(tmp_path):
    data_dir = tmp_path / "data"
    history, changes, last = testdata.build()
    old_v = vault.Vault(data_dir, "старый-пароль-123")
    for n, o in (("history.json", history), ("changes.json", changes), ("last_check.json", last)):
        old_v.save(n, o)
    old_salt = json.loads((data_dir / "history.json.enc").read_text())["salt"]
    before = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    env = {"DATA_DIR": str(data_dir), "OUT_DIR": str(tmp_path / "out")}
    # без старого пароля — отказ, данные целы
    r = run("check.py", ["--reencrypt"], env)
    assert r.returncode != 0 and {p.name: p.read_bytes() for p in data_dir.iterdir()} == before
    # со старым паролем — перешифровано новым, соль новая
    r = run("check.py", ["--reencrypt"], {**env, "DASHBOARD_PASSWORD_OLD": "старый-пароль-123"})
    assert r.returncode == 0 and "старым паролем" in r.stdout
    assert read_enc(data_dir, "history.json") == history
    assert json.loads((data_dir / "history.json.enc").read_text())["salt"] != old_salt
    with pytest.raises(vault.WrongPassword):
        vault.Vault(data_dir, "старый-пароль-123").load("history.json", None)


# ------------------------------------------------ сборка сайта
def _build(tmp_path, data_dir) -> Path:
    out = tmp_path / "_site"
    r = run("build_site.py", [str(out)], {"DATA_DIR": str(data_dir)})
    assert r.returncode == 0
    return out


def test_build_site_publishes_only_ciphertext(tmp_path):
    data_dir = tmp_path / "data"
    history, changes, last = testdata.build()
    for n, o in (("history.json", history), ("changes.json", changes), ("last_check.json", last)):
        write_enc(data_dir, n, o)
    out = _build(tmp_path, data_dir)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    print("\nФайлы сборки:", files)
    assert not [f for f in files if f.endswith(".json")], "ни одного открытого JSON"
    assert set(files) == {"index.html", "app.js", "style.css", "ui.json.enc",
                          "data/history.json.enc", "data/changes.json.enc", "data/last_check.json.enc",
                          "testdata/history.json.enc", "testdata/changes.json.enc", "testdata/last_check.json.enc"}
    enc_text = "\n".join((out / f).read_text(encoding="utf-8") for f in files if f.endswith(".enc"))
    assert "tashkent" not in enc_text and "1000000" not in enc_text
    env = lambda f: json.loads((out / f).read_text())  # noqa: E731
    assert {env(f)["v"] for f in files if f.endswith(".enc")} == {2}, "публикуется только формат v2"
    assert env("data/history.json.enc")["salt"] == env("testdata/history.json.enc")["salt"] == env("ui.json.enc")["salt"]
    assert read_enc(out / "testdata", "changes.json") == changes
    assert read_enc(out / "data", "history.json") == history
    ui = read_enc(out, "ui.json")
    assert 'id="app-header"' in ui["html"] and "function startDashboard" in ui["js"]


def test_public_files_are_neutral(tmp_path):
    """Открытые файлы сайта не упоминают тему проекта; сборка падает, если упоминание появится."""
    import build_site
    for name in ("index.html", "app.js", "style.css"):
        text = (ROOT / "site" / name).read_text(encoding="utf-8")
        print(f"\n{name}: найдено {build_site.forbidden_in(text)}")
        assert build_site.forbidden_in(text) == []
    for name in build_site.PRIVATE_FILES:  # тема есть только в закрытых файлах
        assert build_site.forbidden_in((ROOT / "site" / name).read_text(encoding="utf-8"))
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "index.html").write_text("<title>Курьер Bonus</title>", encoding="utf-8")
    (bad / "dashboard.js").write_text("// x", encoding="utf-8")
    problems = build_site.verify_site(bad)
    print("Проверка сборки с нарушениями:", problems)
    assert any("курьер" in p and "bonus" in p for p in problems) and any("dashboard.js" in p for p in problems)


def test_legacy_v1_files_are_still_readable(tmp_path):
    """Данные в репозитории в старом формате v1 читаются; при записи становятся v2."""
    import base64
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    key = vault.derive_key(TEST_PASSWORD, salt, vault.ITERATIONS)
    ct = AESGCM(key).encrypt(iv, json.dumps([1, 2]).encode(), vault.aad("history.json", 1))
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    tmp_path.joinpath("history.json.enc").write_text(json.dumps(
        {"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": vault.ITERATIONS, "salt": b(salt), "iv": b(iv), "ct": b(ct)}))
    v = vault.Vault(tmp_path, TEST_PASSWORD)
    assert v.load("history.json", None) == [1, 2]
    v.save("history.json", [1, 2])
    e = json.loads(tmp_path.joinpath("history.json.enc").read_text())
    assert e["v"] == 2 and e["salt"] == b(salt), "соль та же — запомненный ключ подходит"


def test_build_site_never_publishes_plaintext(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "history.json").write_text('[{"date": "2026-11-01"}]', encoding="utf-8")
    out = _build(tmp_path, data_dir)
    assert not (out / "data").exists()
    assert not [p for p in out.rglob("*.json")]


def test_set_dashboard_password_validation():
    cases = [("короткий", False), ("достаточно-длинный", True), (" с-пробелом-впереди", False), ("с-пробелом-в-конце ", False)]
    for value, ok in cases:
        cmd = f"$env:SET_SECRETS_SELFTEST='1'; . '{ROOT / 'set_dashboard_password.ps1'}'; Test-Password '{value}'"
        r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd], capture_output=True, text=True)
        print(f"Test-Password '{value}' → {r.stdout.strip()}")
        assert r.stdout.strip().splitlines()[-1] == ("True" if ok else "False")


# ------------------------------------------------ notify ждёт зашифрованный last_check на Pages
def test_notify_waits_for_encrypted_pages(tmp_path, fake_server):
    base, handler = fake_server
    site = tmp_path / "pages"
    write_enc(site, "last_check.json", {"run_id": "4242"})
    handler.routes["/data/last_check.json.enc"] = (site / "last_check.json.enc").read_text(encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "pending.json").write_text(json.dumps({"group": {"text": "t", "url": base + "/?date=2026-11-16"}, "admin": []}), encoding="utf-8")
    env = {"OUT_DIR": str(out_dir), "DATA_DIR": str(tmp_path / "data"), "GITHUB_RUN_ID": "4242", "DASHBOARD_URL": base + "/",
           "BOT_TOKEN": "123:TEST", "GROUP_CHAT_ID": "-1001", "ADMIN_CHAT_ID": "555", "TELEGRAM_API": base}
    r = run("notify.py", [], env)
    assert r.returncode == 0 and "показывает запуск 4242" in r.stdout
    assert [c["payload"]["chat_id"] for c in handler.calls] == ["-1001"]


# ------------------------------------------------ дашборд в браузере (Web Crypto ↔ Python), 390 px
@pytest.fixture
def served_site(tmp_path):
    data_dir = tmp_path / "data"
    history, changes, last = testdata.build()
    # «настоящие» данные для теста — те же вымышленные, но с пометкой, чтобы отличать от ?test=1
    last = {**last, "status": "changed", "run_id": "real"}
    for n, o in (("history.json", history), ("changes.json", changes), ("last_check.json", last)):
        write_enc(data_dir, n, o)
    out = _build(tmp_path, data_dir)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def test_dashboard_password_gate_in_browser(served_site):
    from playwright.sync_api import sync_playwright
    EVIDENCE.mkdir(exist_ok=True)
    url = served_site
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        visible = lambda sel: page.locator(sel).is_visible()  # noqa: E731

        # 1. Открытие: нейтральная форма «Панель» — поле пароля и кнопка, больше ничего
        page.goto(url)
        page.wait_for_selector("#login-form", state="visible")
        assert page.title() == "Панель"
        assert page.inner_text(".login-title") == "Панель"
        assert page.get_attribute("#pw", "placeholder") == "Введите пароль"
        assert page.inner_text("#login-btn") == "Войти"
        body = page.inner_text("body")
        print("\nТекст страницы до входа:", repr(body))
        assert page.locator("#app-header").count() == 0 and page.locator("#current-table").count() == 0
        html = page.content()  # до входа нет ни темы, ни данных
        low = html.lower()
        for w in ("uzum", "tezkor", "реферал", "курьер", "бонус", "referral", "courier", "bonus", "наманган", "заказов"):
            assert w not in low, w
        page.screenshot(path=str(EVIDENCE / "390_01_login.png"))

        # 2. Неверный пароль
        page.fill("#pw", "неверный-пароль")
        page.click("#login-btn")
        page.wait_for_selector("#login-error", state="visible")
        assert page.inner_text("#login-error") == "Неверный пароль"
        assert not visible("#app-main")
        page.screenshot(path=str(EVIDENCE / "390_02_wrong_password.png"))

        # 3. Верный пароль без «Запомнить» → данные видны, ключ не сохранён
        page.fill("#pw", TEST_PASSWORD)
        page.click("#login-btn")
        page.wait_for_selector("#app-main", state="visible")
        page.wait_for_selector("#current-table td.city")
        assert "Ташкент" in page.inner_text("#current-table")
        assert page.evaluate("localStorage.getItem('panel-key-v2')") is None
        page.wait_for_timeout(400)
        page.screenshot(path=str(EVIDENCE / "390_03_after_login.png"))
        page.reload()
        page.wait_for_selector("#login-form", state="visible")

        # 4. «Запомнить на этом устройстве» → в localStorage производный ключ, не пароль
        page.fill("#pw", TEST_PASSWORD)
        page.check("#remember")
        page.click("#login-btn")
        page.wait_for_selector("#app-main", state="visible")
        stored = page.evaluate("localStorage.getItem('panel-key-v2')")
        print("\nlocalStorage:", stored[:60] + "…")
        st = json.loads(stored)
        assert set(st) == {"v", "salt", "iter", "k"} and TEST_PASSWORD not in stored and len(vault._b64d(st["k"])) == 32
        page.reload()
        page.wait_for_selector("#app-main", state="visible")
        assert not visible("#login-form")

        # 5. Глубокая ссылка + ?test=1 после входа (тот же ключ подходит к тестовым данным)
        page.goto(url + "?test=1&city=namangan&date=2026-09-10")
        page.wait_for_selector("#change-log li.target")
        assert visible("#test-banner")
        assert page.input_value("#f-log-city") == "namangan" and page.input_value("#f-city") == "namangan"
        assert page.input_value("#f-transport") == "ebike"
        targets = page.locator("#change-log li.target").all_inner_texts()
        print("Подсвечено в журнале:", targets)
        assert any("Наманган" in t and "10.09.2026" in t for t in targets)
        page.wait_for_timeout(600)
        page.screenshot(path=str(EVIDENCE / "390_04_deeplink_test.png"))
        sw = page.evaluate("document.documentElement.scrollWidth")
        print(f"Ширина страницы {sw}px при окне 390px")
        assert sw <= 390

        # 6. «Выйти» → ключ удалён, снова форма пароля (параметры ссылки сохраняются)
        page.click("#logout")
        page.wait_for_selector("#login-form", state="visible")
        assert page.evaluate("localStorage.getItem('panel-key-v2')") is None
        assert "city=namangan" in page.url
        page.screenshot(path=str(EVIDENCE / "390_05_after_logout.png"))

        # 7. Вход с глубокой ссылкой сразу из формы
        page.fill("#pw", TEST_PASSWORD)
        page.press("#pw", "Enter")
        page.wait_for_selector("#change-log li.target")
        print("Ошибки JS:", errors or "нет")
        assert errors == []
        b.close()
    print("Скриншоты:", EVIDENCE)
