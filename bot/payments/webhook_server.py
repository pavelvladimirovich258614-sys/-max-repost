
"""YooKassa webhook server for receiving payment notifications."""



import asyncio

from aiohttp import web

from loguru import logger

from decimal import Decimal



from bot.database.connection import get_session

from bot.database.repositories.yookassa_payment import YooKassaPaymentRepository

from bot.database.repositories.balance import UserBalanceRepository, BalanceTransactionRepository

from bot.payments.yookassa_client import YooKassaClient



# Global references

_webhook_runner = None

_bot_instance = None





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

            return web.Response(status=200)



        await process_webhook_payment(payment_id, event)



        return web.Response(status=200)



    except Exception as e:

        logger.error(f"Webhook error: {e}")

        return web.Response(status=200)





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



            # Send notifications

            if _bot_instance:

                # Notify user

                try:

                    await _bot_instance.send_message(

                        payment.user_id,

                        f"✅ <b>Оплата прошла успешно!</b>\n\n"

                        f"Сумма: {int(payment.amount)}₽\n"

                        f"Баланс пополнен.\n\n"

                        f"Спасибо за оплату!",

                        parse_mode="HTML",

                    )

                    logger.info(f"Webhook: notified user {payment.user_id}")

                except Exception as e:

                    logger.warning(f"Webhook: could not notify user {payment.user_id}: {e}")



                # Notify admins

                try:

                    from config.settings import settings

                    for admin_id in settings.admin_ids:

                        try:

                            await _bot_instance.send_message(

                                admin_id,

                                f"💰 <b>Новая оплата!</b>\n\n"

                                f"User: <code>{payment.user_id}</code>\n"

                                f"Сумма: {int(payment.amount)}₽\n"

                                f"Платёж: <code>{payment_id}</code>",

                                parse_mode="HTML",

                            )

                        except Exception:

                            pass

                    logger.info(f"Webhook: notified admins about payment {payment_id}")

                except Exception as e:

                    logger.warning(f"Webhook: could not notify admins: {e}")



        elif status == "canceled" and payment.status == "pending":

            await payment_repo.update_status(payment_id, "canceled")

            logger.info(f"Webhook: payment {payment_id} marked as canceled")





async def start_webhook_server(

    host: str = "0.0.0.0",

    port: int = 8080,

    bot=None,

) -> None:

    """Start the webhook server."""

    global _webhook_runner, _bot_instance

    _bot_instance = bot



    app = web.Application()

    app.router.add_post("/webhook/yookassa", handle_webhook)



    _webhook_runner = web.AppRunner(app)

    await _webhook_runner.setup()



    site = web.TCPSite(_webhook_runner, host, port)

    await site.start()



    logger.info(f"Webhook server started on http://{host}:{port}/webhook/yookassa")



    try:

        while True:

            await asyncio.sleep(3600)

    except asyncio.CancelledError:

        logger.info("Webhook server task cancelled")

        raise





async def cleanup_webhook_server() -> None:

    """Cleanup the webhook server runner."""

    global _webhook_runner

    if _webhook_runner is not None:

        try:

            await _webhook_runner.cleanup()

            logger.debug("Webhook server runner cleaned up")

        except Exception as e:

            logger.error(f"Error cleaning up webhook server: {e}")

        finally:

            _webhook_runner = None

