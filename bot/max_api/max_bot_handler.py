"""Max Bot handler for responding to user messages in Max messenger."""

import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

MAX_BOT_INSTRUCTION = """✅ Вы успешно начали диалог с ботом!

Осталось совсем немного:

1. Откройте <b>Настройки канала</b> ➡️ <b>Подписчики</b>.
2. Добавьте подписчика «MAX Постер» (@id5504200417_bot).

3. Перейдите в <b>Настройки канала</b> ➡️ <b>Администраторы</b>.
4. Добавьте администратора «MAX Постер» (@id5504200417_bot).
5. Включите функцию «<b>Писать посты</b>» и нажмите «Сохранить».

👉 Теперь <b>вернитесь в ТГ бота</b> и пришлите ему <b>ссылку на канал в MAX</b>

<i>Если Max не находит бота по нику — попробуйте найти по названию.</i>

⚠️ <b>Сюда присылать ничего не нужно</b> ⚠️"""


class MaxBotListener:
    """Слушает сообщения в Max-боте и отвечает инструкцией"""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://platform-api.max.ru"
        self.headers = {"Authorization": access_token}
        self._running = False
        self._marker = None
    
    async def start(self):
        """Запустить прослушивание сообщений"""
        self._running = True
        logger.info("Max bot listener started")
        
        while self._running:
            try:
                await self._poll_updates()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Max bot listener error: {e}")
                await asyncio.sleep(5)
    
    async def stop(self):
        """Остановить прослушивание"""
        self._running = False
        logger.info("Max bot listener stopped")
    
    async def _poll_updates(self):
        """Получить и обработать обновления"""
        params = {
            "timeout": 30,
            "limit": 100,
            "types": "message_created,bot_started"
        }
        if self._marker:
            params["marker"] = self._marker
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/updates",
                headers=self.headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=35)
            ) as resp:
                data = await resp.json()
                self._marker = data.get("marker")
                updates = data.get("updates", [])
                
                for update in updates:
                    await self._handle_update(update, session)
    
    async def _handle_update(self, update: dict, session: aiohttp.ClientSession):
        """Обработать одно обновление"""
        update_type = update.get("update_type")
        
        if update_type == "bot_started":
            # Пользователь нажал "Старт"
            user_id = update.get("user", {}).get("user_id")
            chat_id = update.get("chat_id")
            if chat_id:
                await self._send_instruction(session, chat_id)
                logger.info(f"Max bot: sent instruction to chat {chat_id} (bot_started)")
            elif user_id:
                await self._send_instruction_to_user(session, user_id)
                
        elif update_type == "message_created":
            message = update.get("message", {})
            recipient = message.get("recipient", {})
            chat_type = recipient.get("chat_type")
            
            # Отвечаем только на личные сообщения (dialog), не на каналы
            if chat_type == "dialog":
                chat_id = recipient.get("chat_id")
                if chat_id:
                    await self._send_instruction(session, chat_id)
                    sender = message.get("sender", {})
                    logger.info(f"Max bot: sent instruction to {sender.get('name', 'unknown')} in chat {chat_id}")
    
    async def _send_instruction(self, session: aiohttp.ClientSession, chat_id: int):
        """Отправить инструкцию в чат"""
        payload = {
            "text": MAX_BOT_INSTRUCTION,
            "format": "html"
        }
        
        async with session.post(
            f"{self.base_url}/messages",
            headers=self.headers,
            params={"chat_id": chat_id},
            json=payload
        ) as resp:
            if resp.status != 200:
                error = await resp.text()
                logger.error(f"Max bot: failed to send instruction: {resp.status} {error}")
    
    async def _send_instruction_to_user(self, session: aiohttp.ClientSession, user_id: int):
        """Отправить инструкцию пользователю по user_id"""
        payload = {
            "text": MAX_BOT_INSTRUCTION,
            "format": "html"
        }
        
        async with session.post(
            f"{self.base_url}/messages",
            headers=self.headers,
            params={"user_id": user_id},
            json=payload
        ) as resp:
            if resp.status != 200:
                error = await resp.text()
                logger.error(f"Max bot: failed to send instruction to user {user_id}: {resp.status} {error}")
