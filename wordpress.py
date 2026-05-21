# ======================================================
# wordpress.py — публікація через XML-RPC
#
# Зовнішній інтерфейс:
#   upload_image(image_bytes, filename) -> {id, url, alt}
#   create_post(title, content, category_id,
#               featured_media_id, status, publish_date) -> {id, link}
# ======================================================

import logging
from datetime import datetime
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
# ВСТАНОВЛЕННЯ FEATURED IMAGE (сировий XML-RPC)
# ══════════════════════════════════════════════════════
def _set_featured_image(post_id: str | int, media_id: str | int) -> None:
    """
    Встановлює featured image через сировий XML-RPC виклик.

    ЧОМУ не через python-wordpress-xmlrpc:
      EditPost з новим WordPressPost() надсилає ВСІ поля об'єкта,
      включно з порожніми — це скидає заголовок поста на "Untitled".

    РІШЕННЯ: wp.editPost з мінімальним словником {"post_thumbnail": id}.
      WordPress оновлює ТІЛЬКИ ці поля, решта залишається без змін.
    """
    proxy = ServerProxy(_XMLRPC_URL)
    logger.info(
        "XML-RPC wp.editPost: встановлюю post_thumbnail=%s для поста %s",
        media_id, post_id,
    )
    try:
        result = proxy.wp.editPost(
            0,           # blog_id (завжди 0 для single-site)
            WP_USER,
            WP_PASSWORD,
            int(post_id),
            {"post_thumbnail": int(media_id)},
        )
        logger.info("XML-RPC wp.editPost OK: result=%s", result)
    except Exception as e:
        # Не падаємо — пост вже створено
        logger.warning(
            "wp.editPost: не вдалось встановити featured image: %s\n"
            "Встановіть мініатюру вручну в WordPress Admin → Медіафайли.",
            e,
        )


# ══════════════════════════════════════════════════════
# СТВОРЕННЯ ПОСТУ
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
    Крок 1 — wp.newPost через python-wordpress-xmlrpc:
              title, content, status, category, publish_date.
    Крок 2 — wp.editPost через сировий ServerProxy:
              ТІЛЬКИ post_thumbnail, без торкання інших полів.

    status: "draft" | "publish" | "future"
    publish_date: timezone-aware datetime (Київ) для status="future"

    Повертає: {id: str, link: str}
    """
    client = _client()

    # ── Крок 1: NewPost ───────────────────────────────
    post = WordPressPost()
    post.title       = str(title)          # явний str — захист від None
    post.content     = content
    post.post_status = status

    # Дата запланованої публікації
    if publish_date is not None and status == "future":
        # Передаємо як naive local time (WordPress інтерпретує за timezone блогу)
        dt_naive = publish_date.replace(tzinfo=None)
        post.date = XmlRpcDateTime(dt_naive)
        logger.info("Запланована дата: %s (local naive)", dt_naive)

    # Категорія
    term          = WordPressTerm()
    term.id       = str(category_id)
    term.taxonomy = "category"
    post.terms    = [term]

    logger.info(
        "XML-RPC NewPost: title='%s' status=%s cat=%s",
        title, status, category_id,
    )
    try:
        post_id = client.call(posts.NewPost(post))
    except Exception as e:
        raise RuntimeError(f"Помилка створення поста: {e}") from e
    logger.info("XML-RPC NewPost OK: post_id=%s", post_id)

    # ── Крок 2: featured image (тільки post_thumbnail) ─
    _set_featured_image(post_id, featured_media_id)

    # ── Отримуємо permalink ───────────────────────────
    try:
        created = client.call(posts.GetPost(post_id, ["link"]))
        link    = getattr(created, "link", None) or f"{WP_URL.rstrip('/')}/?p={post_id}"
    except Exception:
        link = f"{WP_URL.rstrip('/')}/?p={post_id}"

    logger.info("XML-RPC готово: id=%s link=%s", post_id, link)
    return {"id": str(post_id), "link": link}