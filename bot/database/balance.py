"""Balance operations for autopost charging."""

from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, AutopostSubscription, BalanceTransaction
from bot.database.repositories.user import UserRepository
from bot.database.repositories.autopost_subscription import AutopostSubscriptionRepository


# Cost per autopost in rubles (converted to internal units)
AUTOPOST_COST_RUBLES = Decimal("3.00")
# Conversion rate: 1 ruble = 1 post in internal balance
AUTOPOST_COST_POSTS = 3


async def get_balance(session: AsyncSession, user_id: int) -> int:
    """Get user balance by Telegram user ID.
    
    Args:
        session: Database session
        user_id: Telegram user ID
        
    Returns:
        User balance in posts (0 if user not found)
    """
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(user_id)
    if user is None:
        return 0
    return user.balance


async def charge_autopost(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    channel_name: str,
    post_id: int
) -> bool:
    """Charge user for autopost.
    
    Args:
        session: Database session
        user_id: Telegram user ID
        amount: Amount to charge (in rubles, converted to posts)
        channel_name: Telegram channel name for logging
        post_id: Telegram post ID for logging
        
    Returns:
        True if charge successful, False if insufficient funds
    """
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(user_id)
    
    if user is None:
        logger.warning(f"Charge failed: user {user_id} not found")
        return False
    
    # Convert rubles to posts (1 ruble = 1 post)
    cost_posts = int(amount)  # 3 rubles = 3 posts
    
    if user.balance < cost_posts:
        logger.info(
            f"Charge failed: insufficient funds for user {user_id} "
            f"(balance: {user.balance}, required: {cost_posts})"
        )
        return False
    
    # Deduct balance
    result = await user_repo.update_balance(user.id, -cost_posts)
    
    if result is None:
        logger.error(f"Charge failed: could not update balance for user {user_id}")
        return False
    
    logger.info(
        f"Charged {cost_posts} posts from user {user_id} "
        f"for autopost from @{channel_name} post #{post_id}. "
        f"New balance: {result.balance}"
    )
    
    return True


async def charge_autopost_with_subscription(
    session: AsyncSession,
    user_id: int,
    tg_channel: str,
    post_id: int
) -> tuple[bool, str | None]:
    """Charge user for autopost and update subscription stats.
    
    Args:
        session: Database session
        user_id: Telegram user ID
        tg_channel: Telegram channel username
        post_id: Telegram post ID
        
    Returns:
        Tuple of (success: bool, error_reason: str | None)
        Error reason can be "insufficient_funds" or None
    """
    user_repo = UserRepository(session)
    sub_repo = AutopostSubscriptionRepository(session)
    
    user = await user_repo.get_by_telegram_id(user_id)
    if user is None:
        logger.warning(f"Charge failed: user {user_id} not found")
        return False, "user_not_found"
    
    # Get or create subscription
    subscription = await sub_repo.get_by_channel(user_id, tg_channel)
    if subscription is None:
        # Create new subscription
        subscription = await sub_repo.create(
            user_id=user_id,
            tg_channel=tg_channel.lstrip('@'),
            max_chat_id=0,  # Will be updated later
            is_active=True,
            cost_per_post=AUTOPOST_COST_RUBLES,
        )
        logger.info(f"Created autopost subscription for user {user_id} channel {tg_channel}")
    
    # Check and deduct balance
    if user.balance < AUTOPOST_COST_POSTS:
        logger.info(
            f"Charge failed: insufficient funds for user {user_id} "
            f"(balance: {user.balance}, required: {AUTOPOST_COST_POSTS})"
        )
        return False, "insufficient_funds"
    
    # Deduct balance
    result = await user_repo.update_balance(user.id, -AUTOPOST_COST_POSTS)
    if result is None:
        logger.error(f"Charge failed: could not update balance for user {user_id}")
        return False, "update_failed"
    
    # Create transaction record
    transaction = BalanceTransaction(
        user_id=user_id,
        amount=-AUTOPOST_COST_RUBLES,
        transaction_type="autopost_charge",
        description=f"Autopost charge for @{tg_channel} post #{post_id}",
    )
    session.add(transaction)
    
    # Update subscription stats
    await sub_repo.increment_posts_count(subscription.id, AUTOPOST_COST_RUBLES)
    await sub_repo.update_last_post_id(subscription.id, post_id)
    
    logger.info(
        f"Charged {AUTOPOST_COST_POSTS} posts from user {user_id} "
        f"for autopost from @{tg_channel} post #{post_id}. "
        f"New balance: {result.balance}"
    )
    
    return True, None


async def get_autopost_stats(
    session: AsyncSession,
    user_id: int,
    tg_channel: str
) -> dict | None:
    """Get autopost statistics for user and channel.
    
    Args:
        session: Database session
        user_id: Telegram user ID
        tg_channel: Telegram channel username
        
    Returns:
        Dict with stats or None if no subscription
    """
    sub_repo = AutopostSubscriptionRepository(session)
    subscription = await sub_repo.get_by_channel(user_id, tg_channel)
    
    if subscription is None:
        return None
    
    return {
        "is_active": subscription.is_active,
        "posts_transferred": subscription.posts_transferred,
        "total_spent": float(subscription.total_spent),
        "paused_reason": subscription.paused_reason,
        "last_post_id": subscription.last_post_id,
        "created_at": subscription.created_at,
    }
