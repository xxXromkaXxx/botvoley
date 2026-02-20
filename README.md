# telegram-intro-bot (Railway)

Telegram-бот для приватних повідомлень:

1. Ловить ключові слова в лічці (`KEYWORDS`).
2. Відповідає текстом (`REPLY_TEXT`).
3. Чекає наступне повідомлення (представлення).
4. Пише його в канал і пробує додати користувача в канал.

## Що залишилось у проєкті

- `bot.py` — основна логіка бота (довгий запуск для worker).
- `requirements.txt` — залежності (тільки `telethon`).
- `Dockerfile` — контейнерний запуск.
- `Procfile` — команда для worker (`python -u bot.py`).
- `gen_session.py` — згенерувати `SESSION_STRING`.
- `get_ids.py` — допоміжний скрипт, щоб дістати ID.

## Railway: кроки запуску

1. Запуш код у GitHub.
2. Railway -> `New Project` -> `Deploy from GitHub repo` -> обери цей репозиторій.
3. Після першого деплою відкрий сервіс -> `Variables` і задай:
   - `API_ID`
   - `API_HASH`
   - `SESSION_STRING`
   - `CHANNEL_ID` (наприклад `-1001234567890`)
   - `KEYWORDS` (наприклад `дайвінчик,волейбол`)
   - `REPLY_TEXT`
   - `PROCESS_ONCE=1`
   - `TEST_USER_ID=0`
4. Дуже бажано зробити persistent volume:
   - `Settings` -> `Volumes` -> `Add Volume`
   - Mount path: `/data`
   - додай змінну `STATE_FILE=/data/state.json`
5. У `Settings` перевір `Start Command`:
   - або залиш автоматично з `Procfile`
   - або явно вкажи `python -u bot.py`
6. Зроби `Redeploy`.
7. Перевір логи: має бути `Starting intro bot...` і `Started as ...`.

## Локальний запуск (опційно)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -u bot.py
```

## Важливо

- Якщо не підключити volume, `state.json` буде скидатися після redeploy/restart.
- Якщо `API_HASH` або `SESSION_STRING` десь світилися публічно, перевипусти їх.
