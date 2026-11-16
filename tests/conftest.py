import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "monitor"))

T = lambda d, o, b: [{"days": d, "orders": o, "bonus": b}]  # noqa: E731
BASELINE = {
    "tashkent":  {"moto": T(30, 120, 1200000), "car": T(30, 120, 1200000), "ebike": T(30, 120, 900000), "bike": T(30, 120, 900000), "walking": []},
    "samarkand": {"moto": T(30, 120, 1200000), "car": T(30, 120, 1200000), "ebike": T(30, 120, 900000), "bike": T(30, 120, 900000), "walking": []},
    "andijan":   {"moto": T(30, 80, 1200000),  "car": T(30, 80, 1200000),  "ebike": T(30, 80, 900000),  "bike": T(30, 80, 900000),  "walking": []},
    "namangan":  {"moto": T(30, 80, 500000),   "car": T(30, 80, 500000),   "ebike": [],                 "bike": T(30, 80, 500000),  "walking": []},
    "fergana":   {"moto": T(30, 80, 500000),   "car": T(30, 80, 500000),   "ebike": [],                 "bike": T(30, 80, 500000),  "walking": []},
    "bukhara":   {"moto": T(30, 80, 500000),   "car": T(30, 80, 500000),   "ebike": [],                 "bike": T(30, 80, 500000),  "walking": []},
}


def transports_only(data: dict) -> dict:
    return {slug: c["transports"] for slug, c in data["cities"].items()}


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
