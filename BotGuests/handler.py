"""
Модуль хендлеров Telegram бота CyberMG v4.
+ Реферальная система "Приведи друга"
+ Проверка новичков через Smartshell API
"""
import logging
import asyncio
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import connectAPI
from connectAPI import check_client_exists_on_any_warehouse
from optionDB import (
    check_tg_id_exists, get_phone_by_tgid, save_phone_tg_link,
    check_sword_tg_exists, save_sword_phone, check_sword_secret_status,
    set_sword_secret_success,
    # 🆕 РЕФЕРАЛЬНАЯ СИСТЕМА:
    save_referal, get_referals_count, get_referrals_page, update_telegram_username, update_state_sub,
)
from normalizePhone import format_phone_pretty
from config import CHANNEL_ID, SECRET_WORD, REFERAL_FEATURE_ENABLED
import functionFormat as fmt

logger = logging.getLogger(__name__)
router = Router()


# ==========================================================
# === FSM СОСТОЯНИЯ ДЛЯ ФЕСТИВАЛЯ ===
# ==========================================================

class FestivalStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_secret = State()


# ==========================================================
# === 🚫 БЛОКИРОВКА ГРУППОВЫХ СООБЩЕНИЙ ===
# ==========================================================

@router.message(F.chat.type != "private")
async def block_group_messages(message: Message):
    try:
        await message.answer(
            "🤫 <b>Эмджей работает только в личных сообщениях!</b>\n\n"
            "Напиши мне напрямую, чтобы защитить свои данные.",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ==========================================================
# === 🚀 /start (С ПАРСИНГОМ DEEP LINK) ===
# ==========================================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    tg_id = message.from_user.id
    
    # 🆕 ПАРСИМ DEEP LINK (например: /start 123456789)
    # Если ссылка "чистая" (без параметров) — args будет None, рефералка не сработает
    referrer_tg_id = None
    if message.text and ' ' in message.text:
        args = message.text.split(' ', 1)[1]
        if args.isdigit():
            referrer_tg_id = int(args)
            if referrer_tg_id == tg_id:
                referrer_tg_id = None  # Сам себя пригласить нельзя
    if referrer_tg_id:
        await state.update_data(referrer_tg_id=referrer_tg_id)
        logger.info(f"🔗 Новый юзер {tg_id} пришёл по реф.ссылке от {referrer_tg_id}")
    
    is_known = await check_tg_id_exists(tg_id)
    if is_known and referrer_tg_id and REFERAL_FEATURE_ENABLED:
        known_phone = await get_phone_by_tgid(tg_id)
        if known_phone:
            await _register_referral_from_link(
                message, referrer_tg_id, tg_id, known_phone)
        else:
            request_contact = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True)
            await message.answer(
                "⚠️ Номер в CyberMJ не найден. Поделись контактом для участия.",
                reply_markup=request_contact)

    if is_known:
        await update_telegram_username(tg_id, message.from_user.username)
        raw_phone = await get_phone_by_tgid(tg_id)
        pretty = format_phone_pretty(raw_phone) if raw_phone else "—"
        text = fmt.get_returning_user_message(pretty)

        is_subscribed = await _real_check_sub(tg_id)
        await update_state_sub(tg_id, is_subscribed)  # 🆕 обновляем stateSub

        if is_subscribed:
            await message.answer(
                fmt.get_subscription_success_message(),
                reply_markup=fmt.get_main_menu_keyboard(),
                parse_mode="HTML"
            )
            logger.info(f"🟢 Вернувшийся {tg_id} подписан -> хаб")
        else:
            builder = InlineKeyboardBuilder()
            builder.button(text="📢 Подписаться на наш канал", url=fmt.get_channel_url())
            builder.button(text="✅ Проверить подписку", callback_data="check_sub")
            builder.adjust(1)
            await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
            logger.info(f"🟡 Вернувшийся {tg_id} НЕ подписан")
    else:
        text = fmt.get_welcome_message()
        builder = InlineKeyboardBuilder()
        builder.button(text="📱 Поделиться номером", callback_data="share_phone")
        builder.adjust(1)
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        logger.info(f"🆕 Новый юзер {tg_id}")


# ==========================================================
# === 📱 ТЕЛЕФОН (ОСНОВНОЙ + РЕФЕРАЛКА) ===
# ==========================================================

@router.callback_query(StateFilter(None), F.data == "share_phone")
async def ask_phone(callback: CallbackQuery):
    tg_id = callback.from_user.id
    is_known = await check_tg_id_exists(tg_id)
    prefix = "🔄 <b>Смена номера:</b>\n\n" if is_known else ""

    phone_btn = KeyboardButton(text="📱 Поделиться номером", request_contact=True)
    kb = ReplyKeyboardMarkup(keyboard=[[phone_btn]], resize_keyboard=True, one_time_keyboard=True)

    await callback.message.answer(
        f"{prefix}👇 Нажмите кнопку ниже:", reply_markup=kb, parse_mode="HTML"
    )
    await callback.answer()

@router.message(StateFilter(None), F.contact)
async def got_contact(message: Message, state: FSMContext):
    """Основная привязка телефона + логика рефералов (по ТЗ)."""
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer("⚠️ Номер принадлежит другому пользователю!")
        return

    phone = contact.phone_number
    tg_id = message.from_user.id

    data = await state.get_data()
    referrer_tg_id = data.get('referrer_tg_id')

    saved = await save_phone_tg_link(phone, tg_id)
    await update_telegram_username(tg_id, message.from_user.username)
    pretty = format_phone_pretty(phone)
    # Unified referral eligibility for new and returning CyberMJ users.
    if referrer_tg_id and REFERAL_FEATURE_ENABLED:
        verified_phone = await get_phone_by_tgid(tg_id)
        if verified_phone:
            await _register_referral_from_link(
                message, referrer_tg_id, tg_id, verified_phone)
        else:
            await message.answer(
                "⚠️ Телефон не сохранён в базе, приглашение не создано.")
        # Suppress legacy referral handling below, preserving all other flow.
        referrer_tg_id = None

    # 🆕 РЕФЕРАЛЬНАЯ ЛОГИКА ПО ТЗ
    if referrer_tg_id and saved and REFERAL_FEATURE_ENABLED:
        # Параллельная проверка на всех 6 точках через clients(q=phone)
        # Теперь возвращает список найденных клиентов
        found_clients = await connectAPI.check_client_exists_on_any_warehouse(phone)
        is_existing_client = len(found_clients) > 0

        if is_existing_client:
            # Старичок - грустный диалог с детальной информацией
            points_info = []
            for client in found_clients:
                points_info.append(
                    f"📍 <b>{client['point']}</b>\n"
                    f"   💰 Депозит: <code>{client['deposit']:.2f}₽</code>\n"
                    f"   🎁 Бонусы: <code>{client['bonus']:.2f}₽</code>"
                )
            
            points_text = "\n\n".join(points_info)
            
            await message.answer(
                f"😔 <b>Ой, кажется мы уже знакомы!</b>\n\n"
                f"Ты уже зарегистрирован в системе CYBERMG.\n\n"
                f"<b>Твои аккаунты в клубах:</b>\n\n{points_text}\n\n"
                f"Акция 'Приведи друга' действует только для новичков, "
                f"которых ещё не было ни в одном клубе нашей сети.\n\n"
                "Не переживайте(&gt;&lt;)\n"
                "У нас есть крутая акция на первые 3 пополнения!!! "
                "Вы можете получить до 3000 бонусов 🎁.\n\n"
                "Следите за нашими новостями в нашем сообществе, "
                "чтобы всегда быть в центре событий нашей вселенной!\n\n"
                "С любовью, CyberMG!",
                parse_mode="HTML"
            )
        else:
            # Новичок - крутой диалог + запись в Referals (val=0, used=0)
            await message.answer(
                "🔥 <b>Добро пожаловать в CYBERMG, новичок!</b>\n\n"
                "У тебя теперь есть <b>наставник</b>, который привел тебя! "
                "Ты можешь получить бонусы за пополнение и скрестить шпаги с другом ⚔️",
                parse_mode="HTML",
                reply_markup=fmt.get_referal_success_keyboard()
            )
            await save_referal(referrer_tg_id, tg_id)
            logger.info(f"🤝 НОВИЧОК: {tg_id} привязан к наставнику {referrer_tg_id}")

    elif referrer_tg_id and saved and not REFERAL_FEATURE_ENABLED:
        await message.answer('ℹ️ Реферальная программа пока недоступна.')

    elif referrer_tg_id and not saved:
        await message.answer(
            "ℹ️ <b>Ты уже с нами!</b>\n\n"
            "Реферальная программа доступна только для новых гостей, "
            "которых ещё нет в базе клубов.",
            parse_mode="HTML"
        )

    await state.clear()

    if saved:
        phone_msg = f"✅ <b>Номер привязан!</b>\n📱 <code>{pretty}</code>"
    else:
        phone_msg = f"ℹ️ <b>Номер обновлён/уже привязан</b>\n📱 <code>{pretty}</code>"

    await message.answer(phone_msg, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

    is_subscribed = await _real_check_sub(tg_id)

    if is_subscribed:
        await message.answer(
            fmt.get_subscription_success_message(),
            reply_markup=fmt.get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    else:
        text = (
            "Отлично, осталось совсем немного! 🚀\n"
            "Чтобы открыть все возможности бота и всегда быть в курсе новостей, "
            "акций и событий CYBERMG, подпишись на наш Telegram-канал."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="📢 Подписаться на наш канал", url=fmt.get_channel_url())
        builder.button(text="✅ Проверить подписку", callback_data="check_sub")
        builder.adjust(1)
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# ==========================================================
# === 🔍 ПРОВЕРКА ПОДПИСКИ ===
# ==========================================================

@router.callback_query(F.data == "check_sub")
async def process_check_sub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_subscribed = await _real_check_sub(callback.from_user.id)
    await update_state_sub(callback.from_user.id, is_subscribed)  # 🆕 обновляем stateSub

    if is_subscribed:
        await _safe_edit(
            callback.message,
            fmt.get_subscription_success_message(),
            fmt.get_main_menu_keyboard()
        )
    else:
        builder = InlineKeyboardBuilder()
        builder.button(text="🐾 Вступить в отряд", url=fmt.get_channel_url())
        builder.button(text="🔄 Проверить подписку", callback_data="check_sub")
        builder.button(text="🏠 Стартовое окно", callback_data="back_to_start")
        builder.adjust(1)
        await _safe_edit(
            callback.message,
            fmt.get_subscription_fail_message(),
            builder.as_markup()
        )
    await callback.answer()


# ==========================================================
# === 🏠 ГЛАВНОЕ МЕНЮ ===
# ==========================================================

@router.callback_query(F.data == "main_menu")
async def show_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit(
        callback.message,
        fmt.get_main_menu_text(),
        fmt.get_main_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    tg_id = callback.from_user.id
    raw_phone = await get_phone_by_tgid(tg_id)
    pretty = format_phone_pretty(raw_phone) if raw_phone else "—"

    text = fmt.get_returning_user_message(pretty)
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Наш канал", url=fmt.get_channel_url())
    builder.button(text="✅ Проверить подписку", callback_data="check_sub")
    builder.adjust(1)

    await _safe_edit(callback.message, text, builder.as_markup())
    await callback.answer()


# ==========================================================
# === 📩 ДИРЕКТОР ===
# ==========================================================

@router.callback_query(F.data == "director")
async def contact_director(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.get_director_text(),
        fmt.get_feedback_keyboard()
    )
    await callback.answer()


# ==========================================================
# === 🖥 БРОНИРОВАНИЕ ===
# ==========================================================

@router.callback_query(F.data == "booking")
async def booking_menu(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.get_booking_text(),
        fmt.get_booking_keyboard()
    )
    await callback.answer()


# ==========================================================
# === 🗺 НАВИГАЦИЯ ===
# ==========================================================

@router.callback_query(F.data == "nav:hub")
async def nav_hub(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.get_nav_hub_text(),
        fmt.get_nav_hub_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "nav:promo")
async def nav_promo(callback: CallbackQuery):
    image = fmt.get_promo_image()
    if image:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=image,
            caption=fmt.PROMO_TEXT,
            parse_mode="HTML",
            reply_markup=fmt.get_back_to_nav_keyboard()
        )
    else:
        await _safe_edit(
            callback.message,
            fmt.PROMO_TEXT,
            fmt.get_back_to_nav_keyboard()
        )
    await callback.answer()


@router.callback_query(F.data == "nav:pc_build")
async def nav_pc_build(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.get_pc_build_intro_text(),
        fmt.get_branch_selector_keyboard("pc_build")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pc_build:"))
async def pc_build_branch(callback: CallbackQuery):
    slug = callback.data.split(":", 1)[1]
    branch_name = fmt.BRANCH_NAMES.get(slug, slug)

    image = fmt.get_branch_image("pc_build", slug)
    text = fmt.get_pc_build_result_text(branch_name)
    kb = fmt.get_back_to_menu_keyboard()

    if image:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=image, caption=text, parse_mode="HTML", reply_markup=kb
        )
    else:
        await _safe_edit(callback.message, text + "\n\n<i>(Изображение в разработке)</i>", kb)
    await callback.answer()


@router.callback_query(F.data == "nav:price")
async def nav_price(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.get_price_intro_text(),
        fmt.get_branch_selector_keyboard("price")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("price:"))
async def price_branch(callback: CallbackQuery):
    slug = callback.data.split(":", 1)[1]
    branch_name = fmt.BRANCH_NAMES.get(slug, slug)

    image = fmt.get_branch_image("price", slug)
    text = fmt.get_price_result_text(branch_name)
    kb = fmt.get_back_to_menu_keyboard()

    if image:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=image, caption=text, parse_mode="HTML", reply_markup=kb
        )
    else:
        await _safe_edit(callback.message, text + "\n\n<i>(Изображение в разработке)</i>", kb)
    await callback.answer()


@router.callback_query(F.data == "nav:social")
async def nav_social(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.get_social_text(),
        fmt.get_social_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "nav:smartgamer")
async def nav_smartgamer(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.SMARTGAMER_TEXT,
        fmt.get_smartgamer_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "nav:review")
async def nav_review(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.get_review_intro_text(),
        fmt.get_review_branch_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("review_branch:"))
async def review_select_branch(callback: CallbackQuery):
    branch = callback.data.split(":", 1)[1]
    await _safe_edit(
        callback.message,
        fmt.get_review_aggregator_text(branch),
        fmt.get_review_aggregator_keyboard(branch)
    )
    await callback.answer()


@router.callback_query(F.data == "nav:about")
async def nav_about(callback: CallbackQuery):
    await _safe_edit(
        callback.message,
        fmt.get_about_intro_text(),
        fmt.get_about_branch_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("about:"))
async def about_branch(callback: CallbackQuery):
    """О клубах: фото без подписи + полное описание отдельно."""
    slug = callback.data.split(":", 1)[1]
    
    full_text = fmt.get_about_branch_text(slug)
    image = fmt.get_about_branch_image(slug)
    kb = fmt.get_back_to_menu_keyboard()

    try:
        await callback.message.delete()
    except Exception:
        pass

    if image:
        await callback.message.answer_photo(
            photo=image,
            parse_mode="HTML"
        )
    
    await callback.message.answer(
        full_text,
        parse_mode="HTML",
        reply_markup=kb
    )
    
    await callback.answer()


# ==========================================================
# === 🤝 РЕФЕРАЛЬНАЯ СИСТЕМА ===
# ==========================================================

@router.callback_query(F.data == "referals_dev")
async def referals_dev_placeholder(callback: CallbackQuery):
    """
    🚧 Заглушка: функция в разработке.
    Срабатывает когда REFERAL_FEATURE_ENABLED = false
    """
    await callback.answer(
        "🚧Функция находится в разработке!\n"
        "Скоро здесь появится возможность приглашать друзей "
        "и получать бонусы за их первое пополнение!\n\n"
        "Следи за новостями в нашем канале 📢",
        show_alert=True,
        cache_time=5
    )


@router.callback_query(F.data == "referals")
async def referals_menu(callback: CallbackQuery):
    """Реферальное меню, проверяем флаг даже для старых кнопок."""
    if not REFERAL_FEATURE_ENABLED:
        await callback.answer('Реферальная программа пока недоступна', show_alert=True)
        return
    await _safe_edit(
        callback.message,
        fmt.get_referal_menu_text(),
        fmt.get_referal_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "ref:my_link")
async def referal_my_link(callback: CallbackQuery):
    """Страница с персональной реферальной ссылкой."""
    if not REFERAL_FEATURE_ENABLED:
        await callback.answer('Реферальная программа пока недоступна', show_alert=True)
        return
    tg_id = callback.from_user.id
    bot_info = await connectAPI.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={tg_id}"
    await _safe_edit(
        callback.message,
        fmt.get_referal_link_text(link),
        fmt.get_referal_link_keyboard(link)
    )
    await callback.answer()


# === CYBERMJ REFERRALS LIST V1 ===
@router.callback_query(F.data.startswith("ref:my_referals"))
async def referal_my_list(callback: CallbackQuery):
    """Only inviter gets their friends; refresh public handles on page view."""
    if not REFERAL_FEATURE_ENABLED:
        await callback.answer('Программа пока недоступна', show_alert=True)
        return
    page = 0
    if callback.data and callback.data.startswith('ref:my_referals:'):
        try:
            page = max(0, min(100000, int(callback.data.rsplit(':', 1)[1])))
        except (ValueError, TypeError):
            page = 0
    stats = await get_referrals_page(callback.from_user.id, page=page)
    if stats is None:
        await callback.answer('Не удалось получить список. Попробуй позже.', show_alert=True)
        return

    # Acknowledge Telegram callback before potentially slow get_chat calls.
    await callback.answer()

    # Backfill pre-existing referrals and refresh changed public usernames.
    # Telegram get_chat() may fail for privacy/access reasons: never show stale
    # cached usernames that could have been reassigned to a different person.
    semaphore = asyncio.Semaphore(3)

    async def refresh_public_username(row):
        async with semaphore:
            try:
                chat = await asyncio.wait_for(
                    callback.bot.get_chat(row['tg_referal']), timeout=5.0
                )
            except Exception as exc:
                logger.debug('Cannot refresh TG username %s: %s', row['tg_referal'], exc)
                row['username'] = None
                return
            fresh_username = getattr(chat, 'username', None)
            if fresh_username != row.get('username'):
                await update_telegram_username(row['tg_referal'], fresh_username)
            row['username'] = fresh_username

    if stats['rows']:
        await asyncio.gather(*(refresh_public_username(row) for row in stats['rows']))

    await _safe_edit(
        callback.message,
        fmt.get_referrals_list_text(stats),
        fmt.get_referrals_page_keyboard(stats['page'], stats['pages'])
    )

# ==========================================================
# === ⚔️ ФЕСТИВАЛЬ СРЕДНЕВЕКОВЬЯ (FSM) ===
# ==========================================================

@router.callback_query(F.data == "festival:start")
async def festival_start(callback: CallbackQuery, state: FSMContext):
    """Начало фестиваля."""
    await state.clear()
    tg_id = callback.from_user.id
    is_known = await check_sword_tg_exists(tg_id)

    if is_known:
        secret_ok = await check_sword_secret_status(tg_id)
        if secret_ok:
            await _safe_edit(
                callback.message,
                fmt.get_festival_success_text(),
                fmt.get_festival_back_keyboard()
            )
        else:
            await state.set_state(FestivalStates.waiting_for_secret)
            await _safe_edit(
                callback.message,
                fmt.get_festival_ask_word_text(),
                fmt.get_festival_back_keyboard()
            )
    else:
        await state.set_state(FestivalStates.waiting_for_phone)
        
        phone_btn = KeyboardButton(text="📱 Поделиться номером", request_contact=True)
        kb = ReplyKeyboardMarkup(keyboard=[[phone_btn]], resize_keyboard=True, one_time_keyboard=True)
        
        await callback.message.answer(
            fmt.get_festival_welcome_text(),
            reply_markup=kb,
            parse_mode="HTML"
        )
    await callback.answer()


@router.message(FestivalStates.waiting_for_phone, F.contact)
async def festival_got_phone(message: Message, state: FSMContext):
    """Получили номер для фестиваля — сохраняем в swordbase."""
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer("⚠️ Номер принадлежит другому пользователю!")
        return

    phone = contact.phone_number
    tg_id = message.from_user.id
    
    await save_sword_phone(phone, tg_id)
    pretty = format_phone_pretty(phone)
    
    await message.answer(
        f"⚔️ <b>Номер принят!</b>\n📱 <code>{pretty}</code>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    
    await state.set_state(FestivalStates.waiting_for_secret)
    await message.answer(
        fmt.get_festival_ask_word_text(),
        reply_markup=fmt.get_festival_back_keyboard(),
        parse_mode="HTML"
    )
    logger.info(f"⚔️ Фестиваль: {tg_id} зарегистрирован, ждём секретное слово")


@router.message(FestivalStates.waiting_for_secret, F.text)
async def festival_check_secret(message: Message, state: FSMContext):
    """Проверяем секретное слово."""
    user_word = message.text.strip().lower()
    tg_id = message.from_user.id
    
    if user_word == SECRET_WORD.lower():
        await set_sword_secret_success(tg_id)
        await state.clear()
        await message.answer(
            fmt.get_festival_success_text(),
            reply_markup=fmt.get_festival_back_keyboard(),
            parse_mode="HTML"
        )
        logger.info(f"⚔️ Фестиваль: {tg_id} ввёл верное слово!")
    else:
        await message.answer(
            fmt.get_festival_wrong_word_text(),
            reply_markup=fmt.get_festival_retry_keyboard(),
            parse_mode="HTML"
        )
        logger.info(f"⚔️ Фестиваль: {tg_id} ввёл НЕверное слово: {user_word}")


@router.callback_query(F.data == "festival:retry")
async def festival_retry(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FestivalStates.waiting_for_secret)
    await _safe_edit(
        callback.message,
        fmt.get_festival_ask_word_text(),
        fmt.get_festival_back_keyboard()
    )
    await callback.answer()

# ==========================================================
# === 🛠️ УТИЛИТЫ ===
# ==========================================================

async def _real_check_sub(user_id: int) -> bool:
    if not connectAPI.bot:
        return False
    try:
        member = await connectAPI.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "creator", "administrator", "restricted"]
    except Exception as e:
        logger.error(f"❌ _real_check_sub для {user_id}: {e}")
        return False


async def _safe_edit(message: Message, text: str, reply_markup):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")

# === CYBERMJ REFERRAL USERNAME LINKS V1 ===

from optionDB import get_referral_owner, create_referral_once
from config import REFERAL_FEATURE_ENABLED


# === CYBERMJ EXISTING BOT USERS REFERRALS V1 ===
async def _register_referral_from_link(message, inviter_tg_id: int,
                                       invitee_tg_id: int, phone: str):
    """Eligibility is about SmartShell accounts, not prior CyberMJ registration.

    Never changes phone_tg_links or an existing referral owner. Fail closed on
    unavailable SmartShell or DB. Returning a message is not a payment promise.
    """
    if inviter_tg_id == invitee_tg_id:
        return
    try:
        owner = await get_referral_owner(invitee_tg_id)
    except Exception:
        logger.exception('Referral owner lookup failed')
        await message.answer('⚠️ Не удалось проверить приглашение. Попробуй ещё раз позже.')
        return
    if owner is not None:
        await message.answer('ℹ️ Ты уже закреплён за пригласившим. Повторная ссылка не меняет участие.')
        return

    try:
        found_clients = await connectAPI.check_clients_for_referral_strict(phone)
    except Exception:
        logger.exception('SmartShell client status check failed for invitee %s', invitee_tg_id)
        await message.answer('⚠️ Проверка SmartShell временно недоступна. Открой ссылку позже — запись пока не создана.')
        return

    if found_clients:
        points_info = []
        for client in found_clients:
            points_info.append(
                f"📍 <b>{client['point']}</b>\n"
                f"   💰 Депозит: <code>{client['deposit']:.2f}₽</code>\n"
                f"   🎁 Бонусы: <code>{client['bonus']:.2f}₽</code>"
            )
        points_text = '\n\n'.join(points_info)
        await message.answer(
            f'😔 <b>Ой, кажется мы уже знакомы!</b>\n\n'
            f'Ты уже зарегистрирован в системе CYBERMG.\n\n'
            f'<b>Твои аккаунты в клубах:</b>\n\n{points_text}\n\n'
            f'Акция «Приведи друга» действует только для новичков, '
            f'которых ещё не было ни в одном клубе нашей сети.\n\n'
            f'Не переживайте(&gt;&lt;)\n'
            f'У нас есть крутая акция на первые 3 пополнения!!! '
            f'Вы можете получить до 3000 бонусов 🎁.\n\n'
            f'Следите за нашими новостями в нашем сообществе, '
            f'чтобы всегда быть в центре событий нашей вселенной!\n\n'
            f'С любовью, CyberMG!',
            parse_mode='HTML'
        )
        return

    try:
        status = await create_referral_once(inviter_tg_id, invitee_tg_id)
    except Exception:
        logger.exception('Referral creation failed for %s', invitee_tg_id)
        await message.answer('⚠️ Не удалось сохранить приглашение. Попробуй ещё раз позже.')
        return
    if status == 'created':
        await message.answer(
            '🔥 <b>Добро пожаловать в реферальную программу CYBERMG!</b>\n\n'
            'Твой номер уже привязан к боту, и в SmartShell пока нет клубных '
            'аккаунтов. Приглашение зарегистрировано — осталось выполнить '
            'условия акции! 🎁',
            parse_mode='HTML',
            reply_markup=fmt.get_referal_success_keyboard()
        )
        logger.info('Referral created: inviter %s, invitee %s', inviter_tg_id, invitee_tg_id)
    elif status == 'exists':
        await message.answer('ℹ️ Приглашение уже зарегистрировано. Смена пригласившего недоступна.')
    else:
        await message.answer('⚠️ Не удалось сохранить приглашение. Попробуй ещё раз позже.')

