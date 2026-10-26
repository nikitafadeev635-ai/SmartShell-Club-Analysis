"""CyberMG: монитор реферальных начислений, вариант с фиксацией филиала.

Первая по времени авторизация друга закрепляет филиал на весь период акции.
История платежей проверяется через company_id первой авторизации. Первый
депозитный платёж с разрешённым paymentMethod после авторизации определяет
исход; другие покупки (например, поминутный тариф) игнорируются.

ВНИМАНИЕ: запрос getPaymentsByClientId содержит uuid/page/first, но не
company_id. Филиал задаёт контекст Bearer SmartShell, полученный для точки
первой авторизации. Если API выдаёт глобальную историю в любом контексте,
филиал отдельного платежа по этому ответу определить невозможно. До проверки
поведения API в живом окружении необходим тестовый запуск.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import aiohttp
import aiomysql
from aiogram import Bot
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / '.env')
sys.path.insert(0, str(HERE.parent / 'BotGuests'))
from config import (  # noqa: E402
    SMARTSHELL_GRAPHQL_URL, SMARTSHELL_LOGIN, SMARTSHELL_PASSWORD,
    SMARTSHELL_WAREHOUSES, MIN_TOPUP_AMOUNT, BONUS_PREMIUM, BONUS_STANDARD,
    PREMIUM_WAREHOUSES, REFERAL_TIMEOUT_DAYS,
)

log = logging.getLogger('friendMon')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s | %(levelname)-8s | %(message)s',
                    handlers=[logging.StreamHandler(),
                              logging.FileHandler(HERE / 'friend_monitor.log', encoding='utf-8')])

TARGET_METHODS = {'CASH', 'CARD', 'COMPOSITE', 'TINKOFF_SBP'}
TOKEN_REFRESH_AFTER_SECONDS = max(300, min(3500, int(os.getenv('SMARTSHELL_REFRESH_AFTER_SECONDS', '3300'))))
TOKEN_REFRESH_CHECK_SECONDS = max(15, int(os.getenv('SMARTSHELL_REFRESH_CHECK_SECONDS', '60')))
POLL_SECONDS = max(5, int(os.getenv('REFERRAL_LOOP_SECONDS', '10')))
EVENT_POLL_SECONDS = max(15, int(os.getenv('REFERRAL_EVENT_POLL_SECONDS', '40')))
EVENT_OVERLAP_SECONDS = max(5, int(os.getenv('REFERRAL_EVENT_OVERLAP_SECONDS', '90')))
EVENT_SETTLE_SECONDS = max(1, int(os.getenv('REFERRAL_EVENT_SETTLE_SECONDS', '10')))
EVENT_BACKFILL_WINDOW_SECONDS = max(40, int(os.getenv('REFERRAL_EVENT_BACKFILL_WINDOW_SECONDS', '3600')))
PAYMENT_RECHECK_SECONDS = max(40, int(os.getenv('REFERRAL_PAYMENT_RECHECK_SECONDS', '180')))
# Раздельные квоты на каждую точку. Всего 15 запросов/мин на точку,
# включая вход по Bearer. Оставляем резерв для depMon.py и бота.
API_RATE_LIMITS = {
    'event': max(1, int(os.getenv('REFERRAL_EVENT_RPM', '4'))),
    'payment': max(1, int(os.getenv('REFERRAL_PAYMENT_RPM', '8'))),
    'credit': max(2, int(os.getenv('REFERRAL_CREDIT_RPM', '2'))),
    'auth': max(1, int(os.getenv('REFERRAL_AUTH_RPM', '1'))),
}
if sum(API_RATE_LIMITS.values()) > 40:
    raise RuntimeError('Сумма квот friendMon превышает 40 запросов/мин/филиал')
API_CONCURRENCY = max(1, int(os.getenv('REFERRAL_API_CONCURRENCY', '6')))
WORKER_CONCURRENCY = max(1, int(os.getenv('REFERRAL_WORKER_CONCURRENCY', '8')))
MAX_HISTORY_PAGES = max(1, int(os.getenv('REFERRAL_HISTORY_MAX_PAGES', '12')))
MAX_EVENT_PAGES = max(1, int(os.getenv('REFERRAL_EVENT_MAX_PAGES', '60')))
REFERRAL_DRY_RUN = os.getenv('REFERRAL_DRY_RUN', 'true').lower() in ('1', 'yes', 'true')
ALERT_TOKEN = os.getenv('TELEGRAM_TOKEN')
ALERT_CHAT = os.getenv('REPORT_CHAT_ID')
ALERT_THREAD = os.getenv('THREAD_CHAT_ID')
SUB_TOKEN = os.getenv('BOT_TOKEN')
SUB_CHANNEL = os.getenv('CHANNEL_ID')
# Фиксированные часовые смещения работают на Windows без пакета tzdata.
# Для Владивостока обычно +10; обязательна сверка с реальными timestamp API/БД.
API_TZ = timezone(timedelta(hours=int(os.getenv('SMARTSHELL_UTC_OFFSET', '10'))))
DB_SOURCE_TZ = timezone(timedelta(hours=int(os.getenv('REFERRAL_DB_UTC_OFFSET', '10'))))
WAREHOUSES = {str(k): int(v) for k, v in SMARTSHELL_WAREHOUSES.items()}
CID_TO_POINT = {v: k for k, v in WAREHOUSES.items()}

EVENTS = '''
query EventList($input: EventsInput, $page: Int, $first: Int) {
  eventList(input: $input, page: $page, first: $first) {
    paginatorInfo { currentPage lastPage }
    data { timestamp type client { uuid phone nickname first_name last_name } }
  }
}
'''
PAYMENTS = '''
query ClientPayments($uuid: String!, $page: Int!, $first: Int!) {
  getPaymentsByClientId(uuid: $uuid, page: $page, first: $first) {
    paginatorInfo { currentPage lastPage }
    data { created_at title amount sum card_sum cash_sum paymentMethod is_refunded
           items { id amount type sum } }
  }
}
'''
USER = '''query User($uuid: String!) {
  user(uuid: $uuid) { uuid phone nickname bonus }
}'''
SET_BONUS = '''mutation SetBonus($input: SetBonusInput!) {
  setBonus(input: $input) { uuid bonus }
}'''

STAGE_NEW = 'WAIT_INVITEE_AUTH'
STAGE_PAYMENT = 'WAIT_PAYMENT'
STAGE_MENTOR = 'WAIT_MENTOR_AUTH'
STAGE_BALANCE = 'WAIT_BALANCE'
STAGE_APPLYING = 'APPLYING'
STAGE_REVIEW = 'NEEDS_REVIEW'
STAGE_DONE = 'DONE'
STAGE_CLOSED = 'CLOSED'
ACTIVE = (STAGE_NEW, STAGE_PAYMENT, STAGE_MENTOR, STAGE_BALANCE)

class ApiError(RuntimeError):
    pass

class AmbiguousPaymentError(ApiError):
    pass

class TooManyEventsError(ApiError):
    pass


def phone_norm(phone: Any) -> str:
    number = re.sub(r'\D', '', str(phone or ''))
    if len(number) == 11 and number[0] == '8':
        number = '7' + number[1:]
    if len(number) == 10:
        number = '7' + number
    return number if len(number) == 11 and number.startswith('7') else ''


def smart_time(v: Any) -> datetime:
    """API SmartShell обычно возвращает YYYY-MM-DD HH:MM:SS без timezone.
    Сравниваем времена событий и платежей из одного API без предположений о UTC.
    """
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    if not v:
        raise ValueError('Время отсутствует')
    result = datetime.fromisoformat(str(v).strip().replace('Z', '+00:00'))
    return result.replace(tzinfo=None) if result.tzinfo is None else result.astimezone(API_TZ).replace(tzinfo=None)


def sql_time(value: Any) -> str:
    return smart_time(value).strftime('%Y-%m-%d %H:%M:%S')


def db_created_to_api(value: Any) -> str:
    """Referals.created_at записан старым процессом в timezone MySQL.
    Преобразование зависит от REFERRAL_DB_TIMEZONE. Проверить на тестовом API!
    """
    moment = smart_time(value).replace(tzinfo=DB_SOURCE_TZ)
    return moment.astimezone(API_TZ).strftime('%Y-%m-%d %H:%M:%S')


def utc_state_to_api(value: Any) -> str:
    """ReferralMonitorState.stage_started_at в UTC (SET time_zone '+00:00')."""
    moment = smart_time(value).replace(tzinfo=timezone.utc)
    return moment.astimezone(API_TZ).strftime('%Y-%m-%d %H:%M:%S')


def api_time_to_utc(value: Any) -> datetime:
    """Дата SmartShell -> UTC-naive для хранения stage_started_at."""
    return smart_time(value).replace(tzinfo=API_TZ).astimezone(timezone.utc).replace(tzinfo=None)



def amount_dec(v: Any) -> Decimal:
    try:
        d = Decimal(str(v))
        if not d.is_finite() or d < 0:
            raise ValueError('Отрицательная/неконечная сумма')
        return d
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ApiError(f'Некорректная сумма API: {v!r}') from e


def payment_key(p: Dict[str, Any]) -> str:
    raw = json.dumps([p.get('created_at'), p.get('sum'), p.get('paymentMethod'),
                      p.get('title'), p.get('items')], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def first_payment_on_fixed_point(payments: list, authorized_at: Any) -> Optional[Dict[str, Any]]:
    """Первый по времени депозитный платёж с допустимым paymentMethod.

    payments запрашиваются через company_id точки первой авторизации.
    Чтобы исключить поминутные тарифы, требуем items[].type=DEPOSIT.
    """
    floor = smart_time(authorized_at)
    events = []
    for p in payments:
        if p.get('is_refunded'):
            continue
        method = str(p.get('paymentMethod') or '').strip().upper()
        if method not in TARGET_METHODS:
            continue
        # Тарифы и услуги не считаются пополнением, даже если paymentMethod=CARD.
        # Структура items[].type и items[].sum подтверждена payload SmartShell.
        items = p.get('items')
        if not isinstance(items, list):
            raise ApiError('У платежа отсутствует items — нельзя проверить тип DEPOSIT')
        deposits = [item for item in items if isinstance(item, dict) and
                    str(item.get('type') or '').upper() == 'DEPOSIT']
        if not deposits:
            continue
        try:
            at = smart_time(p.get('created_at'))
        except (ValueError, TypeError) as exc:
            raise ApiError('SmartShell вернул депозитный платёж без корректной даты') from exc
        if at < floor:
            continue
        # Если оплата составная, считаем только ту часть, что зачислена на депозит.
        deposit_sum = sum((amount_dec(item.get('sum')) for item in deposits), Decimal('0'))
        events.append({'payment': p, 'at': at, 'key': payment_key(p),
                       'sum': deposit_sum})
    if not events:
        return None
    events.sort(key=lambda x: (x['at'], x['key']))
    first = events[0]
    if any(x['at'] == first['at'] and x['key'] != first['key'] for x in events[1:]):
        raise AmbiguousPaymentError('Несколько платежей в одну секунду; первый неизвестен')
    return first


class CompanyRateLimiter:
    """Скользящее окно на 60 секунд отдельно для каждой точки/вида операции.

    Важное условие: защищает только запросы внутри friendMon. Для общего
    лимита с depMon и Telegram-ботом потребуется общий лимитер/учёт их квоты.
    """
    def __init__(self, limits=None, clock=None, sleeper=None):
        self.limits = dict(limits or API_RATE_LIMITS)
        self.calls = defaultdict(deque)
        self.locks = defaultdict(asyncio.Lock)
        self.clock = clock or time.monotonic
        self.sleep = sleeper or asyncio.sleep

    async def acquire(self, cid: int, category: str, cost: int = 1):
        if category not in self.limits:
            raise ValueError(f'Неизвестная категория API: {category}')
        if cost < 1 or cost > self.limits[category]:
            raise ValueError('Некорректная стоимость API-операции')
        key = (int(cid), category)
        async with self.locks[key]:
            while True:
                now = self.clock()
                calls = self.calls[key]
                while calls and now - calls[0] >= 60.0:
                    calls.popleft()
                if len(calls) + cost <= self.limits[category]:
                    calls.extend([now] * cost)
                    return
                await self.sleep(max(0.05, 60.0 - (now - calls[0]) + 0.01))


class SmartShell:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.tokens: Dict[int, Tuple[str, float]] = {}
        self.locks: Dict[int, asyncio.Lock] = {}
        self.limit = asyncio.Semaphore(API_CONCURRENCY)
        self.rate = CompanyRateLimiter()

    async def _http(self, payload: dict, headers: dict, cid: int, category: str, prepaid=False) -> dict:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        if not prepaid:
            await self.rate.acquire(cid, category)
        async with self.limit:
            async with self.session.post(SMARTSHELL_GRAPHQL_URL, json=payload, headers=headers) as res:
                if res.status >= 400:
                    raise ApiError(f'SmartShell HTTP {res.status}')
                content = await res.json(content_type=None)
                if not isinstance(content, dict):
                    raise ApiError('Некорректный JSON SmartShell')
                if content.get('errors'):
                    raise ApiError(str(content['errors'])[:400])
                return content

    async def token(self, cid: int, *, force=False, rejected_token: Optional[str] = None) -> str:
        lock = self.locks.setdefault(cid, asyncio.Lock())
        async with lock:
            old = self.tokens.get(cid)
            if force and rejected_token is not None and old and old[0] != rejected_token:
                # Другой одновременный запрос уже обновил просроченный Bearer.
                return old[0]
            # Кэш токенов и блокировка обновления раздельные для каждого филиала.
            if not force and old and time.monotonic() - old[1] < TOKEN_REFRESH_AFTER_SECONDS:
                return old[0]
            if not SMARTSHELL_LOGIN or not SMARTSHELL_PASSWORD:
                raise ApiError('Нет SMARTSHELL_LOGIN/SMARTSHELL_PASSWORD')
            # Сохраняем синтаксис login(input: ...) из действующего проекта.
            login_query = 'mutation Login { login(input: {login: ' + json.dumps(SMARTSHELL_LOGIN) + \
                ', password: ' + json.dumps(SMARTSHELL_PASSWORD) + \
                ', company_id: ' + str(cid) + '}) { access_token } }'
            data = await self._http({'query': login_query}, {'Content-Type': 'application/json'}, cid, 'auth')
            token = (data.get('data') or {}).get('login') or {}
            token = token.get('access_token')
            if not token:
                raise ApiError(f'Нет access_token для company_id={cid}')
            self.tokens[cid] = (token, time.monotonic())
            return token

    async def query(self, cid: int, query: str, variables: Optional[dict] = None, *, mutation=False, category="payment", prepaid=False) -> dict:
        # Mutation не повторяем автоматически после сетевой ошибки: неизвестно,
        # успел ли сервер выполнить SetBonus.
        token = await self.token(cid)
        try:
            return (await self._http({'query': query, 'variables': variables or {}},
                                     {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}, cid, category, prepaid)).get('data') or {}
        except ApiError as exc:
            if not mutation and any(z in str(exc).lower() for z in ('401', '403', 'unauthorized', 'token')):
                token = await self.token(cid, force=True, rejected_token=token)
                if prepaid:
                    # Баланс нельзя читать повторно за рамками заранее
                    # зарезервированных двух операций: повторим всю пару позднее.
                    raise ApiError('Токен обновлён, чтение баланса повторится позже') from exc
                return (await self._http({'query': query, 'variables': variables or {}},
                                         {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}, cid, category)).get('data') or {}
            raise

    async def proactively_refresh_tokens(self):
        """Прогревает и обновляет Bearer на ВСЕХ филиалах независимо от запросов.

        Обновление производится за 5 минут до часового срока, с защитой от
        конкурентных обновлений. Не использует и не меняет состояние depMon.py.
        """
        while True:
            results = await asyncio.gather(
                *(self.token(cid) for cid in WAREHOUSES.values()),
                return_exceptions=True,
            )
            for (name, cid), result in zip(WAREHOUSES.items(), results):
                if isinstance(result, BaseException):
                    log.warning('Bearer для %s (%s) не удалось обновить: %s',
                                name, cid, type(result).__name__)
            await asyncio.sleep(TOKEN_REFRESH_CHECK_SECONDS)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def event_window(self, cid: int, start_utc: datetime, finish_utc: datetime) -> list:
        """Читаем один временной интервал для ВСЕХ номеров сразу.

        В обычном 40-секундном окне достаточно page=1. Если событий >100
        или был простой, догружаем страницы, иначе контрольную точку НЕ двигаем.
        """
        start = start_utc.replace(tzinfo=timezone.utc).astimezone(API_TZ).strftime('%Y-%m-%d %H:%M:%S')
        finish = finish_utc.replace(tzinfo=timezone.utc).astimezone(API_TZ).strftime('%Y-%m-%d %H:%M:%S')
        events = []
        page = 1
        while True:
            data = await self.query(cid, EVENTS, {
                'input': {'types': ['CLIENT_AUTHORIZED_FROM_HOST'],
                          'start': start, 'finish': finish},
                'page': page, 'first': 100}, category='event')
            wrapper = data.get('eventList')
            if not isinstance(wrapper, dict) or not isinstance(wrapper.get('data'), list):
                raise ApiError(f'eventList недоступен в филиале {cid}')
            rows = wrapper['data']
            info = wrapper.get('paginatorInfo')
            if not isinstance(info, dict):
                raise ApiError('Нет paginatorInfo для гарантии полного просмотра EventList')
            current_page = int(info.get('currentPage') or page)
            last_page = int(info.get('lastPage') or 1)
            if current_page != page or last_page < page:
                raise ApiError('Неверная пагинация EventList; контрольная точка сохранена')
            if last_page > MAX_EVENT_PAGES:
                raise TooManyEventsError(f'EventList: {last_page} страниц (> лимита {MAX_EVENT_PAGES}); '
                               'курсор НЕ сдвинут — требуется догрузка/сужение окна')
            for row in rows:
                if not isinstance(row, dict):
                    raise ApiError('Некорректный элемент EventList')
                # API способен вернуть посторонние types, фильтруем локально.
                if row.get('type') != 'CLIENT_AUTHORIZED_FROM_HOST':
                    continue
                if not row.get('timestamp'):
                    raise ApiError('Событие авторизации без timestamp')
                at = api_time_to_utc(row['timestamp'])
                if not start_utc <= at <= finish_utc:
                    continue
                client = row.get('client')
                if not isinstance(client, dict):
                    raise ApiError('Событие авторизации без данных клиента')
                number = phone_norm(client.get('phone'))
                if not number:
                    continue  # Нечего сопоставлять с phone_tg_links.
                uid = client.get('uuid')
                if not uid:
                    raise ApiError('Событие авторизации с номером без UUID')
                events.append({'company_id': cid, 'timestamp': at, 'phone': number,
                               'uuid': str(uid), 'nickname': client.get('nickname') or ''})
            if page >= last_page:
                return events
            if not rows:
                raise ApiError('Пустая промежуточная страница EventList')
            page += 1

    async def payments_by_company(self, cid: int, uuid: str) -> list:
        page, found = 1, []
        while page <= MAX_HISTORY_PAGES:
            data = await self.query(cid, PAYMENTS, {'uuid': uuid, 'page': page, 'first': 100})
            wrap = data.get('getPaymentsByClientId')
            if wrap is None:
                raise ApiError(f'История платежей недоступна (company_id={cid})')
            found.extend(wrap.get('data') or [])
            last = int((wrap.get('paginatorInfo') or {}).get('lastPage') or 1)
            if last > MAX_HISTORY_PAGES:
                raise ApiError(f'История платежей: {last} страниц на {cid}, превышен лимит')
            if page >= last:
                return found
            page += 1
        raise ApiError('Неполная история платежей')

    async def first_payment_at_company(self, cid: int, uuid: str, since: Any) -> Optional[dict]:
        """Все страницы истории через Bearer точки первой авторизации.

        Это соответствует ТЗ, но саму company-scoped семантику API необходимо
        подтвердить на тестовом аккаунте до включения реальных начислений.
        """
        payments = await self.payments_by_company(cid, uuid)
        return first_payment_on_fixed_point(payments, since)

    async def user(self, cid: int, uuid: str, *, prepaid=False) -> dict:
        data = await self.query(cid, USER, {'uuid': uuid}, category='credit', prepaid=prepaid)
        user = data.get('user')
        if not user or str(user.get('uuid')) != str(uuid):
            raise ApiError('SmartShell user(uuid) не вернул ожидаемого клиента')
        if user.get('bonus') is None:
            raise ApiError('SmartShell не сообщил текущий баланс bonus')
        user['bonus'] = amount_dec(user['bonus'])
        return user

    async def set_bonus(self, cid: int, uuid: str, target: Decimal, *, prepaid=False) -> dict:
        data = await self.query(cid, SET_BONUS,
                                {'input': {'client_uuid': uuid, 'value': float(target)}}, mutation=True, category='credit', prepaid=prepaid)
        result = data.get('setBonus')
        if not result or str(result.get('uuid')) != str(uuid) or result.get('bonus') is None:
            raise ApiError('Ответ setBonus не подтверждает UUID/баланс')
        actual = amount_dec(result['bonus'])
        if actual.quantize(Decimal('0.01')) != target.quantize(Decimal('0.01')):
            raise ApiError(f'Ответ setBonus показывает {actual} вместо {target}')
        return result


CREATE_STATE = '''
CREATE TABLE IF NOT EXISTS ReferralMonitorState (
  referral_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  stage VARCHAR(32) NOT NULL,
  stage_started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  invitee_auth_at DATETIME NULL,
  invitee_uuid VARCHAR(80) NULL,
  invitee_nick VARCHAR(255) NULL,
  invitee_auth_company_id INT NULL,
  payment_at DATETIME NULL,
  payment_key VARCHAR(64) NULL,
  payment_company_id INT NULL,
  payment_point VARCHAR(255) NULL,
  mentor_auth_at DATETIME NULL,
  mentor_uuid VARCHAR(80) NULL,
  mentor_nick VARCHAR(255) NULL,
  mentor_company_id INT NULL,
  bonus_amount DECIMAL(14,2) NULL,
  bonus_before DECIMAL(14,2) NULL,
  bonus_target DECIMAL(14,2) NULL,
  last_error VARCHAR(500) NULL,
  next_check_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  alert_sent TINYINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_next_check (stage, next_check_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
'''

CREATE_EVENT_CURSOR = '''
CREATE TABLE IF NOT EXISTS ReferralEventCursor (
    company_id INT NOT NULL PRIMARY KEY,
    cursor_utc DATETIME NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
'''

CREATE_EVENT_INBOX = '''
CREATE TABLE IF NOT EXISTS ReferralAuthEvents (
    event_key CHAR(64) NOT NULL PRIMARY KEY,
    company_id INT NOT NULL,
    event_at_utc DATETIME NOT NULL,
    phone VARCHAR(24) NOT NULL,
    client_uuid VARCHAR(80) NOT NULL,
    nickname VARCHAR(255) NULL,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    KEY idx_auth_phone_time (phone, event_at_utc),
    KEY idx_auth_event_time (event_at_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
'''


def event_identity(e: dict) -> str:
    parts = [str(e['company_id']), sql_time(e['timestamp']), e['phone'], e['uuid']]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def event_floor_utc(stage: str, row: dict) -> datetime:
    if stage == STAGE_NEW:
        return api_time_to_utc(db_created_to_api(row['created_at']))
    if stage == STAGE_PAYMENT and row.get('invitee_auth_at'):
        return api_time_to_utc(row['invitee_auth_at'])
    # Для WAIT_MENTOR_AUTH берём время платежа, сохранённое UTC-naive.
    if stage == STAGE_MENTOR:
        return smart_time(row['stage_started_at'])
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def bootstrap_event_cursors():
    """При первом запуске восстанавливаем окно от самой старой активной записи.

    Если есть записи WAIT_PAYMENT, также захватываем их период: наставник
    мог успеть авторизоваться до обнаружения первого платежа.
    """
    rows = await db_fetch("""SELECT r.created_at, s.stage, s.stage_started_at,
        s.invitee_auth_at FROM ReferralMonitorState s
        JOIN Referals r ON r.id=s.referral_id
        WHERE r.used=0 AND s.stage IN
          ('WAIT_INVITEE_AUTH','WAIT_PAYMENT','WAIT_MENTOR_AUTH')""")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    floor = min((event_floor_utc(r['stage'], r) for r in rows),
                default=now - timedelta(seconds=EVENT_OVERLAP_SECONDS))
    floor -= timedelta(seconds=EVENT_OVERLAP_SECONDS)
    for cid in WAREHOUSES.values():
        await db_write('''INSERT IGNORE INTO ReferralEventCursor(company_id, cursor_utc)
            VALUES (%s,%s)''', (cid, floor))
    log.info('EventList: инициализация курсоров завершена, нижняя граница %s UTC', floor)


async def store_events_and_cursor(cid: int, events: list, upper: datetime):
    """Атомарно: события записаны в inbox, только затем можно сдвигать курсор."""
    assert pool is not None
    unique = {event_identity(e): e for e in events}
    async with pool.acquire() as conn:
        try:
            await conn.begin()
            async with conn.cursor() as cur:
                if unique:
                    await cur.executemany('''INSERT IGNORE INTO ReferralAuthEvents
                        (event_key, company_id, event_at_utc, phone, client_uuid, nickname)
                        VALUES (%s,%s,%s,%s,%s,%s)''', [
                        (key, e['company_id'], e['timestamp'], e['phone'], e['uuid'], e['nickname'])
                        for key, e in unique.items()])
                await cur.execute('''UPDATE ReferralEventCursor
                    SET cursor_utc=%s WHERE company_id=%s AND cursor_utc<=%s''',
                    (upper, cid, upper))
                if cur.rowcount == 0:
                    await cur.execute('SELECT company_id FROM ReferralEventCursor WHERE company_id=%s', (cid,))
                    if not await cur.fetchone():
                        raise RuntimeError('Не обнаружен курсор филиала')
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
    log.info('EventList: точка %s, окно до %s UTC, авторизаций %d, новых уникальных <=%d',
             CID_TO_POINT.get(cid, cid), upper.strftime('%H:%M:%S'), len(events), len(unique))


async def get_auth_waiters() -> list:
    return await db_fetch("""SELECT r.id, r.created_at, r.tg_id, r.tg_referal,
        p1.phone AS mentor_phone, p2.phone AS invitee_phone,
        s.stage, s.stage_started_at
        FROM ReferralMonitorState s JOIN Referals r ON r.id=s.referral_id
        LEFT JOIN phone_tg_links p1 ON p1.tg_id=r.tg_id
        LEFT JOIN phone_tg_links p2 ON p2.tg_id=r.tg_referal
        WHERE r.used=0 AND s.stage IN ('WAIT_INVITEE_AUTH','WAIT_MENTOR_AUTH')""")


async def process_auth_matches(watermark: datetime):
    """Сопоставление ВСЕХ ожидающих номеров с общим журналом событий.

    Watermark=min(cursor всех точек): нельзя выбирать «первую» авторизацию,
    пока хотя бы один филиал не прочитан до соответствующего времени.
    """
    rows = await get_auth_waiters()
    waiters = defaultdict(list)
    earliest = None
    for r in rows:
        stage = r['stage']
        number = phone_norm(r.get('invitee_phone') if stage == STAGE_NEW else r.get('mentor_phone'))
        if not number:
            await set_delay(int(r['id']), stage, 'Нет номера для поиска в EventList', 300)
            continue
        floor = event_floor_utc(stage, r)
        waiters[number].append((r, floor))
        earliest = min(earliest, floor) if earliest else floor
    if not waiters or earliest is None or earliest > watermark:
        return
    matches = defaultdict(list)
    numbers = list(waiters)
    for offset in range(0, len(numbers), 350):
        batch = numbers[offset:offset + 350]
        marks = ','.join(['%s'] * len(batch))
        query = ('''SELECT company_id, event_at_utc, phone, client_uuid, nickname
                FROM ReferralAuthEvents WHERE phone IN (''' + marks + ''')
                AND event_at_utc >= %s AND event_at_utc <= %s
                ORDER BY event_at_utc, company_id''')
        for e in await db_fetch(query, (*batch, earliest, watermark)):
            for r, floor in waiters.get(e['phone'], []):
                if e['event_at_utc'] >= floor:
                    matches[int(r['id'])].append(e)
    for r in rows:
        rid = int(r['id'])
        candidates = matches.get(rid)
        if not candidates:
            continue
        first = candidates[0]
        tied = [e for e in candidates if e['event_at_utc'] == first['event_at_utc']]
        if len({int(e['company_id']) for e in tied}) > 1:
            await transition(rid, r['stage'], STAGE_REVIEW, {
                'last_error': 'Одинаковое время первой авторизации на нескольких точках'})
            continue
        at = first['event_at_utc'].replace(tzinfo=timezone.utc).astimezone(API_TZ).strftime('%Y-%m-%d %H:%M:%S')
        cid, uid = int(first['company_id']), first['client_uuid']
        if r['stage'] == STAGE_NEW:
            ok = await transition(rid, STAGE_NEW, STAGE_PAYMENT, {
                'invitee_auth_at': at, 'invitee_uuid': uid,
                'invitee_auth_company_id': cid, 'invitee_nick': first['nickname'],
                'stage_started_at': first['event_at_utc'],
                'next_check_at': datetime.now(timezone.utc).replace(tzinfo=None),
                'last_error': None}, referal_values={
                    'uuid_referal': uid, 'point': CID_TO_POINT[cid]})
            if ok:
                log.info('MATCH # %s: приглашённый на %s => WAIT_PAYMENT', rid, CID_TO_POINT[cid])
        elif r['stage'] == STAGE_MENTOR:
            ok = await transition(rid, STAGE_MENTOR, STAGE_BALANCE, {
                'mentor_auth_at': at, 'mentor_uuid': uid,
                'mentor_company_id': cid, 'mentor_nick': first['nickname'],
                'stage_started_at': first['event_at_utc'],
                'next_check_at': datetime.now(timezone.utc).replace(tzinfo=None),
                'last_error': None}, referal_values={'uuid_referer': uid})
            if ok:
                log.info('MATCH # %s: наставник на %s => WAIT_BALANCE', rid, CID_TO_POINT[cid])


pool: Optional[aiomysql.Pool] = None
sub_bot: Optional[Bot] = None
alert_bot: Optional[Bot] = None

async def db_fetch(sql: str, params=()) -> list:
    assert pool is not None
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()

async def db_write(sql: str, params=()) -> None:
    assert pool is not None
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)

# Поля фиксированы внутренним кодом; SQL-идентификаторы не принимаются от пользователей.
STATE_COLUMNS = {'stage_started_at', 'invitee_auth_at', 'invitee_uuid', 'invitee_nick',
                 'invitee_auth_company_id', 'payment_at', 'payment_key', 'payment_company_id',
                 'payment_point', 'mentor_auth_at', 'mentor_uuid', 'mentor_nick', 'mentor_company_id',
                 'bonus_amount', 'bonus_before', 'bonus_target', 'last_error', 'next_check_at'}

async def transition(rid: int, expected: str, to: str, values: Optional[dict] = None,
                     flags: Optional[Tuple[int,int]] = None, referal_values: Optional[dict] = None) -> bool:
    """Атомарно обновляет состояние + существующие флаги Referals."""
    assert pool is not None
    values = dict(values or {})
    assert set(values) <= STATE_COLUMNS
    extra = ', '.join(f'`{k}`=%s' for k in values)
    sql = f'UPDATE ReferralMonitorState SET stage=%s' + (', ' + extra if extra else '') + \
          ' WHERE referral_id=%s AND stage=%s'
    sql_params = (to, *values.values(), rid, expected)
    referal_values = dict(referal_values or {})
    assert set(referal_values) <= {'uuid_referal', 'point', 'uuid_referer'}
    if flags is not None:
        referal_values.update({'used': flags[0], 'value': flags[1]})
    async with pool.acquire() as conn:
        try:
            await conn.begin()
            async with conn.cursor() as cur:
                await cur.execute(sql, sql_params)
                if cur.rowcount != 1:
                    await conn.rollback()
                    return False
                if referal_values:
                    changes = ', '.join(f'`{k}`=%s' for k in referal_values)
                    await cur.execute(f'UPDATE Referals SET {changes} WHERE id=%s',
                                      (*referal_values.values(), rid))
                    if cur.rowcount == 0:
                        # MySQL может вернуть 0 если значения не изменились;
                        # проверяем существование вместо ошибочного rollback.
                        await cur.execute('SELECT id FROM Referals WHERE id=%s', (rid,))
                        if not await cur.fetchone():
                            raise RuntimeError('Строка Referals удалена')
            await conn.commit()
            return True
        except Exception:
            await conn.rollback()
            raise


async def init_db() -> None:
    global pool
    args = {k: os.getenv(k) for k in ['DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME']}
    if not all(args.values()):
        raise RuntimeError('Не заполнены DB_HOST/DB_USER/DB_PASSWORD/DB_NAME')
    pool = await aiomysql.create_pool(host=args['DB_HOST'], user=args['DB_USER'],
                                     password=args['DB_PASSWORD'], db=args['DB_NAME'],
                                     port=int(os.getenv('DB_PORT', '3306')), charset='utf8mb4',
                                     minsize=2, maxsize=10, autocommit=True, pool_recycle=3600,
                                     init_command="SET time_zone = '+00:00'")
    # Не создаём/не меняем таблицу Referals. Сопровождающую таблицу создаём отдельно.
    await db_write(CREATE_STATE)
    await db_write(CREATE_EVENT_CURSOR)
    await db_write(CREATE_EVENT_INBOX)
    await db_write('''INSERT INTO ReferralMonitorState(referral_id, stage)
        SELECT r.id, CASE
            WHEN r.value=1 OR r.uuid_referal IS NOT NULL THEN 'NEEDS_REVIEW'
            ELSE 'WAIT_INVITEE_AUTH' END
        FROM Referals r WHERE r.used=0
        ON DUPLICATE KEY UPDATE referral_id=ReferralMonitorState.referral_id''')
    # Не переотправлять мутацию если процесс умер посередине ответа/DB commit.
    await bootstrap_event_cursors()
    await db_write("UPDATE ReferralMonitorState SET stage='NEEDS_REVIEW', "
                   "last_error='Процесс перезапущен на этапе отправки SetBonus; нужна сверка с балансом' "
                   "WHERE stage='APPLYING'")


async def is_subscribed(tg_id: int) -> bool:
    if sub_bot is None or not SUB_CHANNEL:
        raise ApiError('BOT_TOKEN/CHANNEL_ID отсутствуют')
    user = await sub_bot.get_chat_member(int(SUB_CHANNEL), tg_id)
    return user.status in ('member', 'creator', 'administrator', 'restricted')


async def set_delay(rid: int, stage: str, err: str, seconds: int = 60):
    log.warning('Реферал %s [%s]: %s', rid, stage, err)
    await db_write('''UPDATE ReferralMonitorState
        SET next_check_at=DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND), last_error=%s
        WHERE referral_id=%s AND stage=%s''', (seconds, err[:500], rid, stage))


async def get_referrals(stage: str, limit: int = 16) -> list:
    # Данные берём из существующих таблиц; ограничение по размеру выборки.
    return await db_fetch('''
        SELECT r.id, r.tg_id, r.tg_referal, r.created_at, r.point, r.uuid_referal,
          p1.phone AS mentor_phone, p2.phone AS invitee_phone,
          s.*
        FROM ReferralMonitorState s JOIN Referals r ON r.id=s.referral_id
        LEFT JOIN phone_tg_links p1 ON p1.tg_id=r.tg_id
        LEFT JOIN phone_tg_links p2 ON p2.tg_id=r.tg_referal
        WHERE r.used=0 AND s.stage=%s
          AND s.next_check_at<=UTC_TIMESTAMP()
        ORDER BY s.next_check_at, s.referral_id LIMIT %s
    ''', (stage, limit))

async def send_alert(text: str):
    if alert_bot is None or not ALERT_CHAT:
        raise ApiError('TELEGRAM_TOKEN/REPORT_CHAT_ID отсутствуют')
    kwargs = {'chat_id': int(ALERT_CHAT), 'text': text, 'parse_mode': 'HTML'}
    if ALERT_THREAD:
        kwargs['message_thread_id'] = int(ALERT_THREAD)
    await alert_bot.send_message(**kwargs)


def notification(r: dict) -> str:
    def safe(x):
        return html.escape(str(x if x is not None else '—'))
    cid = r.get('mentor_company_id')
    mentor_point = CID_TO_POINT.get(int(cid), str(cid)) if cid is not None else '—'
    bonus = r.get('bonus_amount') or 0
    return (f'🎁 <b>Реферальный бонус начислен</b>\n'
            f'🆔 Запись #{safe(r["referral_id"])}\n\n'
            f'<b>Пригласивший:</b>\n'
            f'📱 <code>{safe(r.get("mentor_phone"))}</code>\n'
            f'🎮 Ник: {safe(r.get("mentor_nick"))}\n'
            f'📍 Точка авторизации: {safe(mentor_point)}\n'
            f'💰 Получил: <b>+{safe(bonus)} бонусов</b>\n\n'
            f'<b>Приглашённый:</b>\n'
            f'📱 <code>{safe(r.get("invitee_phone"))}</code>\n'
            f'🎮 Ник: {safe(r.get("invitee_nick"))}\n'
            f'📍 Точка первого платежа: {safe(r.get("payment_point"))}')


async def send_pending_alerts():
    rows = await db_fetch('''SELECT s.*, p1.phone AS mentor_phone,
        p2.phone AS invitee_phone FROM ReferralMonitorState s
        JOIN Referals r ON r.id=s.referral_id
        LEFT JOIN phone_tg_links p1 ON p1.tg_id=r.tg_id
        LEFT JOIN phone_tg_links p2 ON p2.tg_id=r.tg_referal
        WHERE s.stage='DONE' AND s.alert_sent=0 LIMIT 20''')
    for r in rows:
        try:
            await send_alert(notification(r))
            await db_write('UPDATE ReferralMonitorState SET alert_sent=1 WHERE referral_id=%s',
                           (r['referral_id'],))
        except Exception as e:
            log.warning('Не удалось отправить алерт по рефералу %s: %s', r['referral_id'], e)


class ReferralMonitor:
    def __init__(self, api: Optional[SmartShell] = None):
        self.api = api or SmartShell()
        self.limiter = asyncio.Semaphore(WORKER_CONCURRENCY)
        self.running = True
        self.mentor_locks: Dict[str, asyncio.Lock] = {}

    async def process(self, row: dict):
        async with self.limiter:
            rid, stage = int(row['id']), row['stage']
            try:
                if stage == STAGE_PAYMENT:
                    await self.payment(row)
                elif stage == STAGE_BALANCE:
                    await self.credit(row)
            except AmbiguousPaymentError as exc:
                await transition(rid, stage, STAGE_REVIEW, {'last_error': str(exc)[:500]})
            except ApiError as exc:
                # Не путать отказ SmartShell с отсутствием клиента/платежа.
                await set_delay(rid, stage, str(exc), 60)
            except Exception as exc:
                log.exception('Неожиданная ошибка для реферала %s', rid)
                await set_delay(rid, stage, f'{type(exc).__name__}: {exc}', 60)

    async def payment(self, r: dict):
        rid = int(r['id'])
        uuid = r['invitee_uuid']
        auth_at = r['invitee_auth_at']
        if not uuid or not auth_at:
            await transition(rid, STAGE_PAYMENT, STAGE_REVIEW,
                             {'last_error': 'Отсутствует UUID или дата авторизации приглашённого'})
            return
        # Филиал закреплён именно ПЕРВОЙ авторизацией приглашённого.
        cid = r.get('invitee_auth_company_id')
        if cid is None or int(cid) not in CID_TO_POINT:
            await transition(rid, STAGE_PAYMENT, STAGE_REVIEW,
                             {'last_error': 'Не сохранён корректный company_id первой авторизации'})
            return
        cid = int(cid)
        point = CID_TO_POINT[cid]
        payment = await self.api.first_payment_at_company(cid, uuid, auth_at)
        if not payment:
            await set_delay(rid, STAGE_PAYMENT,
                            f'Ожидание первого депозитного пополнения на {point}', PAYMENT_RECHECK_SECONDS)
            return
        amount = payment['sum']
        if amount < amount_dec(MIN_TOPUP_AMOUNT):
            await transition(rid, STAGE_PAYMENT, STAGE_CLOSED,
                             {'payment_at': payment['at'], 'payment_key': payment['key'],
                              'payment_company_id': cid, 'payment_point': point,
                              'last_error': f'Первый платёж {amount} ниже порога {MIN_TOPUP_AMOUNT}'}, flags=(1,0))
            return
        try:
            ok_invitee, ok_mentor = await asyncio.gather(
                is_subscribed(int(r['tg_referal'])), is_subscribed(int(r['tg_id'])))
        except Exception as e:
            raise ApiError(f'Ошибка Telegram проверки подписки: {e}') from e
        if not (ok_invitee and ok_mentor):
            await transition(rid, STAGE_PAYMENT, STAGE_CLOSED,
                             {'last_error': 'Кто-то из участников не подписан на канал'}, flags=(1,0))
            return
        bonus = BONUS_PREMIUM if point in PREMIUM_WAREHOUSES else BONUS_STANDARD
        # Для рефералки этот филиал закреплён с первой авторизации.
        await transition(rid, STAGE_PAYMENT, STAGE_MENTOR,
                         {'payment_at': payment['at'], 'payment_key': payment['key'],
                          'payment_company_id': cid, 'payment_point': point,
                          'bonus_amount': amount_dec(bonus), 'stage_started_at': api_time_to_utc(payment['at']),
                          'next_check_at': datetime.now(timezone.utc).replace(tzinfo=None), 'last_error': None},
                         flags=(0,1), referal_values={'point': point})

    async def credit(self, r: dict):
        # Один mentor может иметь несколько рефералов в очереди.
        # Исключаем параллельный read/modify/write его бонусного баланса.
        key = str(r.get('tg_id') or r.get('mentor_uuid') or r['id'])
        async with self.mentor_locks.setdefault(key, asyncio.Lock()):
            await self._credit_locked(r)

    async def _credit_locked(self, r: dict):
        rid = int(r['id'])
        cid, uuid = r.get('mentor_company_id'), r.get('mentor_uuid')
        if cid is None or not uuid:
            await transition(rid, STAGE_BALANCE, STAGE_REVIEW,
                             {'last_error': 'Нет UUID/company_id авторизовавшегося наставника'})
            return
        # user(uuid) привязан к точному UUID. Не используем clients(q=phone)[0]
        # и не подставляем 0 при ошибке.
        # Резервируем ОБА запроса перед чтением баланса: SetBonus не должен
        # стоять минуту в очереди после получения старого баланса.
        await self.api.rate.acquire(int(cid), 'credit', cost=2)
        try:
            user = await self.api.user(int(cid), uuid, prepaid=True)
        except Exception as e:
            await set_delay(rid, STAGE_BALANCE, f'Баланс не получен, повтор позже: {e}', 30)
            return
        before = user['bonus']
        bonus = amount_dec(r.get('bonus_amount'))
        target = before + bonus
        if bonus <= 0:
            await transition(rid, STAGE_BALANCE, STAGE_REVIEW,
                             {'last_error': 'Некорректная величина бонуса'})
            return
        if REFERRAL_DRY_RUN:
            log.info('[DRY_RUN] Реферал %s: баланс %s + %s = %s, SetBonus НЕ отправлен',
                     rid, before, bonus, target)
            await set_delay(rid, STAGE_BALANCE, 'DRY_RUN: начисление не выполнялось', 60)
            return
        # СНАЧАЛА фиксируем намерение во внешне устойчивой БД.
        if not await transition(rid, STAGE_BALANCE, STAGE_APPLYING,
                                {'bonus_before': before, 'bonus_target': target,
                                 'mentor_nick': user.get('nickname') or r.get('mentor_nick'),
                                 'last_error': None}):
            return
        try:
            await self.api.set_bonus(int(cid), uuid, target, prepaid=True)
        except Exception as e:
            # После неизвестного исхода не повторяем SetBonus: он мог выполниться.
            await transition(rid, STAGE_APPLYING, STAGE_REVIEW,
                             {'last_error': f'Результат SetBonus требует сверки: {e}'})
            log.error('РЕФЕРАЛ %s: НЕОПРЕДЕЛЁННЫЙ ИСХОД SetBonus! %s', rid, e)
            return
        await transition(rid, STAGE_APPLYING, STAGE_DONE,
                         {'last_error': None}, flags=(1,1))
        log.info('Реферал %s: начислено +%s на %s', rid, bonus, CID_TO_POINT.get(int(cid)))
        # Алерт отправит send_pending_alerts (устойчиво к временной ошибке Telegram).

    async def expire(self):
        # В WAIT_BALANCE операцию не закрываем по истечении 30 дней — пользователь
        # запросил повторять чтение бонусного баланса до успеха.
        expired = await db_fetch('''SELECT referral_id, stage FROM ReferralMonitorState
            WHERE stage IN ('WAIT_INVITEE_AUTH','WAIT_PAYMENT','WAIT_MENTOR_AUTH')
              AND stage_started_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
            LIMIT 100''', (int(REFERAL_TIMEOUT_DAYS),))
        for r in expired:
            await transition(r['referral_id'], r['stage'], STAGE_CLOSED,
                             {'last_error': 'Истёк срок участия в реферальной программе'}, flags=(1,0))

    async def event_collector_loop(self):
        """Шесть филиалов раз в 40 секунд; один сборщик на все ожидающие номера."""
        while self.running:
            started = time.monotonic()
            try:
                # Регистрация новых Referals без перезапуска.
                await db_write('''INSERT INTO ReferralMonitorState(referral_id, stage)
                    SELECT r.id, CASE WHEN r.value=1 OR r.uuid_referal IS NOT NULL
                        THEN 'NEEDS_REVIEW' ELSE 'WAIT_INVITEE_AUTH' END
                    FROM Referals r WHERE r.used=0
                    ON DUPLICATE KEY UPDATE referral_id=ReferralMonitorState.referral_id''')
                cursors = await db_fetch('SELECT company_id, cursor_utc FROM ReferralEventCursor')
                checkpoint = {int(r['company_id']): r['cursor_utc'] for r in cursors}
                finish = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=EVENT_SETTLE_SECONDS)

                async def read_one(cid):
                    if cid not in checkpoint:
                        raise ApiError(f'Отсутствует курсор точки {cid}')
                    cursor = checkpoint[cid]
                    if cursor >= finish:
                        return
                    end = min(finish, cursor + timedelta(seconds=EVENT_BACKFILL_WINDOW_SECONDS))
                    start = cursor - timedelta(seconds=EVENT_OVERLAP_SECONDS)
                    # При большой накопившейся истории автоматически уменьшаем
                    # временное окно; страницу 2 читаем только если она нужна.
                    while True:
                        try:
                            found = await self.api.event_window(cid, start, end)
                            break
                        except TooManyEventsError:
                            delta = (end - cursor).total_seconds()
                            if delta <= 40:
                                raise
                            end = cursor + timedelta(seconds=max(40, int(delta // 2)))
                            log.warning('EventList %s: слишком много страниц, '
                                        'сужаем окно до %s UTC', CID_TO_POINT.get(cid), end)
                    await store_events_and_cursor(cid, found, end)

                results = await asyncio.gather(*(read_one(cid) for cid in WAREHOUSES.values()),
                                               return_exceptions=True)
                for cid, result in zip(WAREHOUSES.values(), results):
                    if isinstance(result, BaseException):
                        log.warning('EventList филиал %s: %s (курсор НЕ сдвинут)',
                                    CID_TO_POINT.get(cid, cid), result)
                new_cursors = await db_fetch('SELECT cursor_utc FROM ReferralEventCursor')
                if len(new_cursors) == len(WAREHOUSES):
                    watermark = min(r['cursor_utc'] for r in new_cursors)
                    await process_auth_matches(watermark)
                else:
                    log.error('Неполный набор EventList-курсоров, сопоставление приостановлено')
            except Exception:
                log.exception('Ошибка общего сборщика EventList')
            await asyncio.sleep(max(0, EVENT_POLL_SECONDS - (time.monotonic() - started)))

    async def payment_loop(self):
        while self.running:
            try:
                rows = await get_referrals(STAGE_PAYMENT, 16)
                if rows:
                    await asyncio.gather(*(self.process(r) for r in rows))
            except Exception:
                log.exception('Ошибка очереди WAIT_PAYMENT')
            await asyncio.sleep(POLL_SECONDS)

    async def bonus_loop(self):
        # Отдельная очередь: насыщенные платежи не блокируют начисления.
        while self.running:
            try:
                rows = await get_referrals(STAGE_BALANCE, 8)
                if rows:
                    await asyncio.gather(*(self.process(r) for r in rows))
            except Exception:
                log.exception('Ошибка очереди WAIT_BALANCE')
            await asyncio.sleep(5)

    async def housekeeping_loop(self):
        while self.running:
            try:
                await self.expire()
                await send_pending_alerts()
            except Exception:
                log.exception('Ошибка обслуживания очередей')
            await asyncio.sleep(20)

    async def run(self):
        assert pool is not None
        # Не запускаем два экземпляра одного конвейера на одну базу.
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute('SELECT GET_LOCK(%s, 0)', ('cybermg_friendmon_v2',))
                record = await cur.fetchone()
                if not record or record[0] != 1:
                    raise RuntimeError('Уже запущен другой экземпляр friendMon')
            try:
                log.info('friendMon v3: EventList=%s сек, HTTP-квоты=%s/мин/точка, DRY_RUN=%s',
                         EVENT_POLL_SECONDS, API_RATE_LIMITS, REFERRAL_DRY_RUN)
                await asyncio.gather(self.event_collector_loop(), self.payment_loop(),
                                     self.bonus_loop(), self.housekeeping_loop())
            finally:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT RELEASE_LOCK(%s)', ('cybermg_friendmon_v2',))


async def main():
    global sub_bot, alert_bot
    if not WAREHOUSES:
        raise RuntimeError('SMARTSHELL_WAREHOUSES не настроен в .env')
    if SUB_TOKEN:
        sub_bot = Bot(SUB_TOKEN)
    if ALERT_TOKEN:
        alert_bot = Bot(ALERT_TOKEN)
    api = SmartShell()
    refresh_task = None
    try:
        await init_db()
        refresh_task = asyncio.create_task(api.proactively_refresh_tokens(), name='smartshell-bearer-refresh')
        await ReferralMonitor(api).run()
    finally:
        if refresh_task:
            refresh_task.cancel()
            await asyncio.gather(refresh_task, return_exceptions=True)
        await api.close()
        if sub_bot:
            await sub_bot.session.close()
        if alert_bot:
            await alert_bot.session.close()
        if pool:
            pool.close()
            await pool.wait_closed()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info('Монитор остановлен')
