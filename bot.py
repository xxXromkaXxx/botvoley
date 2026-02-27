import asyncio
import html
import json
import os
import random
import re
import time
from pathlib import Path

from telethon import Button, TelegramClient, events
from telethon.errors import UserNotParticipantError
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.channels import InviteToChannelRequest

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
# Hardcoded target chat per current project setup.
CHANNEL_ID_RAW = "-1003885190351"
KEYWORDS_RAW = os.getenv("KEYWORDS", "дайвінчик,волейбол")
REPLY_TEXT = os.getenv(
    "REPLY_TEXT",
    "Привіт, я Рома, радий, що ти написав/ла, зараз додам тебе в чат, але спочатку скажи своє ім'я чи представся, будь ласка :)",
)
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
PROCESS_ONCE = os.getenv("PROCESS_ONCE", "1").strip() == "1"
TEST_USER_ID = int(os.getenv("TEST_USER_ID", "0"))
DAIVINCHIK_CHAT_ID_RAW = "1234060895"
DAIVINCHIK_CHAT_ID = int(DAIVINCHIK_CHAT_ID_RAW)

MEETING_TEXT_FALLBACK = (
    "Ну що, збираємось?\n"
    "Голосуйте.\n"
    "Відповідайте реплаєм на це повідомлення:\n"
    "+ або + 16:00 якщо будете\n"
    "- якщо не будете"
)
ADMIN_MEETING_COMMANDS = {"/meeting", "/discuss", "/збір", "/обговорення"}
ADMIN_EDIT_COMMANDS = {"/editmeeting", "/редзбір"}
ADMIN_WHO_COMMANDS = {"/who", "/rsvp", "/хто"}
ADMIN_LIST_COMMANDS = {"/meetings", "/list", "/список"}
ADMIN_CLOSE_COMMANDS = {"/close", "/закрити"}
ADMIN_FINAL_COMMANDS = {"/final", "/підсумок"}
USER_HELP_COMMANDS = {"/help", "/команди"}
ADMIN_HELP_COMMANDS = {"/helpa", "/адмінка", "/adminhelp"}
ADMIN_AUTOLIKE_COMMANDS = {"/autolike", "/лайкстарт"}
MEETING_CREATE_USER_COOLDOWN_SEC = 5 * 60
MEETING_CREATE_GLOBAL_COOLDOWN_SEC = 90
ALL_ADMIN_COMMANDS = (
    ADMIN_MEETING_COMMANDS
    | ADMIN_EDIT_COMMANDS
    | ADMIN_WHO_COMMANDS
    | ADMIN_LIST_COMMANDS
    | ADMIN_CLOSE_COMMANDS
    | ADMIN_FINAL_COMMANDS
    | ADMIN_HELP_COMMANDS
    | ADMIN_AUTOLIKE_COMMANDS
)
YES_MARKERS = {"+", "+1", "йду", "я за", "пирйду", "прийду", "я в темі", "я буду"}
NO_MARKERS = {"-", "-1", "не йду", "не буду"}
MEETING_BTN_YES = "✅ Йду"
MEETING_BTN_NO = "❌ Не йду"
MEETING_BTN_TIME = "🕒 Пропоную час"

DAIVINCHIK_LIKES_RE = re.compile(r"Ти сподобався\s*(\d+)\s*дівчинам, показати їх\?", re.IGNORECASE)
DAIVINCHIK_PROFILE_LIKED_TEXT = "Комусь сподобалась твоя анкета"
DAIVINCHIK_START_CHAT_TEXT = "Починай спілкуватися"
USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{4,})")
OUTREACH_TEXT = (
    "Це я з дайвінчика .\n"
    "Вітаю! Збираємо нову компанію для спорту та активного дозвілля. "
    "Зараз плануємо волейбол і шукаємо нових людей у команду. "
    "Будемо раді бачити тебе 🙂"
)
DAIV_AUTO_MIN_INTERVAL_SEC = 8 * 60 * 60
DAIV_AUTO_MAX_INTERVAL_SEC = 9 * 60 * 60
DAIV_AUTO_MIN_LIKES = 5
DAIV_AUTO_MAX_LIKES = 6
DAIV_AUTO_CONTROL_TEXTS = {"💤", "1", "1 👍", "❤️"}
DAIV_AUTO_CHECK_EVERY_SEC = 5 * 60

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
        return {
            "processed_users": [],
            "awaiting_intro_users": [],
            "intro_name_tries": {},
            "events": {},
            "events_history": {},
            "contacted_usernames": [],
            "last_manual_daiv_ts": 0,
            "next_auto_daiv_ts": 0,
            "next_meeting_id": 1,
            "meeting_last_create_ts": 0,
            "meeting_creator_last_ts": {},
        }
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        users = [int(x) for x in data.get("processed_users", [])]
        awaiting = [int(x) for x in data.get("awaiting_intro_users", [])]
        intro_name_tries = {str(k): int(v) for k, v in data.get("intro_name_tries", {}).items()}
        contacted = [str(x).lower() for x in data.get("contacted_usernames", [])]

        events_map = {}
        for chat_id, payload in data.get("events", {}).items():
            msg_id = int(payload.get("message_id", 0))
            event_item = {
                "message_id": msg_id,
                "is_open": bool(payload.get("is_open", True)),
                "started_at_message_id": int(payload.get("started_at_message_id", msg_id)),
                "topic": str(payload.get("topic", "Зустріч")),
                "date": str(payload.get("date", "Не вказано")),
                "time": str(payload.get("time", "Не вказано")),
                "place": str(payload.get("place", "Не вказано")),
                "meeting_id": int(payload.get("meeting_id", 0) or 0),
                "created_by": int(payload.get("created_by", 0) or 0),
                "created_by_name": str(payload.get("created_by_name", "")),
                "created_at_ts": int(payload.get("created_at_ts", 0) or 0),
                "participants": {},
            }

            participants = payload.get("participants", {})
            for uid, data_item in participants.items():
                if isinstance(data_item, dict):
                    event_item["participants"][str(uid)] = {
                        "name": str(data_item.get("name", uid)),
                        "time": str(data_item.get("time", "")),
                    }
                else:
                    event_item["participants"][str(uid)] = {
                        "name": str(data_item),
                        "time": "",
                    }

            events_map[str(chat_id)] = event_item

        history_map = {}
        for chat_id, rows in data.get("events_history", {}).items():
            parsed_rows = []
            if not isinstance(rows, list):
                rows = []
            for payload in rows:
                msg_id = int(payload.get("message_id", 0))
                row = {
                    "message_id": msg_id,
                    "is_open": bool(payload.get("is_open", False)),
                    "started_at_message_id": int(payload.get("started_at_message_id", msg_id)),
                    "topic": str(payload.get("topic", "Зустріч")),
                    "date": str(payload.get("date", "Не вказано")),
                    "time": str(payload.get("time", "Не вказано")),
                    "place": str(payload.get("place", "Не вказано")),
                    "meeting_id": int(payload.get("meeting_id", 0) or 0),
                    "created_by": int(payload.get("created_by", 0) or 0),
                    "created_by_name": str(payload.get("created_by_name", "")),
                    "created_at_ts": int(payload.get("created_at_ts", 0) or 0),
                    "participants": {},
                }
                participants = payload.get("participants", {})
                for uid, data_item in participants.items():
                    if isinstance(data_item, dict):
                        row["participants"][str(uid)] = {
                            "name": str(data_item.get("name", uid)),
                            "time": str(data_item.get("time", "")),
                        }
                    else:
                        row["participants"][str(uid)] = {"name": str(data_item), "time": ""}
                parsed_rows.append(row)
            history_map[str(chat_id)] = parsed_rows

        return {
            "processed_users": users,
            "awaiting_intro_users": awaiting,
            "intro_name_tries": intro_name_tries,
            "events": events_map,
            "events_history": history_map,
            "contacted_usernames": contacted,
            "last_manual_daiv_ts": int(data.get("last_manual_daiv_ts", 0) or 0),
            "next_auto_daiv_ts": int(data.get("next_auto_daiv_ts", 0) or 0),
            "next_meeting_id": max(1, int(data.get("next_meeting_id", 1) or 1)),
            "meeting_last_create_ts": int(data.get("meeting_last_create_ts", 0) or 0),
            "meeting_creator_last_ts": {
                str(k): int(v) for k, v in data.get("meeting_creator_last_ts", {}).items()
            },
        }
    except Exception:
        return {
            "processed_users": [],
            "awaiting_intro_users": [],
            "intro_name_tries": {},
            "events": {},
            "events_history": {},
            "contacted_usernames": [],
            "last_manual_daiv_ts": 0,
            "next_auto_daiv_ts": 0,
            "next_meeting_id": 1,
            "meeting_last_create_ts": 0,
            "meeting_creator_last_ts": {},
        }


def save_state(state: dict) -> None:
    payload = {
        "processed_users": sorted(int(x) for x in state.get("processed_users", [])),
        "awaiting_intro_users": sorted(int(x) for x in state.get("awaiting_intro_users", [])),
        "intro_name_tries": {
            str(k): int(v) for k, v in state.get("intro_name_tries", {}).items()
        },
        "events": {},
        "events_history": {},
        "contacted_usernames": sorted(set(str(x).lower() for x in state.get("contacted_usernames", []))),
        "last_manual_daiv_ts": int(state.get("last_manual_daiv_ts", 0) or 0),
        "next_auto_daiv_ts": int(state.get("next_auto_daiv_ts", 0) or 0),
        "next_meeting_id": max(1, int(state.get("next_meeting_id", 1) or 1)),
        "meeting_last_create_ts": int(state.get("meeting_last_create_ts", 0) or 0),
        "meeting_creator_last_ts": {
            str(k): int(v) for k, v in state.get("meeting_creator_last_ts", {}).items()
        },
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
            "time": str(event_data.get("time", "Не вказано")),
            "place": str(event_data.get("place", "Не вказано")),
            "meeting_id": int(event_data.get("meeting_id", 0) or 0),
            "created_by": int(event_data.get("created_by", 0) or 0),
            "created_by_name": str(event_data.get("created_by_name", "")),
            "created_at_ts": int(event_data.get("created_at_ts", 0) or 0),
            "participants": {
                str(uid): {
                    "name": str(data_item.get("name", uid)) if isinstance(data_item, dict) else str(data_item),
                    "time": str(data_item.get("time", "")) if isinstance(data_item, dict) else "",
                }
                for uid, data_item in event_data.get("participants", {}).items()
            },
        }

    for chat_id, rows in state.get("events_history", {}).items():
        safe_rows = []
        if not isinstance(rows, list):
            rows = []
        for event_data in rows:
            safe_rows.append(
                {
                    "message_id": int(event_data.get("message_id", 0)),
                    "is_open": bool(event_data.get("is_open", False)),
                    "started_at_message_id": int(
                        event_data.get("started_at_message_id", event_data.get("message_id", 0))
                    ),
                    "topic": str(event_data.get("topic", "Зустріч")),
                    "date": str(event_data.get("date", "Не вказано")),
                    "time": str(event_data.get("time", "Не вказано")),
                    "place": str(event_data.get("place", "Не вказано")),
                    "meeting_id": int(event_data.get("meeting_id", 0) or 0),
                    "created_by": int(event_data.get("created_by", 0) or 0),
                    "created_by_name": str(event_data.get("created_by_name", "")),
                    "created_at_ts": int(event_data.get("created_at_ts", 0) or 0),
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
            )
        payload["events_history"][str(chat_id)] = safe_rows

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_vote(text: str):
    raw = text.strip().lower()
    if raw.startswith("✅") or raw == "йду":
        return "yes"
    if raw.startswith("❌"):
        return "no"

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


def is_valid_intro_name(text: str) -> bool:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return False
    if len(cleaned) < 4:
        return False
    if len(cleaned) > 220:
        return False
    if "?" in cleaned:
        return False
    if any(x in cleaned.lower() for x in ["http://", "https://", "@", "#"]):
        return False

    parts = re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’-]+", cleaned.lower())
    if len(parts) < 1:
        return False

    # Reject question-like / support-like content.
    bad_tokens = {
        "коли",
        "де",
        "чому",
        "як",
        "питання",
        "допоможіть",
        "допоможи",
        "можна",
        "підкажіть",
        "підкажи",
    }
    if any(p in bad_tokens for p in parts):
        return False

    # Accept if message looks like a self-introduction.
    intro_markers = {
        "я",
        "мене",
        "звуть",
        "звати",
        "мій",
        "i",
        "im",
        "i'm",
        "my",
        "name",
    }
    if any(p in intro_markers for p in parts):
        return True

    # Accept one-word name-like input (e.g. "Іван", "Oleh").
    if len(parts) == 1:
        p = parts[0]
        if 2 <= len(p) <= 24 and p not in bad_tokens:
            return True

    # Fallback: 2-6 word human-like text without question markers.
    if 2 <= len(parts) <= 6:
        return True

    return False


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


def contains_trigger_keyword(text: str) -> bool:
    low = text.lower()
    compact = " ".join(re.sub(r"[^\w\sа-яіїєґ']", " ", low, flags=re.IGNORECASE).split())
    if any(keyword in low for keyword in KEYWORDS):
        return True
    # Fallback for common word forms/typos around target words.
    fuzzy = ("дайвінч", "daiv", "волейбол", "волейб", "volley")
    return any(token in compact for token in fuzzy)


def parse_meeting_id_arg(args: str) -> int | None:
    value = args.strip()
    if not value:
        return None
    if value.startswith("#"):
        value = value[1:].strip()
    if not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def parse_edit_meeting_payload(args: str):
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 5:
        return None, None
    meeting_id = parse_meeting_id_arg(parts[0])
    if meeting_id is None:
        return None, None
    payload = parse_meeting_payload(" | ".join(parts[1:]))
    return meeting_id, payload


def normalize_meeting_date(value: str) -> str:
    low = value.strip().lower()
    if low in {"сьогодні", "сьогоднi", "today"}:
        return "Сьогодні"
    if low in {"завтра", "tomorrow"}:
        return "Завтра"
    return value.strip() or "Не вказано"


def parse_meeting_payload(args: str) -> dict:
    # format: /meeting <date> | <time> | <place> | <text>
    if not args:
        return {
            "topic": "Зустріч",
            "date": "Не вказано",
            "time": "Не вказано",
            "place": "Не вказано",
            "text": MEETING_TEXT_FALLBACK,
        }

    parts = [p.strip() for p in args.split("|")]
    if len(parts) >= 4:
        date = normalize_meeting_date(parts[0])
        time_value = parts[1] or "Не вказано"
        place = parts[2] or "Не вказано"
        topic = parts[3] or "Зустріч"
        text = (
            f"Збір: {topic}\n"
            f"Дата: {date}\n"
            f"Час (база): {time_value}\n"
            f"Місце: {place}\n\n"
            "Голосуйте.\n"
            "Відповідайте реплаєм на це повідомлення:\n"
            "+ або + 16:00 якщо будете\n"
            "- якщо не будете"
        )
        return {"topic": topic, "date": date, "time": time_value, "place": place, "text": text}

    # Backward compatible: /meeting <date> | <place> | <text>
    if len(parts) >= 3:
        date = normalize_meeting_date(parts[0])
        place = parts[1] or "Не вказано"
        topic = parts[2] or "Зустріч"
        time_value = "Не вказано"
        text = (
            f"Збір: {topic}\n"
            f"Дата: {date}\n"
            f"Час (база): {time_value}\n"
            f"Місце: {place}\n\n"
            "Голосуйте.\n"
            "Відповідайте реплаєм на це повідомлення:\n"
            "+ або + 16:00 якщо будете\n"
            "- якщо не будете"
        )
        return {"topic": topic, "date": date, "time": time_value, "place": place, "text": text}

    text = (
        f"Збір: {args}\n"
        "Дата: Не вказано\n"
        "Час (база): Не вказано\n"
        "Місце: Не вказано\n\n"
        "Голосуйте.\n"
        "Відповідайте реплаєм на це повідомлення:\n"
        "+ або + 16:00 якщо будете\n"
        "- якщо не будете"
    )
    return {"topic": args, "date": "Не вказано", "time": "Не вказано", "place": "Не вказано", "text": text}


def choose_final_time(event_data: dict, fallback_time: str) -> str:
    meeting_time = str(event_data.get("time", "Не вказано")).strip() or "Не вказано"
    participant_times = []
    for item in event_data.get("participants", {}).values():
        t = str(item.get("time", "")).strip()
        if t:
            participant_times.append(t)

    if fallback_time.strip():
        return fallback_time.strip()
    if not participant_times:
        return meeting_time

    if len(participant_times) == 1 and meeting_time != "Не вказано" and participant_times[0] != meeting_time:
        return meeting_time

    counts = {}
    for t in participant_times:
        counts[t] = counts.get(t, 0) + 1
    best_count = max(counts.values())
    top = sorted([k for k, v in counts.items() if v == best_count])
    if meeting_time in top:
        return meeting_time
    return top[0]


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
    meeting_id = int(event_data.get("meeting_id", 0) or 0)
    meeting_prefix = f"ID збору: #{meeting_id}\n" if meeting_id else ""
    if not participants:
        return f"{meeting_prefix}Хто приходить: 0\n\nПоки що ніхто не підтвердив участь."

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

    lines = [f"{meeting_prefix}Хто приходить: {len(rows)}", ""]
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
    time_value = choose_final_time(event_data, final_time)
    return (
        f"Отже, збираємось:\n"
        f"ID збору: #{int(event_data.get('meeting_id', 0) or 0)}\n"
        f"Подія: {topic}\n"
        f"Дата: {date}\n"
        f"Час: {time_value}\n"
        f"Місце: {place}\n\n"
        f"{render_rsvp_summary(event_data)}"
    )


def build_user_help_text() -> str:
    return (
        "Довідка користувача\n\n"
        "Важливо: команди пишемо тільки в ПРИВАТ боту.\n\n"
        "Як голосувати в групі (реплаєм на пост збору):\n"
        "+  -> прийду\n"
        "+ 19:00 -> прийду і пропоную свій час\n"
        "-  -> не прийду\n\n"
        "Команди:\n"
        "/who\n"
        "Показати список учасників активного збору.\n\n"
        "/who <id>\n"
        "Показати підсумок конкретного збору за ID (наприклад: /who 3).\n\n"
        "/meetings\n"
        "Показати активний і останні закриті збори.\n\n"
        "/meeting <дата> | <час> | <місце> | <текст>\n"
        "Доступно всім учасникам групи (є антифлуд).\n"
        "У <текст> пиши вид активності + за бажанням короткий опис.\n"
        "Приклад: футбол 5x5, легка гра без жорсткого контакту.\n\n"
        "/editmeeting <id> | <дата> | <час> | <місце> | <текст>\n"
        "Редагувати активний збір за ID без створення нового.\n\n"
        "Для повної адмін-довідки: /helpa"
    )


def build_admin_help_text() -> str:
    return (
        "Адмін-довідка\n\n"
        "Усі команди пишемо тільки в ПРИВАТ боту.\n\n"
        "1) Створити збір:\n"
        "/meeting <дата> | <час> | <місце> | <текст>\n"
        "<текст> = вид активності + опціонально деталі.\n"
        "Напр.: футбол | або футбол + короткий опис умов.\n"
        "Приклад: /meeting завтра | 19:30 | Аркадія, 2 майданчик | волейбол\n\n"
        "1.1) Редагувати активний збір:\n"
        "/editmeeting <id> | <дата> | <час> | <місце> | <текст>\n"
        "Приклад: /editmeeting 12 | сьогодні | 18:30 | Аркадія | волейбол + новачки welcome\n\n"
        "2) Перегляд голосування:\n"
        "/who\n"
        "Поточний активний збір.\n\n"
        "/who <id>\n"
        "Конкретний збір за ID (активний або архів), наприклад: /who 3.\n\n"
        "/meetings\n"
        "Активний + останні закриті збори.\n\n"
        "3) Закрити збір:\n"
        "/close <час>\n"
        "Закрити і відправити фінал у групу.\n\n"
        "/final <час>\n"
        "Те саме, що /close.\n\n"
        "4) Автолайк (тільки для акаунта сесії):\n"
        "/autolike <кількість>\n"
        "Запуск ручного автолайку в Дайвінчику (1..20).\n"
        "Команду може виконати тільки той самий акаунт, на якому запущений SESSION_STRING.\n\n"
        "5) Довідка:\n"
        "/helpa\n"
        "Показати цю інструкцію."
    )


async def sender_is_admin(chat, user_id: int) -> bool:
    try:
        perms = await client.get_permissions(chat, user_id)
        return bool(getattr(perms, "is_creator", False) or getattr(perms, "is_admin", False))
    except Exception:
        return False


async def sender_is_member(chat, user_id: int) -> bool:
    try:
        await client.get_permissions(chat, user_id)
        return True
    except UserNotParticipantError:
        return False
    except Exception:
        return False


async def expected_votes_for_target_chat(chat) -> int | None:
    try:
        count = 0
        async for p in client.iter_participants(chat):
            pid = int(getattr(p, "id", 0) or 0)
            if pid == self_user_id:
                continue
            if getattr(p, "bot", False):
                continue
            count += 1
        return count
    except Exception as e:
        print(f"Warning: cannot calculate expected votes: {e}", flush=True)
        return None


async def maybe_auto_finalize_meeting(chat, chat_key: str):
    active_event = events_state.get(chat_key)
    if not active_event or not active_event.get("is_open", True):
        return

    expected_votes = await expected_votes_for_target_chat(chat)
    if expected_votes is None or expected_votes <= 0:
        return

    current_votes = len(active_event.get("participants", {}))
    if current_votes < expected_votes:
        return

    active_event["is_open"] = False
    events_state.pop(chat_key, None)
    state["events"] = events_state
    archive_event(chat_key, active_event)
    save_state(state)

    final_post = render_final_event_text(active_event, "")
    await client.send_message(chat, "Збір закрито автоматично (проголосували всі).\n\n" + final_post)


def archive_event(chat_key: str, event_data: dict) -> None:
    meeting_id = int(event_data.get("meeting_id", 0) or 0)
    history = state.get("events_history", {})
    rows = history.get(chat_key, [])
    updated = False
    for idx, row in enumerate(rows):
        if int(row.get("meeting_id", 0) or 0) == meeting_id and meeting_id > 0:
            rows[idx] = event_data
            updated = True
            break
    if not updated:
        rows.append(event_data)
    history[chat_key] = rows
    state["events_history"] = history


def find_event_by_meeting_id(chat_key: str, meeting_id: int):
    active = events_state.get(chat_key)
    if active and int(active.get("meeting_id", 0) or 0) == meeting_id:
        return active, "active"
    for row in state.get("events_history", {}).get(chat_key, []):
        if int(row.get("meeting_id", 0) or 0) == meeting_id:
            return row, "history"
    return None, ""


def render_meetings_list(chat_key: str) -> str:
    lines = ["Мітінги:"]
    active = events_state.get(chat_key)
    if active and active.get("is_open", True):
        mid = int(active.get("meeting_id", 0) or 0)
        topic = str(active.get("topic", "Зустріч"))
        date = str(active.get("date", "Не вказано"))
        time_value = str(active.get("time", "Не вказано"))
        lines.append(f"Активний: #{mid} | {topic} | {date} {time_value}")
    else:
        lines.append("Активний: немає")

    history_rows = state.get("events_history", {}).get(chat_key, [])
    if not history_rows:
        lines.append("")
        lines.append("Закриті: немає")
        return "\n".join(lines)

    lines.append("")
    lines.append("Останні закриті:")
    sorted_rows = sorted(
        history_rows,
        key=lambda x: int(x.get("meeting_id", 0) or 0),
        reverse=True,
    )
    for row in sorted_rows[:10]:
        mid = int(row.get("meeting_id", 0) or 0)
        topic = str(row.get("topic", "Зустріч"))
        date = str(row.get("date", "Не вказано"))
        time_value = str(row.get("time", "Не вказано"))
        count = len(row.get("participants", {}))
        lines.append(f"#{mid} | {topic} | {date} {time_value} | учасників: {count}")
    return "\n".join(lines)


def meeting_buttons():
    return [
        [Button.text(MEETING_BTN_YES), Button.text(MEETING_BTN_NO)],
        [Button.text(MEETING_BTN_TIME)],
    ]


async def post_meeting_message(chat, text: str):
    try:
        return await client.send_message(chat, text, buttons=meeting_buttons())
    except Exception as e:
        print(f"Warning: cannot attach meeting buttons, fallback to plain text: {e}", flush=True)
        return await client.send_message(chat, text)


async def edit_meeting_message(chat, message_id: int, text: str):
    try:
        await client.edit_message(chat, message=message_id, text=text, buttons=meeting_buttons())
    except Exception as e:
        print(f"Warning: cannot edit meeting with buttons, fallback to plain text: {e}", flush=True)
        await client.edit_message(chat, message=message_id, text=text)


async def user_is_member_of_target_chat(user_id: int) -> bool:
    if channel_entity is None:
        return False
    try:
        await client.get_permissions(channel_entity, user_id)
        return True
    except UserNotParticipantError:
        return False
    except Exception:
        return False


async def invite_username_to_target_chat(username: str):
    if channel_entity is None:
        return False, "no_target_chat"
    try:
        user_entity = await client.get_entity(f"@{username}")
    except Exception as e:
        return False, f"resolve_failed:{e}"

    user_id = int(getattr(user_entity, "id", 0) or 0)
    if user_id and await user_is_member_of_target_chat(user_id):
        return True, "already_member"

    try:
        await client(InviteToChannelRequest(channel=channel_entity, users=[user_entity]))
        return True, "invited"
    except Exception as e:
        return False, f"invite_failed:{e}"


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

        print(f"Warning: cannot resolve CHANNEL_ID={CHANNEL_ID_RAW}: not found in dialogs", flush=True)
        print("Bot will continue, but channel features are disabled.", flush=True)
        return None


def is_daiv_chat_message(event) -> bool:
    if not DAIVINCHIK_CHAT_ID:
        return False
    if not event.is_private:
        return False
    return int(event.chat_id or 0) == int(DAIVINCHIK_CHAT_ID)


async def send_daiv_message(text: str, delay_sec: tuple | None = None):
    global last_bot_daiv_action_ts
    if not DAIVINCHIK_CHAT_ID:
        return
    if delay_sec:
        await asyncio.sleep(random.uniform(delay_sec[0], delay_sec[1]))
    await client.send_message(DAIVINCHIK_CHAT_ID, text)
    last_bot_daiv_action_ts = int(time.time())


def schedule_next_daiv_auto_run(from_ts: int | None = None) -> int:
    base_ts = int(from_ts or time.time())
    next_ts = base_ts + random.randint(DAIV_AUTO_MIN_INTERVAL_SEC, DAIV_AUTO_MAX_INTERVAL_SEC)
    state["next_auto_daiv_ts"] = next_ts
    save_state(state)
    return next_ts


async def finish_daiv_auto_session(force_sleep: bool = False):
    if not daiv_auto_session["active"]:
        return
    daiv_auto_session["active"] = False
    daiv_auto_session["cooldown_until"] = int(time.time()) + 120
    if force_sleep:
        try:
            await send_daiv_message("💤", (0.7, 1.3))
        except Exception:
            pass


async def start_daiv_auto_session():
    if not DAIVINCHIK_CHAT_ID or daiv_auto_session["active"]:
        return
    daiv_auto_session["active"] = True
    daiv_auto_session["done"] = 0
    daiv_auto_session["target"] = random.randint(DAIV_AUTO_MIN_LIKES, DAIV_AUTO_MAX_LIKES)
    daiv_auto_session["started_ts"] = int(time.time())
    daiv_auto_session["cooldown_until"] = 0
    try:
        schedule_next_daiv_auto_run()
        await send_daiv_message("💤")
        await send_daiv_message("1", (2.0, 3.0))
    except Exception as e:
        print(f"Warning: cannot start daiv auto session: {e}", flush=True)
        await finish_daiv_auto_session(force_sleep=True)


async def start_daiv_auto_session_with_target(target_likes: int):
    if not DAIVINCHIK_CHAT_ID or daiv_auto_session["active"]:
        return False
    daiv_auto_session["active"] = True
    daiv_auto_session["done"] = 0
    daiv_auto_session["target"] = max(1, min(20, int(target_likes)))
    daiv_auto_session["started_ts"] = int(time.time())
    daiv_auto_session["cooldown_until"] = 0
    try:
        schedule_next_daiv_auto_run()
        await send_daiv_message("💤")
        await send_daiv_message("1", (2.0, 3.0))
        return True
    except Exception as e:
        print(f"Warning: cannot start manual daiv auto session: {e}", flush=True)
        await finish_daiv_auto_session(force_sleep=True)
        return False


async def daiv_auto_worker():
    while True:
        await asyncio.sleep(DAIV_AUTO_CHECK_EVERY_SEC)

        if not DAIVINCHIK_CHAT_ID:
            continue
        if daiv_auto_session["active"]:
            continue

        now_ts = int(time.time())
        next_ts = int(state.get("next_auto_daiv_ts", 0) or 0)
        if next_ts == 0:
            schedule_next_daiv_auto_run(now_ts)
            continue
        if now_ts < next_ts:
            continue

        last_manual = int(state.get("last_manual_daiv_ts", 0) or 0)
        idle_for = now_ts - last_manual if last_manual else 10**9
        if idle_for < DAIV_AUTO_MIN_INTERVAL_SEC:
            schedule_next_daiv_auto_run(now_ts)
            continue

        await start_daiv_auto_session()


state = load_state()
processed_users = set(state["processed_users"])
awaiting_intro_users = set(state["awaiting_intro_users"])
intro_name_tries = state.get("intro_name_tries", {})
events_state = state["events"]
contacted_usernames = set(state.get("contacted_usernames", []))
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
channel_entity = None
self_user_id = 0
last_bot_daiv_action_ts = 0
daiv_auto_session = {"active": False, "done": 0, "target": 0, "started_ts": 0, "cooldown_until": 0}


@client.on(events.NewMessage())
async def handle_message(event):
    if not event.raw_text:
        return

    sender = await event.get_sender()
    text = event.raw_text.strip()
    text_lower = text.lower()
    command, command_args = split_command_and_args(text)
    user_id = int(event.sender_id or 0)

    # Track manual activity in Daiv chat to pause periodic auto sessions.
    if is_daiv_chat_message(event) and event.out:
        now_ts = int(time.time())
        is_bot_generated = (now_ts - last_bot_daiv_action_ts <= 6) and (text in DAIV_AUTO_CONTROL_TEXTS)
        if not is_bot_generated:
            state["last_manual_daiv_ts"] = now_ts
            schedule_next_daiv_auto_run(now_ts)
        return

    # Auto-flow for Daivinchik-like bot messages.
    if event.is_private and getattr(sender, "bot", False):
        if not is_daiv_chat_message(event):
            return

        now_ts = int(time.time())
        if int(daiv_auto_session.get("cooldown_until", 0) or 0) > now_ts:
            if DAIVINCHIK_PROFILE_LIKED_TEXT.lower() in text_lower:
                return

        likes_match = DAIVINCHIK_LIKES_RE.search(text)
        if likes_match:
            likes_count = int(likes_match.group(1))
            if likes_count > 0:
                await send_daiv_message("1 👍")
            return

        if DAIVINCHIK_PROFILE_LIKED_TEXT.lower() in text_lower:
            await send_daiv_message("❤️")
            return

        if DAIVINCHIK_START_CHAT_TEXT.lower() in text_lower:
            username_match = USERNAME_RE.search(text)
            if username_match:
                username = username_match.group(1).lower()
                invite_ok, invite_status = await invite_username_to_target_chat(username)
                if not invite_ok:
                    print(f"Warning: cannot add @{username} to target chat: {invite_status}", flush=True)

                if username not in contacted_usernames and invite_status != "already_member":
                    try:
                        dm_text = OUTREACH_TEXT
                        if invite_ok and invite_status == "invited":
                            dm_text += "\n\nТебе вже додано в групу 🙂"
                        await client.send_message(f"@{username}", dm_text)
                        contacted_usernames.add(username)
                        state["contacted_usernames"] = sorted(contacted_usernames)
                        save_state(state)
                    except Exception as e:
                        print(f"Warning: cannot outreach @{username}: {e}", flush=True)
            return

        # Auto-like session for profile browsing in Daiv chat.
        if daiv_auto_session["active"]:
            try:
                # After sending "1", wait 4-5s before first possible like.
                if daiv_auto_session["done"] == 0:
                    started_ts = int(daiv_auto_session.get("started_ts", now_ts) or now_ts)
                    if now_ts - started_ts < 5:
                        await asyncio.sleep(max(0, 5 - (now_ts - started_ts)))

                end_markers = [
                    "Я більше не хочу нікого дивитись",
                    "більше немає",
                    "закінчились",
                    "не знайдено",
                ]
                if any(m.lower() in text_lower for m in end_markers):
                    await finish_daiv_auto_session(force_sleep=True)
                    return

                skip_markers = [
                    "Ти сподобався",
                    "Комусь сподобалась твоя анкета",
                    "Починай спілкуватися",
                ]
                if any(m.lower() in text_lower for m in skip_markers):
                    return

                if daiv_auto_session["done"] < daiv_auto_session["target"]:
                    await send_daiv_message("❤️", (2.0, 2.4))
                    daiv_auto_session["done"] += 1
                    if daiv_auto_session["done"] >= daiv_auto_session["target"]:
                        await finish_daiv_auto_session(force_sleep=True)
                return
            except Exception as e:
                print(f"Warning: daiv auto-like flow failed: {e}", flush=True)
                await finish_daiv_auto_session(force_sleep=True)
                return

        # Ignore other bot messages.
        return

    if getattr(sender, "bot", False):
        return

    # Private user help.
    if event.is_private and command in USER_HELP_COMMANDS:
        await client.send_message(event.chat_id, build_user_help_text())
        return

    # Private command control: commands from private chat only.
    if event.is_private and command in ALL_ADMIN_COMMANDS:
        if command in ADMIN_HELP_COMMANDS:
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
                    f"/helpa доступна тільки адмінам групи CHANNEL_ID={CHANNEL_ID_RAW}.",
                )
                return
            await client.send_message(event.chat_id, build_admin_help_text())
            return

        if channel_entity is None:
            await client.send_message(
                event.chat_id,
                f"Не можу знайти CHANNEL_ID={CHANNEL_ID_RAW}. Перевір, що акаунт із SESSION_STRING є в цьому чаті.",
            )
            return

        is_member_in_target = await sender_is_member(channel_entity, user_id)
        if not is_member_in_target:
            await client.send_message(
                event.chat_id,
                f"Доступ заборонено: user_id={user_id} не є учасником чату CHANNEL_ID={CHANNEL_ID_RAW}.",
            )
            return
        is_admin_in_target = await sender_is_admin(channel_entity, user_id)

        target_chat = channel_entity
        target_chat_key = str(int(getattr(target_chat, "id", 0) or 0))
        active_event = events_state.get(target_chat_key)

        if command in ADMIN_MEETING_COMMANDS:
            if active_event and active_event.get("is_open", True):
                active_id = int(active_event.get("meeting_id", 0) or 0)
                await client.send_message(
                    event.chat_id,
                    f"Вже є активний збір #{active_id}. Закрий його перед створенням нового.",
                )
                return

            now_ts = int(time.time())
            last_global = int(state.get("meeting_last_create_ts", 0) or 0)
            if now_ts - last_global < MEETING_CREATE_GLOBAL_COOLDOWN_SEC:
                left = MEETING_CREATE_GLOBAL_COOLDOWN_SEC - (now_ts - last_global)
                await client.send_message(event.chat_id, f"Антифлуд: новий збір можна створити через {left} сек.")
                return

            creator_last = state.get("meeting_creator_last_ts", {})
            last_user = int(creator_last.get(str(user_id), 0) or 0)
            if now_ts - last_user < MEETING_CREATE_USER_COOLDOWN_SEC:
                left = MEETING_CREATE_USER_COOLDOWN_SEC - (now_ts - last_user)
                await client.send_message(
                    event.chat_id,
                    f"Антифлуд: для тебе створення нового збору буде доступне через {left} сек.",
                )
                return

            payload = parse_meeting_payload(command_args)
            meeting_id = int(state.get("next_meeting_id", 1) or 1)
            state["next_meeting_id"] = meeting_id + 1
            post_text = f"ID збору: #{meeting_id}\n{payload['text']}"
            posted = await post_meeting_message(target_chat, post_text)
            events_state[target_chat_key] = {
                "message_id": int(posted.id),
                "is_open": True,
                "started_at_message_id": int(posted.id),
                "topic": payload["topic"],
                "date": payload["date"],
                "time": payload["time"],
                "place": payload["place"],
                "meeting_id": meeting_id,
                "created_by": user_id,
                "created_by_name": user_display_name(sender),
                "created_at_ts": now_ts,
                "participants": {},
            }
            state["events"] = events_state
            state["meeting_last_create_ts"] = now_ts
            creator_last[str(user_id)] = now_ts
            state["meeting_creator_last_ts"] = creator_last
            save_state(state)
            await client.send_message(event.chat_id, f"Збір #{meeting_id} створено.")
            return

        if command in ADMIN_EDIT_COMMANDS:
            if not active_event or not active_event.get("is_open", True):
                await client.send_message(event.chat_id, "Активного збору немає. Нема що редагувати.")
                return
            edit_id, payload = parse_edit_meeting_payload(command_args)
            if edit_id is None or payload is None:
                await client.send_message(
                    event.chat_id,
                    "Формат: /editmeeting <id> | <дата> | <час> | <місце> | <текст>",
                )
                return
            active_id = int(active_event.get("meeting_id", 0) or 0)
            if edit_id != active_id:
                await client.send_message(
                    event.chat_id,
                    f"Редагувати можна тільки активний збір #{active_id}. Ти вказав #{edit_id}.",
                )
                return
            created_by = int(active_event.get("created_by", 0) or 0)
            if not is_admin_in_target and created_by != user_id:
                await client.send_message(
                    event.chat_id,
                    "Редагувати збір може тільки автор цього збору або адмін групи.",
                )
                return

            active_event["topic"] = payload["topic"]
            active_event["date"] = payload["date"]
            active_event["time"] = payload["time"]
            active_event["place"] = payload["place"]
            post_text = f"ID збору: #{active_id}\n{payload['text']}"
            try:
                await edit_meeting_message(
                    target_chat,
                    int(active_event.get("message_id", 0) or 0),
                    post_text,
                )
            except Exception as e:
                await client.send_message(event.chat_id, f"Не вдалося оновити пост збору: {e}")
                return

            events_state[target_chat_key] = active_event
            state["events"] = events_state
            save_state(state)
            await client.send_message(event.chat_id, f"Збір #{active_id} оновлено.")
            return

        if command in ADMIN_WHO_COMMANDS:
            lookup_id = parse_meeting_id_arg(command_args)
            if lookup_id is not None:
                found, source = find_event_by_meeting_id(target_chat_key, lookup_id)
                if found:
                    suffix = " (активний)" if source == "active" else " (архів)"
                    await client.send_message(event.chat_id, render_rsvp_summary(found) + suffix)
                else:
                    await client.send_message(event.chat_id, f"Збір з ID #{lookup_id} не знайдено.")
                return
            if active_event and active_event.get("is_open", True):
                await client.send_message(event.chat_id, render_rsvp_summary(active_event))
            else:
                await client.send_message(
                    event.chat_id, "Активного збору немає. Для архіву: /who <id> (наприклад /who 3)"
                )
            return

        if command in ADMIN_LIST_COMMANDS:
            await client.send_message(event.chat_id, render_meetings_list(target_chat_key))
            return

        if command in ADMIN_CLOSE_COMMANDS or command in ADMIN_FINAL_COMMANDS:
            if active_event and active_event.get("is_open", True):
                created_by = int(active_event.get("created_by", 0) or 0)
                if not is_admin_in_target and created_by != user_id:
                    await client.send_message(
                        event.chat_id,
                        "Закрити збір може тільки автор цього збору або адмін групи.",
                    )
                    return
                active_event["is_open"] = False
                events_state.pop(target_chat_key, None)
                state["events"] = events_state
                archive_event(target_chat_key, active_event)
                save_state(state)
                final_post = render_final_event_text(active_event, command_args)
                await client.send_message(target_chat, "Збір закрито.\n\n" + final_post)
                await client.send_message(
                    event.chat_id, f"Збір #{int(active_event.get('meeting_id', 0) or 0)} закрито."
                )
            else:
                await client.send_message(event.chat_id, "Активного збору немає. Запусти /meeting")
            return

        if command in ADMIN_AUTOLIKE_COMMANDS:
            if user_id != self_user_id:
                await client.send_message(
                    event.chat_id,
                    f"/autolike доступна тільки акаунту сесії (user_id={self_user_id}).",
                )
                return
            if not DAIVINCHIK_CHAT_ID:
                await client.send_message(event.chat_id, "DAIVINCHIK_CHAT_ID не налаштовано.")
                return
            if daiv_auto_session["active"]:
                await client.send_message(
                    event.chat_id,
                    f"Автолайк вже запущений ({daiv_auto_session['done']}/{daiv_auto_session['target']}).",
                )
                return

            likes_target = DAIV_AUTO_MIN_LIKES
            if command_args.strip():
                try:
                    likes_target = int(command_args.strip())
                except Exception:
                    await client.send_message(event.chat_id, "Формат: /autolike <число_1_20>")
                    return

            ok = await start_daiv_auto_session_with_target(likes_target)
            if ok:
                await client.send_message(
                    event.chat_id,
                    f"Автолайк стартував. Ціль: {daiv_auto_session['target']} лайків.",
                )
            else:
                await client.send_message(event.chat_id, "Не вдалося запустити автолайк.")
            return

    # Group/channel listener for RSVP only (commands disabled there).
    if event.is_group or event.is_channel:
        chat = await event.get_chat()
        chat_id = int(getattr(chat, "id", 0) or 0)
        target_chat_id = int(getattr(channel_entity, "id", 0) or 0) if channel_entity is not None else 0

        chat_key = str(chat_id)
        active_event = events_state.get(chat_key)

        if command in (
            ADMIN_MEETING_COMMANDS
            | ADMIN_EDIT_COMMANDS
            | ADMIN_FINAL_COMMANDS
            | ADMIN_WHO_COMMANDS
            | ADMIN_CLOSE_COMMANDS
            | ADMIN_LIST_COMMANDS
        ):
            return

        if target_chat_id == 0 or chat_id != target_chat_id:
            return

        if active_event and active_event.get("is_open", True):
            reply_to_id = getattr(event.message, "reply_to_msg_id", None)
            event_msg_id = int(active_event.get("message_id", 0))
            started_at = int(active_event.get("started_at_message_id", event_msg_id))

            is_reply_vote = bool(reply_to_id and int(reply_to_id) == event_msg_id)
            is_after_start = int(getattr(event.message, "id", 0) or 0) >= started_at

            vote = normalize_vote(text)
            if (is_reply_vote or is_after_start) and text_lower.startswith("🕒") and "пропоную" in text_lower:
                await client.send_message(chat, "Напиши реплаєм у форматі: + 16:30", reply_to=event.message.id)
                return
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
                await maybe_auto_finalize_meeting(chat, chat_key)
        return

    if not event.is_private:
        return

    is_admin_in_target = False
    if channel_entity is not None:
        is_admin_in_target = await sender_is_admin(channel_entity, user_id)

    # Admin accounts in private chat are command-only: no keyword auto-replies.
    if is_admin_in_target:
        return

    username = getattr(sender, "username", None)

    if user_id in awaiting_intro_users:
        print(f"Intro received from {user_id}: {text[:80]}", flush=True)
        user_key = str(user_id)

        if not is_valid_intro_name(text):
            tries = int(intro_name_tries.get(user_key, 0)) + 1
            intro_name_tries[user_key] = tries
            state["intro_name_tries"] = intro_name_tries
            save_state(state)
            if tries >= 2:
                await client.send_message(
                    event.chat_id,
                    "Щоб додати в групу, напиши коротке представлення: як тебе звати і 1-2 речення про себе, без питань.",
                )
            else:
                await client.send_message(
                    event.chat_id,
                    "Напиши, будь ласка, коротке представлення: ім'я + кілька слів про себе.",
                )
            return

        if channel_entity is not None:
            already_member = await user_is_member_of_target_chat(user_id)
            if not already_member:
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
        intro_name_tries.pop(user_key, None)
        state["awaiting_intro_users"] = sorted(awaiting_intro_users)
        state["intro_name_tries"] = intro_name_tries
        if user_id != TEST_USER_ID:
            processed_users.add(user_id)
            state["processed_users"] = sorted(processed_users)
        save_state(state)
        return

    if not contains_trigger_keyword(text):
        return

    if user_id in processed_users and user_id != TEST_USER_ID:
        print(f"Skip user {user_id}: already processed", flush=True)
        return

    print(f"Triggered by user {user_id}: {text[:80]}", flush=True)
    await client(UpdateStatusRequest(offline=False))
    await client.send_message(event.chat_id, REPLY_TEXT)
    await client(UpdateStatusRequest(offline=True))

    # Non-admin users: one-time trigger lock immediately.
    if user_id != TEST_USER_ID:
        processed_users.add(user_id)
        state["processed_users"] = sorted(processed_users)

    awaiting_intro_users.add(user_id)
    intro_name_tries[str(user_id)] = 0
    state["awaiting_intro_users"] = sorted(awaiting_intro_users)
    state["intro_name_tries"] = intro_name_tries
    save_state(state)


async def main():
    global channel_entity, self_user_id

    print("Starting intro bot...", flush=True)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Session is not authorized. Regenerate SESSION_STRING.")

    channel_entity = await resolve_channel_entity_safe()

    me = await client.get_me()
    self_user_id = int(getattr(me, "id", 0) or 0)
    print(f"Started as {me.id}", flush=True)
    print(f"Keywords: {', '.join(KEYWORDS)}", flush=True)
    print(f"Channel ref: {CHANNEL_REF}", flush=True)
    if channel_entity is not None:
        print(f"Resolved channel entity id: {getattr(channel_entity, 'id', 'unknown')}", flush=True)
    print(f"Process once: {PROCESS_ONCE}", flush=True)
    print(f"Daiv chat id (raw): {DAIVINCHIK_CHAT_ID_RAW}", flush=True)
    print(f"Daiv chat id (parsed): {DAIVINCHIK_CHAT_ID}", flush=True)
    if DAIVINCHIK_CHAT_ID:
        print(f"Next daiv auto run ts: {int(state.get('next_auto_daiv_ts', 0) or 0)}", flush=True)

    client.loop.create_task(daiv_auto_worker())

    await client(UpdateStatusRequest(offline=True))
    await client.run_until_disconnected()


if __name__ == "__main__":
    client.loop.run_until_complete(main())
