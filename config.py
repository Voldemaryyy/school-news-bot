import os
# ======================================================
# config.py — конфігурація бота
# ======================================================

# --- Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN")# Залиш порожнім [], щоб дозволити всім
ALLOWED_USERS: list[int] = []

# --- WordPress ---
WP_URL = "http://pervomaisk-9.raiosvita.mk.ua"
WP_USER = "redaktor"
WP_PASSWORD = os.getenv("WP_PASSWORD")# --- Категорії WordPress ---
WP_CATEGORIES = {
    "НОВИНИ": 258,
    "Новини": 283,
    "📢ОГОЛОШЕННЯ": 288,

}

# --- Текст ---
INDENT_SPACES = 5
