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


# ==========================================================
# === 🆕 РЕФЕРАЛЬНАЯ СИСТЕМА (Referals) ===
# ==========================================================

async def save_referal(tg_id: int, tg_referal: int, value: float) -> bool:
    """
    Сохраняет реферальную связку в таблицу Referals.
    tg_id - тот кто пригласил (наставник)
    tg_referal - тг ID реферала (ученик)
    value - сумма пополнения для активации бонуса
    """
    if not connectAPI.db_pool:
        return False
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO Referals (tg_id, tg_referal, used, value)
                    VALUES (%s, %s, 0, %s)
                    ON DUPLICATE KEY UPDATE 
                        tg_id = VALUES(tg_id),
                        value = VALUES(value)
                """, (tg_id, tg_referal, value))
                logger.info(f"🤝 Реферал сохранён: наставник {tg_id} -> ученик {tg_referal}")
                return True
    except Exception as e:
        logger.error(f"❌ Ошибка save_referal: {e}")
        return False


async def update_state_sub(tg_id: int, is_subscribed: bool) -> bool:
    """
    Обновляет поле stateSub в таблице phone_tg_links.
    Показывает, подписан ли пользователь на ТГ канал.
    """
    if not connectAPI.db_pool:
        return False
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE {DB_TABLE} SET stateSub = %s WHERE tg_id = %s",
                    (1 if is_subscribed else 0, tg_id)
                )
                logger.info(f"📢 Обновлён stateSub для {tg_id}: {is_subscribed}")
                return True
    except Exception as e:
        logger.error(f"❌ Ошибка update_state_sub: {e}")
        return False


async def get_referals_count(tg_id: int) -> int:
    """
    Возвращает количество рефералов у наставника.
    """
    if not connectAPI.db_pool:
        return 0
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM Referals WHERE tg_id = %s", (tg_id,)
                )
                result = await cur.fetchone()
                count = result[0] if result else 0
                logger.info(f"👥 Наставник {tg_id} имеет {count} рефералов")
                return count
    except Exception as e:
        logger.error(f"❌ Ошибка get_referals_count: {e}")
        return 0


async def check_referal_exists(tg_referal: int) -> bool:
    """
    Проверяет, есть ли уже запись о реферале в таблице Referals.
    """
    if not connectAPI.db_pool:
        return False
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM Referals WHERE tg_referal = %s LIMIT 1", (tg_referal,)
                )
                return (await cur.fetchone()) is not None
    except Exception as e:
        logger.error(f"❌ Ошибка check_referal_exists: {e}")
        return False


# ==========================================================
# === 🤝 РЕФЕРАЛЬНАЯ СИСТЕМА (по ТЗ) ===
# ==========================================================

async def save_referal(tg_id: int, tg_referal: int) -> bool:
    """Создаёт запись в Referals (val=0, used=0) для новичка."""
    if not connectAPI.db_pool:
        return False
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO Referals (tg_id, tg_referal, used, value)
                    VALUES (%s, %s, 0, 0)
                    ON DUPLICATE KEY UPDATE tg_id = VALUES(tg_id)
                """, (tg_id, tg_referal))
                logger.info(f"🤝 Запись Referals создана: наставник {tg_id} -> ученик {tg_referal}")
                return True
    except Exception as e:
        logger.error(f"❌ save_referal: {e}")
        return False


async def update_referal_state(referal_id: int, val: int, used: int):
    if not connectAPI.db_pool:
        return
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE Referals SET value=%s, used=%s WHERE id=%s",
                    (val, used, referal_id)
                )
    except Exception as e:
        logger.error(f"❌ update_referal_state: {e}")


async def update_referal_uuid_point(referal_id: int, uuid_referal: str, point: str):
    if not connectAPI.db_pool:
        return
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE Referals SET uuid_referal=%s, point=%s WHERE id=%s",
                    (uuid_referal, point, referal_id)
                )
    except Exception as e:
        logger.error(f"❌ update_referal_uuid_point: {e}")


async def update_referal_uuid_referer(referal_id: int, uuid_referer: str):
    if not connectAPI.db_pool:
        return
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE Referals SET uuid_referer=%s WHERE id=%s",
                    (uuid_referer, referal_id)
                )
    except Exception as e:
        logger.error(f"❌ update_referal_uuid_referer: {e}")


async def get_referals_count(tg_id: int) -> int:
    """Счётчик для раздела 'Мои рефералы'."""
    if not connectAPI.db_pool:
        return 0
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM Referals WHERE tg_id = %s", (tg_id,)
                )
                result = await cur.fetchone()
                return result[0] if result else 0
    except Exception as e:
        logger.error(f"❌ get_referals_count: {e}")
        return 0


# === CYBERMJ REFERRALS LIST V1 ===
async def get_referrals_page(tg_id: int, page: int = 0, page_size: int = 8):
    """Page of only this inviter's referrals, including the Telegram handle cache."""
    if not connectAPI.db_pool:
        logger.error('get_referrals_page: MySQL pool unavailable')
        return None
    import re
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', DB_TABLE):
        raise ValueError('Invalid DB_TABLE identifier')
    page_size = max(1, min(int(page_size), 15))
    page = max(0, int(page))
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    'SELECT COUNT(*), '
                    'COALESCE(SUM(CASE WHEN used=1 AND value=1 THEN 1 ELSE 0 END),0) '
                    'FROM Referals WHERE tg_id=%s',
                    (tg_id,)
                )
                total, rewarded = await cur.fetchone()
                total, rewarded = int(total or 0), int(rewarded or 0)
                pages = max(1, (total + page_size - 1) // page_size)
                page = min(page, pages - 1)
                await cur.execute(
                    f'SELECT r.id, r.tg_referal, r.used, r.value, p.phone, '
                    f'p.telegram_username FROM Referals AS r '
                    f'LEFT JOIN {DB_TABLE} AS p ON p.tg_id = r.tg_referal '
                    f'WHERE r.tg_id = %s '
                    f'ORDER BY r.id DESC LIMIT %s OFFSET %s',
                    (tg_id, page_size, page * page_size)
                )
                rows = await cur.fetchall()
        return {
            'total': total,
            'rewarded': rewarded,
            'page': page,
            'pages': pages,
            'page_size': page_size,
            'rows': [
                {
                    'referral_id': int(r[0]),
                    'tg_referal': int(r[1]),
                    'phone': r[4],
                    'username': r[5],
                    'bonus_received': int(r[2] or 0) == 1 and int(r[3] or 0) == 1,
                }
                for r in rows
            ],
        }
    except Exception:
        logger.exception('get_referrals_page failed for inviter %s', tg_id)
        return None

async def update_telegram_username(tg_id: int, username: str | None) -> bool:
    """Save username only for an existing, phone-verified TG user.

    A missing username clears a previous one. Never creates a new identity.
    """
    if not connectAPI.db_pool:
        return False
    import re
    safe_name = str(username or '').strip().lstrip('@')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', safe_name):
        safe_name = None
    try:
        async with connectAPI.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f'UPDATE {DB_TABLE} SET telegram_username=%s WHERE tg_id=%s',
                    (safe_name, tg_id)
                )
        return True
    except Exception:
        logger.exception('Failed to update TG username for %s', tg_id)
        return False

# === CYBERMJ REFERRAL USERNAME LINKS V1 ===


# === CYBERMJ EXISTING BOT USERS REFERRALS V1 ===
async def get_referral_owner(tg_referal: int):
    """Return inviter id or None; DB failure raises rather than implying absence."""
    if not connectAPI.db_pool:
        raise RuntimeError('MySQL pool unavailable')
    async with connectAPI.db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                'SELECT tg_id FROM Referals WHERE tg_referal=%s LIMIT 1',
                (tg_referal,)
            )
            row = await cur.fetchone()
            return int(row[0]) if row else None


async def create_referral_once(inviter_tg_id: int, invitee_tg_id: int) -> str:
    """No UPDATE/UPSERT: preserve existing inviter and any settled used/value flags.

    IMPORTANT: requires UNIQUE KEY on Referals(tg_referal) for race safety.
    """
    if inviter_tg_id == invitee_tg_id:
        return 'exists'
    if not connectAPI.db_pool:
        raise RuntimeError('MySQL pool unavailable')
    async with connectAPI.db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Protect from parallel clicks across bot workers even when MySQL
            # was deployed without the expected schema migration.
            await cur.execute('SHOW INDEX FROM Referals')
            index_rows = await cur.fetchall()
            unique_indexes = {}
            for idx in index_rows:
                if int(idx[1]) == 0:
                    unique_indexes.setdefault(idx[2], []).append(idx[4])
            if ['tg_referal'] not in unique_indexes.values():
                raise RuntimeError('Referals.tg_referal must have its own UNIQUE index')
            try:
                await cur.execute(
                    'INSERT INTO Referals (tg_id,tg_referal,used,value) '
                    'VALUES (%s,%s,0,0)',
                    (inviter_tg_id, invitee_tg_id)
                )
                return 'created'
            except Exception as exc:
                # MySQL 1062 is duplicate key. Other failures must never look
                # like existing referral or successful insertion.
                code = exc.args[0] if getattr(exc, 'args', None) else None
                if code == 1062:
                    return 'exists'
                raise

