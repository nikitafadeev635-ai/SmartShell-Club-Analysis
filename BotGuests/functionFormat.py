"""
Модуль диалогов, меню и форматирования.
Архитектура CyberMG v4 (PROD) — финальная версия.
"""
import logging
from pathlib import Path
from typing import Optional
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import connectAPI
from config import (
    CHANNEL_ID, CHANNEL_USERNAME, IMAGE_DIR
)

logger = logging.getLogger(__name__)


# ==========================================================
# === 🖼️ СИСТЕМА РАБОТЫ С ИЗОБРАЖЕНИЯМИ ===
# ==========================================================

def get_image_path(filename: str) -> Optional[FSInputFile]:
    """
    Возвращает FSInputFile по имени файла из папки /image/.
    Кроссплатформенно (Windows + Debian).
    Если файл не найден - возвращает None.
    """
    path = IMAGE_DIR / filename
    if path.exists() and path.is_file():
        return FSInputFile(path)
    logger.warning(f"⚠️ Изображение не найдено: {path}")
    return None


# ==========================================================
# === 👋 ПРИВЕТСТВИЯ ===
# ==========================================================

def get_welcome_message(style: str = "classic") -> str:
    """Приветствие для НОВИЧКОВ."""
    return (
        "🚀 <b>Добро пожаловать в CYBERMG!</b>\n\n"
        "Я — <b>Эмджей</b>, твой помощник и проводник по CYBERMG ✌🏻\n\n"
        "Здесь собрано всё, что может понадобиться нашим текущим и будущим гостям: "
        "от знакомства с клубами и выбора игрового места до персональной статистики, "
        "бонусов и связи с командой.\n\n"
        "<b>Я помогу тебе:</b>\n"
        "🔸 выбрать клуб и забронировать ПК;\n"
        "🔸 узнать цены, сборки ПК и преимущества каждого филиала;\n"
        "🔸 рассказать о действующих акциях и предложениях;\n"
        "🔸 проверить баланс и игровую статистику;\n"
        "🔸 пригласить друга и получить бонусы;\n"
        "🔸 найти социальные сети CYBERMG;\n"
        "🔸 поделиться мнением или связаться с директором.\n\n"
        "💛 <b>CYBERMG всегда под рукой</b> — всё важное собрано в одном месте.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📱 Для начала <b>поделись своим номером телефона</b> через кнопку ниже.\n"
        "Он нужен, чтобы подготовить твой клубный аккаунт и открыть персональные возможности бота."
    )


def get_returning_user_message(phone_pretty: str) -> str:
    """Для вернувшихся — сразу к подписке."""
    return (
        "👋 <b>С возвращением, чемпион!</b> 🎮\n\n"
        f"Твой номер: <code>{phone_pretty}</code>\n\n"
        "Отлично, осталось совсем немного! 🚀\n"
        "Чтобы открыть все возможности бота и всегда быть в курсе новостей, "
        "акций и событий CYBERMG, подпишись на наш Telegram-канал."
    )


# ==========================================================
# === 📢 ПОДПИСКА ===
# ==========================================================

def get_subscription_fail_message() -> str:
    return (
        "😿 <b>Еще не подписались?</b>\n\n"
        "Эмджей одиноко в своем жилом отсеке. "
        "Без подписки на канал тебе закрыт доступ к функциям бота.\n\n"
        "Жми <b>\"Вступить в отряд\"</b>, подписывайся и возвращайся! 🚀"
    )


def get_subscription_success_message() -> str:
    return (
        "🎉 <b>Ура! Эмджей приняла тебя в семью CYBERMG!</b>\n\n"
        "Теперь тебе доступны все акции и возможности бота.\n"
        "Выбирай, с чем помочь прямо сейчас:"
    )


def get_channel_url() -> str:
    return f"https://t.me/{CHANNEL_USERNAME}"


# ==========================================================
# === 🏠 ГЛАВНОЕ МЕНЮ (ХАБ) ===
# ==========================================================

def get_main_menu_text() -> str:
    return (
        "🚀 <b>Эмджей на связи!</b>\n\n"
        "Выбирай, с чем помочь тебе прямо сейчас:"
    )


def get_main_menu_keyboard():
    """Главное меню: без стартового окна, с каналом и фестивалем."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📩 Связаться с директором", callback_data="director")
    builder.button(text="🖥 Забронировать ПК", callback_data="booking")
    builder.button(text="🗺 Навигация", callback_data="nav:hub")
    builder.button(text="🤝 Приведи друга", callback_data="referals")  # 🆕 НОВАЯ КНОПКА
    builder.button(text="⚔️ Фестиваль средневековья", callback_data="festival:start")
    builder.button(text="📢 Наш канал", url=f"https://t.me/{CHANNEL_USERNAME}")
    builder.adjust(1)
    return builder.as_markup()


def get_back_to_menu_keyboard():
    """Возврат в ГЛАВНОЕ меню."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Вернуться в меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_back_to_nav_keyboard():
    """Возврат в раздел НАВИГАЦИИ."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К навигации", callback_data="nav:hub")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === 🗺 НАВИГАЦИЯ (Новый хаб) ===
# ==========================================================

def get_nav_hub_text() -> str:
    return (
        "🗺 <b>Навигация по вселенной CYBERMG</b>\n\n"
        "В данном разделе ты можешь узнать актуальную информацию о наших ивентах, "
        "забронировать игровую сессию со скидкой, пообщаться в соц.сетях, "
        "а также ознакомиться с ценами, девайсами и клубами поближе."
    )


def get_nav_hub_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Наши акции и предложения", callback_data="nav:promo")
    builder.button(text="⚙️ Сборки ПК (железо и девайсы)", callback_data="nav:pc_build")
    builder.button(text="💰 Прайс", callback_data="nav:price")
    builder.button(text="📱 Социальные сети", callback_data="nav:social")
    builder.button(text="🔥 Бронируй со скидкой 10%", callback_data="nav:smartgamer")
    builder.button(text="💬 Поделиться мнением", callback_data="nav:review")
    builder.button(text="🏢 О наших клубах", callback_data="nav:about")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === 🎁 АКЦИИ ===
# ==========================================================

PROMO_TEXT = (
    "🔥 <b>Актуальные акции CYBERMG</b>\n\n"
    "🎁 <b>3000 бонусов за первые три пополнения</b>\n"
    "Пополняй баланс клубного аккаунта и получай бонусы за первые три пополнения. "
    "В сумме можно забрать до 3000 бонусов!\n\n"
    "🤝 <b>250 бонусов за приведённого друга</b>\n"
    "Пригласи друга по своей персональной ссылке. После его первого пополнения "
    "мы начислим тебе 250 бонусов.\n\n"
    "📱 <b>Скидка 10% при бронировании в приложении</b>\n"
    "Бронируй игровое место через приложение SmartGamer и получай скидку 10%. "
    "Удобно, быстро и выгодно!\n\n"
    "🎂 <b>Подарок в день рождения</b>\n"
    "В день рождения мы умножим сумму твоего пополнения на 1,5. "
    "Отличный повод отметить праздник большой каткой в CYBERMG!\n\n"
    "🏆 <b>Система лояльности — до 50 000 бонусов</b>\n"
    "Играй в наших клубах, повышай свой уровень и получай награды за достижения. "
    "В рамках системы лояльности можно получить до 50 000 бонусов!\n\n"
    "💬 <i>Уточнить детали можно у администратора клуба.</i>"
)


def get_promo_image():
    return get_image_path("promotions_cover.png")


# ==========================================================
# === 🔧 СБОРКИ ПК ===
# ==========================================================

def get_pc_build_intro_text() -> str:
    return (
        "⚙️ <b>Сборки ПК</b>\n\n"
        "Хочешь знать, на чём пойдёшь в катку? "
        "Выбирай филиал — покажу железо и девайсы 🎮"
    )


def get_pc_build_result_text(branch_name: str) -> str:
    return (
        f"⚡ Вот какое железо ждёт тебя в клубе <b>«{branch_name}»</b>\n\n"
        "Характеристики компьютеров и девайсов — на картинке."
    )


PC_BUILD_FILES = {
    "kalinina": "pc_build_kalinina.png",
    "russkaya": "pc_build_russkaya.png",
    "sakhalinskaya": "pc_build_sakhalinskaya.png",
    "svetlanskaya": "pc_build_svetlanskaya.png",
    "tramvaynaya": "pc_build_tramvaynaya.png",
    "ulyanovskaya": "pc_build_ulyanovskaya.png",
}


# ==========================================================
# === 💰 ПРАЙС ===
# ==========================================================

def get_price_intro_text() -> str:
    return (
        "💰 <b>Прайс</b>\n\n"
        "Выбери клуб — покажу его актуальный прайс ⚡"
    )


def get_price_result_text(branch_name: str) -> str:
    return (
        f"🎮 А вот и актуальный прайс клуба <b>«{branch_name}»</b>\n"
        "Все тарифы собрала на картинке."
    )


PRICE_FILES = {
    "kalinina": "price_kalinina.png",
    "russkaya": "price_russkaya.png",
    "sakhalinskaya": "price_tihaya.png",
    "svetlanskaya": "price_centr.png",
    "tramvaynaya": "price_lugovaya.png",
    "ulyanovskaya": "price_bam.png",
}


# ==========================================================
# === 📱 СОЦ. СЕТИ ===
# ==========================================================

def get_social_text() -> str:
    return (
        "📱 <b>Социальные сети CYBERMG</b>\n\n"
        "Следи за новостями, турнирами и ивентами в наших соц. сетях!"
    )


def get_social_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔵 VK", url="https://vk.ru/cybergymvl")
    builder.button(text="📷 Instagram", url="https://www.instagram.com/cybermg_vl")
    builder.button(text="⬅️ К навигации", callback_data="nav:hub")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === 🔥 SMARTGAMER (бронь со скидкой в навигации) ===
# ==========================================================

SMARTGAMER_TEXT = (
    "🔥 <b>Бронируй со скидкой 10%</b>\n\n"
    "Ты можешь бронировать наши ПК со скидкой при помощи приложения <b>SmartGamer</b>! "
    "Это позволит тебе отслеживать свой баланс, место в рейтинге клуба, "
    "а также использовать бонусные пополнения и не стоять в очереди у админской стойки "
    "в ожидании жаркой катки!"
)


def get_smartgamer_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📲 Открыть SmartGamer", url="https://smartgamer.ru/")
    builder.button(text="⬅️ К навигации", callback_data="nav:hub")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === 💬 ОТЗЫВЫ (сначала филиал, потом агрегатор) ===
# ==========================================================

REVIEW_LINKS = {
    "2gis": {
        "✨ Калинина • NEW": "https://go.2gis.com/Iubc3",
        "Светланская": "https://go.2gis.com/HFrWw",
        "Русская": "https://go.2gis.com/bAjvs",
        "Ульяновская": "https://go.2gis.com/uGR1I",
        "Трамвайная": "https://go.2gis.com/6jR1K",
        "Сахалинская": "https://go.2gis.com/z1Di5",
    },
    "yandex": {
        "✨ Калинина • NEW": "https://yandex.ru/maps/org/cybermg/102097226030/",
        "Светланская": "https://yandex.ru/maps/-/CTFBIT77",
        "Русская": "https://yandex.ru/maps/-/CTFBMZoW",
        "Ульяновская": "https://yandex.ru/maps/-/CTFBMY4B",
        "Трамвайная": "https://yandex.ru/maps/-/CTFBME5d",
        "Сахалинская": "https://yandex.ru/maps/-/CTFBMF0~",
    },
}


def get_review_intro_text() -> str:
    return (
        "💬 <b>Поделись мнением о CYBERMG!</b>\n\n"
        "Выбери клуб, о котором хочешь оставить отзыв:"
    )


def get_review_branch_keyboard():
    """Шаг 1: выбор филиала."""
    builder = InlineKeyboardBuilder()
    for branch in REVIEW_LINKS["2gis"].keys():
        builder.button(text=branch, callback_data=f"review_branch:{branch}")
    builder.button(text="⬅️ К навигации", callback_data="nav:hub")
    builder.adjust(1)
    return builder.as_markup()


def get_review_aggregator_text(branch: str) -> str:
    return (
        f"📍 Ты выбрал: <b>{branch}</b>\n\n"
        "Теперь выбери площадку для отзыва:"
    )


def get_review_aggregator_keyboard(branch: str):
    """Шаг 2: выбор агрегатора для конкретного филиала."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📍 2ГИС", url=REVIEW_LINKS["2gis"].get(branch, "#"))
    builder.button(text="🗺 Яндекс Карты", url=REVIEW_LINKS["yandex"].get(branch, "#"))
    builder.button(text="⬅️ К выбору клуба", callback_data="nav:review")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === 🏢 О НАШИХ КЛУБАХ ===
# ==========================================================

def get_about_intro_text() -> str:
    return (
        "🏢 <b>О наших клубах</b>\n\n"
        "Для того, чтобы узнать о каждом нашем клубе поближе, "
        "можешь нажать на представленную ниже кнопку."
    )


def get_about_branch_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✨ Калинина • NEW", callback_data="about:kalinina")
    builder.button(text="Светланская", callback_data="about:svetlanskaya")
    builder.button(text="Русская", callback_data="about:russkaya")
    builder.button(text="Ульяновская", callback_data="about:ulyanovskaya")
    builder.button(text="Трамвайная", callback_data="about:tramvaynaya")
    builder.button(text="Сахалинская", callback_data="about:sakhalinskaya")
    builder.button(text="⬅️ К навигации", callback_data="nav:hub")
    builder.adjust(1)
    return builder.as_markup()


# Стандарт качества (общая часть)
STANDARD_QUALITY_BLOCK = (
    "\n\n⚡ <b>Во всех клубах CYBERMG вас ждёт единый стандарт качества:</b>\n\n"
    "— Поможем установить и запустить то, что не запускается дома\n"
    "— Обрабатываем игровые места после каждого гостя и регулярно проводим генеральную уборку\n"
    "— Предоставляем клубные аккаунты с популярными играми и добавляем новые по запросу\n"
    "— Своевременно обновляем игры, даже если патч вышел сегодня утром\n"
    "— Возможность бронировать места и пополнять баланс через мобильное приложение\n"
    "— Всегда готовы помочь с настройками, игрой или просто поддержать разговор\n\n"
    "<b>CYBERMG — комфортная игра, живая атмосфера и места, куда хочется возвращаться.</b>"
)


BRANCH_DESCRIPTIONS = {
    "kalinina": (
        "📍 <b>CYBERMG на Калинина</b> — шестой филиал сети и новый уровень приватного "
        "игрового пространства на Чуркине.\n\n"
        "Главная особенность клуба — отдельные комнаты с индивидуальной световой атмосферой. "
        "Вы сами выбираете, какой вайб подходит вам сегодня: спокойный, яркий, контрастный "
        "или максимально погружающий в игру.\n\n"
        "В клубе представлены приватные комнаты формата Solo, Duo и Trio. Можно полностью "
        "сосредоточиться на игре, провести вечер вдвоём или собрать свою небольшую команду — "
        "без постороннего шума и случайных зрителей.\n\n"
        "Техническое оснащение соответствует уровню филиала. На игровых местах установлены "
        "процессоры AMD Ryzen 7 7800X3D, 2К-мониторы с частотой 300 Гц, магнитные клавиатуры, "
        "мыши Logitech G Pro X Superlight 2 и наушники Logitech G Pro X.\n\n"
        "В комнатах категории Pro установлены OLED-мониторы 2К с частотой 500 Гц — для тех, "
        "кому важны максимальная плавность изображения, минимальная задержка и преимущество "
        "в каждом движении.\n\n"
        "Для отдыха между матчами предусмотрена премиальная зона с PlayStation 5 и большим "
        "удобным диваном. Здесь можно поиграть с друзьями, посмотреть фильм или просто "
        "переключиться после напряжённой сессии.\n\n"
        "<i>Калинина — это технологии, приватность и возможность каждый раз выбирать "
        "игровую атмосферу под своё настроение.</i>"
    ),
    "russkaya": (
        "📍 <b>CYBERMG на Русской</b> — первый клуб сети и место, с которого началась "
        "наша история. После полного свежего ремонта он стал ещё комфортнее, технологичнее "
        "и приватнее.\n\n"
        "Главная особенность филиала — отдельные закрытые комнаты формата Solo, Duo и Trio. "
        "Каждая комната оборудована кодовым замком: можно играть одному, прийти вдвоём или "
        "собрать небольшую компанию — без постороннего шума и отвлекающих факторов.\n\n"
        "Для командных игр предусмотрена просторная bootcamp-зона, а для отдыха с друзьями — "
        "отдельная комната с PlayStation 5.\n\n"
        "Техника соответствует атмосфере клуба: игровые места оснащены 2К-мониторами с "
        "частотой 300 Гц, а в Solo-комнате установлен OLED-монитор 2К с частотой 280 Гц. "
        "На каждом месте — мышь Logitech G Pro X Superlight 2 и премиальные наушники "
        "Logitech G Pro.\n\n"
        "Здесь всё продумано для полного погружения в игру: от расположения мониторов "
        "на кронштейнах до чистоты и порядка после каждого гостя.\n\n"
        "<i>Русская — это приватность, технологичность и тот самый фирменный комфорт, "
        "с которого начался CYBERMG.</i>"
    ),
    "sakhalinskaya": (
        "📍 <b>CYBERMG на Сахалинской</b> — атмосферный клуб в прибрежной части города, "
        "где одинаково удобно играть одному, небольшой компанией или полноценной командой.\n\n"
        "Все компьютерные места грамотно зонированы. Даже в общем пространстве игроки не "
        "мешают друг другу, поэтому можно общаться с друзьями, координировать команду и "
        "при этом сохранять концентрацию.\n\n"
        "Для компаний и команд предусмотрены две VIP-комнаты по пять компьютеров, а также "
        "отдельный bootcamp на пять игровых мест. Это удобный формат для тренировок, "
        "рейтинговых матчей, небольших турниров или вечера с постоянным составом.\n\n"
        "Также в клубе есть открытая зона на троих — отличный вариант для тех, кто хочет "
        "играть рядом с друзьями, но предпочитает более свободное пространство.\n\n"
        "Оборудование помогает сосредоточиться на игре и не отвлекаться на технические мелочи.\n\n"
        "Сахалинская стала третьим проектом сети и получила свой характер: спокойный, "
        "дружелюбный и по-настоящему командный.\n\n"
        "<i>Сахалинская — это баланс между общением, личным комфортом и полной "
        "концентрацией на игре.</i>"
    ),
    "svetlanskaya": (
        "📍 <b>CYBERMG на Светланской</b> — мощный игровой клуб в самом центре Владивостока, "
        "рядом с центральной площадью. Удобное расположение, топовое оборудование и множество "
        "отдельных комнат делают его подходящим для любого игрового сценария.\n\n"
        "В филиале предусмотрены приватные игровые пространства формата Solo, Duo и Trio. "
        "Можно уединиться для рейтинговой игры, прийти вдвоём или провести вечер своей "
        "компанией. Комнаты не запираются, но благодаря отдельному расположению в них "
        "сохраняется камерная и комфортная атмосфера.\n\n"
        "Для тех, кто предпочитает открытые пространства, работает большой Premium-зал "
        "на 15 компьютеров. Здесь удобно играть большой компанией, собирать команду или "
        "погружаться в атмосферу общего игрового зала.\n\n"
        "Главное преимущество Светланской — производительность. Здесь установлены топовые "
        "игровые компьютеры, характеристики которых значительно выше среднего уровня "
        "компьютерных клубов города.\n\n"
        "На игровых местах используются премиальные наушники Logitech G Pro, а также мыши "
        "Logitech G Pro X Superlight и Lamzu Atlantis. Точное управление, качественный звук "
        "и стабильный FPS позволяют сосредоточиться на результате.\n\n"
        "Светланская стала пятым филиалом сети и объединила камерность отдельных комнат "
        "с динамикой большого центрального клуба.\n\n"
        "<i>Светланская — это максимальная мощность, приватные комнаты и удобное "
        "расположение в сердце Владивостока.</i>"
    ),
    "tramvaynaya": (
        "📍 <b>CYBERMG на Трамвайной</b> — самый крупный и просторный клуб сети. Здесь "
        "много воздуха, свободного пространства и места для комфортной игры — как одному, "
        "так и большой компанией.\n\n"
        "Просторные игровые комнаты позволяют чувствовать себя свободно даже во время "
        "самых напряжённых матчей. Никто не сидит слишком близко, ничего не мешает "
        "сосредоточиться, а уютная атмосфера помогает расслабиться и получать удовольствие "
        "от игры.\n\n"
        "Для совместного отдыха предусмотрена отдельная зона с игровой приставкой — можно "
        "переключиться с ПК, устроить турнир с друзьями или просто провести вечер за "
        "любимой игрой.\n\n"
        "Мощные компьютеры обеспечивают стабильную "
        "производительность и плавную картинку в современных играх.\n\n"
        "В ближайшем будущем филиал ждёт большой ремонт, который сделает пространство ещё "
        "современнее, удобнее и эффектнее.\n\n"
        "Трамвайная стала вторым проектом CYBERMG и до сих пор остаётся филиалом для тех, "
        "кто ценит масштаб и свободу.\n\n"
        "<i>Трамвайная — это простор, лёгкость и комфорт без ощущения тесного "
        "компьютерного клуба.</i>"
    ),
    "ulyanovskaya": (
        "📍 <b>CYBERMG на Ульяновской</b> — самый спокойный и камерный филиал сети. Сюда "
        "приходят, когда хочется тишины, личного пространства и игры без лишней суеты.\n\n"
        "В клубе царит расслабленная атмосфера: минимум отвлекающих факторов, комфортная "
        "посадка и продуманное пространство. Это подходящее место, чтобы отдохнуть после "
        "рабочего дня, сосредоточиться на рейтинговой игре или просто провести несколько "
        "часов наедине с любимым проектом.\n\n"
        "Особенность филиала — два полностью отдельных Solo-места. Они созданы для игроков, "
        "которые ценят приватность и не хотят находиться в общем зале.\n\n"
        "Ульяновская стала четвёртым проектом CYBERMG и заняла особое место в сети: это "
        "клуб для тех, кому не нужен шум большой компании, чтобы получить максимум "
        "удовольствия.\n\n"
        "<i>Ульяновская — это тишина, фокус и личное игровое пространство.</i>"
    ),
}


BRANCH_NAMES = {
    "kalinina": "Калинина NEW",
    "russkaya": "Русская",
    "sakhalinskaya": "Сахалинская",
    "svetlanskaya": "Светланская",
    "tramvaynaya": "Трамвайная",
    "ulyanovskaya": "Ульяновская",
}


def get_about_branch_text(branch_slug: str) -> str:
    """Полное описание филиала + стандарт качества."""
    base = BRANCH_DESCRIPTIONS.get(branch_slug, "Описание в разработке...")
    return base + STANDARD_QUALITY_BLOCK


# Картинки преимуществ для каждого клуба
ADVANTAGES_FILES = {
    "kalinina": "advantages_kalinina.png",
    "russkaya": "advantages_russkaya.png",
    "sakhalinskaya": "advantages_sakhalinskaya.png",
    "svetlanskaya": "advantages_svetlanskaya.png",
    "tramvaynaya": "advantages_tramvaynaya.png",
    "ulyanovskaya": "advantages_ulyanovskaya.png",
}


def get_about_branch_image(slug: str):
    """Возвращает картинку преимуществ филиала."""
    filename = ADVANTAGES_FILES.get(slug)
    if not filename:
        return None
    return get_image_path(filename)


# ==========================================================
# === 🖥 БРОНИРОВАНИЕ ПК (главное меню) ===
# ==========================================================

def get_booking_text() -> str:
    return (
        "🖥 <b>Пора занять своё место за компьютером!</b> 🚀\n\n"
        "Выбери филиал — Эмджей сразу отправит тебя к администратору клуба. "
        "Он поможет подобрать свободный ПК и удобное время для игры.\n\n"
        "🔥 <b>А ещё бронируй со скидкой 10%!</b>\n"
        "Скачай приложение <b>SmartGamer</b> — это позволит тебе отслеживать баланс, "
        "место в рейтинге клуба, использовать бонусные пополнения и не стоять "
        "в очереди у админской стойки в ожидании жаркой катки!"
    )


# Ссылки на администраторов филиалов (для личного общения)
BOOKING_BRANCHES = {
    "✨ Калинина • NEW": "https://t.me/cybermg_kalinina",
    "Светланская": "https://t.me/cybermg_centr",
    "Русская": "https://t.me/cybermg_russkaya",
    "Ульяновская": "https://t.me/cybermg_bam",
    "Трамвайная": "https://t.me/cybermg_lugovaya",
    "Сахалинская": "https://t.me/cybermg_tihaya",
}


def get_booking_keyboard():
    """
    Большая кнопка SmartGamer сверху (бронь со скидкой 10%) +
    список филиалов (ведут к администраторам каждого клуба).
    """
    builder = InlineKeyboardBuilder()
    
    # 🔥 Большая кнопка сверху — бронь со скидкой через приложение
    builder.button(text="🔥 Бронируй со скидкой 10%", url="https://smartgamer.ru/")
    
    # Список филиалов — ведут в личный чат администратора клуба
    for name, url in BOOKING_BRANCHES.items():
        builder.button(text=name, url=url)
    
    # Кнопка возврата в главное меню
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === 📩 СВЯЗЬ С ДИРЕКТОРОМ ===
# ==========================================================

def get_director_text() -> str:
    return (
        "У тебя есть идея, предложение или ситуация, "
        "которую хочется обсудить лично с директором? 💫\n\n"
        "Эмджей открыла <b>прямой канал связи</b> — для того, чтобы твоё сообщение попало "
        "напрямую к руководству CYBERMG, без фильтров и посредников ✨\n\n"
        "Каждое обращение важно. Каждое будет прочитано лично 💛\n\n"
        "Напиши директору: <b>@cybermg_feedback</b>"
    )


def get_feedback_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✉️ Написать", url="https://t.me/cybermg_feedback")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === ⚔️ ФЕСТИВАЛЬ СРЕДНЕВЕКОВЬЯ ===
# ==========================================================

def get_festival_welcome_text() -> str:
    return (
        "⚔️ <b>Е-хо-хо!</b>\n\n"
        "Хочешь получить <b>400 монет</b> для игры на нашем звездном корабле? "
        "Тогда поделись своими данными с нами и ответь на наш вопрос!"
    )


def get_festival_ask_word_text() -> str:
    return (
        "🗡 <b>Воин, докажи, что ты сейчас на фестивале</b> ⚔️\n\n"
        "Узнай секретное слово у наших сотрудников на площадке "
        "и отправь его мне одним сообщением."
    )


def get_festival_wrong_word_text() -> str:
    return (
        "🧙 <b>Похоже, заклинание не сработало</b>\n\n"
        "Попробуешь ещё раз?"
    )


def get_festival_success_text() -> str:
    return (
        "🎉 <b>Отлично, боец!</b>\n\n"
        "Покажи это сообщение администратору в клубе, "
        "как будешь готов посетить один из наших космических кораблей "
        "сети компьютерных клубов <b>CyberMG</b> и получи "
        "<b>2 часа игрового времени БЕСПЛАТНО!</b>"
    )


def get_festival_retry_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Ввести еще раз", callback_data="festival:retry")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_festival_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === 🤝 РЕФЕРАЛЬНАЯ СИСТЕМА (НОВОЕ) === 🆕
# ==========================================================

def get_referal_menu_text() -> str:
    """Текст меню 'Приведи друга'."""
    return (
        "🤝 <b>Приведи друга — получи бонусы!</b>\n\n"
        "Приглашай друзей в CYBERMG по своей уникальной ссылке.\n"
        "Как только друг зарегистрируется и пополнит баланс на <b>1000₽</b>, "
        "ты получишь <b>300 бонусов</b> на свой аккаунт 🎁\n\n"
        "Выбери нужный раздел:"
    )


def get_referal_menu_keyboard():
    """Меню реферальной программы."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Моя ссылка", callback_data="ref:my_link")
    builder.button(text="👥 Мои рефералы", callback_data="ref:my_referals")
    builder.button(text="⬅️ В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_referal_link_text(link: str) -> str:
    """Текст с реферальной ссылкой."""
    return (
        "🔗 <b>Твоя реферальная ссылка:</b>\n\n"
        f"<code>{link}</code>\n\n"
        "Отправь её другу! Как только он по ней зарегистрируется и пополнит баланс "
        "на <b>1000₽</b> — ты получишь <b>300 бонусов</b> на свой клубный аккаунт 🎁"
    )


def get_referal_link_keyboard(link: str):
    """Клавиатура для страницы с ссылкой."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Отправить другу", url=f"https://t.me/share/url?url={link}&text=Го_в_CYBERMG!")
    builder.button(text="⬅️ Назад", callback_data="referals")
    builder.adjust(1)
    return builder.as_markup()


def get_referals_count_text(count: int, active_count: int = 0) -> str:
    """Текст со статистикой рефералов."""
    return (
        "👥 <b>Твои рефералы</b>\n\n"
        f"Всего приглашено: <b>{count}</b> чел.\n"
        f"Активных (с бонусом): <b>{active_count}</b> чел.\n\n"
        "Продолжай приглашать друзей и копи бонусы! 💎"
    )


def get_referal_success_keyboard():
    """Клавиатура после успешной регистрации реферала (новичок)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🏢 Выбрать клуб", callback_data="nav:about")
    builder.button(text="🏠 В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


# ==========================================================
# === 🛠️ УТИЛИТЫ ===
# ==========================================================

def get_branch_selector_keyboard(prefix: str):
    """
    Универсальная клавиатура выбора филиала.
    prefix = 'pc_build' | 'price' | 'about'
    """
    builder = InlineKeyboardBuilder()
    for slug, name in BRANCH_NAMES.items():
        builder.button(text=name, callback_data=f"{prefix}:{slug}")
    builder.button(text="⬅️ К навигации", callback_data="nav:hub")
    builder.adjust(1)
    return builder.as_markup()


def get_branch_image(prefix: str, slug: str):
    """Возвращает FSInputFile картинки филиала или None."""
    files_map = PC_BUILD_FILES if prefix == "pc_build" else PRICE_FILES
    filename = files_map.get(slug)
    if not filename:
        return None
    return get_image_path(filename)