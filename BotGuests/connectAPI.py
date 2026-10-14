"""Модуль подключений: Telegram + MySQL + Smartshell API."""
import logging
import time
import asyncio
import aiohttp
import aiomysql
from aiogram import Bot
from config import (
    BOT_TOKEN, DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_TABLE,
    SMARTSHELL_GRAPHQL_URL, SMARTSHELL_LOGIN, SMARTSHELL_PASSWORD, SMARTSHELL_WAREHOUSES
)
from normalizePhone import normalize_phone

logger = logging.getLogger(__name__)

bot: Bot = None
db_pool = None

_smartshell_token = None
_smartshell_token_time = 0
_smartshell_token_company_id = None

# GraphQL запрос поиска клиента по q (по ТЗ)
CLIENTS_SEARCH_QUERY = """
query clients($input: ClientsInput, $first: Int, $page: Int) {
  clients(input: $input, first: $first, page: $page) {
    paginatorInfo { count currentPage lastPage total }
    data { id uuid nickname phone deposit bonus }
  }
}
"""


def init_telegram_bot() -> Bot:
    global bot
    if not BOT_TOKEN:
        raise ValueError("❌ BOT_TOKEN не задан в config.py")
    bot = Bot(token=BOT_TOKEN)
    logger.info("✅ Telegram Bot инициализирован")
    return bot


async def init_database():
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
        return db_pool
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к БД: {e}")
        return None


async def _get_smartshell_token(company_id: int) -> str:
    global _smartshell_token, _smartshell_token_time, _smartshell_token_company_id
    if (_smartshell_token
        and (time.time() - _smartshell_token_time) < 3500
        and _smartshell_token_company_id == company_id):
        return _smartshell_token

    login_query = f"""
    mutation login {{
        login(input: {{ login: "{SMARTSHELL_LOGIN}", password: "{SMARTSHELL_PASSWORD}", company_id: {company_id} }}) {{
            access_token
        }}
    }}
    """
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            SMARTSHELL_GRAPHQL_URL,
            json={"query": login_query},
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            data = await resp.json()
            token = data.get("data", {}).get("login", {}).get("access_token")
            if not token:
                raise RuntimeError(f"Токен Smartshell не получен: {data}")
            _smartshell_token = token
            _smartshell_token_time = time.time()
            _smartshell_token_company_id = company_id
            return token


async def search_client_by_phone_on_warehouse(phone: str, company_id: int) -> bool:
    """Проверяет наличие клиента на конкретной точке через clients(q=phone)."""
    normalized = normalize_phone(phone)
    try:
        token = await _get_smartshell_token(company_id)
        variables = {
            "input": {"q": normalized, "sort": {"field": "last_client_activity", "direction": "DESC"}},
            "first": 25,
            "page": 1
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                SMARTSHELL_GRAPHQL_URL,
                json={"query": CLIENTS_SEARCH_QUERY, "variables": variables},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                if "errors" in data:
                    logger.warning(f"⚠️ Ошибка GraphQL на {company_id}: {data['errors']}")
                    return False
                clients = data.get("data", {}).get("clients", {}).get("data", []) or []
                return len(clients) > 0
    except Exception as e:
        logger.error(f"❌ search_client_by_phone_on_warehouse({company_id}): {e}")
        return False


async def check_client_exists_on_any_warehouse(phone: str) -> bool:
    """
    Параллельно проверяет клиента на ВСЕХ 6 точках через clients(q=phone).
    Возвращает True если найден хотя бы на одной точке (старичок).
    False если нигде не найден (новичок).
    """
    if not all([SMARTSHELL_GRAPHQL_URL, SMARTSHELL_LOGIN, SMARTSHELL_PASSWORD]):
        logger.warning("⚠️ Smartshell API не настроен в .env")
        return False

    company_ids = list(SMARTSHELL_WAREHOUSES.values())

    # Запускаем параллельные запросы ко всем точкам
    tasks = [search_client_by_phone_on_warehouse(phone, cid) for cid in company_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if r is True:
            logger.info(f"✅ Клиент {phone} найден хотя бы на одной точке (СТАРЫЙ)")
            return True

    logger.info(f"❌ Клиент {phone} не найден ни на одной точке (НОВИЧОК)")
    return False


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