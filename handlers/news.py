# ======================================================
# handlers/news.py — головний діалог створення новини
# ======================================================

import io
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from config import WP_CATEGORIES, ALLOWED_USERS, WP_URL
import wordpress as wp
from states import (
    TITLE, TEXT, PHOTOS, CATEGORY,
    MAIN_PHOTO, INSERT_PHOTOS, PREVIEW, HTML_PREVIEW, SCHEDULE_DATE,
)
from utils.text import split_into_paragraphs, build_full_html, make_wp_image_html

logger = logging.getLogger(__name__)

# ── Часовий пояс України (з fallback для Windows) ────
try:
    KYIV_TZ = ZoneInfo("Europe/Kyiv")
    _tz_source = "zoneinfo built-in"
except Exception:
    try:
        import importlib
        importlib.import_module("tzdata")
        from importlib.resources import files          # noqa: F401
        KYIV_TZ = ZoneInfo("Europe/Kyiv")
        _tz_source = "zoneinfo + tzdata package"
    except Exception:
        try:
            import pytz  # type: ignore
            KYIV_TZ = pytz.timezone("Europe/Kyiv")   # type: ignore
            _tz_source = "pytz"
        except ImportError:
            from datetime import timezone, timedelta as _td
            KYIV_TZ = timezone(_td(hours=2))          # type: ignore
            _tz_source = "UTC+2 fallback (без літнього часу)"

import logging as _logging
_logging.getLogger(__name__).info("Часовий пояс: %s (%s)", KYIV_TZ, _tz_source)

# ── Ключі для user_data ──────────────────────────────
KEY_TITLE      = "title"
KEY_PARAGRAPHS = "paragraphs"
KEY_PHOTOS     = "photos"
KEY_CATEGORY   = "category_id"
KEY_MAIN_IDX   = "main_photo_idx"
KEY_PHOTO_MAP  = "photo_map"
KEY_SCHED_DATE = "sched_date"
HTML_PREVIEW_MAX_CHARS = 3000


# ── Захист ───────────────────────────────────────────
def _is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


def _clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        KEY_TITLE, KEY_PARAGRAPHS, KEY_PHOTOS,
        KEY_CATEGORY, KEY_MAIN_IDX, KEY_PHOTO_MAP,
        KEY_SCHED_DATE,
        "other_photo_indices", "other_photo_pos",
    ):
        context.user_data.pop(key, None)


# ══════════════════════════════════════════════════════
# КРОК 1 — /start або /news
# ══════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ У вас немає доступу до цього бота.")
        return ConversationHandler.END

    _clear(context)
    await update.message.reply_text(
        "📰 *Створення нової новини*\n\n"
        "Крок 1 — Введіть *заголовок* новини:",
        parse_mode="Markdown",
    )
    return TITLE


# ══════════════════════════════════════════════════════
# КРОК 2 — Заголовок
# ══════════════════════════════════════════════════════
async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("⚠️ Заголовок не може бути порожнім. Спробуйте ще раз:")
        return TITLE

    context.user_data[KEY_TITLE] = title
    await update.message.reply_text(
        f"✅ Заголовок: *{title}*\n\n"
        "Крок 2 — Введіть *текст новини*.\n"
        "Кожен рядок = окремий абзац.",
        parse_mode="Markdown",
    )
    return TEXT


# ══════════════════════════════════════════════════════
# КРОК 3 — Текст
# ══════════════════════════════════════════════════════
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_text = update.message.text.strip()
    paragraphs = split_into_paragraphs(raw_text)

    if not paragraphs:
        await update.message.reply_text("⚠️ Текст не може бути порожнім. Спробуйте ще раз:")
        return TEXT

    context.user_data[KEY_PARAGRAPHS] = paragraphs
    context.user_data[KEY_PHOTOS] = []

    para_lines = "\n\n".join(
        f"{i}. {p}"
        for i, p in enumerate(paragraphs, 1)
    )
    header = f"✅ *Знайдено {len(paragraphs)} абзац(и):*\n\n"
    footer = "\n\nКрок 3 — Надішліть *фото* для новини (одне або кілька).\nКоли завершите — надішліть /done"

    full_msg = header + para_lines + footer
    if len(full_msg) <= 4096:
        await update.message.reply_text(full_msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(header + para_lines, parse_mode="Markdown")
        await update.message.reply_text(
            "Крок 3 — Надішліть *фото* для новини (одне або кілька).\n"
            "Коли завершите — надішліть /done",
            parse_mode="Markdown",
        )
    return PHOTOS


# ══════════════════════════════════════════════════════
# КРОК 4 — Збір фото
# ══════════════════════════════════════════════════════
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo[-1]
    file  = await photo.get_file()
    buf   = io.BytesIO()
    await file.download_to_memory(buf)
    context.user_data[KEY_PHOTOS].append(buf.getvalue())

    count = len(context.user_data[KEY_PHOTOS])
    await update.message.reply_text(
        f"🖼 Фото #{count} збережено. Надішліть ще або /done щоб продовжити."
    )
    return PHOTOS


async def handle_done_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.get(KEY_PHOTOS, [])
    if not photos:
        await update.message.reply_text("⚠️ Ви не надіслали жодного фото. Надішліть хоча б одне.")
        return PHOTOS

    buttons = [
        [InlineKeyboardButton(name, callback_data=f"cat_{cat_id}")]
        for name, cat_id in WP_CATEGORIES.items()
    ]
    await update.message.reply_text(
        f"✅ Отримано {len(photos)} фото.\n\n"
        "Крок 4 — Оберіть *категорію*:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return CATEGORY


# ══════════════════════════════════════════════════════
# КРОК 5 — Категорія
# ══════════════════════════════════════════════════════
async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    cat_id   = int(query.data.split("_")[1])
    cat_name = next(k for k, v in WP_CATEGORIES.items() if v == cat_id)
    context.user_data[KEY_CATEGORY] = cat_id

    photos = context.user_data[KEY_PHOTOS]

    # Якщо фото одне — автоматично робимо його головним, крок вибору пропускаємо
    if len(photos) == 1:
        context.user_data[KEY_MAIN_IDX]  = 0
        context.user_data[KEY_PHOTO_MAP] = {}
        await query.edit_message_text(
            f"✅ Категорія: *{cat_name}*\n\n"
            "🖼 Єдине фото автоматично обрано головним.\n\n"
            "Генерую перегляд…",
            parse_mode="Markdown",
        )
        return await _show_preview(query.message, context)

    # Кілька фото — показуємо вибір головного
    buttons = [
        [InlineKeyboardButton(f"Фото #{i+1}", callback_data=f"main_{i}")]
        for i in range(len(photos))
    ]
    await query.edit_message_text(
        f"✅ Категорія: *{cat_name}*\n\n"
        "Крок 5 — Оберіть *головне фото* (буде мініатюрою статті):",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return MAIN_PHOTO


# ══════════════════════════════════════════════════════
# КРОК 6 — Головне фото → ручне розміщення решти
# ══════════════════════════════════════════════════════
async def handle_main_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query    = update.callback_query
    await query.answer()

    main_idx = int(query.data.split("_")[1])
    context.user_data[KEY_MAIN_IDX]  = main_idx
    context.user_data[KEY_PHOTO_MAP] = {}

    other_indices = [
        i for i in range(len(context.user_data[KEY_PHOTOS])) if i != main_idx
    ]

    # Немає додаткових фото — одразу до прев'ю
    if not other_indices:
        await query.edit_message_text(
            f"✅ Головне фото: #{main_idx + 1}\n\nІнших фото немає. Генерую перегляд…"
        )
        return await _show_preview(query.message, context)

    # Є додаткові фото — одразу до ручного розміщення
    context.user_data["other_photo_indices"] = other_indices
    context.user_data["other_photo_pos"]     = 0

    await query.edit_message_text(
        f"✅ Головне фото: #{main_idx + 1}\n\n"
        f"Додаткових фото: {len(other_indices)} — розставте їх між абзацами."
    )
    return await _ask_photo_placement(query.message, context)


# ══════════════════════════════════════════════════════
# РУЧНЕ РОЗМІЩЕННЯ (INSERT_PHOTOS)
# ══════════════════════════════════════════════════════
async def _ask_photo_placement(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    indices = context.user_data["other_photo_indices"]
    pos     = context.user_data["other_photo_pos"]

    if pos >= len(indices):
        return await _show_preview(message, context)

    photo_num  = indices[pos] + 1
    paragraphs = context.user_data[KEY_PARAGRAPHS]

    buttons = [[InlineKeyboardButton("⬆ Перед усіма абзацами", callback_data="ins_0")]]
    for i, p in enumerate(paragraphs, start=1):
        label = (
            f"Після абзацу {i}: «{p[:28]}…»" if len(p) > 28
            else f"Після абзацу {i}: «{p}»"
        )
        buttons.append([InlineKeyboardButton(label, callback_data=f"ins_{i}")])
    buttons.append([InlineKeyboardButton("🚫 Не вставляти це фото", callback_data="ins_skip")])

    await message.reply_text(
        f"📷 Фото #{photo_num} — де його розмістити?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return INSERT_PHOTOS


async def handle_insert_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query    = update.callback_query
    await query.answer()

    data      = query.data
    indices   = context.user_data["other_photo_indices"]
    pos       = context.user_data["other_photo_pos"]
    photo_idx = indices[pos]

    if data != "ins_skip":
        para_pos = int(data.split("_")[1])
        context.user_data[KEY_PHOTO_MAP].setdefault(para_pos, []).append(photo_idx)
        await query.edit_message_text(
            f"✅ Фото #{photo_idx + 1} буде вставлено на позицію {para_pos}."
        )
    else:
        await query.edit_message_text(f"⏭ Фото #{photo_idx + 1} пропущено.")

    context.user_data["other_photo_pos"] = pos + 1
    return await _ask_photo_placement(query.message, context)


# ══════════════════════════════════════════════════════
# КРОК 7 — Текстовий попередній перегляд
# ══════════════════════════════════════════════════════
async def _show_preview(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    title      = context.user_data[KEY_TITLE]
    paragraphs = context.user_data[KEY_PARAGRAPHS]
    main_idx   = context.user_data[KEY_MAIN_IDX]
    photo_map  = context.user_data.get(KEY_PHOTO_MAP, {})

    para_text = "\n".join(f"     {p}" for p in paragraphs)

    placements = []
    for para_pos, idxs in sorted(photo_map.items()):
        where = "перед усіма абзацами" if para_pos == 0 else f"після абзацу {para_pos}"
        for pi in idxs:
            placements.append(f"  • Фото #{pi+1} — {where}")
    placement_text = "\n".join(placements) if placements else "  (тільки головне фото)"

    preview = (
        f"👁 *ПОПЕРЕДНІЙ ПЕРЕГЛЯД*\n"
        f"{'─' * 30}\n"
        f"📌 *{title}*\n\n"
        f"{para_text}\n\n"
        f"{'─' * 30}\n"
        f"🖼 Головне фото: #{main_idx + 1}\n"
        f"📷 Розміщення фото:\n{placement_text}\n"
        f"{'─' * 30}\n\n"
        f"_Крок 7 — Перевірте текст. Далі покажу HTML._"
    )

    buttons = [[
        InlineKeyboardButton("➡ Показати HTML", callback_data="publish_yes"),
        InlineKeyboardButton("❌ Скасувати",    callback_data="publish_no"),
    ]]
    await message.reply_text(
        preview,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )
    return PREVIEW


# ══════════════════════════════════════════════════════
# КРОК 8 — HTML preview + вибір дії публікації
# ══════════════════════════════════════════════════════
async def handle_preview_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "publish_no":
        _clear(context)
        await query.edit_message_text("❌ Скасовано. Введіть /news щоб почати знову.")
        return ConversationHandler.END

    await query.edit_message_text("⏳ Генерую HTML…")

    title       = context.user_data[KEY_TITLE]
    paragraphs  = context.user_data[KEY_PARAGRAPHS]
    photos      = context.user_data[KEY_PHOTOS]
    main_idx    = context.user_data[KEY_MAIN_IDX]
    category_id = context.user_data[KEY_CATEGORY]
    cat_name = next(
        (k for k, v in WP_CATEGORIES.items() if v == category_id), f"ID={category_id}"
    )

    html_preview = _build_html_preview(context)

    summary = (
        f"📋 *Підсумок перед відправкою:*\n"
        f"  📌 Заголовок: {title}\n"
        f"  🏷 Категорія: {cat_name}\n"
        f"  🖼 Головне фото: #{main_idx + 1}\n"
        f"  📝 Абзаців: {len(paragraphs)}, фото: {len(photos)}\n"
        f"  📄 HTML: {len(html_preview)} символів"
    )
    await query.message.reply_text(summary, parse_mode="Markdown")

    if len(html_preview) > HTML_PREVIEW_MAX_CHARS:
        shown = html_preview[:HTML_PREVIEW_MAX_CHARS]
        tail  = f"\n… [обрізано, всього {len(html_preview)} символів]"
    else:
        shown = html_preview
        tail  = ""

    html_msg = (
        f"📄 ФІНАЛЬНИЙ HTML (крок 8):\n"
        f"{'─' * 30}\n"
        f"{shown}{tail}\n"
        f"{'─' * 30}"
    )

    buttons = [
        [InlineKeyboardButton("✅ Опублікувати зараз",     callback_data="confirm_publish")],
        [InlineKeyboardButton("🗓 Запланувати публікацію", callback_data="confirm_schedule")],
        [InlineKeyboardButton("📝 Зберегти як чернетку",   callback_data="confirm_draft")],
        [InlineKeyboardButton("❌ Скасувати",              callback_data="confirm_no")],
    ]
    await query.message.reply_text(
        html_msg,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return HTML_PREVIEW


async def handle_html_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_no":
        _clear(context)
        await query.edit_message_text("❌ Скасовано. Введіть /news щоб почати знову.")
        return ConversationHandler.END

    if query.data == "confirm_schedule":
        await query.edit_message_text("📅 Обираємо дату публікації…")
        # Передаємо chat_id явно — надійніше ніж query.message після edit
        await _show_schedule_picker(update.effective_chat.id, context)
        return SCHEDULE_DATE

    status = "publish" if query.data == "confirm_publish" else "draft"
    label  = "Публікую новину…" if status == "publish" else "Зберігаю чернетку…"

    await query.edit_message_text(
        f"⏳ {label}\nЗавантажую фото в медіатеку WordPress…"
    )

    try:
        result = await _do_publish(context, status=status)
    except Exception as e:
        logger.error("Publish error: %s", e, exc_info=True)
        await query.message.reply_text(
            f"❗ Помилка під час публікації:\n\n{e}\n\n"
            "Перевірте config.py (WP_URL, WP_USER, WP_PASSWORD)"
        )
        return ConversationHandler.END

    title = context.user_data.get(KEY_TITLE, "")
    _clear(context)
    await _send_result(query.message, result, status, title)
    return ConversationHandler.END


# ══════════════════════════════════════════════════════
# КРОК 9 — Запланована публікація
# ══════════════════════════════════════════════════════
async def _show_schedule_picker(
    chat_id: int, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Надсилає picker дати через context.bot.send_message(chat_id=...).
    НЕ через message.reply_text — той об'єкт після edit_message_text
    може бути нестабільним і не надіслати повідомлення.
    """
    now   = datetime.now(KYIV_TZ)
    today = now.date()

    def day_btn(label: str, d: date) -> list:
        return [InlineKeyboardButton(
            f"{label} ({d.strftime('%d.%m.%Y')})",
            callback_data=f"sched_{d.isoformat()}",
        )]

    buttons = [
        day_btn("📅 Сьогодні",    today),
        day_btn("📅 Завтра",      today + timedelta(days=1)),
        day_btn("📅 Післязавтра", today + timedelta(days=2)),
        [InlineKeyboardButton("⌨️ Ввести дату вручну", callback_data="sched_manual")],
        [InlineKeyboardButton("❌ Скасувати",          callback_data="sched_cancel")],
    ]

    await context.bot.send_message(
        chat_id    = chat_id,
        text       = "🗓 *Запланована публікація*\n\nОберіть дату або введіть вручну:",
        reply_markup = InlineKeyboardMarkup(buttons),
        parse_mode = "Markdown",
    )
    return SCHEDULE_DATE


async def handle_schedule_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "sched_cancel":
        _clear(context)
        await query.edit_message_text("❌ Скасовано. Введіть /news щоб почати знову.")
        return ConversationHandler.END

    if query.data == "sched_manual":
        context.user_data[KEY_SCHED_DATE] = None
        await query.edit_message_text(
            "✏️ Введіть дату і час публікації:\n\n"
            "`ДД.ММ.РРРР ГГ:ХХ`\n\n"
            "Наприклад: `25.12.2024 09:00`\n\n"
            "_(час за Києвом)_",
            parse_mode="Markdown",
        )
        return SCHEDULE_DATE

    date_iso    = query.data[len("sched_"):]
    chosen_date = date.fromisoformat(date_iso)
    context.user_data[KEY_SCHED_DATE] = chosen_date

    await query.edit_message_text(
        f"🗓 Дата: *{chosen_date.strftime('%d.%m.%Y')}*\n\n"
        "Тепер введіть час публікації:\n"
        "`ГГ:ХХ`  (наприклад: `09:00`)\n\n"
        "_(час за Києвом)_",
        parse_mode="Markdown",
    )
    return SCHEDULE_DATE


async def handle_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обробляє текстове введення дати/часу в стані SCHEDULE_DATE.
    Викликається після того як користувач обрав день (KEY_SCHED_DATE = date)
    або натиснув «Ввести вручну» (KEY_SCHED_DATE = None).
    """
    if KEY_SCHED_DATE not in context.user_data:
        # Це не має траплятись у нормальному флоу, але якщо трапилось —
        # повідомляємо користувача замість тихого ігнорування
        await update.message.reply_text(
            "⚠️ Спочатку оберіть дату зі списку або натисніть «⌨️ Ввести дату вручну»."
        )
        return SCHEDULE_DATE

    text        = update.message.text.strip()
    chosen_date = context.user_data[KEY_SCHED_DATE]

    try:
        if chosen_date is None:
            dt_naive = datetime.strptime(text, "%d.%m.%Y %H:%M")
        else:
            t        = datetime.strptime(text, "%H:%M").time()
            dt_naive = datetime.combine(chosen_date, t)

        dt_kyiv = dt_naive.replace(tzinfo=KYIV_TZ)

    except ValueError:
        hint = (
            "`ДД.ММ.РРРР ГГ:ХХ` — наприклад: `12.05.2026 10:00`"
            if chosen_date is None
            else "`ГГ:ХХ` — наприклад: `09:00`"
        )
        await update.message.reply_text(
            f"⚠️ Невірний формат. Введіть:\n{hint}",
            parse_mode="Markdown",
        )
        return SCHEDULE_DATE

    # Дата в майбутньому → WordPress заплановує (status="future")
    # Дата зараз або в минулому → публікуємо з вказаною датою (status="publish")
    # WordPress у будь-якому випадку отримує publish_date і виставляє правильний час.
    is_future = dt_kyiv > datetime.now(KYIV_TZ)
    status    = "future" if is_future else "publish"
    time_label = dt_kyiv.strftime("%d.%m.%Y о %H:%M")
    action_label = (
        f"⏳ Планую публікацію на *{time_label}* (Київ)…"
        if is_future
        else f"⏳ Публікую заднім числом: *{time_label}* (Київ)…"
    )

    await update.message.reply_text(
        f"{action_label}\nЗавантажую фото…",
        parse_mode="Markdown",
    )

    try:
        result = await _do_publish(context, status=status, publish_date=dt_kyiv)
    except Exception as e:
        logger.error("Schedule publish error: %s", e, exc_info=True)
        await update.message.reply_text(f"❗ Помилка:\n{e}")
        return ConversationHandler.END

    title = context.user_data.get(KEY_TITLE, "")
    _clear(context)
    await _send_result(update.message, result, status, title, publish_date=dt_kyiv)
    return ConversationHandler.END


# ══════════════════════════════════════════════════════
# Допоміжні функції
# ══════════════════════════════════════════════════════
async def _send_result(
    message,
    result: dict,
    status: str,
    title: str,
    publish_date: datetime | None = None,
) -> None:
    post_id   = result["id"]
    link      = result["link"]
    admin_url = f"{WP_URL.rstrip('/')}/wp-admin/post.php?post={post_id}&action=edit"
    edit_btn  = InlineKeyboardButton("✏️ Відкрити в WordPress", url=admin_url)

    if status == "publish":
        text = (
            f"🎉 *Новину опубліковано!*\n\n"
            f"📌 {title}\n"
            f"🔗 {link}"
        )
        buttons = [
            [InlineKeyboardButton("👁 Переглянути на сайті", url=link)],
            [edit_btn],
        ]
    elif status == "draft":
        text = (
            f"📝 *Чернетку збережено!*\n\n"
            f"📌 {title}\n\n"
            f"_Відкрийте в WordPress щоб перевірити та опублікувати._"
        )
        buttons = [[edit_btn]]
    else:  # future
        date_str = publish_date.strftime("%d.%m.%Y о %H:%M") if publish_date else "—"
        text = (
            f"🗓 *Публікацію заплановано!*\n\n"
            f"📌 {title}\n"
            f"📅 Дата: {date_str} (Київ)\n"
            f"🔗 {link}"
        )
        buttons = [
            [InlineKeyboardButton("👁 Попередній перегляд", url=link)],
            [edit_btn],
        ]

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


def _build_html_preview(context: ContextTypes.DEFAULT_TYPE) -> str:
    paragraphs = context.user_data[KEY_PARAGRAPHS]
    photo_map  = context.user_data.get(KEY_PHOTO_MAP, {})
    main_idx   = context.user_data[KEY_MAIN_IDX]

    html_photo_map: dict[int, list[str]] = {}
    for para_pos, idxs in photo_map.items():
        for pi in idxs:
            placeholder = make_wp_image_html(f"[URL фото #{pi+1}]")
            html_photo_map.setdefault(para_pos, []).append(placeholder)

    content = build_full_html(paragraphs, html_photo_map)
    header  = f"<!-- featured image: фото #{main_idx+1} -->\n\n"
    return header + content


async def _do_publish(
    context: ContextTypes.DEFAULT_TYPE,
    status: str = "draft",
    publish_date: datetime | None = None,
) -> dict:
    photos      : list[bytes] = context.user_data[KEY_PHOTOS]
    main_idx    : int         = context.user_data[KEY_MAIN_IDX]
    paragraphs  : list[str]   = context.user_data[KEY_PARAGRAPHS]
    category_id : int         = context.user_data[KEY_CATEGORY]
    photo_map   : dict        = context.user_data.get(KEY_PHOTO_MAP, {})

    logger.info("Завантажую %d фото…", len(photos))
    wp_media: list[dict] = []
    for i, photo_bytes in enumerate(photos):
        m = wp.upload_image(photo_bytes, filename=f"news_photo_{i+1}.jpg")
        logger.info("  Фото #%d → %s", i + 1, m["url"])
        wp_media.append(m)

    html_photo_map: dict[int, list[str]] = {}
    for para_pos, idxs in photo_map.items():
        for pi in idxs:
            html = make_wp_image_html(wp_media[pi]["url"])
            html_photo_map.setdefault(para_pos, []).append(html)

    content_html = build_full_html(paragraphs, html_photo_map)

    logger.info("Створюю пост status=%s…", status)
    result = wp.create_post(
        title            = context.user_data[KEY_TITLE],
        content          = content_html,
        category_id      = category_id,
        featured_media_id= wp_media[main_idx]["id"],
        status           = status,
        publish_date     = publish_date,
    )
    logger.info("Готово: %s", result["link"])
    return result


# ══════════════════════════════════════════════════════
# Скасування в будь-який момент
# ══════════════════════════════════════════════════════
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear(context)
    await update.message.reply_text(
        "🛑 Створення новини скасовано.\nВведіть /news щоб почати знову."
    )
    return ConversationHandler.END
