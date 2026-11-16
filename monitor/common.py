"""Общие утилиты: пути, HTTP с повторами, форматирование, JSON."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
SITE_URL = os.environ.get("SITE_URL", "https://referral.uzumtezkor.uz/")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
TASHKENT_TZ = timezone(timedelta(hours=5))

TRANSPORT_ORDER = ["moto", "car", "ebike", "bike", "walking"]
TRANSPORT_RU = {"moto": "Мото", "car": "Авто", "ebike": "Электровело", "bike": "Вело", "walking": "Пешие"}
TRANSPORT_ICON = {"moto": "🏍", "car": "🚗", "ebike": "🚲", "bike": "🚴", "walking": "🚶"}
CITY_RU_FALLBACK = {
    "tashkent": "Ташкент", "samarkand": "Самарканд", "namangan": "Наманган",
    "andijan": "Андижан", "fergana": "Фергана", "bukhara": "Бухара",
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# Логи публичного репозитория видны всем: суммы бонусов и текст изменений в лог не пишутся.
# Это страховка на случай, если сумма всё же попадёт в сообщение (например, в текст ошибки):
# «1 200 000», «1200000», «12e5» → «•••». Номера запусков (11+ цифр) и даты не трогаются.
_AMOUNT_RES = [
    re.compile(r"(?<![\w.])\d{1,3}(?:[   ]\d{3})+(?![\w.])"),   # 1 200 000
    re.compile(r"(?<![\w.])\d+(?:\.\d+)?e\d+(?![\w.])", re.I),             # 12e5, 1.2e6
    re.compile(r"(?<![\w.:/-])\d{5,8}(?![\w.:/-])"),                        # 1200000, 90000
]


def redact(msg: str) -> str:
    for rx in _AMOUNT_RES:
        msg = rx.sub("•••", msg)
    return msg


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {redact(str(msg))}", flush=True)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_tashkent() -> str:
    return datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")


def http_get(url: str, timeout: int = 30, retries: int = 3, binary: bool = False):
    """GET с таймаутом 30с, 3 попытки, экспоненциальная пауза."""
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return body if binary else body.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"HTTP ошибка ({attempt}/{retries}) {url}: {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Не удалось загрузить {url}: {last_err}")


def http_post_json(url: str, payload: dict, timeout: int = 30, retries: int = 3) -> dict:
    data = json.dumps(payload).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            last_err = RuntimeError(f"HTTP {e.code}: {body[:300]}")
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise RuntimeError(str(last_err))


def fmt_sum(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", " ")


def fmt_diff(n) -> str:
    return ("+" if n > 0 else "−") + fmt_sum(abs(n))


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def run_url() -> str | None:
    server, repo, run_id = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


def dashboard_url() -> str:
    url = os.environ.get("DASHBOARD_URL")
    if url:
        return url.rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return "https://example.github.io/tezkor-referral-monitor/"
