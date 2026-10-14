"""
friendMon.py - Монитор реферальной программы "Приведи друга" (по ТЗ)

Архитектура:
- Главный цикл каждые 60 сек сканирует БД
- Для val=0,used=0 → запускает worker1 (ожидание авторизации реферала + пополнения)
- Для val=1,used=0 → запускает worker2 (ожидание авторизации наставника + бонус)
- Каждый worker - отдельная asyncio задача с вложенными мониторами
- Семафор через словари in_progress: {id: Task}
- Проверка подписки обоих в момент первого пополнения >=150₽
- Таймаут 30 дней → used=1, val=0
"""
import sys
import os
import asyncio
import aiohttp
import logging
import time
import re
import aiomysql
from typing import Optional, Dict, Any, Tuple, Set
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'BotGuests')))
from config import (
    SMARTSHELL_GRAPHQL_URL, SMARTSHELL_LOGIN, SMARTSHELL_PASSWORD,
    SMARTSHELL_WAREHOUSES, MIN_TOPUP_AMOUNT, BONUS_PREMIUM, BONUS_STANDARD,
    PREMIUM_WAREHOUSES, REFERAL_TIMEOUT_DAYS
)

# Telegram для проверки подписки
CYBERMG_BOT_TOKEN = os.getenv("BOT_TOKEN")
CYBERMG_CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("friend_monitor.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# === КОНСТАНТЫ ===
DB_SCAN_INTERVAL = 60           # сек - главный цикл
EVENT_POLL_INTERVAL = 30        # сек - опрос событий авторизации
PAYMENT_POLL_INTERVAL = 20      # сек - опрос платежей
TARGET_PAYMENT_METHODS = {"CARD", "CASH", "COMPOSITE"}


# === GraphQL ЗАПРОСЫ ===
CLIENTS_SEARCH_QUERY = """
query clients($input: ClientsInput, $first: Int, $page: Int) {
  clients(input: $input, first: $first, page: $page) {
    paginatorInfo { count currentPage lastPage total }
    data { id uuid nickname phone deposit bonus }
  }
}
"""

EVENT_LIST_QUERY = """
query EventList($input: EventsInput, $page: Int, $first: Int) {
  eventList(input: $input, page: $page, first: $first) {
    data {
      timestamp type description
      host { title }
      client { uuid phone nickname first_name last_name }
    }
    paginatorInfo { count currentPage lastPage }
  }
}
"""

GET_PAYMENTS_QUERY = """
query getPaymentsByClientId($uuid: String!, $page: Int, $first: Int) {
  getPaymentsByClientId(uuid: $uuid, page: $page, first: $first) {
    paginatorInfo { currentPage lastPage }
    data {
      created_at title amount sum card_sum cash_sum
      paymentMethod is_refunded
      items { id amount type sum }
    }
  }
}
"""

SET_BONUS_MUTATION = """
mutation SetBonus($input: SetBonusInput!) {
  setBonus(input: $input) { uuid bonus }
}
"""

# === MySQL ===
mysql_pool: Optional[aiomysql.Pool] = None
cybermg_bot: Optional[Bot] = None


async def init_mysql_pool() -> bool:
    global mysql_pool
    host = os.getenv("DB_HOST")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    database = os.getenv("DB_NAME")
    port = int(os.getenv("DB_PORT", 3306))
    if not all([host, user, password, database]):
        logger.error("❌ Не все переменные БД заданы!")
        return False
    try:
        mysql_pool = await aiomysql.create_pool(
            host=host, port=port, user=user, password=password,
            db=database, charset='utf8mb4', autocommit=True,
            minsize=2, maxsize=10, pool_recycle=3600,
        )
        logger.info(f"✅ MySQL пул создан")
        return True
    except Exception as e:
        logger.error(f"❌ MySQL: {e}")
        return False


async def init_telegram_bot():
    global cybermg_bot
    if not CYBERMG_BOT_TOKEN:
        logger.warning("⚠️ BOT_TOKEN не задан, проверки подписки отключены")
        return
    try:
        cybermg_bot = Bot(token=CYBERMG_BOT_TOKEN)
        logger.info("✅ CyberMG Bot для подписки")
    except Exception as e:
        logger.error(f"❌ CyberMG Bot: {e}")


# === УТИЛИТЫ ===
def normalize_phone(phone: str) -> str:
    if not phone: return ""
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) == 10:
        digits = '7' + digits
    return digits


async def check_subscription(tg_id: int) -> bool:
    if not cybermg_bot or not CYBERMG_CHANNEL_ID:
        return False
    try:
        member = await cybermg_bot.get_chat_member(CYBERMG_CHANNEL_ID, tg_id)
        return member.status in ["member", "creator", "administrator", "restricted"]
    except Exception as e:
        logger.error(f"❌ check_subscription({tg_id}): {e}")
        return False


async def db_execute(query: str, params=None):
    if not mysql_pool: return None
    try:
        async with mysql_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, params)
                return await cur.fetchall()
    except Exception as e:
        logger.error(f"❌ db_execute: {e}")
        return None


async def db_update(query: str, params):
    if not mysql_pool: return
    try:
        async with mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
    except Exception as e:
        logger.error(f"❌ db_update: {e}")


# === SMARTSHELL CLIENT ===
class SmartshellClient:
    def __init__(self):
        self.tokens: Dict[int, Tuple[str, float]] = {}  # company_id → (token, time)
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_token(self, company_id: int) -> str:
        cached = self.tokens.get(company_id)
        if cached and (time.time() - cached[1]) < 3500:
            return cached[0]

        login_query = f"""
        mutation login {{
            login(input: {{ login: "{SMARTSHELL_LOGIN}", password: "{SMARTSHELL_PASSWORD}", company_id: {company_id} }}) {{
                access_token
            }}
        }}
        """
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        async with self.session.post(
            SMARTSHELL_GRAPHQL_URL,
            json={"query": login_query},
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            data = await resp.json()
            token = data.get("data", {}).get("login", {}).get("access_token")
            if not token:
                raise RuntimeError(f"Token not received: {data}")
            self.tokens[company_id] = (token, time.time())
            return token

    async def execute(self, query: str, variables: Dict, company_id: int) -> Dict:
        token = await self._get_token(company_id)
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        for attempt in range(3):
            try:
                async with self.session.post(
                    SMARTSHELL_GRAPHQL_URL,
                    json={"query": query, "variables": variables},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    },
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status in (401, 403):
                        token = await self._get_token(company_id)
                        await asyncio.sleep(1)
                        continue
                    result = await resp.json()
                    if "errors" in result:
                        err_msg = result["errors"][0].get("message", "").lower()
                        if "unauthorized" in err_msg or "token" in err_msg:
                            token = await self._get_token(company_id)
                            await asyncio.sleep(1)
                            continue
                    return result
            except Exception as e:
                if attempt == 2: raise
                await asyncio.sleep(2 * (attempt + 1))
        return {"errors": [{"message": "Max retries"}]}

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# === SMARTSHELL API МЕТОДЫ ===
async def search_client_uuid(client: SmartshellClient, phone: str, company_id: int) -> Optional[str]:
    """Ищет UUID клиента через clients(q=phone) на конкретной точке."""
    try:
        result = await client.execute(
            CLIENTS_SEARCH_QUERY,
            {
                "input": {"q": phone, "sort": {"field": "last_client_activity", "direction": "DESC"}},
                "first": 25, "page": 1
            },
            company_id
        )
        if "errors" in result:
            return None
        data = result.get("data", {}).get("clients", {}).get("data", []) or []
        if data:
            return data[0].get("uuid")
    except Exception as e:
        logger.warning(f"search_client_uuid на {company_id}: {e}")
    return None


async def poll_auth_events(client: SmartshellClient, target_phone: str, 
                            company_id: int, warehouse_name: str,
                            start_time: str, processed_events: Set[str]) -> Optional[Tuple[str, str]]:
    """
    Опрашивает EventList для конкретной точки (company_id).
    Возвращает (uuid, warehouse_name) если нашёл нужное событие.
    warehouse_name передаётся извне, потому что мы УЖЕ ЗНАЕМ, какую точку опрашиваем.
    """
    try:
        result = await client.execute(
            EVENT_LIST_QUERY,
            {
                "input": {
                    "types": ["CLIENT_AUTHORIZED_FROM_HOST"],
                    "start": start_time,
                    "finish": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                },
                "page": 1, "first": 100
            },
            company_id
        )
        if "errors" in result:
            return None
        events = result.get("data", {}).get("eventList", {}).get("data", []) or []
        for event in events:
            event_key = f"{event.get('timestamp')}_{event.get('client', {}).get('uuid')}"
            if event_key in processed_events:
                continue
            processed_events.add(event_key)

            client_info = event.get("client") or {}
            if not client_info:
                continue
            phone_event = normalize_phone(client_info.get("phone", ""))
            if phone_event == target_phone:
                # 🎯 КЛЮЧЕВОЙ МОМЕНТ: возвращаем warehouse_name, который уже знаем
                # (мы его получили из SMARTSHELL_WAREHOUSES при итерации)
                logger.info(f"✅ Авторизация: {target_phone} на {warehouse_name} (company_id={company_id})")
                return client_info.get("uuid"), warehouse_name
    except Exception as e:
        logger.warning(f"poll_auth_events ({warehouse_name}): {e}")
    return None

async def get_client_payments(client: SmartshellClient, uuid: str, company_id: int) -> list:
    """Возвращает список платежей клиента (первая страница)."""
    try:
        result = await client.execute(
            GET_PAYMENTS_QUERY,
            {"uuid": uuid, "page": 1, "first": 50},
            company_id
        )
        if "errors" in result:
            return []
        return result.get("data", {}).get("getPaymentsByClientId", {}).get("data", []) or []
    except Exception as e:
        logger.warning(f"get_client_payments: {e}")
        return []


async def get_client_bonus(client: SmartshellClient, phone: str, company_id: int) -> Optional[float]:
    """Получает текущий bonus через clients(q=phone)."""
    try:
        result = await client.execute(
            CLIENTS_SEARCH_QUERY,
            {
                "input": {"q": phone, "sort": {"field": "last_client_activity", "direction": "DESC"}},
                "first": 25, "page": 1
            },
            company_id
        )
        if "errors" in result:
            return None
        data = result.get("data", {}).get("clients", {}).get("data", []) or []
        if data:
            return float(data[0].get("bonus", 0) or 0)
    except Exception as e:
        logger.warning(f"get_client_bonus: {e}")
    return None


async def set_client_bonus(client: SmartshellClient, uuid: str, new_value: float, company_id: int) -> bool:
    """Устанавливает новое значение bonus через SetBonus."""
    try:
        result = await client.execute(
            SET_BONUS_MUTATION,
            {"input": {"client_uuid": uuid, "value": new_value}},
            company_id
        )
        if "errors" in result:
            logger.error(f"❌ SetBonus error: {result['errors']}")
            return False
        logger.info(f"✅ SetBonus: uuid={uuid}, value={new_value}")
        return True
    except Exception as e:
        logger.error(f"❌ set_client_bonus: {e}")
        return False


# === ГЛАВНЫЙ МОНИТОР ===
class ReferalMonitor:
    def __init__(self):
        self.client = SmartshellClient()
        self.worker1_tasks: Dict[int, asyncio.Task] = {}  # id → Task (val=0, used=0)
        self.worker2_tasks: Dict[int, asyncio.Task] = {}  # id → Task (val=1, used=0)

    async def run(self):
        logger.info("🚀 ReferalMonitor запущен")
        while True:
            try:
                await self._scan_database()
                self._cleanup_finished_tasks()
                await self._expire_old_records()
            except Exception as e:
                logger.error(f"💥 Главный цикл: {e}", exc_info=True)
            await asyncio.sleep(DB_SCAN_INTERVAL)

    async def _scan_database(self):
        # Worker 1: val=0, used=0 (ожидание авторизации реферала)
        rows = await db_execute(
            "SELECT r.id, r.tg_id, r.tg_referal, p1.phone as mentor_phone, p2.phone as referal_phone "
            "FROM Referals r "
            "LEFT JOIN phone_tg_links p1 ON p1.tg_id = r.tg_id "
            "LEFT JOIN phone_tg_links p2 ON p2.tg_id = r.tg_referal "
            "WHERE r.used = 0 AND r.value = 0"
        )
        if rows:
            for row in rows:
                if row["id"] not in self.worker1_tasks and row["id"] not in self.worker2_tasks:
                    task = asyncio.create_task(self._worker1_process(row))
                    self.worker1_tasks[row["id"]] = task
                    logger.info(f"📥 Worker1 запущен для id={row['id']}")

        # Worker 2: val=1, used=0 (ожидание авторизации наставника)
        rows = await db_execute(
            "SELECT r.id, r.tg_id, r.tg_referal, r.uuid_referal, r.point, "
            "p1.phone as mentor_phone, p2.phone as referal_phone "
            "FROM Referals r "
            "LEFT JOIN phone_tg_links p1 ON p1.tg_id = r.tg_id "
            "LEFT JOIN phone_tg_links p2 ON p2.tg_id = r.tg_referal "
            "WHERE r.used = 0 AND r.value = 1"
        )
        if rows:
            for row in rows:
                if row["id"] not in self.worker2_tasks and row["id"] not in self.worker1_tasks:
                    task = asyncio.create_task(self._worker2_process(row))
                    self.worker2_tasks[row["id"]] = task
                    logger.info(f"📥 Worker2 запущен для id={row['id']}")

    def _cleanup_finished_tasks(self):
        for d in [self.worker1_tasks, self.worker2_tasks]:
            finished = [k for k, t in d.items() if t.done()]
            for k in finished:
                task = d.pop(k)
                if task.exception():
                    logger.error(f"💥 Task id={k} упал: {task.exception()}")

    async def _expire_old_records(self):
        """Помечает записи старше 30 дней как used=1, val=0."""
        await db_update(
            "UPDATE Referals SET used=1, value=0 "
            "WHERE used=0 AND created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
            (REFERAL_TIMEOUT_DAYS,)
        )

    async def _worker1_process(self, row: Dict):
        referal_id = row["id"]
        mentor_tg = row["tg_id"]
        referal_tg = row["tg_referal"]
        mentor_phone = normalize_phone(row.get("mentor_phone") or "")
        referal_phone = normalize_phone(row.get("referal_phone") or "")

        logger.info(f"[W1-{referal_id}] Старт. Реферал: {referal_phone}")

        if not referal_phone:
            logger.warning(f"[W1-{referal_id}] У реферала нет телефона → used=1")
            await db_update("UPDATE Referals SET used=1 WHERE id=%s", (referal_id,))
            return

        # 1. Ожидание авторизации реферала
        start_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        processed_events: Set[str] = set()
        uuid_referal, point = None, None

        deadline = time.time() + REFERAL_TIMEOUT_DAYS * 86400
        while time.time() < deadline:
            # 🎯 Параллельный опрос всех 6 точек с их НАЗВАНИЯМИ
            tasks = [
                poll_auth_events(
                    self.client, 
                    referal_phone, 
                    company_id,           # company_id (например, 3241)
                    warehouse_name,       # название точки (например, "Сахалинская")
                    start_time, 
                    processed_events
                )
                for warehouse_name, company_id in SMARTSHELL_WAREHOUSES.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, tuple) and r[0]:
                    uuid_referal, point = r
                    break
            if uuid_referal:
                break
            await asyncio.sleep(EVENT_POLL_INTERVAL)

        if not uuid_referal:
            logger.warning(f"[W1-{referal_id}] Таймаут авторизации → used=1, val=0")
            await db_update("UPDATE Referals SET used=1, value=0 WHERE id=%s", (referal_id,))
            return

        # Сохраняем в БД: uuid + НАЗВАНИЕ ТОЧКИ (например, "Сахалинская")
        await db_update(
            "UPDATE Referals SET uuid_referal=%s, point=%s WHERE id=%s",
            (uuid_referal, point, referal_id)
        )
        logger.info(f"[W1-{referal_id}] ✅ Авторизация: uuid={uuid_referal}, point={point}")

        # 2. Ожидание пополнения
        await self._monitor_payments(referal_id, uuid_referal, mentor_tg, referal_tg, point)

    async def _monitor_payments(self, referal_id: int, uuid_referal: str,
                                 mentor_tg: int, referal_tg: int, point: str):
        """Вложенный монитор платежей. Раз в 20 сек проверяет getPaymentsByClientId."""
        logger.info(f"[W1-{referal_id}] 🔄 Мониторинг платежей uuid={uuid_referal}")
        processed_payment_ids: Set[int] = set()
        first_company_id = list(SMARTSHELL_WAREHOUSES.values())[0]

        deadline = time.time() + REFERAL_TIMEOUT_DAYS * 86400
        while time.time() < deadline:
            payments = await get_client_payments(self.client, uuid_referal, first_company_id)
            for p in payments:
                pid = p.get("id") or f"{p.get('created_at')}_{p.get('sum')}"
                if pid in processed_payment_ids:
                    continue
                processed_payment_ids.add(pid)

                method = (p.get("paymentMethod") or "").upper()
                if method not in TARGET_PAYMENT_METHODS:
                    continue
                if p.get("is_refunded"):
                    continue

                amount = float(p.get("sum") or 0)
                if amount < MIN_TOPUP_AMOUNT:
                    # Платёж < 150₽ → used=1, val=0 (СТОП)
                    logger.info(f"[W1-{referal_id}] ❌ Платёж {amount}₽ < {MIN_TOPUP_AMOUNT} → used=1")
                    await db_update("UPDATE Referals SET used=1, value=0 WHERE id=%s", (referal_id,))
                    return

                # Платёж >= 150₽ - проверяем подписку обоих
                mentor_sub = await check_subscription(mentor_tg)
                referal_sub = await check_subscription(referal_tg)

                if mentor_sub and referal_sub:
                    # Всё ок → val=1, used=0 (переход в worker2)
                    logger.info(f"[W1-{referal_id}] ✅ Платёж {amount}₽, оба подписаны → val=1")
                    await db_update("UPDATE Referals SET value=1, used=0 WHERE id=%s", (referal_id,))
                    return
                else:
                    # Кто-то не подписан → used=1, val=0 (СТОП)
                    logger.info(f"[W1-{referal_id}] ❌ Подписка: mentor={mentor_sub}, referal={referal_sub} → used=1")
                    await db_update("UPDATE Referals SET used=1, value=0 WHERE id=%s", (referal_id,))
                    return

            await asyncio.sleep(PAYMENT_POLL_INTERVAL)

        # Таймаут
        logger.warning(f"[W1-{referal_id}] Таймаут ожидания платежа → used=1, val=0")
        await db_update("UPDATE Referals SET used=1, value=0 WHERE id=%s", (referal_id,))

    async def _worker2_process(self, row: Dict):
        referal_id = row["id"]
        mentor_tg = row["tg_id"]
        point = row.get("point") or ""  # Уже содержит "Сахалинская" или другое название
        mentor_phone = normalize_phone(row.get("mentor_phone") or "")

        logger.info(f"[W2-{referal_id}] Старт. Наставник: {mentor_phone}, точка реферала: {point}")

        if not mentor_phone:
            logger.warning(f"[W2-{referal_id}] У наставника нет телефона → used=1")
            await db_update("UPDATE Referals SET used=1 WHERE id=%s", (referal_id,))
            return

        if not point:
            logger.warning(f"[W2-{referal_id}] Неизвестна точка авторизации реферала → used=1")
            await db_update("UPDATE Referals SET used=1 WHERE id=%s", (referal_id,))
            return

        # 1. Ожидание авторизации наставника (на ЛЮБОЙ точке)
        start_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        processed_events: Set[str] = set()
        uuid_referer = None
        mentor_warehouse_id = None

        deadline = time.time() + REFERAL_TIMEOUT_DAYS * 86400
        while time.time() < deadline:
            tasks = [
                self._poll_mentor_auth(mentor_phone, company_id, start_time, processed_events)
                for _, company_id in SMARTSHELL_WAREHOUSES.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, tuple) and r[0]:
                    uuid_referer, mentor_warehouse_id = r
                    break
            if uuid_referer:
                break
            await asyncio.sleep(EVENT_POLL_INTERVAL)

        if not uuid_referer:
            logger.warning(f"[W2-{referal_id}] Таймаут авторизации наставника → used=1")
            await db_update("UPDATE Referals SET used=1 WHERE id=%s", (referal_id,))
            return

        await db_update(
            "UPDATE Referals SET uuid_referer=%s WHERE id=%s",
            (uuid_referer, referal_id)
        )
        logger.info(f"[W2-{referal_id}] ✅ Наставник авторизован: uuid={uuid_referer}")

        # 2. Получаем текущий bonus наставника
        current_bonus = None
        for cid in SMARTSHELL_WAREHOUSES.values():
            current_bonus = await get_client_bonus(self.client, mentor_phone, cid)
            if current_bonus is not None:
                break
        if current_bonus is None:
            current_bonus = 0.0

        # 🎯 3. Сумма бонуса зависит от ТОЧКИ РЕФЕРАЛА (не наставника!)
        if point in PREMIUM_WAREHOUSES:  # ["Сахалинская", "Калинина"]
            bonus_amount = BONUS_PREMIUM  # 500
        else:
            bonus_amount = BONUS_STANDARD  # 250

        new_value = current_bonus + bonus_amount

        # 4. SetBonus - начисляем на той точке, где авторизовался НАСТАВНИК
        success = await set_client_bonus(self.client, uuid_referer, new_value, mentor_warehouse_id)

        if success:
            await db_update("UPDATE Referals SET used=1, value=1 WHERE id=%s", (referal_id,))
            logger.info(
                f"[W2-{referal_id}] 🎁 БОНУС: наставник {mentor_tg} получил +{bonus_amount}₽ "
                f"(было {current_bonus}, стало {new_value}). "
                f"Реферал был на точке '{point}'"
            )
        else:
            logger.error(f"[W2-{referal_id}] ❌ SetBonus не удался, оставляем used=0 для повтора")

    async def _poll_mentor_auth(self, mentor_phone: str, company_id: int,
                                start_time: str, processed_events: Set[str]):
        """Возвращает (uuid, company_id) если нашёл наставника на этой точке."""
        try:
            result = await self.client.execute(
                EVENT_LIST_QUERY,
                {
                    "input": {
                        "types": ["CLIENT_AUTHORIZED_FROM_HOST"],
                        "start": start_time,
                        "finish": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    },
                    "page": 1, "first": 100
                },
                company_id
            )
            if "errors" in result:
                return (None, None)
            events = result.get("data", {}).get("eventList", {}).get("data", []) or []
            for event in events:
                event_key = f"{event.get('timestamp')}_{event.get('client', {}).get('uuid')}_{company_id}"
                if event_key in processed_events:
                    continue
                processed_events.add(event_key)
                client_info = event.get("client") or {}
                if not client_info:
                    continue
                phone_event = normalize_phone(client_info.get("phone", ""))
                if phone_event == mentor_phone:
                    return client_info.get("uuid"), company_id
        except Exception as e:
            logger.warning(f"_poll_mentor_auth: {e}")
        return (None, None)

async def main():
    logger.info("=" * 60)
    logger.info("🤖 FriendMonitor (ТЗ версия): 2 воркера + семафор")
    logger.info(f"⏱️ Интервалы: БД={DB_SCAN_INTERVAL}с, events={EVENT_POLL_INTERVAL}с, payments={PAYMENT_POLL_INTERVAL}с")
    logger.info(f"💰 Порог: {MIN_TOPUP_AMOUNT}₽ | Бонусы: premium={BONUS_PREMIUM}, standard={BONUS_STANDARD}")
    logger.info(f"⏳ Таймаут: {REFERAL_TIMEOUT_DAYS} дней")
    logger.info("=" * 60)

    if not await init_mysql_pool():
        return
    await init_telegram_bot()

    monitor = ReferalMonitor()
    try:
        await monitor.run()
    except KeyboardInterrupt:
        logger.info("⏹️ Ctrl+C")
    finally:
        for task in list(monitor.worker1_tasks.values()) + list(monitor.worker2_tasks.values()):
            task.cancel()
        await monitor.client.close()
        if cybermg_bot:
            try: await cybermg_bot.session.close()
            except: pass
        if mysql_pool:
            mysql_pool.close()
            await mysql_pool.wait_closed()
        logger.info("✅ Завершено")


if __name__ == "__main__":
    asyncio.run(main())