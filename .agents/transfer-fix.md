# Суб-агент: TRANSFER-FIX (логика переноса)

## Задача
Исправить пропуски постов и лимит 4000 символов.

## Файлы для работы
- `bot/core/transfer_engine.py`

---

## Проблема 1: Посты пропускаются как "empty message"

### Текущая логика (функция should_skip_message):
```python
if not message.text and not message.media:
    return True, "empty message"
```

### Что нужно исправить
Посты с медиа но без текста НЕ должны пропускаться.

### Решение
В функции `should_skip_message` замени:
```python
# Было:
# Skip empty messages
if not message.text and not message.media:
    return True, "empty message"

# Стало:
# Skip empty messages (no text AND no media)
has_text = bool(message.raw_text and message.raw_text.strip())
has_media = message.media is not None
if not has_text and not has_media:
    return True, "empty message (no text, no media)"
```

Добавь логирование для медиа-постов:
```python
# After the check, log media-only posts
if has_media and not has_text:
    logger.info(f"Post {message.id}: media-only, will transfer with empty text")
```

---

## Проблема 2: Улучшить логирование пропусков

В функции `should_skip_message` замени возвращаемые значения на более информативные:

```python
# Было:
return True, "service message"

# Стало:
return True, f"service message: {type(message.action).__name__}"
```

```python
# Было:
return True, f"unsupported media type: {type(message.media).__name__}"

# Стало:
return True, f"unsupported media: {type(message.media).__name__}"
```

В `transfer_posts` обнови логирование пропусков:
```python
# Было:
logger.info(f"Skipping post {message.id}: reason={skip_reason}, text_preview='{(message.text or '')[:50]}...'")

# Стало:
has_text = bool(message.raw_text and message.raw_text.strip())
has_media = message.media is not None
media_type = type(message.media).__name__ if message.media else "none"
logger.info(
    f"Skipping post {message.id}: "
    f"reason={skip_reason}, "
    f"has_text={has_text}, "
    f"has_media={has_media}, "
    f"media_type={media_type}"
)
```

---

## Проблема 3: Текст > 4000 символов

### Создать функцию split_text

Добавь в начало файла (после импортов):

```python
def split_text(text: str, max_length: int = 4000) -> list[str]:
    """
    Split text into chunks of max_length characters.
    
    Tries to split at newlines first, then at spaces, then hard split.
    
    Args:
        text: Text to split
        max_length: Maximum length of each chunk
        
    Returns:
        List of text chunks
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    remaining = text
    
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break
        
        # Try to find a newline to split at
        split_pos = remaining.rfind('\n', 0, max_length + 1)
        
        # If no newline, try to find a space
        if split_pos == -1:
            split_pos = remaining.rfind(' ', 0, max_length + 1)
        
        # If no space either, hard split at max_length
        if split_pos == -1 or split_pos == 0:
            split_pos = max_length
        
        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip()
    
    return chunks
```

### Обновить _transfer_single_post для разбивки

```python
async def _transfer_single_post(
    self,
    message: Message,
    max_channel_id: str | int,
) -> None:
    """
    Transfer a single post (not part of an album).
    """
    media_type = detect_media_type(message)
    raw_text = message.raw_text or ""
    
    # Convert entities to HTML for Max API
    if message.entities:
        text = convert_entities_to_html(raw_text, message.entities)
    else:
        text = html.escape(raw_text)
    
    # Check if text needs splitting
    text_chunks = split_text(text, max_length=4000)
    
    # Handle posts based on media type
    match media_type:
        case MediaType.TEXT:
            # Text-only post - send all chunks
            for i, chunk in enumerate(text_chunks):
                await self.max_client.send_message(
                    chat_id=max_channel_id,
                    text=chunk,
                    format="html",
                )
                if i < len(text_chunks) - 1:
                    await asyncio.sleep(1)  # Rate limit between chunks
                    
        case MediaType.PHOTO:
            await self._transfer_photo(message, max_channel_id, text_chunks)
        case MediaType.VIDEO:
            await self._transfer_video(message, max_channel_id, text_chunks)
        case MediaType.AUDIO:
            await self._transfer_audio(message, max_channel_id, text_chunks)
        case MediaType.FILE:
            await self._transfer_file(message, max_channel_id, text_chunks)
        case MediaType.UNSUPPORTED:
            # Unsupported - send as text only
            for i, chunk in enumerate(text_chunks):
                await self.max_client.send_message(
                    chat_id=max_channel_id,
                    text=chunk,
                    format="html",
                )
                if i < len(text_chunks) - 1:
                    await asyncio.sleep(1)
        case _:
            # Fallback - send as text only
            for i, chunk in enumerate(text_chunks):
                await self.max_client.send_message(
                    chat_id=max_channel_id,
                    text=chunk,
                    format="html",
                )
                if i < len(text_chunks) - 1:
                    await asyncio.sleep(1)
```

### Обновить _transfer_photo и другие медиа-методы

Измени сигнатуру методов:
```python
# Было:
async def _transfer_photo(
    self,
    message: Message,
    max_channel_id: str | int,
    text: str,
    markup: list[dict] | None = None,
) -> None:

# Стало:
async def _transfer_photo(
    self,
    message: Message,
    max_channel_id: str | int,
    text_chunks: list[str],
) -> None:
```

И внутри метода:
```python
async def _transfer_photo(
    self,
    message: Message,
    max_channel_id: str | int,
    text_chunks: list[str],
) -> None:
    """Transfer a photo post."""
    # Download photo to BytesIO
    buf = io.BytesIO()
    await message.download_media(file=buf)
    buf.seek(0)
    photo_bytes = buf.read()

    if not photo_bytes or len(photo_bytes) == 0:
        logger.warning(f"Empty photo bytes for message {message.id}, skipping")
        raise MaxAPIError("Failed to download photo: empty bytes")

    logger.info(f"Downloaded photo: {len(photo_bytes)} bytes")

    # Upload to Max
    token = await self.max_client.upload_image(photo_bytes)

    # Send first chunk with photo, remaining chunks as separate messages
    first_chunk = text_chunks[0] if text_chunks else ""
    attachment = {"type": "image", "payload": {"token": token}}
    
    await self.max_client.send_message(
        chat_id=max_channel_id,
        text=first_chunk,
        attachments=[attachment],
        format="html",
    )
    
    # Send remaining text chunks
    for chunk in text_chunks[1:]:
        await asyncio.sleep(1)
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=chunk,
            format="html",
        )
```

Аналогично обнови `_transfer_video`, `_transfer_audio`, `_transfer_file`.

---

## Проверка

После изменений:
1. `python -m py_compile bot/core/transfer_engine.py` — нет ошибок
2. Посты с медиа без текста НЕ пропускаются
3. Длинные посты (>4000) разбиваются на части
4. Логирование информативное
