# PATCH REPORT — Bug Fixes Applied

## PATCH-1: BUG-4 — Unicode escape cleanup in payment_checker.py
**File:** bot/payments/payment_checker.py, lines 100-103
**Was:** Escaped unicode sequences (`\U0001f4b0`, `\u041d\u043e\u0432\u0430\u044f`, etc.)
**Became:** Literal unicode characters (💰, Новая, etc.)
**Reason:** Escaped unicode was valid but inconsistent with rest of codebase. No double-escaping found.

## PATCH-2: ISSUE-A — Missing get_or_create in payment.py
**File:** bot/telegram/handlers/payment.py, line 383
**Was:** Direct `balance_repo.update_balance()` without ensuring balance record exists
**Became:** Added `await balance_repo.get_or_create(payment.user_id)` before `update_balance()`
**Reason:** Inconsistent with webhook_server.py and payment_checker.py which both call get_or_create first. Could fail if UserBalance record doesn't exist.

## Already Fixed (verified, no changes needed):
- **BUG-1**: autopost.py `callback_data="balance_deposit"` — correct
- **BUG-2**: user.py `case()` instead of `func.least()` — correct
- **BUG-3**: webhook_server.py `_bot_instance` pattern — correct
- **BUG-5**: main.py `bot=bot` passed to `start_webhook_server()` — correct
- **BUG-6**: main.py no duplicate tasks — correct
- **BUG-7**: `payment.user_id` is telegram_id — verified OK
