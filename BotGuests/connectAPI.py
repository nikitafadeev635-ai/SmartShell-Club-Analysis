"""Модуль подключений: Telegram + MySQL."""
import logging
import aiomysql
from aiogram import Bot
from config import (
    BOT_TOKEN, DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_TABLE
)

logger = logging.getLogger(__name__)

bot: Bot = None
db_pool = None


def init_telegram_bot() -> Bot:
    """Инициализирует Telegram бота."""
    global bot
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не задан в config.py")
    bot = Bot(token=BOT_TOKEN)
    logger.info("✅ Telegram Bot инициализирован")
    return bot


async def init_database():
    """Создаёт пул соединений с MySQL."""
    global db_pool

    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        logger.error("❌ Не все переменные БД заданы в .env!")
        return None

    try:
        db_pool = await aiomysql.create_pool(
            host=DB_HOST, port=DB_PORT,
            user=DB_USER, password=DB_PASSWORD, db=DB_NAME,
            charset='utf8mb4', autocommit=True,
            minsize=2, maxsize=10, pool_recycle=3600
        )
        logger.info(f"✅ БД подключена: {DB_USER}@{DB_HOST}/{DB_NAME}")

        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"SHOW TABLES LIKE '{DB_TABLE}'")
                if not await cur.fetchone():
                    logger.warning(f"⚠️ Таблица {DB_TABLE} не найдена!")
                else:
                    await cur.execute(f"SELECT COUNT(*) FROM {DB_TABLE}")
                    count = (await cur.fetchone())[0]
                    logger.info(f"📊 Записей в {DB_TABLE}: {count}")

        return db_pool
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к БД: {e}")
        db_pool = None
        return None


async def init_all_connections():
    logger.info("=" * 50)
    logger.info("🔌 ИНИЦИАЛИЗАЦИЯ ПОДКЛЮЧЕНИЙ")
    logger.info("=" * 50)
    init_telegram_bot()
    await init_database()
    logger.info(f"🔌 ИТОГ: bot={'✅' if bot else '❌'} | db={'✅' if db_pool else '❌'}")
    logger.info("=" * 50)


async def close_all_connections():
    global bot, db_pool
    if bot:
        await bot.session.close()
    if db_pool:
        db_pool.close()
        await db_pool.wait_closed()
    logger.info("🔌 Все подключения закрыты")