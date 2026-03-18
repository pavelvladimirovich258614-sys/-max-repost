"""Keyboards for payment flow."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def payment_amount_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting payment amount."""
    builder = InlineKeyboardBuilder()
    
    builder.button(text="💰 100₽", callback_data="pay_amount:100")
    builder.button(text="💰 300₽", callback_data="pay_amount:300")
    builder.button(text="💰 500₽", callback_data="pay_amount:500")
    builder.button(text="💰 1000₽", callback_data="pay_amount:1000")
    builder.button(text="✏️ Другая сумма", callback_data="pay_custom")
    builder.button(text="← Назад", callback_data="menu_balance")
    
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def payment_confirmation_keyboard(payment_id: str, confirmation_url: str) -> InlineKeyboardMarkup:
    """Keyboard with payment link and check button."""
    builder = InlineKeyboardBuilder()
    
    builder.button(text="💳 Оплатить", url=confirmation_url)
    builder.button(text="✅ Проверить оплату", callback_data=f"pay_check:{payment_id}")
    builder.button(text="❌ Отменить", callback_data=f"pay_cancel:{payment_id}")
    
    builder.adjust(1)
    return builder.as_markup()


def payment_history_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for payment history screen."""
    builder = InlineKeyboardBuilder()
    
    builder.button(text="💰 Пополнить ещё", callback_data="balance_deposit")
    builder.button(text="🏠 Меню", callback_data="nav_goto_menu")
    
    builder.adjust(1)
    return builder.as_markup()


def back_to_balance_keyboard() -> InlineKeyboardMarkup:
    """Keyboard with back to balance button."""
    builder = InlineKeyboardBuilder()
    
    builder.button(text="💰 Баланс", callback_data="menu_balance")
    builder.button(text="🏠 Меню", callback_data="nav_goto_menu")
    
    builder.adjust(1)
    return builder.as_markup()
