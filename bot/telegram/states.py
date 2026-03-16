"""FSM states for all bot flows using aiogram FSM."""

from aiogram.filters.state import State, StatesGroup


class AutopostStates(StatesGroup):
    """States for auto-posting setup flow."""

    waiting_tg_channel = State()  # Waiting for Telegram channel link
    waiting_tg_admin_check = State()  # Waiting for admin check confirmation
    waiting_max_channel = State()  # Waiting for Max channel link


class TransferStates(StatesGroup):
    """States for post transfer setup flow."""

    transfer_waiting_tg_channel = State()  # Waiting for TG channel link
    transfer_waiting_verification = State()  # Waiting for channel verification
    transfer_waiting_max_channel = State()  # Waiting for Max channel link
    transfer_enter_max_chat_id = State()  # Manual entry of Max chat_id (when /chats empty)
    transfer_select_count = State()  # Waiting for post count selection


class ChannelStates(StatesGroup):
    """States for channel management flow."""

    viewing_channel = State()  # Viewing channel details
    confirm_delete = State()  # Confirming channel deletion
    custom_post_count = State()  # Entering custom post count for transfer
