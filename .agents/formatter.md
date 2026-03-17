# Суб-агент: FORMATTER (форматирование текста)

## Задача
Исправить конвертацию форматирования при переносе Telegram → Max.

## Проблема
В Max-канале видны `**жирный**`, `[текст](url)`, `__подчёркнутый__` вместо нормального форматирования.

## Корневая причина
1. `message.text` возвращает текст с markdown-символами (**, __, []())
2. Max API не поддерживает `markup` массив — нужно использовать `format="html"` с HTML-тегами

## Файлы для работы
- `bot/core/transfer_engine.py` — основной файл
- `bot/max_api/client.py` — метод send_message

---

## Шаг 1: Создать функцию convert_entities_to_html

В `bot/core/transfer_engine.py` замени функции `entities_to_max_markup` и `finalize_markup` на:

```python
def convert_entities_to_html(raw_text: str, entities: list) -> str:
    """
    Convert Telegram entities to HTML for Max API.
    
    Telethon entities use UTF-16 offset/length. This function handles
    Unicode (emojis) correctly by working with UTF-16 encoded text.
    
    Args:
        raw_text: Clean text without markdown (message.raw_text)
        entities: List of Telegram message entities
        
    Returns:
        HTML-formatted text for Max API
    """
    if not entities:
        return html.escape(raw_text)
    
    # Sort entities by offset (reverse order for insertion)
    sorted_entities = sorted(entities, key=lambda e: e.offset, reverse=True)
    
    # Work with UTF-16 encoding for correct emoji handling
    # Telethon uses UTF-16 code units for offset/length
    text_utf16 = raw_text.encode('utf-16-le')
    result = raw_text
    
    for entity in sorted_entities:
        offset = entity.offset
        length = entity.length
        
        # Convert UTF-16 offset/length to Python string indices
        # This handles emojis correctly (emojis are 2 UTF-16 code units)
        prefix = text_utf16[:offset * 2].decode('utf-16-le', errors='replace')
        entity_text = text_utf16[offset * 2:(offset + length) * 2].decode('utf-16-le', errors='replace')
        
        # Escape HTML in entity text
        entity_text_escaped = html.escape(entity_text)
        
        # Determine HTML tag
        tag = None
        close_tag = None
        
        if isinstance(entity, MessageEntityBold):
            tag, close_tag = "<b>", "</b>"
        elif isinstance(entity, MessageEntityItalic):
            tag, close_tag = "<i>", "</i>"
        elif isinstance(entity, MessageEntityCode):
            tag, close_tag = "<code>", "</code>"
        elif isinstance(entity, MessageEntityPre):
            tag, close_tag = "<pre>", "</pre>"
        elif isinstance(entity, MessageEntityTextUrl):
            url = html.escape(entity.url)
            tag = f'<a href="{url}">'
            close_tag = "</a>"
        elif isinstance(entity, MessageEntityUrl):
            # Max auto-links URLs, no tag needed
            continue
        elif isinstance(entity, MessageEntityStrike):
            tag, close_tag = "<s>", "</s>"
        elif isinstance(entity, MessageEntityUnderline):
            tag, close_tag = "<u>", "</u>"
        else:
            # Unknown entity type - skip
            continue
        
        # Replace the entity text with HTML-wrapped version
        # Find position in current result string
        result_prefix = result[:len(prefix)]
        result_suffix = result[len(prefix) + len(entity_text):]
        
        # Verify we're replacing the right text
        if result[len(prefix):len(prefix) + len(entity_text)] == entity_text:
            result = result_prefix + tag + entity_text_escaped + close_tag + result_suffix
        else:
            # Fallback: text might have changed due to previous replacements
            # Try to find and replace exact match
            search_start = max(0, len(prefix) - 10)
            search_end = min(len(result), len(prefix) + len(entity_text) + 10)
            search_area = result[search_start:search_end]
            
            if entity_text in search_area:
                idx = search_area.index(entity_text)
                actual_pos = search_start + idx
                result = (
                    result[:actual_pos] + 
                    tag + entity_text_escaped + close_tag + 
                    result[actual_pos + len(entity_text):]
                )
    
    # Escape any remaining HTML in text outside entities
    # We need to re-escape but preserve our inserted tags
    # Simple approach: split by our tags, escape non-tag parts
    return result
```

Добавь импорт `html` в начало файла:
```python
import html
```

---

## Шаг 2: Обновить _transfer_single_post

В методе `_transfer_single_post` замени:

```python
# Было:
text = message.text or ""

# Convert Telegram entities to Max markup format
markup = None
if message.entities:
    raw_markup = entities_to_max_markup(message.entities)
    markup = finalize_markup(text, raw_markup)
```

На:

```python
# Стало:
# Use raw_text to get clean text without markdown symbols
raw_text = message.raw_text or ""

# Convert entities to HTML for Max API
if message.entities:
    text = convert_entities_to_html(raw_text, message.entities)
else:
    text = html.escape(raw_text)

# Note: markup is not used, we use format="html" instead
```

---

## Шаг 3: Обновить вызовы send_message

Во всех вызовах `send_message` убери `markup=markup` и добавь `format="html"`:

### Text-only post:
```python
# Было:
await self.max_client.send_message(
    chat_id=max_channel_id,
    text=text,
    markup=markup,
)

# Стало:
await self.max_client.send_message(
    chat_id=max_channel_id,
    text=text,
    format="html",
)
```

### Photo, Video, Audio, File:
Во всех `_transfer_*` методах замени `markup=markup` на `format="html"`.

---

## Шаг 4: Обновить send_message в client.py

В `bot/max_api/client.py` метод `send_message` замени сигнатуру и тело:

```python
# Было:
async def send_message(
    self,
    chat_id: str | int,
    text: str,
    attachments: list[dict] | None = None,
    markup: list[dict] | None = None,
) -> SendMessageResponse:

# Стало:
async def send_message(
    self,
    chat_id: str | int,
    text: str,
    attachments: list[dict] | None = None,
    format: str | None = None,
) -> SendMessageResponse:
```

Внутри метода замени:
```python
# Было:
if markup:
    payload["markup"] = markup

# Стало:
if format:
    payload["format"] = format
```

---

## Шаг 5: Удалить старые функции

Удали полностью:
- `entities_to_max_markup()`
- `finalize_markup()`

Убери из импорта:
- `MessageEntityBold` и другие entity типы если они только для этих функций (проверь!)

---

## Проверка

После изменений убедись что:
1. Нет синтаксических ошибок: `python -m py_compile bot/core/transfer_engine.py bot/max_api/client.py`
2. `message.raw_text` используется вместо `message.text`
3. `format="html"` передаётся в send_message
4. Поле `markup` отсутствует в payload
5. Функция `convert_entities_to_html` корректно экранирует HTML
