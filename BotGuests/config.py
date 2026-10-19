"""Конфигурация бота CyberMG (PROD)."""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# === БАЗОВЫЕ ПУТИ ===
BASE_DIR = Path(__file__).resolve().parent
IMAGE_DIR = BASE_DIR / "image"

# === TELEGRAM ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "cybergymvl")

# === 🗄️ DATABASE ===
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_TABLE = os.getenv("DB_TABLE", "phone_tg_links")
SWORD_TABLE = os.getenv("SWORD_TABLE", "swordbase")

# === СЕКРЕТНОЕ СЛОВО ===
SECRET_WORD = os.getenv("SECRET_WORD", "ведьмак")

# === ОТОБРАЖЕНИЕ ===
WELCOME_STYLE = "classic"

# === 🏢 SMARTSHELL API ===
SMARTSHELL_GRAPHQL_URL = os.getenv("SMARTSHELL_GRAPHQL_URL", "https://billing.smartshell.gg/api/graphql")
SMARTSHELL_LOGIN = os.getenv("SMARTSHELL_LOGIN")
SMARTSHELL_PASSWORD = os.getenv("SMARTSHELL_PASSWORD")

warehouses_json = os.getenv("SMARTSHELL_WAREHOUSES", "{}")
SMARTSHELL_WAREHOUSES = json.loads(warehouses_json)

# === 🤝 РЕФЕРАЛЬНАЯ ПРОГРАММА ===
MIN_TOPUP_AMOUNT = 150.0                    # Минимальная сумма пополнения
BONUS_PREMIUM = 500                         # Для Сахалинская/Калинина
BONUS_STANDARD = 250                        # Для остальных точек
PREMIUM_WAREHOUSES = ["Сахалинская", "Калинина"]
REFERAL_TIMEOUT_DAYS = 30                   # Таймаут ожидания (дней)

# === ЛОГИРОВАНИЕ ===
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
LOG_DATEFMT = "%H:%M:%S"

# === 🚧 ФЛАГ РЕФЕРАЛЬНОЙ СИСТЕМЫ ===
# false = "в разработке" (кнопка не работает, показывает alert)
# true  = включена (кнопка ведёт в меню рефералов)
REFERAL_FEATURE_ENABLED = os.getenv("REFERAL_FEATURE_ENABLED", "false").lower() == "true"
# === КЭШ ПОДПИСКИ (для middleware) ===
SUBSCRIPTION_CACHE_TTL = int(os.getenv("SUBSCRIPTION_CACHE_TTL", 300))