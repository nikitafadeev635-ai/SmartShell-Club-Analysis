"""Модуль работы с MySQL. Асинхронный."""
import logging
import connectAPI
from normalizePhone import normalize_phone
from config import DB_TABLE, SWORD_TABLE

logger = logging.getLogger(__name__)


# ==========================================================
# === ОСНОВНАЯ ТАБЛИЦА (testWork) ===
# ==========================================================

async def check_tg_id_exists(tg_id: int) -> bool:
    if not connectAPI.db_pool:
        return False
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT 1 FROM {DB_TABLE} WHERE tg_id = %s LIMIT 1", (tg_id,)
                )
                return (await cur.fetchone()) is not None
    except Exception as e:
        logger.error(f"❌ Ошибка check_tg_id_exists: {e}")
        return False


async def get_phone_by_tgid(tg_id: int) -> str:
    if not connectAPI.db_pool:
        return ""
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT phone FROM {DB_TABLE} WHERE tg_id = %s", (tg_id,)
                )
                result = await cur.fetchone()
                return result[0] if result else ""
    except Exception as e:
        logger.error(f"❌ Ошибка get_phone_by_tgid: {e}")
        return ""


async def save_phone_tg_link(phone: str, tg_id: int, source: str = None) -> bool:
    """UPSERT для основной таблицы."""
    if not connectAPI.db_pool:
        return False

    normalized = normalize_phone(phone)
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT id FROM {DB_TABLE} WHERE phone = %s OR tg_id = %s",
                    (normalized, tg_id)
                )
                existing = await cur.fetchone()

                await cur.execute(f"""
                    INSERT INTO {DB_TABLE} (phone, tg_id, source)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        tg_id = VALUES(tg_id),
                        source = COALESCE(VALUES(source), source),
                        updated_at = CURRENT_TIMESTAMP
                """, (normalized, tg_id, source))

                if existing:
                    logger.info(f"ℹ️ Обновлена связка: {normalized} <-> {tg_id}")
                    return False
                else:
                    logger.info(f"✅ НОВАЯ связка: {normalized} <-> {tg_id}")
                    return True
    except Exception as e:
        logger.error(f"❌ Ошибка save_phone_tg_link: {e}")
        return False


# ==========================================================
# === 🆕 ТАБЛИЦА ФЕСТИВАЛЯ (swordbase) ===
# ==========================================================

async def check_sword_tg_exists(tg_id: int) -> bool:
    """Проверяет, есть ли пользователь в базе фестиваля."""
    if not connectAPI.db_pool:
        return False
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT 1 FROM {SWORD_TABLE} WHERE tg_id = %s LIMIT 1", (tg_id,)
                )
                return (await cur.fetchone()) is not None
    except Exception as e:
        logger.error(f"❌ Ошибка check_sword_tg_exists: {e}")
        return False


async def save_sword_phone(phone: str, tg_id: int) -> bool:
    """Сохраняет телефон в базу фестиваля."""
    if not connectAPI.db_pool:
        return False

    normalized = normalize_phone(phone)
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    INSERT INTO {SWORD_TABLE} (phone, tg_id, source, secretWord)
                    VALUES (%s, %s, %s, 0)
                    ON DUPLICATE KEY UPDATE
                        tg_id = VALUES(tg_id),
                        updated_at = CURRENT_TIMESTAMP
                """, (normalized, tg_id, 'festival'))
                logger.info(f"⚔️ Фестиваль: сохранён {normalized} <-> {tg_id}")
                return True
    except Exception as e:
        logger.error(f"❌ Ошибка save_sword_phone: {e}")
        return False


async def check_sword_secret_status(tg_id: int) -> bool:
    """Проверяет, ввел ли пользователь секретное слово (1 = да, 0 = нет)."""
    if not connectAPI.db_pool:
        return False
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT secretWord FROM {SWORD_TABLE} WHERE tg_id = %s", (tg_id,)
                )
                result = await cur.fetchone()
                return bool(result[0]) if result else False
    except Exception as e:
        logger.error(f"❌ Ошибка check_sword_secret_status: {e}")
        return False


async def set_sword_secret_success(tg_id: int) -> bool:
    """Устанавливает secretWord = 1 (успешный ввод)."""
    if not connectAPI.db_pool:
        return False
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE {SWORD_TABLE} SET secretWord = 1 WHERE tg_id = %s",
                    (tg_id,)
                )
                logger.info(f"✅ Фестиваль: {tg_id} ввёл секретное слово верно!")
                return True
    except Exception as e:
        logger.error(f"❌ Ошибка set_sword_secret_success: {e}")
        return False