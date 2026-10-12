"""
Точка входа. Запуск Telegram бота CyberMG.
"""
import asyncio
import logging

import config
import connectAPI
from handler import router
from aiogram import Dispatcher


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
    
    # 🆕 Асинхронная инициализация (Telegram + MySQL)
    await connectAPI.init_all_connections()
    
    if not connectAPI.bot:
        logger.error("❌ Bot не инициализирован! Проверьте BOT_TOKEN в .env")
        return
    
    if not connectAPI.db_pool:
        logger.error("❌ MySQL не подключен! Проверьте DB_* переменные в .env")
        return
    
    dp = Dispatcher()
    dp.include_router(router)
    
    await connectAPI.bot.delete_webhook(drop_pending_updates=True)
    logger.info(f"🚀 Космокот-бот запущен | Канал: @{config.CHANNEL_USERNAME}")
    logger.info(f"🗄️ БД: {config.DB_NAME}@{config.DB_HOST}")
    
    try:
        await dp.start_polling(connectAPI.bot)
    finally:
        await connectAPI.close_all_connections()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Бот остановлен")