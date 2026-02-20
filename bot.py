import asyncio
import html
import json
import os
from pathlib import Path

from telethon import TelegramClient, events
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
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
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
        return {"processed_users": [], "awaiting_intro_users": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        users = [int(x) for x in data.get("processed_users", [])]
        awaiting = [int(x) for x in data.get("awaiting_intro_users", [])]
        return {"processed_users": users, "awaiting_intro_users": awaiting}
    except Exception:
        return {"processed_users": [], "awaiting_intro_users": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


state = load_state()
processed_users = set(state["processed_users"])
awaiting_intro_users = set(state["awaiting_intro_users"])
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


@client.on(events.NewMessage(incoming=True))
async def handle_private_message(event):
    if not event.is_private or event.out or not event.raw_text:
        return

    sender = await event.get_sender()
    if getattr(sender, "bot", False):
        return

    text = event.raw_text.strip()
    text_lower = text.lower()
    username = getattr(sender, "username", None)
    user_id = event.sender_id

    # Step 2: user already asked to introduce themselves; now take this message as intro.
    if user_id in awaiting_intro_users:
        print(f"Intro received from {user_id}: {text[:80]}", flush=True)
        channel = await client.get_entity(CHANNEL_REF)

        try:
            await client(InviteToChannelRequest(channel=channel, users=[user_id]))
        except Exception:
            pass

        if username:
            log_text = (
                f"До нас приєднався новий користувач: @{html.escape(username)}\n"
                f"Його повідомлення: {html.escape(text)}"
            )
        else:
            log_text = f"До нас приєднався новий користувач.\nЙого повідомлення: {html.escape(text)}"

        await client.send_message(channel, log_text)
        await client(UpdateStatusRequest(offline=True))

        awaiting_intro_users.discard(user_id)
        state["awaiting_intro_users"] = sorted(awaiting_intro_users)
        if PROCESS_ONCE and user_id != TEST_USER_ID:
            processed_users.add(user_id)
            state["processed_users"] = sorted(processed_users)
        save_state(state)
        return

    if not any(keyword in text_lower for keyword in KEYWORDS):
        return

    if PROCESS_ONCE and user_id in processed_users and user_id != TEST_USER_ID:
        print(f"Skip user {user_id}: already processed", flush=True)
        return

    print(f"Triggered by user {user_id}: {text[:80]}", flush=True)
    await client(UpdateStatusRequest(offline=False))
    await client.send_message(event.chat_id, REPLY_TEXT)
    await client(UpdateStatusRequest(offline=True))
    awaiting_intro_users.add(user_id)
    state["awaiting_intro_users"] = sorted(awaiting_intro_users)
    save_state(state)


async def main():
    print("Starting intro bot...", flush=True)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Session is not authorized. Regenerate SESSION_STRING.")
    me = await client.get_me()
    print(f"Started as {me.id}", flush=True)
    print(f"Keywords: {', '.join(KEYWORDS)}", flush=True)
    print(f"Channel ref: {CHANNEL_REF}", flush=True)
    print(f"Process once: {PROCESS_ONCE}", flush=True)
    await client(UpdateStatusRequest(offline=True))
    await client.run_until_disconnected()


if __name__ == "__main__":
    client.loop.run_until_complete(main())
