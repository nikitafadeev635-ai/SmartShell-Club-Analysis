"""Middleware для проверки подписки на канал. ИСПРАВЛЕННАЯ ВЕРСИЯ."""
import time
import logging
from typing import Any, Awaitable, Callable, Dict
from collections import defaultdict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery

import connectAPI
from config import CHANNEL_ID, CHANNEL_USERNAME, SUBSCRIPTION_CACHE_TTL
from functionFormat import (
    get_subscription_fail_message
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

# Память кэша подписки
_subscription_cache: Dict[int, Dict[str, Any]] = defaultdict(dict)

# Callback-данные, которые НЕ требуют проверки подписки
PUBLIC_CALLBACKS = {
    "share_phone", "check_sub", "back_to_start", "main_menu"
}

# Префиксы, требующие защиты
PROTECTED_PREFIXES = (
    "nav:", "nav_", "promo", "pc_build", "price_", "social",
    "smartgamer", "review_", "about", "branch:", "director",
    "booking", "reviews", "festival", "sword", "referals", "ref:"
)


def is_protected(data: str) -> bool:
    if not data:
        return False
    if data in PUBLIC_CALLBACKS:
        return False
    return any(data.startswith(p) or data == p for p in PROTECTED_PREFIXES)


async def _check_subscription_real(user_id: int) -> bool:
    if not connectAPI.bot:
        return False
    try:
        member = await connectAPI.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "creator", "administrator", "restricted"]
    except Exception as e:
        logger.error(f"❌ check_subscription_real: {e}")
        return False


def invalidate_cache(user_id: int):
    """Сбрасывает кэш подписки."""
    _subscription_cache.pop(user_id, None)


async def check_subscription_cached(user_id: int) -> bool:
    """Проверяет подписку с кэшированием."""
    now = time.time()
    cached = _subscription_cache.get(user_id)

    if cached and (now - cached.get("time", 0)) < SUBSCRIPTION_CACHE_TTL:
        return cached.get("is_subscribed", False)

    is_subscribed = await _check_subscription_real(user_id)
    _subscription_cache[user_id] = {
        "is_subscribed": is_subscribed,
        "time": now
    }
    return is_subscribed


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if isinstance(event, CallbackQuery):
            callback_data = event.data
            user_id = event.from_user.id

            if callback_data == "check_sub":
                invalidate_cache(user_id)
                return await handler(event, data)

            if callback_data and is_protected(callback_data):
                is_subscribed = await check_subscription_cached(user_id)

                if not is_subscribed:
                    logger.warning(f"🚫 Блокировка {user_id}: нет подписки (пытался: {callback_data})")

                    text = get_subscription_fail_message()
                    builder = InlineKeyboardBuilder()
                    # 🆕 ИСПРАВЛЕНО: используем CHANNEL_USERNAME из config
                    builder.button(text="🐾 Вступить в отряд", url=f"https://t.me/{CHANNEL_USERNAME}")
                    builder.button(text="🔄 Проверить подписку", callback_data="check_sub")
                    builder.adjust(1)

                    try:
                        await event.message.edit_text(
                            text, reply_markup=builder.as_markup(), parse_mode="HTML"
                        )
                    except Exception:
                        await event.message.answer(
                            text, reply_markup=builder.as_markup(), parse_mode="HTML"
                        )
                    await event.answer("⚠️ Сначала подпишись на канал!", show_alert=True)
                    return None

        return await handler(event, data)

# === CYBERMJ REFERRALS LIST V1 ===
