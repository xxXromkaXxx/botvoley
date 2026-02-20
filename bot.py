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

# Admin commands in the target channel.
MEETING_PROMPT_TEXT = (
    "Ну що, обговорюємо?\n"
    "На яку годину збираємось?\n\n"
    "Варіанти:\n"
    "1) 18:00\n"
    "2) 19:00\n"
    "3) 20:00\n\n"
    "Напишіть номер або свій варіант у коментарях.\n\n"
    "Щоб порахувати хто прийде: відповідайте РЕПЛАЄМ на це повідомлення:\n"
    "+ або 'йду' = буду\n"
    "- або 'не йду' = не буду"
)
FINAL_DECISION_TEMPLATE_TEXT = (
    "Фіксуємо фінальне рішення:\n"
    "День: ___\n"
    "Час: ___\n"
    "Формат/місце: ___\n\n"
    "Якщо є заперечення, напишіть у коментарях протягом 30 хв."
)
ADMIN_CHANNEL_COMMANDS = {
    "/meeting": MEETING_PROMPT_TEXT,
    "/discuss": MEETING_PROMPT_TEXT,
    "/збір": MEETING_PROMPT_TEXT,
    "/обговорення": MEETING_PROMPT_TEXT,
    "/final": FINAL_DECISION_TEMPLATE_TEXT,
    "/підсумок": FINAL_DECISION_TEMPLATE_TEXT,
}
ADMIN_SHOW_RSVP_COMMANDS = {"/who", "/rsvp", "/хто"}
ADMIN_CLOSE_RSVP_COMMANDS = {"/close", "/закрити"}
RSVP_YES_MARKERS = {"+", "+1", "йду", "буду", "прийду", "yes", "ok"}
RSVP_NO_MARKERS = {"-", "-1", "не йду", "небуду", "не буду", "no"}

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
        return {"processed_users": [], "awaiting_intro_users": [], "events": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        users = [int(x) for x in data.get("processed_users", [])]
        awaiting = [int(x) for x in data.get("awaiting_intro_users", [])]
        events = {}
        for chat_id, payload in data.get("events", {}).items():
            participants = payload.get("participants", {})
            events[str(chat_id)] = {
                "message_id": int(payload.get("message_id", 0)),
                "participants": {str(uid): str(name) for uid, name in participants.items()},
                "is_open": bool(payload.get("is_open", True)),
            }
        return {"processed_users": users, "awaiting_intro_users": awaiting, "events": events}
    except Exception:
        return {"processed_users": [], "awaiting_intro_users": [], "events": {}}


def save_state(state: dict) -> None:
    payload = {
        "processed_users": sorted(int(x) for x in state.get("processed_users", [])),
        "awaiting_intro_users": sorted(int(x) for x in state.get("awaiting_intro_users", [])),
        "events": {},
    }
    for chat_id, event_data in state.get("events", {}).items():
        payload["events"][str(chat_id)] = {
            "message_id": int(event_data.get("message_id", 0)),
            "participants": {
                str(uid): str(name) for uid, name in event_data.get("participants", {}).items()
            },
            "is_open": bool(event_data.get("is_open", True)),
        }
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_vote(text_lower: str):
    normalized = " ".join(text_lower.split())
    if normalized in RSVP_YES_MARKERS:
        return "yes"
    if normalized in RSVP_NO_MARKERS:
        return "no"
    return None


def user_display_name(sender) -> str:
    username = getattr(sender, "username", None)
    if username:
        return f"@{username}"
    first_name = (getattr(sender, "first_name", "") or "").strip()
    last_name = (getattr(sender, "last_name", "") or "").strip()
    full_name = " ".join(x for x in [first_name, last_name] if x).strip()
    if full_name:
        return full_name
    return str(getattr(sender, "id", "unknown"))


def render_rsvp_summary(event_data: dict) -> str:
    participants = event_data.get("participants", {})
    if not participants:
        return "Поки що ніхто не підтвердив участь."
    names = [name for _, name in sorted(participants.items(), key=lambda item: item[1].lower())]
    lines = [f"Хто приходить: {len(names)}", ""]
    for idx, name in enumerate(names, start=1):
        lines.append(f"{idx}. {name}")
    return "\n".join(lines)


async def sender_is_admin(chat, user_id: int) -> bool:
    try:
        perms = await client.get_permissions(chat, user_id)
        return bool(getattr(perms, "is_creator", False) or getattr(perms, "is_admin", False))
    except Exception:
        return False


state = load_state()
processed_users = set(state["processed_users"])
awaiting_intro_users = set(state["awaiting_intro_users"])
events_state = state["events"]
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
channel_entity = None
target_channel_id = None


@client.on(events.NewMessage(incoming=True))
async def handle_message(event):
    if event.out or not event.raw_text:
        return

    sender = await event.get_sender()
    if getattr(sender, "bot", False):
        return

    text = event.raw_text.strip()
    text_lower = text.lower()
    user_id = event.sender_id

    if event.is_channel and target_channel_id is not None:
        chat = await event.get_chat()
        chat_id = str(getattr(chat, "id", ""))

        if str(target_channel_id) != chat_id:
            return

        is_admin = await sender_is_admin(chat, user_id)
        active_event = events_state.get(chat_id)

        # Admin shortcuts: post prepared messages in target channel.
        if text_lower in ADMIN_CHANNEL_COMMANDS:
            if is_admin:
                posted = await client.send_message(chat, ADMIN_CHANNEL_COMMANDS[text_lower])
                if text_lower in {"/meeting", "/discuss", "/збір", "/обговорення"}:
                    events_state[chat_id] = {
                        "message_id": int(posted.id),
                        "participants": {},
                        "is_open": True,
                    }
                    state["events"] = events_state
                    save_state(state)
                try:
                    await event.delete()
                except Exception:
                    pass
            return

        if text_lower in ADMIN_SHOW_RSVP_COMMANDS:
            if is_admin:
                if active_event and active_event.get("is_open", True):
                    await client.send_message(chat, render_rsvp_summary(active_event))
                else:
                    await client.send_message(chat, "Активного збору немає. Запусти /meeting")
                try:
                    await event.delete()
                except Exception:
                    pass
            return

        if text_lower in ADMIN_CLOSE_RSVP_COMMANDS:
            if is_admin:
                if active_event and active_event.get("is_open", True):
                    active_event["is_open"] = False
                    events_state[chat_id] = active_event
                    state["events"] = events_state
                    save_state(state)
                    await client.send_message(chat, "Збір закрито.\n\n" + render_rsvp_summary(active_event))
                else:
                    await client.send_message(chat, "Активного збору немає. Запусти /meeting")
                try:
                    await event.delete()
                except Exception:
                    pass
            return

        # RSVP replies from users to active meeting post.
        if active_event and active_event.get("is_open", True):
            reply_to_id = getattr(event.message, "reply_to_msg_id", None)
            if reply_to_id and int(reply_to_id) == int(active_event.get("message_id", 0)):
                vote = normalize_vote(text_lower)
                if vote:
                    participants = active_event.get("participants", {})
                    uid = str(user_id)
                    if vote == "yes":
                        participants[uid] = user_display_name(sender)
                    else:
                        participants.pop(uid, None)
                    active_event["participants"] = participants
                    events_state[chat_id] = active_event
                    state["events"] = events_state
                    save_state(state)
        return

    if not event.is_private:
        return

    username = getattr(sender, "username", None)

    # Step 2: user already asked to introduce themselves; now take this message as intro.
    if user_id in awaiting_intro_users:
        print(f"Intro received from {user_id}: {text[:80]}", flush=True)

        try:
            await client(InviteToChannelRequest(channel=channel_entity, users=[user_id]))
        except Exception:
            pass

        if username:
            log_text = (
                f"До нас приєднався новий користувач: @{html.escape(username)}\n"
                f"Його повідомлення: {html.escape(text)}"
            )
        else:
            log_text = f"До нас приєднався новий користувач.\nЙого повідомлення: {html.escape(text)}"

        await client.send_message(channel_entity, log_text)
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
    global channel_entity, target_channel_id

    print("Starting intro bot...", flush=True)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Session is not authorized. Regenerate SESSION_STRING.")

    channel_entity = await client.get_entity(CHANNEL_REF)
    target_channel_id = getattr(channel_entity, "id", None)

    me = await client.get_me()
    print(f"Started as {me.id}", flush=True)
    print(f"Keywords: {', '.join(KEYWORDS)}", flush=True)
    print(f"Channel ref: {CHANNEL_REF}", flush=True)
    print(f"Process once: {PROCESS_ONCE}", flush=True)
    all_admin_commands = sorted(
        set(ADMIN_CHANNEL_COMMANDS) | ADMIN_SHOW_RSVP_COMMANDS | ADMIN_CLOSE_RSVP_COMMANDS
    )
    print(f"Admin discussion commands: {', '.join(all_admin_commands)}", flush=True)
    await client(UpdateStatusRequest(offline=True))
    await client.run_until_disconnected()


if __name__ == "__main__":
    client.loop.run_until_complete(main())
