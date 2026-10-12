"""Конфигурация бота CyberMG (PROD)."""
import os
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

# === ЛИМИТЫ АКЦИИ ===
MAX_BONUS_PER_TOPUP = 1000
MAX_ELIGIBLE_TOPUPS = 3

# === КЭШ ПОДПИСКИ ===
SUBSCRIPTION_CACHE_TTL = 300

# === ЛОГИРОВАНИЕ ===
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
LOG_DATEFMT = "%H:%M:%S"