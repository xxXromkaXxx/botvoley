# telegram-intro-bot

Бот для user-акаунта (Telethon):
- у лічці ловить ключові слова (`дайвінчик`, `волейбол`, ...),
- відповідає шаблоном,
- просить представитись,
- після наступного повідомлення користувача пише лог у канал.

## Локальний запуск

```bash
cd "/Users/romka/Documents/New project/telegram-intro-bot"
source ../.venv/bin/activate
pip install -r requirements.txt
set -a; source .env; set +a
python -u bot.py
```

## Деплой на Scalingo

Потрібні файли вже є:
- `Procfile` (`worker: python -u bot.py`)
- `requirements.txt`

### 1) Підготуй git

```bash
cd "/Users/romka/Documents/New project/telegram-intro-bot"
git init
git add .
git commit -m "init telegram intro bot"
```

### 2) Створи app у Scalingo

1. Відкрий [https://dashboard.scalingo.com](https://dashboard.scalingo.com)
2. `Create an app`
3. Runtime: `Python`
4. Після створення скопіюй `Git remote URL` з вкладки Deploy.

### 3) Задеплой код

```bash
cd "/Users/romka/Documents/New project/telegram-intro-bot"
git remote add scalingo <GIT_REMOTE_URL_FROM_SCALINGO>
git push scalingo main
```

Якщо локальна гілка не `main`, використовуй:
```bash
git push scalingo HEAD:main
```

### 4) Додай змінні оточення в Scalingo

У `Environment` додай:
- `API_ID`
- `API_HASH`
- `SESSION_STRING`
- `CHANNEL_ID`
- `KEYWORDS`
- `REPLY_TEXT`
- `PROCESS_ONCE`
- `TEST_USER_ID`
- `STATE_FILE=state.json`

### 5) Увімкни worker

У вкладці `Resources` вистав:
- `worker = 1`

`web` процес не потрібен.

## Важливо

- На Scalingo файлова система не гарантує довгострокове збереження стану після redeploy/restart.
- Якщо `PROCESS_ONCE=1`, і потрібна стабільна пам'ять між перезапусками, краще зберігати стан у БД.
- Якщо раніше світив `API_HASH` або `SESSION_STRING`, перевипусти їх.
