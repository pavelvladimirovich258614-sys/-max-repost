"""YooKassa webhook server for receiving payment notifications."""

import asyncio
from aiohttp import web
from loguru import logger
from decimal import Decimal

from bot.database.connection import get_session
from bot.database.repositories.yookassa_payment import YooKassaPaymentRepository
from bot.database.repositories.balance import UserBalanceRepository, BalanceTransactionRepository
from bot.payments.yookassa_client import YooKassaClient


async def handle_webhook(request: web.Request) -> web.Response:
    """Handle incoming YooKassa webhook."""
    try:
        data = await request.json()
        logger.debug(f"Webhook received: {data}")
        
        event = data.get("event", "")
        payment_obj = data.get("object", {})
        payment_id = payment_obj.get("id", "")
        
        if not payment_id:
            logger.warning("Webhook: missing payment_id")
            return web.Response(status=200)  # Still return 200
        
        # Process the webhook
        await process_webhook_payment(payment_id, event)
        
        return web.Response(status=200)
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=200)  # Return 200 to prevent retries


async def process_webhook_payment(payment_id: str, event: str) -> None:
    """Process payment from webhook."""
    async with get_session() as session:
        payment_repo = YooKassaPaymentRepository(session)
        balance_repo = UserBalanceRepository(session)
        transaction_repo = BalanceTransactionRepository(session)
        yookassa_client = YooKassaClient()
        
        # Verify with YooKassa API (security)
        status = yookassa_client.check_payment(payment_id)
        
        # Get payment from DB
        payment = await payment_repo.get_by_payment_id(payment_id)
        if not payment:
            logger.warning(f"Webhook: payment {payment_id} not found in DB")
            return
        
        if status == "succeeded" and payment.status == "pending":
            # Update payment status
            await payment_repo.update_status(payment_id, "succeeded")
            
            # Add balance
            await balance_repo.get_or_create(payment.user_id)
            await balance_repo.update_balance(
                user_id=payment.user_id,
                amount=payment.amount,
                is_deposit=True,
            )
            
            # Create transaction record
            await transaction_repo.create_transaction(
                user_id=payment.user_id,
                amount=payment.amount,
                transaction_type="deposit",
                description=f"Пополнение через YooKassa webhook (платёж {payment_id})",
            )
            
            logger.info(f"Webhook: payment {payment_id} confirmed for user {payment.user_id}")
            
        elif status == "canceled" and payment.status == "pending":
            await payment_repo.update_status(payment_id, "canceled")
            logger.info(f"Webhook: payment {payment_id} marked as canceled")


async def start_webhook_server(
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """Start the webhook server."""
    app = web.Application()
    app.router.add_post("/webhook/yookassa", handle_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logger.info(f"Webhook server started on http://{host}:{port}/webhook/yookassa")
    
    # Keep running
    while True:
        await asyncio.sleep(3600)
