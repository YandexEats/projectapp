import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "monitor"))

T = lambda d, o, b: [{"days": d, "orders": o, "bonus": b}]  # noqa: E731

# Эталон живого сайта хранится как SHA-256 канонического JSON, а не открытыми цифрами.
# Если сайт официально поменял бонусы: тест 1 напечатает новые значения и новый хэш — вставьте его сюда.
BASELINE_SHA256 = "12384499228258be9f9b65122a6e82bb2ac29e2d2a3672e00f7ce7307d781cbb"
HERO_SHA256 = "cfecc92332590d621a3b4606a84b826df15952e9c593adb672a618c1d6869b2d"
HERO_UZ_SHA256 = "089360da99ec911517eba0dcc92d0fe3b6e10b227a698a893691e3f0bc557bf9"  # запасной способ видит только uz

TEST_PASSWORD = "тест-пароль-для-pytest"  # только для локальных тестов, к настоящим данным не подходит


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def transports_only(data: dict) -> dict:
    return {slug: c["transports"] for slug, c in data["cities"].items()}


def write_enc(folder: Path, name: str, obj) -> None:
    from vault import Vault
    Vault(folder, TEST_PASSWORD).save(name, obj)


def read_enc(folder: Path, name: str):
    from vault import Vault
    return Vault(folder, TEST_PASSWORD).load(name, None)


class _Recorder(BaseHTTPRequestHandler):
    routes: dict = {}
    calls: list = []

    def log_message(self, *a):  # тише
        pass

    def do_GET(self):
        body = self.routes.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(body.encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        type(self).calls.append({"path": self.path, "payload": payload})
        if str(payload.get("chat_id")) in type(self).fail_chats:
            err = json.dumps({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}).encode()
            self.send_response(400); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(err)
            return
        out = json.dumps({"ok": True, "result": {"message_id": len(type(self).calls)}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)


@pytest.fixture(scope="session")
def live():
    """Данные с живого сайта (основной способ), один раз на весь прогон."""
    import scraper
    return scraper.fetch_primary()


@pytest.fixture
def fake_server():
    """Локальный HTTP-сервер: отдаёт заданные страницы и записывает POST (поддельный Telegram)."""
    handler = type("H", (_Recorder,), {"routes": {}, "calls": [], "fail_chats": set()})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", handler
    srv.shutdown()
