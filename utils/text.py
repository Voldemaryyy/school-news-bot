# ======================================================
# utils/text.py — утиліти для обробки тексту новини
# ======================================================

# ── 5 нерозривних пробілів для абзацного відступу ────
# Нерозривний пробіл (\u00a0) надійніший за text-indent:
# він гарантовано зберігається в будь-якій темі WordPress.
NBSP   = "\u00a0"
INDENT = NBSP * 5   # рівно 5 символів-відступів

# ── Стиль абзацу (шкільна новина) ────────────────────
# margin:0       — абсолютно без розривів між абзацами
# line-height    — 1.5 як замовлено
# text-align     — вирівнювання по ширині
# color:#000000  — чорний текст
PARA_STYLE = (
    "color:#000000;"
    "line-height:1.5;"
    "margin:0;"
    "padding:0;"
    "text-align:justify;"
)

# ── Стиль фото ────────────────────────────────────────
IMG_STYLE = (
    "max-width:100%;"
    "height:auto;"
    "display:block;"
    "margin:1em auto;"
)


def split_into_paragraphs(raw_text: str) -> list[str]:
    """
    Розбиває текст на абзаци по рядках.
    Порожні рядки пропускаються.
    """
    return [line.strip() for line in raw_text.split("\n") if line.strip()]


def build_full_html(
    paragraphs: list[str],
    photo_map: dict[int, list[str]],
) -> str:
    """
    Збирає фінальний HTML.

    photo_map: {position: [html, ...]}
        0 → перед усіма абзацами
        N → після абзацу N

    Кожен абзац: <p style="...">INDENT текст</p>
    5 нерозривних пробілів на початку = абзацний відступ.
    margin:0 = без розривів між абзацами.
    """
    parts: list[str] = []

    for img_html in photo_map.get(0, []):
        parts.append(img_html)

    for i, para in enumerate(paragraphs, start=1):
        parts.append(f'<p style="{PARA_STYLE}">{INDENT}{para}</p>')
        for img_html in photo_map.get(i, []):
            parts.append(img_html)

    # Абзаци стикаються (без пустих рядків між ними)
    return "\n".join(parts)


def make_wp_image_html(wp_image_url: str, alt: str = "") -> str:
    """
    WordPress Gutenberg image block.
    Inline margin:1em auto — відступ від тексту зверху та знизу.
    """
    return (
        f'<!-- wp:image {{"sizeSlug":"large","linkDestination":"none"}} -->\n'
        f'<figure class="wp-block-image size-large aligncenter">'
        f'<img src="{wp_image_url}" alt="{alt}" style="{IMG_STYLE}"/>'
        f'</figure>\n'
        f'<!-- /wp:image -->'
    )