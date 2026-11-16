"""Шифрование данных дашборда паролем.

Формат файла *.json.enc (JSON-конверт, совместим с Web Crypto в site/app.js):
    {"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": 600000,
     "salt": "<base64, 16 байт>", "iv": "<base64, 12 байт>", "ct": "<base64: шифртекст + тег 16 байт>"}

- Ключ: PBKDF2-HMAC-SHA256(пароль NFC в UTF-8, salt, iter) → 32 байта (AES-256).
- AAD = "tezkor-referral-monitor/v1/<имя файла без .enc>": файл нельзя подменить другим.
- IV новый (случайный) при КАЖДОЙ записи. Соль случайная, но постоянная, пока не сменился
  пароль: так ключ, «запомненный» в браузере, продолжает работать после ежедневной проверки.
- Открытый текст в репозиторий не пишется никогда: save() удаляет старый *.json рядом.

CLI:
    python monitor/vault.py require-password   — упасть с понятной ошибкой и предупредить
                                                 администратора, если секрет не задан
"""
from __future__ import annotations

import base64
import functools
import json
import os
import sys
import unicodedata
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

VERSION = 1
ALG, KDF = "AES-256-GCM", "PBKDF2-SHA256"
ITERATIONS = 600_000
MIN_ITERATIONS = 300_000
SALT_BYTES, IV_BYTES = 16, 12
# Метка формата шифрования, НЕ адрес и НЕ имя репозитория. При переименовании репозитория не менять:
# она входит в каждый зашифрованный файл, после смены старые данные перестанут расшифровываться.
AAD_PREFIX = "tezkor-referral-monitor/v1/"
ENV_PASSWORD, ENV_PASSWORD_OLD = "DASHBOARD_PASSWORD", "DASHBOARD_PASSWORD_OLD"
DATA_NAMES = ("history.json", "changes.json", "last_check.json")

MISSING_MSG = (
    "Секрет DASHBOARD_PASSWORD не задан. Данные дашборда зашифрованы, без пароля робот их не читает "
    "и не записывает (в открытом виде ничего не сохраняется). Задайте секрет: репозиторий → Settings → "
    "Secrets and variables → Actions → New repository secret, Name: DASHBOARD_PASSWORD."
)
WRONG_MSG = (
    "Пароль из секрета DASHBOARD_PASSWORD не подходит к зашифрованным данным ({name}). "
    "Данные НЕ изменены. Если пароль меняли, запустите set_dashboard_password.ps1 — он передаст "
    "старый пароль для перешифровки."
)


class VaultError(RuntimeError):
    pass


class PasswordMissing(VaultError):
    pass


class WrongPassword(VaultError):
    pass


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def normalize_password(pw: str) -> bytes:
    # BOM и переводы строк по краям могли попасть в секрет при вставке/передаче через stdin — убираем только их
    return unicodedata.normalize("NFC", pw.lstrip("﻿").strip("\r\n")).encode("utf-8")


@functools.lru_cache(maxsize=16)
def derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    if iterations < MIN_ITERATIONS:
        raise VaultError(f"Слишком мало итераций PBKDF2: {iterations} < {MIN_ITERATIONS}")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(normalize_password(password))


def aad(name: str) -> bytes:
    return (AAD_PREFIX + name).encode("utf-8")


def encrypt_obj(obj, name: str, password: str, salt: bytes, iterations: int = ITERATIONS) -> dict:
    key = derive_key(password, salt, iterations)
    iv = os.urandom(IV_BYTES)
    plain = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(key).encrypt(iv, plain, aad(name))
    return {"v": VERSION, "alg": ALG, "kdf": KDF, "iter": iterations, "salt": _b64e(salt), "iv": _b64e(iv), "ct": _b64e(ct)}


def check_envelope(env) -> None:
    if not isinstance(env, dict) or env.get("v") != VERSION or env.get("alg") != ALG or env.get("kdf") != KDF:
        raise VaultError("Неизвестный формат зашифрованного файла")
    if int(env.get("iter", 0)) < MIN_ITERATIONS:
        raise VaultError("Слишком мало итераций PBKDF2 в файле")


def decrypt_obj(env: dict, name: str, password: str):
    """Расшифровать конверт. InvalidTag → WrongPassword (неверный пароль или повреждённый файл)."""
    check_envelope(env)
    salt, iters = _b64d(env["salt"]), int(env["iter"])
    try:
        plain = AESGCM(derive_key(password, salt, iters)).decrypt(_b64d(env["iv"]), _b64d(env["ct"]), aad(name))
    except InvalidTag:
        raise WrongPassword(WRONG_MSG.format(name=name)) from None
    return json.loads(plain.decode("utf-8"))


def password_from_env(name: str = ENV_PASSWORD) -> str | None:
    v = os.environ.get(name, "")
    return v if v.strip() else None


class Vault:
    """Зашифрованное хранилище JSON-файлов в папке (data/ или testdata/)."""

    def __init__(self, folder: Path, password: str | None, old_password: str | None = None,
                 salt: bytes | None = None, iterations: int = ITERATIONS):
        if not password:
            raise PasswordMissing(MISSING_MSG)
        self.folder = Path(folder)
        self.password = password
        self.old_password = old_password or None
        self.iterations = iterations
        self._salt = salt
        self.migrated: list[str] = []      # файлы, прочитанные из старого открытого *.json
        self.used_old_password = False

    @classmethod
    def from_env(cls, folder: Path) -> "Vault":
        return cls(folder, password_from_env(), password_from_env(ENV_PASSWORD_OLD))

    def enc_path(self, name: str) -> Path:
        return self.folder / (name + ".enc")

    def plain_path(self, name: str) -> Path:
        return self.folder / name

    def _read_env(self, name: str):
        with open(self.enc_path(name), encoding="utf-8") as f:
            return json.load(f)

    def load(self, name: str, default):
        p = self.enc_path(name)
        if p.exists():
            env = self._read_env(name)
            try:
                obj = decrypt_obj(env, name, self.password)
            except WrongPassword:
                if not self.old_password:
                    raise
                obj = decrypt_obj(env, name, self.old_password)  # ещё раз WrongPassword → наружу
                self.used_old_password = True
                return obj
            if self._salt is None and int(env["iter"]) == self.iterations:
                self._salt = _b64d(env["salt"])  # пароль тот же → соль сохраняется
            return obj
        pp = self.plain_path(name)
        if pp.exists():  # одноразовая миграция со старого открытого формата
            with open(pp, encoding="utf-8") as f:
                obj = json.load(f)
            self.migrated.append(name)
            return obj
        return default

    def salt(self) -> bytes:
        if self._salt is None:
            for n in DATA_NAMES:
                if self.enc_path(n).exists():
                    env = self._read_env(n)
                    try:
                        decrypt_obj(env, n, self.password)
                    except WrongPassword:
                        continue
                    if int(env["iter"]) == self.iterations:
                        self._salt = _b64d(env["salt"])
                        break
            if self._salt is None:
                self._salt = os.urandom(SALT_BYTES)
        return self._salt

    def new_salt(self) -> None:
        """Сменить соль (при смене пароля): «запомненные» в браузерах ключи перестанут подходить."""
        self._salt = os.urandom(SALT_BYTES)

    def save(self, name: str, obj) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        env = encrypt_obj(obj, name, self.password, self.salt(), self.iterations)
        p = self.enc_path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(env, f)
            f.write("\n")
        os.replace(tmp, p)
        pp = self.plain_path(name)
        if pp.exists():
            pp.unlink()  # старый открытый файл больше не нужен (git add -A data зафиксирует удаление)
        return p


def require_password_cli() -> int:
    if password_from_env():
        print("DASHBOARD_PASSWORD задан.")
        return 0
    print(f"::error title=Не задан DASHBOARD_PASSWORD::{MISSING_MSG}")
    print("ОШИБКА: " + MISSING_MSG)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import telegram  # noqa: E402
    telegram.alert_admin("⛔ <b>Дашборд: не задан пароль</b>\n\n" + telegram._e(MISSING_MSG))
    return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["require-password"]:
        sys.exit(require_password_cli())
    print(__doc__)
    sys.exit(2)
