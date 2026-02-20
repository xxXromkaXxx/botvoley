# telegram-intro-bot (GitHub Actions)

Бот працює через GitHub Actions (кожні 5 хв), без Scalingo.

## Що робить

1. Якщо в лічку пишуть повідомлення з `KEYWORDS` (наприклад `дайвінчик`, `волейбол`) — бот відповідає:
   `REPLY_TEXT`.
2. Чекає наступне повідомлення цього користувача (представлення).
3. Пише в канал:
   - `До нас приєднався новий користувач: @username` (якщо є)
   - `Його повідомлення: ...`
4. Намагається додати користувача в канал.

## Файли

- `gh_poller.py` — опитування діалогів
- `.github/workflows/intro-poller.yml` — запуск кожні 5 хв
- `gh_state.json` — збережений стан (оновлюється автокомітом)

## Налаштування GitHub

У репозиторії GitHub -> `Settings` -> `Secrets and variables` -> `Actions` додай:

- `API_ID`
- `API_HASH`
- `SESSION_STRING`
- `CHANNEL_ID`
- `KEYWORDS` (наприклад `дайвінчик,волейбол`)
- `REPLY_TEXT`
- `PROCESS_ONCE` (`1` або `0`)
- `TEST_USER_ID` (для безлімітних тестів)

## Запуск

1. Запуш цей проєкт у GitHub.
2. У вкладці `Actions` відкрий `Intro Bot Poller`.
3. Натисни `Run workflow`.
4. Далі workflow запускається автоматично кожні 5 хв.

## Важливо

- Це не realtime: затримка до ~5 хв.
- Якщо `API_HASH`/`SESSION_STRING` були десь публічно показані — перевипусти їх.
