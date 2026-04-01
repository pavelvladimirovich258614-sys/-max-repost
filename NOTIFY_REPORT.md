# NOTIFY REPORT — Payment Notifications Implementation

## webhook_server.py (WEBHOOK PATH)
**Lines: ~120-165 (notification section)**
- User notification: Added `InlineKeyboardMarkup` with "💰 Баланс" button (`callback_data="menu_balance"`)
- Admin notification: Already present, preserved
- Consolidated logging: Added `logger.info(f"Notifications sent for payment {payment_id}, user {payment.user_id}")`
- All notifications wrapped in try/except
- File also cleaned up: removed triple blank lines between code lines

## payment_checker.py (POLLING PATH)
**Lines: ~81-97 (user notification)**
- User notification: Added `InlineKeyboardMarkup` with "💰 Баланс" button (`callback_data="menu_balance"`)
- Admin notification: Already present (cleaned up unicode escapes in PATCH-1)
- Consolidated logging: Added `logger.info(f"Notifications sent for payment {payment.payment_id}, user {payment.user_id}")`
- All notifications wrapped in try/except

## payment.py handlers (MANUAL CHECK PATH)
**Lines: ~414-433 (after edit_text response)**
- Admin notification: **NEW** — loops through `settings.admin_ids`, sends payment details to each admin
- Consolidated logging: **NEW** — `logger.info(f"Payment {payment_id} confirmed...")`
- User notification: Uses existing `callback.message.edit_text()` with `back_to_balance_keyboard()` (sufficient for manual check)
- All notifications wrapped in try/except
- Import added: `from config.settings import settings` at file top

## Summary
| Path | User Notify | Admin Notify | Logging |
|------|-------------|--------------|---------|
| webhook_server.py | ✅ + inline KB | ✅ | ✅ |
| payment_checker.py | ✅ + inline KB | ✅ | ✅ |
| payment.py handler | ✅ via edit_text | ✅ NEW | ✅ NEW |
