"""Admin panel inline keyboards."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_main_keyboard() -> InlineKeyboardMarkup:
    """
    Create main admin menu keyboard.

    Returns:
        Inline keyboard with admin options
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="👥 Пользователи", callback_data="admin_users")
    builder.button(text="💰 Финансы", callback_data="admin_finance")
    builder.button(text="💳 Начислить баланс", callback_data="admin_add_balance")
    builder.button(text="← Назад", callback_data="nav_goto_menu")

    builder.adjust(2, 2, 1)
    return builder.as_markup()


def admin_stats_keyboard() -> InlineKeyboardMarkup:
    """
    Create statistics screen keyboard.

    Returns:
        Inline keyboard with back button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад", callback_data="admin_main")
    return builder.as_markup()


def admin_users_keyboard(
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """
    Create users list keyboard with pagination.

    Args:
        page: Current page number (1-based)
        total_pages: Total number of pages

    Returns:
        Inline keyboard with pagination and back button
    """
    builder = InlineKeyboardBuilder()

    # Pagination buttons
    if total_pages > 1:
        if page > 1:
            builder.button(text="← Пред", callback_data=f"admin_users_page:{page - 1}")
        else:
            builder.button(text=" ", callback_data="admin_ignore")

        builder.button(text=f"Стр. {page}/{total_pages}", callback_data="admin_ignore")

        if page < total_pages:
            builder.button(text="След. →", callback_data=f"admin_users_page:{page + 1}")
        else:
            builder.button(text=" ", callback_data="admin_ignore")

        builder.adjust(3)

    builder.button(text="← Назад", callback_data="admin_main")
    return builder.as_markup()


def admin_finance_keyboard() -> InlineKeyboardMarkup:
    """
    Create finance screen keyboard.

    Returns:
        Inline keyboard with back button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад", callback_data="admin_main")
    return builder.as_markup()
