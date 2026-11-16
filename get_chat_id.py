"""Узнать ID группы и ваш личный ID в Telegram.

1. Напишите боту /start в личном чате.
2. Добавьте бота в группу и напишите там /start (или /start@имя_бота).
3. Запустите:  python get_chat_id.py   — и вставьте токен бота (он не отображается на экране).
"""
import getpass
import json
import sys
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def call(token: str, method: str) -> dict:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    token = getpass.getpass("Вставьте BOT_TOKEN (символы не отображаются) и нажмите Enter: ").strip()
    if not token:
        print("Токен не введён.")
        return 1
    try:
        me = call(token, "getMe")
        data = call(token, "getUpdates?allowed_updates=%5B%22message%22%2C%22my_chat_member%22%2C%22edited_message%22%5D")
    except Exception as e:  # noqa: BLE001
        print("Ошибка запроса к Telegram (проверьте токен):", str(e).replace(token, "***"))
        return 1
    if not data.get("ok"):
        print("Telegram вернул ошибку:", data.get("description"))
        return 1
    bot = (me.get("result") or {}).get("username", "имя_бота")
    print(f"Бот: @{bot}")

    chats = {}
    for upd in data.get("result", []):
        for key in ("message", "edited_message", "my_chat_member"):
            obj = upd.get(key)
            if not obj:
                continue
            chat = obj.get("chat", {})
            if chat.get("id") is not None:
                name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username")
                chats[chat["id"]] = (chat.get("type"), name)
            if obj.get("migrate_to_chat_id"):
                chats.pop(chat.get("id"), None)
                chats[obj["migrate_to_chat_id"]] = ("supergroup", chat.get("title"))

    groups = [(i, n, t) for i, (t, n) in chats.items() if t in ("group", "supergroup")]
    private = [(i, n) for i, (t, n) in chats.items() if t == "private"]

    print()
    for cid, name, typ in groups:
        print(f"  ГРУППА: {name} → {cid}")
        if not str(cid).startswith("-100"):
            print("     ⚠ Это «обычная» группа (ID без -100). Включите в настройках группы")
            print("       «История чата для новых участников: Видна» — группа станет супергруппой")
            print("       с ID -100…; затем напишите в ней /start и запустите скрипт снова.")
    for cid, name in private:
        print(f"  ЛИЧНЫЙ: {name} → {cid}")

    if not groups and not private:
        print("Ничего не найдено. Сделайте так:")
        print(f"  1) откройте личный чат с @{bot} и отправьте /start;")
        print(f"  2) напишите в группе /start@{bot};")
        print("  3) запустите этот скрипт снова.")
        return 1
    if not groups:
        print("\nГруппа не видна. Добавьте бота в группу и напишите там команду "
              f"/start@{bot} — из-за режима приватности бот видит в группе только команды. Затем запустите скрипт снова.")
    if not private:
        print(f"\nЛичный ID не виден. Откройте личный чат с @{bot}, отправьте /start и запустите скрипт снова.")
    if groups and private:
        print("\nДальше: set_secrets.ps1 → GROUP_CHAT_ID = ID группы (-100…), ADMIN_CHAT_ID = ваш ЛИЧНЫЙ ID.")
    return 0 if groups and private else 1


if __name__ == "__main__":
    sys.exit(main())
