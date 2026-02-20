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
CHANNEL_ID_RAW = "-1003885190351"
KEYWORDS_RAW = os.getenv("KEYWORDS", "дайвінчик,волейбол")
REPLY_TEXT = os.getenv(
    "REPLY_TEXT",
    "Привіт, скажи своє ім'я чи представся, будь ласка 🙂",
)
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
PROCESS_ONCE = os.getenv("PROCESS_ONCE", "1").strip() == "1"
TEST_USER_ID = int(os.getenv("TEST_USER_ID", "0"))

MEETING_TEXT_FALLBACK = (
    "Ну що, збираємось?\n"
    "Напишіть реплаєм на це повідомлення:\n"
    "+ якщо будете\n"
    "- якщо не будете"
)
FINAL_TEXT = (
    "Фіксуємо фінальне рішення:\n"
    "День: ___\n"
    "Час: ___\n"
    "Формат/місце: ___"
)
ADMIN_MEETING_COMMANDS = {"/meeting", "/discuss", "/збір", "/обговорення"}
ADMIN_WHO_COMMANDS = {"/who", "/rsvp", "/хто"}
ADMIN_CLOSE_COMMANDS = {"/close", "/закрити"}
ADMIN_FINAL_COMMANDS = {"/final", "/підсумок"}
ALL_ADMIN_COMMANDS = (
    ADMIN_MEETING_COMMANDS | ADMIN_WHO_COMMANDS | ADMIN_CLOSE_COMMANDS | ADMIN_FINAL_COMMANDS
)
YES_MARKERS = {"+", "+1", "йду", "я за", "пирйду", "прийду", "я в темі", "я буду"}
NO_MARKERS = {"-", "-1", "не йду"}

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
        events_map = {}
        for chat_id, payload in data.get("events", {}).items():
            msg_id = int(payload.get("message_id", 0))
            events_map[str(chat_id)] = {
                "message_id": msg_id,
                "is_open": bool(payload.get("is_open", True)),
                "started_at_message_id": int(payload.get("started_at_message_id", msg_id)),
                "participants": {
                    str(uid): str(name) for uid, name in payload.get("participants", {}).items()
                },
            }
        return {"processed_users": users, "awaiting_intro_users": awaiting, "events": events_map}
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
            "is_open": bool(event_data.get("is_open", True)),
            "started_at_message_id": int(
                event_data.get("started_at_message_id", event_data.get("message_id", 0))
            ),
            "participants": {
                str(uid): str(name) for uid, name in event_data.get("participants", {}).items()
            },
        }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_vote(text: str):
    normalized = " ".join(
        text.lower()
        .replace(",", " ")
        .replace(".", " ")
        .replace("!", " ")
        .replace("?", " ")
        .split()
    )
    if normalized.startswith("-"):
        return "no"
    if normalized.startswith("+"):
        return "yes"

    no_phrases = ["не йду", "не буду", "не зможу", "no"]
    yes_phrases = ["йду", "прийду", "я за", "я в темі", "я буду", "yes", "ok"]

    if normalized in NO_MARKERS or any(p in normalized for p in no_phrases):
        return "no"
    if normalized in YES_MARKERS or any(p in normalized for p in yes_phrases):
        return "yes"
    return None


def split_command_and_args(text: str):
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    cmd = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return cmd, args


def normalize_meeting_date(value: str) -> str:
    low = value.strip().lower()
    if low in {"сьогодні", "сьогоднi", "today"}:
        return "Сьогодні"
    if low in {"завтра", "tomorrow"}:
        return "Завтра"
    return value.strip() or "Не вказано"


def build_meeting_text(args: str) -> str:
    # format: /meeting <date> | <place> | <text>
    if not args:
        return MEETING_TEXT_FALLBACK
    parts = [p.strip() for p in args.split("|")]
    if len(parts) >= 3:
        date = normalize_meeting_date(parts[0])
        place = parts[1] or "Не вказано"
        topic = parts[2] or "Зустріч"
        return (
            f"Збір: {topic}\n"
            f"Дата: {date}\n"
            f"Місце: {place}\n\n"
            "Голосування: відповідайте реплаєм на це повідомлення\n"
            "+ якщо будете\n"
            "- якщо не будете"
        )
    return (
        f"Збір: {args}\n\n"
        "Голосування: відповідайте реплаєм на це повідомлення\n"
        "+ якщо будете\n"
        "- якщо не будете"
    )


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
        return "Хто приходить: 0\n\nПоки що ніхто не підтвердив участь."
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


async def resolve_channel_entity_safe():
    try:
        dialogs = await client.get_dialogs(limit=200)
    except Exception:
        dialogs = []

    try:
        return await client.get_entity(CHANNEL_REF)
    except Exception:
        # Fallback: search in loaded dialogs by possible id formats.
        try:
            base = int(str(CHANNEL_ID_RAW).replace("-100", ""))
            candidates = {base, -base, int(CHANNEL_ID_RAW)}
        except Exception:
            candidates = set()

        for d in dialogs:
            try:
                did = int(getattr(d, "id", 0) or 0)
                eid = int(getattr(d.entity, "id", 0) or 0)
                if did in candidates or eid in candidates or -eid in candidates:
                    return d.entity
            except Exception:
                continue

        try:
            async for d in client.iter_dialogs():
                try:
                    did = int(getattr(d, "id", 0) or 0)
                    eid = int(getattr(d.entity, "id", 0) or 0)
                    if did in candidates or eid in candidates or -eid in candidates:
                        return d.entity
                except Exception:
                    continue
        except Exception:
            pass

        e = "not found in dialogs"
        print(f"Warning: cannot resolve CHANNEL_ID={CHANNEL_ID_RAW}: {e}", flush=True)
        print("Bot will continue, but channel features are disabled.", flush=True)
        return None


state = load_state()
processed_users = set(state["processed_users"])
awaiting_intro_users = set(state["awaiting_intro_users"])
events_state = state["events"]
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
channel_entity = None


@client.on(events.NewMessage())
async def handle_message(event):
    if not event.raw_text:
        return

    sender = await event.get_sender()
    if getattr(sender, "bot", False):
        return

    text = event.raw_text.strip()
    text_lower = text.lower()
    command, command_args = split_command_and_args(text)
    user_id = event.sender_id

    # Private admin control: any user who is admin in target channel/group can manage events from private chat.
    if event.is_private and command in ALL_ADMIN_COMMANDS:
        if channel_entity is None:
            await client.send_message(
                event.chat_id,
                f"Не можу знайти CHANNEL_ID={CHANNEL_ID_RAW}. Перевір, що акаунт із SESSION_STRING є в цьому чаті.",
            )
            return

        is_admin_in_target = await sender_is_admin(channel_entity, user_id)
        if not is_admin_in_target:
            await client.send_message(
                event.chat_id,
                f"Доступ заборонено: user_id={user_id} не адмін у чаті CHANNEL_ID={CHANNEL_ID_RAW}.",
            )
            return

        target_chat = channel_entity
        target_chat_key = str(int(getattr(target_chat, "id", 0) or 0))
        active_event = events_state.get(target_chat_key)

        if command in ADMIN_MEETING_COMMANDS:
            posted = await client.send_message(target_chat, build_meeting_text(command_args))
            events_state[target_chat_key] = {
                "message_id": int(posted.id),
                "is_open": True,
                "started_at_message_id": int(posted.id),
                "participants": {},
            }
            state["events"] = events_state
            save_state(state)
            await client.send_message(event.chat_id, "Збір створено.")
            return

        if command in ADMIN_WHO_COMMANDS:
            if active_event:
                await client.send_message(event.chat_id, render_rsvp_summary(active_event))
            else:
                await client.send_message(event.chat_id, "Активного збору немає. Запусти /meeting")
            return

        if command in ADMIN_CLOSE_COMMANDS:
            if active_event and active_event.get("is_open", True):
                active_event["is_open"] = False
                events_state[target_chat_key] = active_event
                state["events"] = events_state
                save_state(state)
                summary = render_rsvp_summary(active_event)
                await client.send_message(target_chat, "Збір закрито.\n\n" + summary)
                await client.send_message(event.chat_id, "Збір закрито.")
            else:
                await client.send_message(event.chat_id, "Активного збору немає. Запусти /meeting")
            return

        if command in ADMIN_FINAL_COMMANDS:
            await client.send_message(target_chat, FINAL_TEXT)
            await client.send_message(event.chat_id, "Підсумок відправлено.")
            return

    if event.is_group or event.is_channel:
        chat = await event.get_chat()
        chat_id = int(getattr(chat, "id", 0) or 0)
        target_chat_id = int(getattr(channel_entity, "id", 0) or 0) if channel_entity is not None else 0

        chat_key = str(chat_id)
        active_event = events_state.get(chat_key)

        # Commands in groups/channels are intentionally disabled.
        if command in (
            ADMIN_MEETING_COMMANDS
            | ADMIN_FINAL_COMMANDS
            | ADMIN_WHO_COMMANDS
            | ADMIN_CLOSE_COMMANDS
        ):
            return

        if target_chat_id == 0 or chat_id != target_chat_id:
            return

        if active_event and active_event.get("is_open", True):
            reply_to_id = getattr(event.message, "reply_to_msg_id", None)
            event_msg_id = int(active_event.get("message_id", 0))
            started_at = int(active_event.get("started_at_message_id", event_msg_id))

            # Count explicit replies to event post, and also short vote messages
            # after event start in target chat.
            is_reply_vote = bool(reply_to_id and int(reply_to_id) == event_msg_id)
            is_after_start = int(getattr(event.message, "id", 0) or 0) >= started_at

            vote = normalize_vote(text)
            if vote and (is_reply_vote or is_after_start):
                participants = active_event.get("participants", {})
                uid = str(user_id)
                if vote == "yes":
                    participants[uid] = user_display_name(sender)
                else:
                    participants.pop(uid, None)
                active_event["participants"] = participants
                events_state[chat_key] = active_event
                state["events"] = events_state
                save_state(state)
        return

    if not event.is_private:
        return

    username = getattr(sender, "username", None)

    if user_id in awaiting_intro_users:
        print(f"Intro received from {user_id}: {text[:80]}", flush=True)

        if channel_entity is not None:
            try:
                await client(InviteToChannelRequest(channel=channel_entity, users=[user_id]))
            except Exception:
                pass

            if username:
                log_text = (
                    f"До нас приєднався новий користувач: @{html.escape(username)}\\n"
                    f"Його повідомлення: {html.escape(text)}"
                )
            else:
                log_text = f"До нас приєднався новий користувач.\\nЙого повідомлення: {html.escape(text)}"

            try:
                await client.send_message(channel_entity, log_text)
            except Exception as e:
                print(f"Warning: cannot send intro to channel: {e}", flush=True)

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
    global channel_entity

    print("Starting intro bot...", flush=True)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Session is not authorized. Regenerate SESSION_STRING.")

    channel_entity = await resolve_channel_entity_safe()

    me = await client.get_me()
    print(f"Started as {me.id}", flush=True)
    print(f"Keywords: {', '.join(KEYWORDS)}", flush=True)
    print(f"Channel ref: {CHANNEL_REF}", flush=True)
    if channel_entity is not None:
        print(f"Resolved channel entity id: {getattr(channel_entity, 'id', 'unknown')}", flush=True)
    print(f"Process once: {PROCESS_ONCE}", flush=True)

    await client(UpdateStatusRequest(offline=True))
    await client.run_until_disconnected()


if __name__ == "__main__":
    client.loop.run_until_complete(main())
