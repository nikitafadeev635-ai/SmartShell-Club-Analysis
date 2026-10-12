"""
Модуль хендлеров Telegram бота CyberMG v4.
Исправлен конфликт хендлеров контактов через StateFilter.
"""
import logging
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
from optionDB import (
    check_tg_id_exists, get_phone_by_tgid, save_phone_tg_link,
    check_sword_tg_exists, save_sword_phone, check_sword_secret_status,
    set_sword_secret_success
)
from normalizePhone import format_phone_pretty
from config import CHANNEL_ID, SECRET_WORD
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
# === 🚀 /start ===
# ==========================================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    tg_id = message.from_user.id
    is_known = await check_tg_id_exists(tg_id)

    if is_known:
        raw_phone = await get_phone_by_tgid(tg_id)
        pretty = format_phone_pretty(raw_phone) if raw_phone else "—"
        text = fmt.get_returning_user_message(pretty)

        is_subscribed = await _real_check_sub(tg_id)

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
# === 📱 ТЕЛЕФОН (ОСНОВНОЙ) ===
# ==========================================================

# 🆕 ИСПРАВЛЕНО: добавлен StateFilter(None) - работает ТОЛЬКО вне состояний!
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


# 🆕 ИСПРАВЛЕНО: добавлен StateFilter(None) - перехватывает только ОСНОВНОЙ контакт!
@router.message(StateFilter(None), F.contact)
async def got_contact(message: Message):
    """Основная привязка телефона (стартовое окно)."""
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer("⚠️ Номер принадлежит другому пользователю!")
        return

    phone = contact.phone_number
    tg_id = message.from_user.id
    saved = await save_phone_tg_link(phone, tg_id)
    pretty = format_phone_pretty(phone)

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
    await state.clear()  # Сбрасываем состояния при проверке подписки
    is_subscribed = await _real_check_sub(callback.from_user.id)

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
    
    # Полный текст описания
    full_text = fmt.get_about_branch_text(slug)
    # Картинка
    image = fmt.get_about_branch_image(slug)
    # Клавиатура
    kb = fmt.get_back_to_menu_keyboard()

    try:
        await callback.message.delete()
    except Exception:
        pass

    if image:
        # 📸 Фото БЕЗ подписи
        await callback.message.answer_photo(
            photo=image,
            parse_mode="HTML"
        )
    
    # 📝 Полное описание отдельным сообщением (с кнопкой возврата)
    await callback.message.answer(
        full_text,
        parse_mode="HTML",
        reply_markup=kb
    )
    
    await callback.answer()

# ==========================================================
# === ⚔️ ФЕСТИВАЛЬ СРЕДНЕВЕКОВЬЯ (FSM) ===
# ==========================================================

@router.callback_query(F.data == "festival:start")
async def festival_start(callback: CallbackQuery, state: FSMContext):
    """Начало фестиваля."""
    await state.clear()  # Сбрасываем состояния перед стартом
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
    
    # 🆕 Сохраняем в SWORDBASE, а не в testWork!
    await save_sword_phone(phone, tg_id)
    pretty = format_phone_pretty(phone)
    
    await message.answer(
        f"⚔️ <b>Номер принят!</b>\n📱 <code>{pretty}</code>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    
    # Переходим к ожиданию секретного слова
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