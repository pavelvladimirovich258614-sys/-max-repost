"""
Диагностический скрипт для проверки Telethon events.
Запускается ОТДЕЛЬНО от бота.
Проверяет: подключение, получение сообщений, event handlers.

ВАЖНО: Останови бота перед запуском этого скрипта!
Два процесса не могут использовать одну Telethon сессию одновременно.
"""
import asyncio
import logging
from telethon import TelegramClient, events
from config.settings import settings

logging.basicConfig(
    level=logging.DEBUG, 
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger(__name__)

# Используем те же параметры что и бот
API_ID = settings.telegram_api_id
API_HASH = settings.telegram_api_hash
PHONE = settings.telegram_phone
SESSION = 'user_session'  # тот же файл сессии что у бота
CHANNEL = 'Novopoltsev_Pavel'  # канал для теста


async def main():
    logger.info(f"Creating client: api_id={API_ID}, phone={PHONE}")
    
    client = TelegramClient(SESSION, API_ID, API_HASH)
    
    # Тест 1: Подключение
    logger.info("TEST 1: Connecting...")
    try:
        await client.start(phone=PHONE)
        me = await client.get_me()
        logger.info(f"TEST 1 OK: Connected as {me.first_name} ({me.phone})")
    except Exception as e:
        logger.error(f"TEST 1 FAIL: {e}")
        return
    
    # Тест 2: Получение канала
    logger.info(f"TEST 2: Resolving channel @{CHANNEL}...")
    try:
        entity = await client.get_entity(CHANNEL)
        logger.info(f"TEST 2 OK: Channel found: {entity.title}, id={entity.id}")
    except Exception as e:
        logger.error(f"TEST 2 FAIL: {e}")
        await client.disconnect()
        return
    
    # Тест 3: Чтение последних сообщений
    logger.info("TEST 3: Reading last 3 messages...")
    try:
        count = 0
        async for msg in client.iter_messages(entity, limit=3):
            logger.info(f"  msg id={msg.id}, text={msg.text[:50] if msg.text else 'no text'}, date={msg.date}")
            count += 1
        logger.info(f"TEST 3 OK: Read {count} messages")
    except Exception as e:
        logger.error(f"TEST 3 FAIL: {e}")
    
    # Тест 4: Event handler БЕЗ фильтра
    @client.on(events.NewMessage())
    async def handler_all(event):
        logger.warning(
            f"HANDLER ALL TRIGGERED: chat_id={event.chat_id}, "
            f"msg_id={event.message.id}, "
            f"text={event.message.text[:50] if event.message.text else 'no text'}"
        )
    
    # Тест 5: Event handler С фильтром на канал (через entity.id)
    @client.on(events.NewMessage(chats=[entity.id]))
    async def handler_channel_by_id(event):
        logger.warning(
            f"HANDLER CHANNEL (by ID) TRIGGERED: @{CHANNEL}, msg_id={event.message.id}, "
            f"text={event.message.text[:50] if event.message.text else 'no text'}"
        )
    
    # Тест 6: Event handler С фильтром на канал (через username)
    @client.on(events.NewMessage(chats=[CHANNEL]))
    async def handler_channel_by_username(event):
        logger.warning(
            f"HANDLER CHANNEL (by username) TRIGGERED: @{CHANNEL}, msg_id={event.message.id}, "
            f"text={event.message.text[:50] if event.message.text else 'no text'}"
        )
    
    # Тест 7: Raw handler (все raw updates)
    @client.on(events.Raw())
    async def handler_raw(update):
        logger.warning(f"RAW UPDATE: {type(update).__name__}")
    
    # Получаем список зарегистрированных хендлеров
    handlers = client.list_event_handlers()
    
    logger.info("=" * 60)
    logger.info("ALL HANDLERS REGISTERED")
    logger.info(f"Total handlers: {len(handlers)}")
    for handler, event in handlers:
        logger.info(f"  - {handler.__name__}: {event}")
    logger.info("=" * 60)
    logger.info("WAITING FOR MESSAGES...")
    logger.info(f"Send a message to @{CHANNEL} and watch logs.")
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 60)
    
    try:
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        logger.info("Stopped by user (Ctrl+C)")
    finally:
        await client.disconnect()
        logger.info("Client disconnected")


if __name__ == '__main__':
    asyncio.run(main())
