# ======================================================
# wordpress.py — публікація через XML-RPC
#
# Зовнішній інтерфейс:
#   upload_image(image_bytes, filename) -> {id, url, alt}
#   create_post(title, content, category_id,
#               featured_media_id, status, publish_date) -> {id, link}
# ======================================================

import logging
from datetime import datetime, timezone as _utc
from xmlrpc.client import Binary, ServerProxy, DateTime as XmlRpcDateTime

from wordpress_xmlrpc import Client, WordPressPost, WordPressTerm
from wordpress_xmlrpc.methods import media, posts

from config import WP_URL, WP_USER, WP_PASSWORD

logger = logging.getLogger(__name__)

_XMLRPC_URL = WP_URL.rstrip("/") + "/xmlrpc.php"


def _client() -> Client:
    return Client(_XMLRPC_URL, WP_USER, WP_PASSWORD)


# ══════════════════════════════════════════════════════
# ЗАВАНТАЖЕННЯ ФОТО
# ══════════════════════════════════════════════════════
def upload_image(image_bytes: bytes, filename: str = "photo.jpg") -> dict:
    """
    Завантажує зображення в медіатеку WordPress.
    Повертає: {id: str, url: str, alt: str}
    """
    data = {
        "name":      filename,
        "type":      "image/jpeg",
        "bits":      Binary(image_bytes),
        "overwrite": False,
    }

    logger.info("XML-RPC upload: %s (%d байт)", filename, len(image_bytes))
    try:
        resp = _client().call(media.UploadFile(data))
    except Exception as e:
        raise RuntimeError(f"Помилка завантаження '{filename}': {e}") from e

    media_id  = str(resp["id"])
    media_url = resp["url"]
    logger.info("XML-RPC upload OK: id=%s  url=%s", media_id, media_url)
    return {"id": media_id, "url": media_url, "alt": ""}


# ══════════════════════════════════════════════════════
# ФІНАЛІЗАЦІЯ ПОСТА (featured image + дата)
# ══════════════════════════════════════════════════════
def _finalize_post(
    post_id: str | int,
    media_id: str | int,
    publish_date: datetime | None = None,
) -> None:
    """
    Один виклик wp.editPost через сировий ServerProxy, який встановлює:
      1. post_thumbnail  — featured image (мініатюра)
      2. post_date       — дата публікації за Київом (якщо передана)
      3. post_date_gmt   — дата публікації в UTC   (якщо передана)

    ЧОМУ не через python-wordpress-xmlrpc EditPost:
      Новий WordPressPost() надсилає ВСІ поля (включно з порожніми)
      → WordPress скидає заголовок на "Untitled".

    ЧОМУ editPost для дати, а не тільки NewPost:
      WordPress ігнорує post_date в NewPost якщо status="publish"
      і просто ставить поточний час. EditPost після створення
      примусово перезаписує дату — єдиний надійний спосіб.

    ФОРМАТ дати: XmlRpcDateTime (dateTime.iso8601) — рідний тип XML-RPC.
      post_date    = Київський час як naive datetime  → WordPress зберігає as-is
      post_date_gmt = UTC як naive datetime           → WordPress використовує для
                                                        сортування і планування
    """
    proxy  = ServerProxy(_XMLRPC_URL)
    fields: dict = {"post_thumbnail": int(media_id)}

    if publish_date is not None:
        # Київський local time (naive) — те що відображається на сайті
        local_naive = publish_date.replace(tzinfo=None)
        # UTC (naive) — для внутрішньої логіки WordPress і future-планування
        utc_naive   = publish_date.astimezone(_utc).replace(tzinfo=None)

        fields["post_date"]     = XmlRpcDateTime(local_naive)
        fields["post_date_gmt"] = XmlRpcDateTime(utc_naive)

        logger.info(
            "wp.editPost date: local=%s  utc=%s",
            local_naive.strftime("%Y-%m-%d %H:%M:%S"),
            utc_naive.strftime("%Y-%m-%d %H:%M:%S"),
        )

    logger.info(
        "wp.editPost: post_id=%s  fields=%s",
        post_id, list(fields.keys()),
    )
    try:
        result = proxy.wp.editPost(
            0,            # blog_id (завжди 0 для single-site)
            WP_USER,
            WP_PASSWORD,
            int(post_id),
            fields,
        )
        logger.info("wp.editPost OK: result=%s", result)
    except Exception as e:
        # Пост вже створено — не падаємо, тільки попереджаємо
        logger.warning(
            "wp.editPost: не вдалось встановити thumbnail/дату: %s\n"
            "Перевірте мініатюру і дату вручну в WordPress Admin.",
            e,
        )


# ══════════════════════════════════════════════════════
# СТВОРЕННЯ ПОСТА
# ══════════════════════════════════════════════════════
def create_post(
    title: str,
    content: str,
    category_id: int,
    featured_media_id: str | int,
    status: str = "draft",
    publish_date: datetime | None = None,
) -> dict:
    """
    Крок 1 — NewPost: створює пост з title, content, status, category.
              Якщо publish_date передана — додає post.date як перший hint
              для WordPress (може ігноруватись при status="publish").

    Крок 2 — _finalize_post: один wp.editPost встановлює:
              • post_thumbnail (featured image) — завжди
              • post_date + post_date_gmt       — якщо publish_date передана
              Це гарантує правильну дату навіть для backdated publish.

    status: "draft" | "publish" | "future"
    publish_date: timezone-aware datetime (Київ)
                  past/now  → status="publish", дата виставляється заднім числом
                  future    → status="future",  WordPress публікує автоматично

    Повертає: {id: str, link: str}
    """
    client = _client()

    # ── Крок 1: NewPost ───────────────────────────────
    post = WordPressPost()
    post.title       = str(title)   # str() захищає від None
    post.content     = content
    post.post_status = status

    # Передаємо дату в NewPost для БУДЬ-ЯКОГО статусу якщо вона є.
    # Для "future" WordPress її обов'язково враховує.
    # Для "publish" — може ігнорувати, але _finalize_post виправить це.
    if publish_date is not None:
        local_naive = publish_date.replace(tzinfo=None)
        post.date   = XmlRpcDateTime(local_naive)
        logger.info("NewPost publish_date: %s (local naive, status=%s)",
                    local_naive.strftime("%Y-%m-%d %H:%M:%S"), status)

    # Категорія
    term          = WordPressTerm()
    term.id       = str(category_id)
    term.taxonomy = "category"
    post.terms    = [term]

    logger.info("NewPost: title='%s'  status=%s  cat=%s", title, status, category_id)
    try:
        post_id = client.call(posts.NewPost(post))
    except Exception as e:
        raise RuntimeError(f"Помилка створення поста: {e}") from e
    logger.info("NewPost OK: post_id=%s", post_id)

    # ── Крок 2: featured image + дата (один виклик editPost) ─
    _finalize_post(post_id, featured_media_id, publish_date=publish_date)

    # ── Отримуємо permalink ───────────────────────────
    try:
        created = client.call(posts.GetPost(post_id, ["link"]))
        link    = getattr(created, "link", None) or f"{WP_URL.rstrip('/')}/?p={post_id}"
    except Exception:
        link = f"{WP_URL.rstrip('/')}/?p={post_id}"

    logger.info("Готово: id=%s  link=%s", post_id, link)
    return {"id": str(post_id), "link": link}
