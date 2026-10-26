"""Модуль подключений: Telegram + MySQL + Smartshell API."""
import logging
import time
import asyncio
import aiohttp
import aiomysql
from typing import List, Dict, Optional, Tuple
from aiogram import Bot
from config import (
    BOT_TOKEN, DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_TABLE,
    SMARTSHELL_GRAPHQL_URL, SMARTSHELL_LOGIN, SMARTSHELL_PASSWORD, SMARTSHELL_WAREHOUSES
)
from normalizePhone import normalize_phone

logger = logging.getLogger(__name__)

bot: Bot = None
db_pool = None

# 🎯 ПРАВИЛЬНОЕ кэширование токенов: словарь company_id → (token, timestamp)
# Решает проблему race condition при параллельных запросах на 6 точек
_smartshell_tokens: Dict[int, Tuple[str, float]] = {}

# GraphQL запрос поиска клиента по q (по ТЗ)
CLIENTS_SEARCH_QUERY = """
query clients($input: ClientsInput, $first: Int, $page: Int) {
  clients(input: $input, first: $first, page: $page) {
    paginatorInfo { count currentPage lastPage total }
    data {
      id
      uuid
      nickname
      phone
      deposit
      bonus
    }
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
    """
    Получает/обновляет токен Smartshell для конкретной точки (company_id).
    
    🎯 ИСПРАВЛЕНО: используется словарь токенов вместо глобальных переменных.
    Это решает проблему race condition при параллельных запросах на 6 точек.
    Каждая точка имеет свой изолированный кэш токена.
    """
    global _smartshell_tokens
    
    # Проверяем кэш для ЭТОЙ конкретной точки
    cached = _smartshell_tokens.get(company_id)
    if cached and (time.time() - cached[1]) < 3500:
        return cached[0]

    # Запрашиваем новый токен для этой точки
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
                raise RuntimeError(f"Токен Smartshell не получен для company_id={company_id}: {data}")
            
            # Сохраняем в кэш для этой точки
            _smartshell_tokens[company_id] = (token, time.time())
            logger.info(f"🔑 Токен получен/обновлён для company_id={company_id}")
            return token


async def search_client_by_phone_on_warehouse(phone: str, company_id: int) -> Optional[Dict]:
    """
    Проверяет наличие клиента на конкретной точке через clients(q=phone).
    Возвращает словарь с информацией о клиенте или None если не найден.
    """
    normalized = normalize_phone(phone)
    
    # Получаем warehouse_name из company_id через обратный поиск
    warehouse_name = None
    for name, cid in SMARTSHELL_WAREHOUSES.items():
        if cid == company_id:
            warehouse_name = name
            break
    
    if not warehouse_name:
        logger.error(f"❌ Не найден warehouse_name для company_id={company_id}")
        return None
    
    # Пробуем разные форматы номера
    phone_variants = [
        normalized,                    # 79502887547
        f"+{normalized}",              # +79502887547
        f"8{normalized[1:]}",          # 89502887547
    ]
    
    for phone_format in phone_variants:
        try:
            token = await _get_smartshell_token(company_id)
            variables = {
                "input": {"q": phone_format, "sort": {"field": "last_client_activity", "direction": "DESC"}},
                "first": 25,
                "page": 1
            }
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            logger.info(f"🔍 Поиск на точке '{warehouse_name}' (ID:{company_id}): phone={phone_format}")
            
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    SMARTSHELL_GRAPHQL_URL,
                    json={"query": CLIENTS_SEARCH_QUERY, "variables": variables},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    # 🎯 Обработка 401 Unauthorized - обновляем токен и повторяем
                    if resp.status == 401:
                        logger.warning(f"🔑 Токен устарел для {warehouse_name}, обновляю...")
                        token = await _get_smartshell_token(company_id)
                        headers["Authorization"] = f"Bearer {token}"
                        # Повторяем запрос
                        async with sess.post(
                            SMARTSHELL_GRAPHQL_URL,
                            json={"query": CLIENTS_SEARCH_QUERY, "variables": variables},
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp2:
                            data = await resp2.json()
                    else:
                        data = await resp.json()
                    
                    if "errors" in data:
                        # Проверяем, не связана ли ошибка с токеном
                        error_msg = data["errors"][0].get("message", "").lower()
                        if "unauthorized" in error_msg or "token" in error_msg:
                            logger.warning(f"🔑 GraphQL auth error на {warehouse_name}, обновляю токен...")
                            token = await _get_smartshell_token(company_id)
                            headers["Authorization"] = f"Bearer {token}"
                            async with sess.post(
                                SMARTSHELL_GRAPHQL_URL,
                                json={"query": CLIENTS_SEARCH_QUERY, "variables": variables},
                                headers=headers,
                                timeout=aiohttp.ClientTimeout(total=15)
                            ) as resp3:
                                data = await resp3.json()
                        
                        if "errors" in data:
                            logger.warning(f"⚠️ GraphQL ошибка на {warehouse_name}: {data['errors']}")
                            continue
                    
                    clients = data.get("data", {}).get("clients", {}).get("data", []) or []
                    
                    if clients:
                        client = clients[0]
                        result = {
                            "point": warehouse_name,
                            "company_id": company_id,
                            "uuid": client.get("uuid"),
                            "phone": client.get("phone"),
                            "nickname": client.get("nickname"),
                            "deposit": float(client.get("deposit", 0) or 0),
                            "bonus": float(client.get("bonus", 0) or 0),
                        }
                        logger.info(
                            f"✅ НАЙДЕН! Точка '{warehouse_name}': "
                            f"phone={result['phone']}, "
                            f"deposit={result['deposit']}₽, "
                            f"bonus={result['bonus']}₽"
                        )
                        return result
                    else:
                        logger.info(f"   Не найден на точке '{warehouse_name}' (формат {phone_format})")
                        
        except Exception as e:
            logger.error(f"❌ Ошибка поиска на точке '{warehouse_name}': {e}")
            continue
    
    return None


async def check_client_exists_on_any_warehouse(phone: str) -> List[Dict]:
    """
    Параллельно проверяет клиента на ВСЕХ 6 точках через clients(q=phone).
    
    Возвращает:
    - Пустой список [] если клиент нигде не найден (новичок)
    - Список словарей с информацией о клиенте на каждой точке (старичок)
    
    Пример возврата для старичка:
    [
        {"point": "Сахалинская", "deposit": 150.0, "bonus": 300.0, "uuid": "..."},
        {"point": "Калинина", "deposit": 0.0, "bonus": 50.0, "uuid": "..."},
    ]
    """
    if not all([SMARTSHELL_GRAPHQL_URL, SMARTSHELL_LOGIN, SMARTSHELL_PASSWORD]):
        logger.warning("⚠️ Smartshell API не настроен в .env")
        return []

    # Передаём только company_id (warehouse_name получим внутри функции)
    company_ids = list(SMARTSHELL_WAREHOUSES.values())
    tasks = [search_client_by_phone_on_warehouse(phone, cid) for cid in company_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    found_clients = []
    for result in results:
        if isinstance(result, dict) and result is not None:
            found_clients.append(result)
        elif isinstance(result, Exception):
            logger.error(f"❌ Ошибка в параллельном запросе: {result}")

    if found_clients:
        logger.info(f"✅ Клиент {phone} найден на {len(found_clients)} точках (СТАРЫЙ)")
        for client in found_clients:
            logger.info(
                f"   📍 {client['point']}: "
                f"депозит={client['deposit']}₽, "
                f"бонусы={client['bonus']}₽"
            )
    else:
        logger.info(f"❌ Клиент {phone} не найден ни на одной точке (НОВИЧОК)")

    return found_clients


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


# === CYBERMJ EXISTING BOT USERS REFERRALS V1 ===
# Distinguish "not found" from API failures. The older public checker returns []
# in both situations and must NOT be used to create financial referral records.
_referral_search_lock = asyncio.Lock()
_referral_search_times = {}


async def _referral_search_request_slot(company_id: int):
    """Cap THIS checker to 6 API requests/min per company, incl. re-logins."""
    while True:
        async with _referral_search_lock:
            now = time.monotonic()
            used = [t for t in _referral_search_times.get(company_id, ()) if now - t < 60]
            if len(used) < 6:
                used.append(now)
                _referral_search_times[company_id] = used
                return
            delay = max(0.1, 60 - (now - used[0]))
        await asyncio.sleep(delay)


async def check_clients_for_referral_strict(phone: str) -> List[Dict]:
    """All six branches must respond successfully before we call a user new.

    Raises on missing configuration, partial API outage or incomplete pagination.
    Empty list therefore means confidently no matching SmartShell account.
    """
    digits = normalize_phone(phone)
    if len(digits) != 11 or not digits.startswith('7'):
        raise ValueError('Invalid verified phone for referral')
    if not all((SMARTSHELL_GRAPHQL_URL, SMARTSHELL_LOGIN, SMARTSHELL_PASSWORD)):
        raise RuntimeError('SmartShell credentials missing')
    branches = SMARTSHELL_WAREHOUSES
    if not branches or len(branches) != 6:
        raise RuntimeError('Expected six SmartShell branches for safe referral validation')
    variants = [digits, '+' + digits, '8' + digits[1:]]
    semaphore = asyncio.Semaphore(2)  # Do not flood all six tokens simultaneously.

    async def one_branch(name, company_id):
        async with semaphore:
            results = []
            for variant in variants:
                data = None
                for retry in range(2):
                    # Refreshing token may itself call API; reserve an extra slot
                    # on the branch when cache is absent or expired.
                    cached = _smartshell_tokens.get(company_id)
                    if not cached or time.time() - cached[1] >= 3500:
                        await _referral_search_request_slot(company_id)
                    token = await _get_smartshell_token(company_id)
                    await _referral_search_request_slot(company_id)
                    headers = {'Authorization': 'Bearer ' + token,
                               'Content-Type': 'application/json'}
                    variables = {'input': {'q': variant}, 'first': 100, 'page': 1}
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            SMARTSHELL_GRAPHQL_URL,
                            json={'query': CLIENTS_SEARCH_QUERY, 'variables': variables},
                            headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                        ) as response:
                            if response.status == 401:
                                _smartshell_tokens.pop(company_id, None)
                                if retry == 0:
                                    continue
                                raise RuntimeError('SmartShell 401 after retry')
                            if response.status != 200:
                                raise RuntimeError(f'SmartShell HTTP {response.status}, branch {name}')
                            payload = await response.json()
                    if not isinstance(payload, dict):
                        raise RuntimeError('Unexpected SmartShell response type')
                    if payload.get('errors'):
                        detail = str(payload['errors']).lower()
                        if retry == 0 and ('unauthoriz' in detail or 'token' in detail or 'jwt' in detail):
                            _smartshell_tokens.pop(company_id, None)
                            continue
                        raise RuntimeError(f'SmartShell GraphQL error, branch {name}')
                    data = (payload.get('data') or {}).get('clients')
                    break
                if not isinstance(data, dict) or not isinstance(data.get('data'), list):
                    raise RuntimeError(f'Incomplete SmartShell search on {name}')
                paginator = data.get('paginatorInfo')
                if not isinstance(paginator, dict):
                    raise RuntimeError(f'Missing SmartShell pagination on {name}')
                if int(paginator.get('lastPage') or 1) > 1:
                    # Fail closed instead of declaring a newcomer from page 1.
                    raise RuntimeError(f'Paginated client search on {name}; manual review needed')
                for client in data['data']:
                    if not isinstance(client, dict):
                        raise RuntimeError('Malformed SmartShell client row')
                    candidate = client.get('phone')
                    if not candidate:
                        raise RuntimeError(f'SmartShell client without phone on {name}')
                    if normalize_phone(candidate) == digits:
                        results.append({
                            'point': name, 'company_id': company_id,
                            'uuid': client.get('uuid'), 'phone': candidate,
                            'nickname': client.get('nickname'),
                            'deposit': float(client.get('deposit') or 0),
                            'bonus': float(client.get('bonus') or 0),
                        })
                if results:
                    break  # Exact phone match already proves this branch has an account.
            return results

    # If any branch returned a matching account, user is definitely not new.
    # If none matched, ALL branches must succeed before referral registration.
    all_results = await asyncio.gather(
        *(one_branch(name, company_id) for name, company_id in branches.items()),
        return_exceptions=True
    )
    found = [client for branch_result in all_results
             if isinstance(branch_result, list) for client in branch_result]
    if found:
        return list({(c['company_id'], c['uuid']): c for c in found}.values())
    errors = [x for x in all_results if isinstance(x, BaseException)]
    if errors:
        raise RuntimeError('Incomplete SmartShell search across branches') from errors[0]
    return []

