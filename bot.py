import asyncio
import html
import json
import os
import re
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
    "+ або + 19:00 якщо будете\n"
    "- якщо не будете"
)
ADMIN_MEETING_COMMANDS = {"/meeting", "/discuss", "/збір", "/обговорення"}
ADMIN_WHO_COMMANDS = {"/who", "/rsvp", "/хто"}
ADMIN_CLOSE_COMMANDS = {"/close", "/закрити"}
ADMIN_FINAL_COMMANDS = {"/final", "/підсумок"}
ADMIN_HELP_COMMANDS = {"/help", "/команди"}
ALL_ADMIN_COMMANDS = (
    ADMIN_MEETING_COMMANDS
    | ADMIN_WHO_COMMANDS
    | ADMIN_CLOSE_COMMANDS
    | ADMIN_FINAL_COMMANDS
    | ADMIN_HELP_COMMANDS
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
                "topic": str(payload.get("topic", "Зустріч")),
                "date": str(payload.get("date", "Не вказано")),
                "place": str(payload.get("place", "Не вказано")),
                "participants": {},
            }
            participants = payload.get("participants", {})
            for uid, data_item in participants.items():
                if isinstance(data_item, dict):
                    events_map[str(chat_id)]["participants"][str(uid)] = {
                        "name": str(data_item.get("name", uid)),
                        "time": str(data_item.get("time", "")),
                    }
                else:
                    events_map[str(chat_id)]["participants"][str(uid)] = {
                        "name": str(data_item),
                        "time": "",
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
            "topic": str(event_data.get("topic", "Зустріч")),
            "date": str(event_data.get("date", "Не вказано")),
            "place": str(event_data.get("place", "Не вказано")),
            "participants": {
                str(uid): {
                    "name": str(data_item.get("name", uid))
                    if isinstance(data_item, dict)
                    else str(data_item),
                    "time": str(data_item.get("time", "")) if isinstance(data_item, dict) else "",
                }
                for uid, data_item in event_data.get("participants", {}).items()
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


def extract_time_hint(text: str) -> str:
    match = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", text)
    if not match:
        return ""
    h = int(match.group(1))
    m = match.group(2)
    return f"{h:02d}:{m}"


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


def parse_meeting_payload(args: str) -> dict:
    # format: /meeting <date> | <place> | <text>
    if not args:
        return {
            "topic": "Зустріч",
            "date": "Не вказано",
            "place": "Не вказано",
            "text": MEETING_TEXT_FALLBACK,
        }
    parts = [p.strip() for p in args.split("|")]
    if len(parts) >= 3:
        date = normalize_meeting_date(parts[0])
        place = parts[1] or "Не вказано"
        topic = parts[2] or "Зустріч"
        text = (
            f"Збір: {topic}\n"
            f"Дата: {date}\n"
            f"Місце: {place}\n\n"
            "Голосування: відповідайте реплаєм на це повідомлення\n"
            "+ або + 19:00 якщо будете\n"
            "- якщо не будете"
        )
        return {"topic": topic, "date": date, "place": place, "text": text}

    text = (
        f"Збір: {args}\n"
        "Дата: Не вказано\n"
        "Місце: Не вказано\n\n"
        "Голосування: відповідайте реплаєм на це повідомлення\n"
        "+ або + 19:00 якщо будете\n"
        "- якщо не будете"
    )
    return {"topic": args, "date": "Не вказано", "place": "Не вказано", "text": text}


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
    rows = []
    time_stats = {}
    for _, data_item in sorted(
        participants.items(), key=lambda item: str(item[1].get("name", "")).lower()
    ):
        name = str(data_item.get("name", ""))
        time_hint = str(data_item.get("time", "")).strip()
        rows.append((name, time_hint))
        if time_hint:
            time_stats[time_hint] = time_stats.get(time_hint, 0) + 1

    lines = [f"Хто приходить: {len(rows)}", ""]
    for idx, (name, time_hint) in enumerate(rows, start=1):
        if time_hint:
            lines.append(f"{idx}. {name} ({time_hint})")
        else:
            lines.append(f"{idx}. {name}")

    if time_stats:
        lines.append("")
        lines.append("Часові пропозиції:")
        for time_value, count in sorted(time_stats.items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"- {time_value}: {count}")

    return "\n".join(lines)


def render_final_event_text(event_data: dict, final_time: str) -> str:
    topic = event_data.get("topic", "Зустріч")
    date = event_data.get("date", "Не вказано")
    place = event_data.get("place", "Не вказано")
    time_value = final_time.strip() if final_time.strip() else "Не вказано"
    return (
        f"Фінал: {topic}\n"
        f"Дата: {date}\n"
        f"Час: {time_value}\n"
        f"Місце: {place}\n\n"
        f"{render_rsvp_summary(event_data)}"
    )


def build_help_text() -> str:
    return (
        "Команди управління (писати в приват):\n\n"
        "/meeting <дата> | <місце> | <текст>\n"
        "Запустити збір.\n\n"
        "/who\n"
        "Показати поточну кількість і список учасників.\n\n"
        "/close <час>\n"
        "Закрити збір і опублікувати фінал.\n\n"
        "/final <час>\n"
        "Те саме, що /close.\n\n"
        "/help\n"
        "Показати цю довідку.\n\n"
        "Приклад:\n"
        "/meeting сьогодні | Аркадія, 2 майданчик | волейбол\n"
        "У групі голоси: +, + 19:00, йду 19:30, -\n"
        "/close 19:30"
    )


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
        if command in ADMIN_HELP_COMMANDS:
            await client.send_message(event.chat_id, build_help_text())
            return

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
            payload = parse_meeting_payload(command_args)
            posted = await client.send_message(target_chat, payload["text"])
            events_state[target_chat_key] = {
                "message_id": int(posted.id),
                "is_open": True,
                "started_at_message_id": int(posted.id),
                "topic": payload["topic"],
                "date": payload["date"],
                "place": payload["place"],
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
                final_post = render_final_event_text(active_event, command_args)
                await client.send_message(target_chat, "Збір закрито.\n\n" + final_post)
                await client.send_message(event.chat_id, "Збір закрито.")
            else:
                await client.send_message(event.chat_id, "Активного збору немає. Запусти /meeting")
            return

        if command in ADMIN_FINAL_COMMANDS:
            if active_event and active_event.get("is_open", True):
                active_event["is_open"] = False
                events_state[target_chat_key] = active_event
                state["events"] = events_state
                save_state(state)
                final_post = render_final_event_text(active_event, command_args)
                await client.send_message(target_chat, "Збір закрито.\n\n" + final_post)
                await client.send_message(event.chat_id, "Фінал відправлено, збір закрито.")
            else:
                await client.send_message(event.chat_id, "Активного збору немає. Запусти /meeting")
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
                    participants[uid] = {
                        "name": user_display_name(sender),
                        "time": extract_time_hint(text),
                    }
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
    is_admin_in_target = False
    if channel_entity is not None:
        is_admin_in_target = await sender_is_admin(channel_entity, user_id)

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
        if user_id != TEST_USER_ID and (PROCESS_ONCE or not is_admin_in_target):
            processed_users.add(user_id)
            state["processed_users"] = sorted(processed_users)
        save_state(state)
        return

    if not any(keyword in text_lower for keyword in KEYWORDS):
        return

    should_limit_by_default = not is_admin_in_target
    should_limit_by_config = PROCESS_ONCE and is_admin_in_target
    should_limit = should_limit_by_default or should_limit_by_config

    if should_limit and user_id in processed_users and user_id != TEST_USER_ID:
        print(f"Skip user {user_id}: already processed", flush=True)
        return

    print(f"Triggered by user {user_id}: {text[:80]}", flush=True)
    await client(UpdateStatusRequest(offline=False))
    await client.send_message(event.chat_id, REPLY_TEXT)
    await client(UpdateStatusRequest(offline=True))

    # For non-admins we always lock after first trigger; admins follow PROCESS_ONCE.
    if user_id != TEST_USER_ID and should_limit:
        processed_users.add(user_id)
        state["processed_users"] = sorted(processed_users)

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
