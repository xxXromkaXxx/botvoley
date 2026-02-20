import asyncio
import html
import json
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.channels import InviteToChannelRequest

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID", "")
KEYWORDS_RAW = os.getenv("KEYWORDS", "дайвінчик,волейбол")
REPLY_TEXT = os.getenv(
    "REPLY_TEXT",
    "Привіт, я Рома, я радий, що ти написав/ла, зараз додам тебе в чат, але спочатку скажи своє ім'я чи представся, будь ласка 🙂",
)
STATE_FILE = Path(os.getenv("GH_STATE_FILE", "gh_state.json"))
PROCESS_ONCE = os.getenv("PROCESS_ONCE", "1").strip() == "1"
TEST_USER_ID = int(os.getenv("TEST_USER_ID", "0"))

if not all([API_ID, API_HASH, SESSION_STRING, CHANNEL_ID_RAW]):
    raise RuntimeError("Set API_ID, API_HASH, SESSION_STRING, CHANNEL_ID in env")

KEYWORDS = [k.strip().lower() for k in KEYWORDS_RAW.split(",") if k.strip()]


def parse_channel_ref(value: str):
    value = value.strip()
    if value.startswith("-") and value[1:].isdigit():
        return int(value)
    if value.isdigit():
        return int(value)
    return value


CHANNEL_REF = parse_channel_ref(CHANNEL_ID_RAW)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"processed_users": [], "awaiting_intro_users": [], "last_seen_msg_ids": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {
            "processed_users": [int(x) for x in data.get("processed_users", [])],
            "awaiting_intro_users": [int(x) for x in data.get("awaiting_intro_users", [])],
            "last_seen_msg_ids": {str(k): int(v) for k, v in data.get("last_seen_msg_ids", {}).items()},
        }
    except Exception:
        return {"processed_users": [], "awaiting_intro_users": [], "last_seen_msg_ids": {}}


def save_state(state: dict) -> None:
    payload = {
        "processed_users": sorted(set(int(x) for x in state["processed_users"])),
        "awaiting_intro_users": sorted(set(int(x) for x in state["awaiting_intro_users"])),
        "last_seen_msg_ids": {str(k): int(v) for k, v in state["last_seen_msg_ids"].items()},
    }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run() -> None:
    state = load_state()
    processed_users = set(state["processed_users"])
    awaiting_intro_users = set(state["awaiting_intro_users"])
    last_seen_msg_ids = {str(k): int(v) for k, v in state["last_seen_msg_ids"].items()}

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Session is not authorized. Regenerate SESSION_STRING.")

    me = await client.get_me()
    print(f"Polling as {me.id}", flush=True)

    channel = await client.get_entity(CHANNEL_REF)

    async for d in client.iter_dialogs():
        if not d.is_user:
            continue
        entity = d.entity
        if getattr(entity, "bot", False):
            continue

        chat_id = d.id
        key = str(chat_id)
        last_seen = int(last_seen_msg_ids.get(key, 0))
        max_seen = last_seen

        messages = await client.get_messages(chat_id, limit=20)
        for msg in reversed(messages):
            if not msg or not msg.message:
                continue
            if msg.out:
                if msg.id > max_seen:
                    max_seen = msg.id
                continue
            if msg.id <= last_seen:
                continue

            text = msg.message.strip()
            text_lower = text.lower()
            user_id = int(msg.sender_id or chat_id)
            sender = await msg.get_sender()
            username = getattr(sender, "username", None)

            # Step 2: collect introduction message
            if user_id in awaiting_intro_users:
                try:
                    await client(InviteToChannelRequest(channel=channel, users=[user_id]))
                except Exception:
                    pass

                if username:
                    log_text = (
                        f"До нас приєднався новий користувач: @{html.escape(username)}\\n"
                        f"Його повідомлення: {html.escape(text)}"
                    )
                else:
                    log_text = f"До нас приєднався новий користувач.\\nЙого повідомлення: {html.escape(text)}"

                await client.send_message(channel, log_text)
                awaiting_intro_users.discard(user_id)
                if PROCESS_ONCE and user_id != TEST_USER_ID:
                    processed_users.add(user_id)
                if msg.id > max_seen:
                    max_seen = msg.id
                continue

            # Step 1: trigger on keywords
            if not any(keyword in text_lower for keyword in KEYWORDS):
                if msg.id > max_seen:
                    max_seen = msg.id
                continue

            if PROCESS_ONCE and user_id in processed_users and user_id != TEST_USER_ID:
                if msg.id > max_seen:
                    max_seen = msg.id
                continue

            await client(UpdateStatusRequest(offline=False))
            await client.send_message(chat_id, REPLY_TEXT)
            await client(UpdateStatusRequest(offline=True))
            awaiting_intro_users.add(user_id)
            if msg.id > max_seen:
                max_seen = msg.id

        last_seen_msg_ids[key] = max_seen

    state["processed_users"] = sorted(processed_users)
    state["awaiting_intro_users"] = sorted(awaiting_intro_users)
    state["last_seen_msg_ids"] = last_seen_msg_ids
    save_state(state)
    await client.disconnect()
    print("Poll completed", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
