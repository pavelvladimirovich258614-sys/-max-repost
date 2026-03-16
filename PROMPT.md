Скажи Claude Code:

```
Создай файл PROMPT.md в корне проекта max-repost с содержимым ниже. Не меняй текст — скопируй как есть.

Закоммить и запушь.

---

# Полный контекст проекта max-repost

## О проекте

**max-repost** — Telegram-бот, который переносит посты из Telegram-каналов в мессенджер Max (бывший VK Teams / ICQ New). Пользователь добавляет свой TG-канал, добавляет Max-канал, выбирает количество постов — бот переносит их 1:1 (текст + фото + медиа).

Бизнес-модель: платный перенос по формуле 3 руб./пост.

## Стек

- **Python 3.12**, Windows (разработка)
- **aiogram 3.25.0** — Telegram Bot API, FSM, меню, callback-кнопки
- **Telethon 1.34.0** — чтение истории TG-канала через user-сессию (Bot API не даёт доступ к истории)
- **aiohttp** — HTTP-запросы к Max Platform API
- **SQLite** (aiosqlite) — база данных
- **FSM storage: MemoryStorage** (aiogram)

## Боты и аккаунты

### Telegram
- **Бот:** @maxx_repost_bot (ID: 8606450063)
- **Тестовый TG-канал:** @Novopoltsev_Pavel (606 постов)
- **Telethon user-сессия:** авторизована через телефон, файл user_session.session, работает — история канала читается, подсчёт постов корректный

### Max (мессенджер)
- **Бот создан** на https://business.max.ru/self
- **Access token** есть, авторизация работает (API отвечает 200)
- **Max-канал подключён**, chat_id известен: KzH6f71jyBYu4qYh2xCavXPHhlSatLqABA1dddhLAGM

## Что уже сделано и работает

1. Каркас проекта, БД, конфиги
2. Telegram-бот с меню, inline-кнопками, FSM-потоками (автопостинг, перенос, каналы)
3. Тексты инструкций для пользователей
4. Telethon user-сессия — авторизация, чтение истории TG-каналов
5. Реальный подсчёт постов через client.get_messages(channel, limit=0).total — работает (606 постов)
6. TransferEngine — движок переноса с прогресс-баром, обработкой ошибок, rate limiting
7. FSM flow: пользователь выбирает TG-канал → Max-канал → количество → запуск переноса
8. MemoryStorage для FSM (был баг с Redis — починили)
9. Max API авторизация работает — endpoint'ы отвечают, бот виден

## Что НЕ работает — текущая проблема

При попытке перенести 3 тестовых поста результат:

Всего обработано: 4
Успешно: 0
Ошибок: 2
Пропущено: 2

Ошибки:
- Пост 5: Upload response did not contain token. Response keys: ['error_code', 'error_data']
- Пост 7: Client error 400

### Ошибка 1: Upload изображения — NO_IMAGE

При загрузке фото из TG-поста в Max API:

**Шаг 1** — получаем upload URL (работает):
```
POST https://platform-api.max.ru/uploads?type=image
Header: Authorization: {access_token}
→ Ответ: {"url": "https://..."}
```

**Шаг 2** — загружаем файл по URL (ломается):
```
POST {url}
Content-Type: multipart/form-data
Поле формы: "data" = file_bytes
```

Ответ:
```json
{"error_code": "505", "error_data": "NO_IMAGE"}
```

HTTP-статус 200, но сервер не распознал изображение.

**Текущий код загрузки (aiohttp):**
```python
form = aiohttp.FormData()
form.add_field(
    'data',
    file_bytes,
    filename='image.jpg',
    content_type='image/jpeg'
)
async with session.post(upload_url, data=form) as response:
    result = await response.json()
```

**Скачивание медиа из Telethon:**
```python
file_bytes = await message.download_media(file=bytes)
```

**Что уже пробовали:**
- PUT → POST (исправили, 405 ушла)
- Добавили filename и content_type в FormData
- Поле формы "data" — как в официальном curl -F "data=@example.jpg"
- Убрали Bearer из Authorization (Max API принимает токен напрямую)
- Починили переменную data → form (была NameError)

**Гипотезы которые ещё не проверены:**
- message.download_media(file=bytes) может возвращать путь к файлу, а не байты. Нужно использовать BytesIO
- Возможно в коде остался ручной заголовок Content-Type при upload — это ломает boundary в multipart
- Возможно file_bytes пустой — нет проверки и логирования размера

### Ошибка 2: Отправка текста — Client error 400

При отправке текстового поста (без медиа):
```
POST https://platform-api.max.ru/messages?chat_id={chat_id}
Header: Authorization: {access_token}
Body: {"text": "текст поста"}
```

Ответ: HTTP 400 Bad Request.

**Возможные причины:**
- Body отправляется через data=json.dumps(body) вместо json=body — без автоматического Content-Type: application/json
- chat_id передаётся неправильно (строка вместо int, или в body вместо query params)
- Нет заголовка Content-Type: application/json

## Документация Max API

### Общее
- **Базовый URL:** https://platform-api.max.ru
- **Авторизация:** заголовок Authorization: {access_token} (без Bearer, без query params)
- **Rate limit:** 30 rps
- **Формат тела:** JSON, заголовок Content-Type: application/json

### POST /uploads — загрузка файлов
**Документация:** https://dev.max.ru/docs-api/methods/POST/uploads

Параметры:
- type (query): image | video | audio | file
- type=photo устарел — использовать type=image

Ответ:
- url (string) — URL для загрузки файла
- token (string, optional) — только для video/audio

Загрузка файла по URL:
```bash
curl -X POST "%UPLOAD_URL%" \
  -H "Content-Type: multipart/form-data" \
  -F "data=@example.jpg"
```

Для image/file — token приходит в ответе на загрузку.
Для video/audio — token приходит на шаге 1 (POST /uploads).

После загрузки нужна пауза — файл обрабатывается. Ошибка attachment.not.ready = повторить через 2 сек.

### POST /messages — отправка сообщений
**Документация:** https://dev.max.ru/docs-api/methods/POST/messages

Query параметры:
- user_id (int64, optional) — для личных сообщений
- chat_id (int64, optional) — для чатов/каналов
- disable_link_preview (bool, optional)

Body (JSON):
```json
{
  "text": "до 4000 символов",
  "attachments": [
    {
      "type": "image",
      "payload": {"token": "..."}
    }
  ],
  "format": "markdown",
  "notify": true
}
```

Пример curl:
```bash
curl -X POST "https://platform-api.max.ru/messages?chat_id={chat_id}" \
  -H "Authorization: {access_token}" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello"}'
```

### GET /chats — список чатов бота
**Документация:** https://dev.max.ru/docs-api/methods/GET/chats

### Полная документация API
https://dev.max.ru/docs-api

### Создание ботов / получение токена
https://business.max.ru/self → Чат-боты → Интеграция → Получить токен

## Документация Telegram / Telethon
- **Telegram Bot API:** https://core.telegram.org/bots/api
- **Telethon docs:** https://docs.telethon.dev/
- **aiogram 3 docs:** https://docs.aiogram.dev/en/latest/

## Структура проекта (ключевые файлы)

```
max-repost/
├── bot/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── telethon_client.py
│   │   ├── transfer_engine.py
│   │   └── rate_limiter.py
│   ├── max_api/
│   │   └── client.py
│   └── telegram/
│       ├── bot.py
│       └── handlers/
│           └── transfer.py
├── config/
│   └── settings.py
├── .env
├── .env.example
├── requirements.txt
└── user_session.session
```

## Что нужно

1. Починить upload изображений в Max API — чтобы NO_IMAGE ушла и token возвращался
2. Починить отправку текстовых сообщений — чтобы 400 ушла
3. Добиться успешного переноса хотя бы 3 постов (текст + фото) из TG в Max
4. Рабочий пример на Python/aiohttp: полный цикл upload image + send message с attachment в Max API
```
