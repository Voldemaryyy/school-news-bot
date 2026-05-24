# ======================================================
# wordpress.py — публікація через XML-RPC
#
# Зовнішній інтерфейс:
#   upload_image(image_bytes, filename) -> {id, url, alt}
#   create_post(title, content, category_id,
#               featured_media_id, status, publish_date) -> {id, link}
# ======================================================

import logging
from datetime import datetime, timezone
from xmlrpc.client import Binary, ServerProxy, DateTime as XmlRpcDateTime

from wordpress_xmlrpc import Client, WordPressPost, WordPressTerm
from wordpress_xmlrpc.methods import media, posts

from config import WP_URL, WP_USER, WP_PASSWORD

logger = logging.getLogger(__name__)

_XMLRPC_URL = WP_URL.rstrip("/") + "/xmlrpc.php"


def _client() -> Client:
    return Client(_XMLRPC_URL, WP_USER, WP_PASSWORD)


def _to_xmlrpc_date(dt_naive: datetime) -> XmlRpcDateTime:
    """
    Перетворює naive datetime → XmlRpcDateTime через рядок ISO 8601.

    ЧОМУ через рядок, а не XmlRpcDateTime(datetime_obj):
      xmlrpc.client.DateTime(datetime_obj) залежить від версії Python
      і може викликати «DateTime object cannot be interpreted as an integer»
      у деяких mid-version Python 3.x.

    Рядковий шлях: XmlRpcDateTime("20260512T10:00:00") — завжди стабільний.
    WordPress/IXR розбирає формат YYYYMMDDThh:mm:ss без проблем.
    """
    return XmlRpcDateTime(dt_naive.strftime("%Y%m%dT%H:%M:%S"))


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
    Один виклик wp.editPost через сировий ServerProxy:
      1. post_thumbnail  — featured image (завжди)
      2. post_date       — дата публікації за Київом (якщо передана)
      3. post_date_gmt   — дата публікації в UTC     (якщо передана)

    ЧОМУ editPost для дати:
      WordPress ігнорує post_date у NewPost при status="publish"
      і ставить поточний час. EditPost після створення примусово
      перезаписує дату — єдиний надійний спосіб для backdated постів.

    ФОРМАТ дати: XmlRpcDateTime зі строкою "YYYYMMDDThh:mm:ss".
      Так обходимо нестабільність XmlRpcDateTime(datetime_obj)
      у різних версіях Python.
    """
    proxy  = ServerProxy(_XMLRPC_URL)
    fields: dict = {"post_thumbnail": int(media_id)}

    if publish_date is not None:
        # Київський local time — те що відображається на сайті
        local_naive = publish_date.replace(tzinfo=None)
        # UTC — для внутрішньої логіки WordPress і future-планування
        utc_naive   = publish_date.astimezone(timezone.utc).replace(tzinfo=None)

        # Конструюємо через рядок — стабільно в усіх версіях Python
        fields["post_date"]     = _to_xmlrpc_date(local_naive)
        fields["post_date_gmt"] = _to_xmlrpc_date(utc_naive)

        logger.info(
            "wp.editPost date: local=%s  utc=%s",
            local_naive.strftime("%Y-%m-%d %H:%M:%S"),
            utc_naive.strftime("%Y-%m-%d %H:%M:%S"),
        )

    logger.info("wp.editPost: post_id=%s  fields=%s", post_id, list(fields.keys()))
    try:
        result = proxy.wp.editPost(
            0,
            WP_USER,
            WP_PASSWORD,
            int(post_id),
            fields,
        )
        logger.info("wp.editPost OK: result=%s", result)
    except Exception as e:
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
    Крок 1 — NewPost: title, content, status, category, (дата для future).
    Крок 2 — _finalize_post: featured image + примусова дата через editPost.

    status: "draft" | "publish" | "future"
    publish_date: timezone-aware datetime (Київ)
      past/now → status="publish", дата виставляється заднім числом
      future   → status="future",  WordPress публікує автоматично

    Повертає: {id: str, link: str}
    """
    client = _client()

    # ── Крок 1: NewPost ───────────────────────────────
    post = WordPressPost()
    post.title       = str(title)
    post.content     = content
    post.post_status = status

    if publish_date is not None:
        # python-wordpress-xmlrpc очікує datetime.datetime, НЕ XmlRpcDateTime.
        # Бібліотека сама конвертує у xmlrpc.client.DateTime при серіалізації.
        # Передаємо naive local time (WordPress інтерпретує за timezone блогу).
        local_naive = publish_date.replace(tzinfo=None)
        post.date   = local_naive
        logger.info(
            "NewPost publish_date: %s (local naive, status=%s)",
            local_naive.strftime("%Y-%m-%d %H:%M:%S"), status,
        )

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

    # ── Крок 2: featured image + дата ────────────────
    _finalize_post(post_id, featured_media_id, publish_date=publish_date)

    # ── Отримуємо permalink ───────────────────────────
    try:
        created = client.call(posts.GetPost(post_id, ["link"]))
        link    = getattr(created, "link", None) or f"{WP_URL.rstrip('/')}/?p={post_id}"
    except Exception:
        link = f"{WP_URL.rstrip('/')}/?p={post_id}"

    logger.info("Готово: id=%s  link=%s", post_id, link)
    return {"id": str(post_id), "link": link}
