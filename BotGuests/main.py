"""
Точка входа. Запуск Telegram бота CyberMG (PROD).
Включает: FSM для фестиваля + Middleware проверки подписки + очистку команд.
"""
import asyncio
import logging

import config
import connectAPI
from handler import router
from middleware import SubscriptionMiddleware
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand


def setup_logging():
    """Настройка логирования."""
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format=config.LOG_FORMAT,
        datefmt=config.LOG_DATEFMT
    )


async def main():
    """Главная асинхронная функция."""
    setup_logging()
    logger = logging.getLogger(__name__)

    # Асинхронная инициализация (Telegram + MySQL)
    await connectAPI.init_all_connections()

    if not connectAPI.bot:
        logger.error("❌ Bot не инициализирован! Проверьте BOT_TOKEN в .env")
        return

    if not connectAPI.db_pool:
        logger.error("❌ MySQL не подключен! Проверьте DB_* переменные в .env")
        return

    # 🆕 FSM хранилище (нужно для фестиваля средневековья)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # 🆕 Middleware защиты от неподписанных пользователей
    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(SubscriptionMiddleware())

    # Подключаем хендлеры
    dp.include_router(router)

    # Удаляем вебхук
    await connectAPI.bot.delete_webhook(drop_pending_updates=True)

    # 🆕 Устанавливаем список команд (удаляем /menu)
    await connectAPI.bot.set_my_commands([
        BotCommand(command="start", description="Запустить бота 🚀")
    ])

    # Логирование старта
    logger.info("=" * 50)
    logger.info(f"🚀 CYBERMG Bot PROD запущен!")
    logger.info(f"📢 Канал: @{config.CHANNEL_USERNAME} (ID: {config.CHANNEL_ID})")
    logger.info(f"🗄️ БД: {config.DB_NAME}@{config.DB_HOST}")
    logger.info(f"📋 Таблицы: {config.DB_TABLE}, {config.SWORD_TABLE}")
    logger.info("=" * 50)

    try:
        await dp.start_polling(connectAPI.bot)
    finally:
        await connectAPI.close_all_connections()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Бот остановлен")