# === НАСТРОЙКА ПУТЕЙ ДЛЯ ИМПОРТОВ ===
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import aiohttp
import logging
import time
import re
import aiomysql
import gspread
from typing import Set, Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()

# === ИМПОРТ НАСТРОЕК ===
from config import (
    TELEGRAM_TOKEN, REPORT_CHAT_ID, THREAD_CHAT_ID,
    SMARTSHELL_GRAPHQL_URL, SMARTSHELL_LOGIN, SMARTSHELL_PASSWORD,
    SMARTSHELL_WAREHOUSE_IDS, GOOGLE_SHEET_ID,
)

# === ИМПОРТ СУЩЕСТВУЮЩИХ МОДУЛЕЙ ===
from google_sheets import GC, GOOGLE_SHEET_ID as SS_GOOGLE_SHEET_ID
from smartshell import award_client_bonus

# === КОНСТАНТЫ ДЛЯ CYBERMG БОТА (из .env) ===
CYBERMG_BOT_TOKEN = os.getenv("BOT_TOKEN")
CYBERMG_CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1004443148648"))

# === НАСТРОЙКИ ===
WAREHOUSES = SMARTSHELL_WAREHOUSE_IDS
TARGET_PAYMENT_METHODS = {"CASH", "CARD", "DEPOSIT", "COMPOSITE"}
TARGET_TRANSACTION_SERVICES = {"SBP", "TINKOFF_SBP", "TINKOFF", "C2B", "QR_PAYMENT", "ONLINE_TRANSFER"}

METHOD_DISPLAY = {
    "CASH": "Наличные", "CARD": "Карта", "DEPOSIT": "Онлайн",
    "TINKOFF_SBP": "Tinkoff SBP", "SBP": "СБП", "TINKOFF": "Tinkoff",
    "C2B": "C2B", "ONLINE_TRANSFER": "Онлайн-перевод", "QR_PAYMENT": "QR-оплата",
    "COMPOSITE": "Составная оплата"
}

MAX_BONUS_PER_TOPUP = 1000
MAX_ELIGIBLE_TOPUPS = 3

# 🔧 Настройки устойчивости
SMARTSHELL_TOKEN_TTL = 3600
SMARTSHELL_RETRY_DELAY = 5
SMARTSHELL_MAX_RETRIES = 3
TELEGRAM_CLOSE_DELAY = 2
MONITOR_START_DELAY = 3

# 🔧 Настройки фильтров свежести
PAYMENT_FRESH_MINUTES = 5       # Для наличных/карт в активной смене
TRANSACTION_FRESH_MINUTES = 2   # Для онлайн-транзакций
EVENT_FRESH_MINUTES = 10        # Для онлайн-событий

# 🔧 Браузерные заголовки для обхода WAF/CDN
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://billing.smartshell.gg",
    "Referer": "https://billing.smartshell.gg/",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("smartshell_deposit.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ==========================================
# === MYSQL ПОДКЛЮЧЕНИЕ ===
# ==========================================

mysql_pool: Optional[aiomysql.Pool] = None


async def init_mysql_pool() -> bool:
    global mysql_pool

    host = os.getenv("DB_HOST")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    database = os.getenv("DB_NAME")
    port = int(os.getenv("DB_PORT", 3306))
    minsize = int(os.getenv("DB_POOL_MINSIZE", 2))
    maxsize = int(os.getenv("DB_POOL_MAXSIZE", 10))

    if not all([host, user, password, database]):
        logger.error("❌ Не все переменные БД заданы в .env!")
        return False

    try:
        mysql_pool = await aiomysql.create_pool(
            host=host, port=port, user=user, password=password,
            db=database, charset='utf8mb4', autocommit=True,
            minsize=minsize, maxsize=maxsize, pool_recycle=3600,
        )
        logger.info(f"✅ MySQL пул создан: {user}@{host}:{port}/{database}")

        async with mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SHOW TABLES LIKE 'phone_tg_links'")
                if not await cur.fetchone():
                    logger.warning("⚠️ Таблица phone_tg_links не найдена в БД!")
                    return False
                await cur.execute("SELECT COUNT(*) FROM phone_tg_links")
                count = (await cur.fetchone())[0]
                logger.info(f"📊 Записей в phone_tg_links: {count}")

        return True
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к MySQL: {e}")
        mysql_pool = None
        return False


async def close_mysql_pool():
    global mysql_pool
    if mysql_pool:
        mysql_pool.close()
        await mysql_pool.wait_closed()
        logger.info("🔌 MySQL пул закрыт")


# ==========================================
# === НОРМАЛИЗАЦИЯ ТЕЛЕФОНОВ ===
# ==========================================

def normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) == 10:
        digits = '7' + digits
    if len(digits) > 11:
        digits = digits[-11:]
    return digits


# ==========================================
# === ПОИСК TGID ПО ТЕЛЕФОНУ (MySQL) ===
# ==========================================

async def get_tg_id_by_phone(phone: str) -> Optional[int]:
    if not mysql_pool:
        return None

    normalized = normalize_phone(phone)
    if not normalized:
        return None

    try:
        async with mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT tg_id FROM phone_tg_links WHERE phone = %s LIMIT 1",
                    (normalized,)
                )
                result = await cur.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.error(f"❌ Ошибка get_tg_id_by_phone для {phone}: {e}")
        return None


# ==========================================
# === GRAPHQL ЗАПРОСЫ ===
# ==========================================

GET_ACTIVE_SHIFT_PAYMENTS = """
query GetActiveShiftPayments {
  activeWorkShift {
    id
    payments {
      id method sum bonus created_at is_refunded
      client { id uuid nickname phone first_name last_name deposit }
      items { id type sum amount entity { __typename ... on Tariff { id title } ... on Good { id title } ... on Service { id title } } }
    }
  }
}
"""

GET_PAYMENT_TRANSACTIONS = """
query GetPaymentTransactions($company_id: Int!, $first: Int!) {
  paymentTransactions(company_id: $company_id, first: $first) {
    paginatorInfo { hasMorePages lastPage currentPage total }
    data { id service amount sum status label created_at client { uuid nickname phone first_name last_name deposit bonus } additional { data } }
  }
}
"""

GET_CLIENT_PAYMENT_HISTORY = """
query GetClientPayments($uuid: String!, $page: Int!, $first: Int!) {
  getPaymentsByClientId(uuid: $uuid, page: $page, first: $first) {
    data { items { type sum } is_refunded }
    paginatorInfo { lastPage currentPage total }
  }
}
"""

GET_DEPOSIT_EVENTS = """
query GetDepositEvents($first: Int) {
  eventList(first: $first) {
    paginatorInfo { hasMorePages lastPage currentPage }
    data {
      timestamp type description
      client { uuid phone nickname first_name last_name }
      payment { id title value }
      payment_items { sum }
    }
  }
}
"""

GET_CLIENT_INFO = """
query GetClientInfo($uuid: String!) {
  user(uuid: $uuid) { uuid deposit bonus phone nickname first_name last_name }
}
"""


# ==========================================
# === ИНИЦИАЛИЗАЦИЯ БОТОВ ===
# ==========================================

bot = Bot(token=TELEGRAM_TOKEN)

cybermg_bot: Optional[Bot] = None
if CYBERMG_BOT_TOKEN:
    cybermg_bot = Bot(token=CYBERMG_BOT_TOKEN)
    logger.info("✅ CyberMG Bot инициализирован для проверок подписки")
else:
    logger.warning("⚠️ BOT_TOKEN не задан в .env. Проверки подписки отключены.")


# ==========================================
# === ПРОВЕРКА ПОДПИСКИ ===
# ==========================================

async def check_subscription_by_phone(phone: str) -> Tuple[bool, str]:
    if not cybermg_bot:
        return False, "bot_disabled"
    if not mysql_pool:
        return False, "db_disabled"
    if not phone:
        return False, "no_phone"

    tg_id = await get_tg_id_by_phone(phone)
    if not tg_id:
        return False, "no_link"

    try:
        member = await cybermg_bot.get_chat_member(CYBERMG_CHANNEL_ID, tg_id)
        status = member.status
        is_subscribed = status in ["member", "creator", "administrator", "restricted"]
        if is_subscribed:
            return True, "ok"
        else:
            return False, f"not_subscribed ({status})"
    except Exception as e:
        logger.error(f"❌ Ошибка get_chat_member для {phone} (tg_id={tg_id}): {e}")
        return False, "error"


# ==========================================
# === КЛАСС DepositMonitor ===
# ==========================================

class DepositMonitor:
    def __init__(self, warehouse_name: str, company_id: int, polling_interval: float = 30.0):
        self.warehouse_name = warehouse_name
        self.company_id = company_id
        self.interval = polling_interval
        self.token: Optional[str] = None
        self.token_received_at: float = 0
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_running = False

        self.processed_payment_ids: Set[int] = set()
        self.processed_transaction_ids: Set[str] = set()
        self.processed_event_ids: Set[str] = set()

        self.client_cache: Dict[str, Dict] = {}
        self.client_cache_time: Dict[str, float] = {}
        self.client_cache_ttl = 300

        self.first_poll = True
        self.first_transaction_poll = True
        self.first_event_poll = True

    async def auth(self) -> str:
        login_query = f"""
        mutation login {{
            login(input: {{ login: "{SMARTSHELL_LOGIN}", password: "{SMARTSHELL_PASSWORD}", company_id: {self.company_id} }}) {{
                access_token
            }}
        }}
        """
        headers = {**BROWSER_HEADERS, "Content-Type": "application/json"}

        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                SMARTSHELL_GRAPHQL_URL,
                json={"query": login_query},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                content_type = resp.content_type or ""
                if "text/html" in content_type:
                    body = await resp.text()
                    raise RuntimeError(
                        f"❌ SmartShell вернул HTML вместо JSON (status={resp.status}). "
                        f"Возможна блокировка WAF/CDN. Ответ: {body[:200]}"
                    )

                data = await resp.json()
                if "errors" in data:
                    raise RuntimeError(
                        f"❌ Ошибка авторизации {self.warehouse_name}: "
                        f"{data['errors'][0].get('message')}"
                    )
                token = data.get("data", {}).get("login", {}).get("access_token")
                if not token:
                    raise RuntimeError("❌ Токен не получен")

                self.token_received_at = time.time()
                logger.info(f"🔑 Токен обновлён для {self.warehouse_name}")
                return token

    async def _ensure_fresh_token(self):
        if not self.token:
            self.token = await self.auth()
            return

        elapsed = time.time() - self.token_received_at
        if elapsed >= SMARTSHELL_TOKEN_TTL:
            logger.info(f"🔄 Токен устарел ({elapsed:.0f}с). Обновляю превентивно...")
            self.token = await self.auth()

    async def execute_query(self, query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        await self._ensure_fresh_token()

        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(headers=BROWSER_HEADERS)

        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(SMARTSHELL_MAX_RETRIES):
            try:
                async with self.session.post(
                    SMARTSHELL_GRAPHQL_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    content_type = resp.content_type or ""

                    if "text/html" in content_type:
                        body = await resp.text()
                        logger.warning(
                            f"⚠️ SmartShell вернул HTML (status={resp.status}, "
                            f"попытка {attempt + 1}/{SMARTSHELL_MAX_RETRIES}). "
                            f"Ответ: {body[:200]}"
                        )
                        if resp.status in (401, 403):
                            self.token = await self.auth()
                            headers["Authorization"] = f"Bearer {self.token}"
                        await asyncio.sleep(SMARTSHELL_RETRY_DELAY * (attempt + 1))
                        continue

                    if resp.status in (401, 403):
                        logger.warning(
                            f"🔑 SmartShell вернул {resp.status}. "
                            f"Обновляю токен (попытка {attempt + 1}/{SMARTSHELL_MAX_RETRIES})..."
                        )
                        self.token = await self.auth()
                        headers["Authorization"] = f"Bearer {self.token}"
                        await asyncio.sleep(SMARTSHELL_RETRY_DELAY)
                        continue

                    result = await resp.json()

                    if "errors" in result:
                        error_msg = result["errors"][0].get("message", "")
                        if any(kw in error_msg.lower() for kw in [
                            "unauthorized", "token expired", "invalid token",
                            "authentication", "not authenticated"
                        ]):
                            logger.warning(
                                f"🔑 GraphQL ошибка авторизации: {error_msg}. "
                                f"Обновляю токен (попытка {attempt + 1})..."
                            )
                            self.token = await self.auth()
                            headers["Authorization"] = f"Bearer {self.token}"
                            await asyncio.sleep(SMARTSHELL_RETRY_DELAY)
                            continue

                    return result

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    f"⚠️ Сетевая ошибка (попытка {attempt + 1}/{SMARTSHELL_MAX_RETRIES}): {e}"
                )
                if attempt < SMARTSHELL_MAX_RETRIES - 1:
                    await asyncio.sleep(SMARTSHELL_RETRY_DELAY * (attempt + 1))
                    if self.session and not self.session.closed:
                        await self.session.close()
                    self.session = aiohttp.ClientSession(headers=BROWSER_HEADERS)
                else:
                    raise

        logger.error(f"❌ Все {SMARTSHELL_MAX_RETRIES} попытки запроса не удались")
        return {"errors": [{"message": "Max retries exceeded"}]}

    def is_target_deposit(self, payment: Dict) -> bool:
        if not payment.get("client") or payment.get("is_refunded"):
            return False
        if payment.get("method") not in TARGET_PAYMENT_METHODS:
            return False
        return any(item.get("type") == "DEPOSIT" for item in (payment.get("items") or []) if item)

    def is_target_deposit_transaction(self, transaction: Dict) -> bool:
        if not transaction.get("client"):
            return False
        if transaction.get("status", "").lower() not in {"success", "completed", "paid", "confirmed"}:
            return False
        if transaction.get("service") not in TARGET_TRANSACTION_SERVICES:
            return False
        return float(transaction.get("sum", 0) or 0) > 0

    def is_deposit_online_event(self, event: Dict) -> bool:
        return event.get("type") == "DEPOSIT_ADDED_ONLINE"

    def get_event_amount(self, event: Dict) -> float:
        payment = event.get("payment") or {}
        val = payment.get("value")
        if val and float(val) > 0:
            return float(val)
        for item in (event.get("payment_items") or []):
            s = item.get("sum")
            if s and float(s) > 0:
                return float(s)
        return 0.0

    # 🔧 НОВЫЙ МЕТОД: фильтр свежести для платежей в смене (CASH/CARD/COMPOSITE)
    def _is_fresh_payment(self, created_at_str: str, max_minutes: int = PAYMENT_FRESH_MINUTES) -> bool:
        """Проверяет, что платеж в активной смене свежий (не старше max_minutes).
        Защищает от 'кучи бонусов' при восстановлении SmartShell после сбоя."""
        if not created_at_str:
            return False
        try:
            ts = created_at_str.strip().replace(" ", "T")
            if "T" in ts and "+" not in ts and "Z" not in ts:
                ts += "+00:00"
            payment_time = datetime.fromisoformat(ts)
            if payment_time.tzinfo is None:
                payment_time = payment_time.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - payment_time
            return age < timedelta(minutes=max_minutes)
        except Exception:
            return False  # При ошибке парсинга — считаем старым (безопаснее)

    def _is_fresh_event(self, timestamp_str: str, max_minutes: int = EVENT_FRESH_MINUTES) -> bool:
        try:
            ts = timestamp_str.strip().replace(" ", "T")
            if "T" in ts and "+" not in ts and "Z" not in ts:
                ts += "+00:00"
            event_time = datetime.fromisoformat(ts)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - event_time) < timedelta(minutes=max_minutes)
        except Exception:
            return True

    def _is_fresh_transaction(self, created_at_str: str) -> bool:
        try:
            ts = created_at_str.replace("Z", "+00:00")
            tx_time = datetime.fromisoformat(ts)
            if tx_time.tzinfo is None:
                tx_time = tx_time.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - tx_time) < timedelta(minutes=TRANSACTION_FRESH_MINUTES)
        except Exception:
            return True

    def _detect_payment_method(self, event: Dict) -> str:
        payment = event.get("payment") or {}
        title = (payment.get("title") or "").upper()
        if "TINKOFF_SBP" in title or "SBP" in title:
            return METHOD_DISPLAY.get("SBP", "СБП")
        if "TINKOFF" in title:
            return METHOD_DISPLAY.get("TINKOFF_SBP", "Tinkoff SBP")
        if "QR" in title:
            return METHOD_DISPLAY.get("QR_PAYMENT", "QR-оплата")
        if "C2B" in title:
            return METHOD_DISPLAY.get("C2B", "C2B")
        return METHOD_DISPLAY.get("DEPOSIT", "Онлайн")

    async def _get_client_with_cache(self, uuid: str) -> Optional[Dict]:
        now = time.time()
        if uuid in self.client_cache and (now - self.client_cache_time.get(uuid, 0)) < self.client_cache_ttl:
            return self.client_cache[uuid]
        try:
            resp = await self.execute_query(GET_CLIENT_INFO, {"uuid": uuid})
            if "errors" not in resp:
                client = resp.get("data", {}).get("user")
                if client:
                    self.client_cache[uuid] = client
                    self.client_cache_time[uuid] = now
                    return client
        except Exception as e:
            logger.error(f"⚠️ Ошибка получения клиента {uuid}: {e}")
        return None

    async def _fetch_and_process_deposit_events(self):
        try:
            resp = await self.execute_query(GET_DEPOSIT_EVENTS, {"first": 100})
            if "errors" in resp:
                logger.warning(f"⚠️ Ошибка eventList {self.warehouse_name}: {resp['errors']}")
                return

            events = resp.get("data", {}).get("eventList", {}).get("data") or []

            for event in events:
                if not self.is_deposit_online_event(event):
                    continue
                if not self._is_fresh_event(event.get("timestamp", "")):
                    continue

                client_stub = event.get("client") or {}
                client_uuid = client_stub.get("uuid")
                if not client_uuid:
                    continue

                payment_id = event.get("payment", {}).get("id")
                event_key = f"{payment_id}_{event.get('type')}_{client_uuid}"

                if event_key in self.processed_event_ids:
                    continue
                if self.first_event_poll:
                    self.processed_event_ids.add(event_key)
                    continue

                self.processed_event_ids.add(event_key)
                logger.info(f"✅ [EVENT] Новое онлайн-пополнение | payment_id={payment_id} | client={client_stub.get('nickname')}")

                client = await self._get_client_with_cache(client_uuid)
                if not client:
                    continue

                amount = self.get_event_amount(event)
                if amount <= 0:
                    continue

                method = self._detect_payment_method(event)

                await self._process_target(
                    client=client, amount=amount, method_display=method,
                    created_at=event.get("timestamp", ""), source="sbp_event"
                )

        except Exception as e:
            logger.error(f"💥 Ошибка обработки событий {self.warehouse_name}: {e}", exc_info=True)

    async def count_client_deposits(self, client_uuid: str) -> int:
        page, total, max_pages = 1, 0, 10
        while page <= max_pages:
            resp = await self.execute_query(GET_CLIENT_PAYMENT_HISTORY, {"uuid": client_uuid, "page": page, "first": 50})
            if "errors" in resp:
                break
            data = resp.get("data", {}).get("getPaymentsByClientId", {})
            for p in (data.get("data") or []):
                if p.get("is_refunded"):
                    continue
                if any(i.get("type") == "DEPOSIT" and i.get("sum", 0) > 0 for i in (p.get("items") or []) if i):
                    total += 1
            if data.get("paginatorInfo", {}).get("currentPage", 1) >= data.get("paginatorInfo", {}).get("lastPage", 1):
                break
            page += 1
            await asyncio.sleep(0.1)
        return total

    def log_to_google_sheet(self, client, topup_amount, bonus_amount, topup_number, warehouse_name, status, source):
        def _write_sync():
            try:
                ws = GC.open_by_key(SS_GOOGLE_SHEET_ID).worksheet("КорректировкаБонусы")
                ws.append_row([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), warehouse_name,
                    client.get("nickname", ""), client.get("phone", ""), client.get("uuid", ""),
                    topup_number, f"{topup_amount:.2f}", "—", f"{bonus_amount:.2f}", "—", status, source
                ], value_input_option="RAW")
                logger.info(f"📝 Запись в Google Sheets: {client.get('uuid')}")
            except Exception as e:
                logger.error(f"❌ Ошибка Google Sheets: {e}", exc_info=True)

        asyncio.ensure_future(asyncio.to_thread(_write_sync))

    async def send_report_to_bot(self, client, topup_amount, bonus_amount, topup_number, warehouse_name, method_display, source="payment"):
        remaining = MAX_ELIGIBLE_TOPUPS - topup_number
        fn = client.get('first_name') or ''
        ln = client.get('last_name') or ''
        client_name = f"{fn} {ln}".strip() or client.get('nickname') or f"ID:{client.get('uuid')}"

        is_online = source == "sbp_event" or any(kw in method_display.upper() for kw in ["СБП", "SBP", "ONLINE", "QR"])
        header = "🌐 *Начисление бонуса (Онлайн/СБП)*" if is_online else "💳 *Начисление бонуса*"

        message = (
            f"{header}\n📍 Точка: `{warehouse_name}`\n👤 Клиент: `{client_name}`\n"
            f"📱 Телефон: `{client.get('phone', '—')}`\n🔢 Пополнение: №{topup_number} из {MAX_ELIGIBLE_TOPUPS}\n"
            f"💵 Поступило: `{topup_amount:.2f} ₽`\n📊 Депозит: `{float(client.get('deposit', 0) or 0):.2f} ₽`\n"
            f"🎁 Бонус начислен: `+{bonus_amount:.2f} ₽`\n💳 Метод: `{method_display}`\n"
            f"✨ Доступно ещё: {remaining} пополн."
        )
        try:
            await bot.send_message(REPORT_CHAT_ID, text=message, parse_mode="Markdown", message_thread_id=THREAD_CHAT_ID)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки в бот: {e}")

    async def send_skip_report(self, client, topup_number, warehouse_name, reason: str):
        fn = client.get('first_name') or ''
        ln = client.get('last_name') or ''
        client_name = f"{fn} {ln}".strip() or client.get('nickname') or f"ID:{client.get('uuid')}"

        message = (
            f"⚠️ *Бонус НЕ начислен*\n"
            f"📍 Точка: `{warehouse_name}`\n"
            f"👤 Клиент: `{client_name}`\n"
            f"📱 Телефон: `{client.get('phone', '—')}`\n"
            f"🔢 Пополнение: №{topup_number} из {MAX_ELIGIBLE_TOPUPS}\n"
            f"❌ Причина: `{reason}`"
        )
        try:
            await bot.send_message(REPORT_CHAT_ID, text=message, parse_mode="Markdown", message_thread_id=THREAD_CHAT_ID)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки skip-отчёта: {e}")

    async def _process_target(self, client, amount, method_display, created_at, source="payment"):
        phone = client.get("phone")
        nickname = client.get("nickname") or client.get("uuid", "—")

        count = await self.count_client_deposits(client["uuid"])

        if count > MAX_ELIGIBLE_TOPUPS:
            logger.info(f"💳 Лимит исчерпан | {self.warehouse_name} | {nickname} | №{count}")
            return

        if not phone:
            logger.warning(f"⚠️ У клиента {nickname} нет телефона. Бонус пропущен.")
            return

        is_subscribed, sub_reason = await check_subscription_by_phone(phone)

        if not is_subscribed:
            reason_map = {
                "no_link": "не активировал бота (нет связки Phone↔TGID в БД)",
                "not_subscribed": "не подписан на канал (или отписался)",
                "bot_disabled": "CyberMG Bot не инициализирован",
                "db_disabled": "MySQL не подключен",
                "no_phone": "пустой номер",
                "error": "ошибка проверки подписки"
            }
            human_reason = reason_map.get(sub_reason, sub_reason)
            logger.info(f"⚠️ Бонус пропущен | {self.warehouse_name} | {nickname} | №{count} | {human_reason}")
            return

        bonus_to_add = min(amount, MAX_BONUS_PER_TOPUP)
        if bonus_to_add <= 0:
            return

        success, msg = await award_client_bonus(
            warehouse_name=self.warehouse_name,
            client_uuid=client["uuid"],
            bonus_amount=bonus_to_add
        )

        if success:
            await self.send_report_to_bot(client, amount, bonus_to_add, count, self.warehouse_name, method_display, source)
            self.log_to_google_sheet(client, amount, bonus_to_add, count, self.warehouse_name, "bonus_added", source)
            logger.info(f"🎁 БОНУС НАЧИСЛЕН | {nickname} +{bonus_to_add:.2f} ₽ | №{count} | {method_display} | ✅ подписан")
        else:
            logger.warning(f"⚠️ Ошибка начисления SmartShell: {msg}")

    async def _fetch_and_process_transactions(self):
        try:
            page, max_per_page = 1, 50
            while page <= 3:
                resp = await self.execute_query(GET_PAYMENT_TRANSACTIONS, {"company_id": self.company_id, "first": max_per_page})
                if "errors" in resp:
                    break
                tx_data = resp.get("data", {}).get("paymentTransactions", {})
                transactions = tx_data.get("data") or []
                if not transactions:
                    break

                for tx in transactions:
                    tx_id = tx.get("id")
                    if not tx_id or tx_id in self.processed_transaction_ids:
                        continue
                    if not self._is_fresh_transaction(tx.get("created_at", "")):
                        continue

                    if self.first_transaction_poll:
                        self.processed_transaction_ids.add(tx_id)
                        continue
                    if not self.is_target_deposit_transaction(tx):
                        self.processed_transaction_ids.add(tx_id)
                        continue
                    self.processed_transaction_ids.add(tx_id)

                    client = tx.get("client")
                    if client:
                        await self._process_target(
                            client, float(tx.get("sum", 0)),
                            METHOD_DISPLAY.get(tx.get("service", "UNKNOWN"), tx.get("service", "")),
                            tx.get("created_at"), source="transaction"
                        )
                        await asyncio.sleep(2)

                if not tx_data.get("paginatorInfo", {}).get("hasMorePages"):
                    break
                page += 1
                await asyncio.sleep(0.2)
        except Exception as e:
            logger.error(f"💥 Ошибка транзакций {self.warehouse_name}: {e}", exc_info=True)

    async def poll(self):
        self.is_running = True
        self.token = await self.auth()
        logger.info(f"🟢 Монитор запущен: {self.warehouse_name} (интервал: {self.interval}с)")

        while self.is_running:
            try:
                # === ПЛАТЕЖИ В АКТИВНОЙ СМЕНЕ (CASH/CARD/COMPOSITE) ===
                resp = await self.execute_query(GET_ACTIVE_SHIFT_PAYMENTS)
                if "errors" not in resp:
                    shift = resp.get("data", {}).get("activeWorkShift")
                    if shift:
                        for payment in (shift.get("payments") or []):
                            pid = payment.get("id")
                            if not pid or pid in self.processed_payment_ids:
                                continue

                            # 🔧 КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: фильтр свежести для наличных/карт
                            created_at = payment.get("created_at", "")
                            if not self._is_fresh_payment(created_at):
                                logger.debug(
                                    f"⏰ Пропущен старый платёж | {self.warehouse_name} | "
                                    f"id={pid} | старше {PAYMENT_FRESH_MINUTES} мин"
                                )
                                self.processed_payment_ids.add(pid)
                                continue

                            if self.first_poll:
                                self.processed_payment_ids.add(pid)
                                continue
                            if not self.is_target_deposit(payment):
                                self.processed_payment_ids.add(pid)
                                continue
                            self.processed_payment_ids.add(pid)
                            client = payment.get("client")
                            if client:
                                await self._process_target(
                                    client, float(payment.get("sum", 0)),
                                    METHOD_DISPLAY.get(payment.get("method", ""), ""),
                                    payment.get("created_at"), source="payment"
                                )
                                await asyncio.sleep(0.3)

                # === ОНЛАЙН-ТРАНЗАКЦИИ (СБП/Tinkoff/QR) ===
                await self._fetch_and_process_transactions()

                # === ОНЛАЙН-СОБЫТИЯ ===
                await self._fetch_and_process_deposit_events()

                self.first_poll = False
                self.first_transaction_poll = False
                self.first_event_poll = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"💥 Критическая ошибка {self.warehouse_name}: {e}", exc_info=True)
                await asyncio.sleep(min(self.interval * 2, 300))

            await asyncio.sleep(self.interval)

    async def stop(self):
        self.is_running = False
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info(f"🛑 Монитор {self.warehouse_name} остановлен")


# ==========================================
# === MAIN ===
# ==========================================

async def main():
    logger.info("🚀 Запуск мониторинга депозитов (с MySQL + CyberMG проверкой)...")

    mysql_ready = await init_mysql_pool()
    if not mysql_ready:
        logger.error("❌ MySQL не подключен! Проверь .env и доступность Timeweb.")

    if not WAREHOUSES:
        logger.error("❌ Не настроены хранилища в SMARTSHELL_WAREHOUSE_IDS")
        return

    monitors = [DepositMonitor(name, cid) for name, cid in WAREHOUSES.items()]
    tasks = []

    for i, m in enumerate(monitors):
        task = asyncio.create_task(m.poll())
        tasks.append(task)
        if i < len(monitors) - 1:
            logger.info(f"⏳ Пауза {MONITOR_START_DELAY}с перед запуском следующего монитора...")
            await asyncio.sleep(MONITOR_START_DELAY)

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("⏹️ Получен сигнал остановки...")
    finally:
        logger.info("🔄 Останавливаем мониторы...")
        for m in monitors:
            m.is_running = False

        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        for m in monitors:
            if m.session and not m.session.closed:
                await m.session.close()
            await asyncio.sleep(0.5)

        logger.info("🔄 Закрываем Telegram ботов...")
        try:
            await bot.session.close()
        except Exception:
            pass
        await asyncio.sleep(TELEGRAM_CLOSE_DELAY)

        if cybermg_bot:
            try:
                await cybermg_bot.session.close()
            except Exception:
                pass
            await asyncio.sleep(TELEGRAM_CLOSE_DELAY)

        await close_mysql_pool()
        logger.info("✅ Мониторинг завершён корректно.")


if __name__ == "__main__":
    asyncio.run(main())