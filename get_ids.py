import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


def req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Set {name} in env")
    return v


async def main() -> None:
    api_id = int(req("API_ID"))
    api_hash = req("API_HASH")
    session = req("SESSION_STRING")

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.start()

    me = await client.get_me()
    print("=== MY IDS ===")
    print(f"MY_USER_ID={me.id}")
    print(f"SAVED_MESSAGES_CHAT_ID={me.id}")
    print()

    print("=== DIALOGS (copy IDs you need) ===")
    async for d in client.iter_dialogs():
        kind = "USER"
        if d.is_channel:
            kind = "CHANNEL"
        elif d.is_group:
            kind = "GROUP"
        title = (d.name or "").replace("\n", " ").strip()
        print(f"{kind:8} id={d.id:<14} title={title}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
