"""Узнать ID группы Telegram.

1. Добавьте бота в группу.
2. Напишите в группе любое сообщение (лучше команду /start).
3. Запустите:  python get_chat_id.py   — и вставьте токен бота (он не отображается на экране).
"""
import getpass
import json
import sys
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    token = getpass.getpass("Вставьте BOT_TOKEN (символы не отображаются) и нажмите Enter: ").strip()
    if not token:
        print("Токен не введён.")
        return 1
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates?allowed_updates=%5B%5D", timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print("Ошибка запроса к Telegram (проверьте токен):", str(e).replace(token, "***"))
        return 1
    if not data.get("ok"):
        print("Telegram вернул ошибку:", data.get("description"))
        return 1
    chats = {}
    for upd in data.get("result", []):
        for key in ("message", "edited_message", "channel_post", "my_chat_member", "chat_member"):
            obj = upd.get(key)
            if not obj:
                continue
            chat = obj.get("chat", {})
            if chat.get("id") is not None:
                chats[chat["id"]] = (chat.get("type"), chat.get("title") or chat.get("username") or chat.get("first_name"))
            if obj.get("migrate_to_chat_id"):
                chats[obj["migrate_to_chat_id"]] = ("supergroup", (chat.get("title") or "") + " (новый ID после преобразования)")
    groups = {k: v for k, v in chats.items() if v[0] in ("group", "supergroup")}
    if not groups:
        print("\nГруппы не найдены. Проверьте: бот добавлен в группу? В группе после этого написано сообщение?")
        print("Если бот был добавлен давно — удалите его из группы и добавьте снова, затем повторите.")
        if chats:
            print("Найдены другие чаты:", ", ".join(f"{t} «{n}»: {i}" for i, (t, n) in chats.items()))
        return 1
    print("\nНайдены группы:")
    for cid, (typ, title) in groups.items():
        print(f"  «{title}»  →  GROUP_CHAT_ID = {cid}")
    print("\nСкопируйте число (вместе со знаком минус) — оно понадобится в set_secrets.ps1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
