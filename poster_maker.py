# -*- coding: utf-8 -*-
"""
海报合成：背景图 + 文案排版 → 输出指定尺寸图片
"""
import io
import textwrap
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

SIZES = {
    "banner": (2560, 320),
    "square": (1160, 1016),
}

HEADERS = {"User-Agent": "Mozilla/5.0"}

# 字体路径（Windows 自带）
FONT_PATHS = [
    "C:/Windows/Fonts/msyh.ttc",    # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",  # 黑体
    "C:/Windows/Fonts/arial.ttf",   # 回退
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ============ 主题色（参考图驱动） ============

# 默认配色（无参考图时沿用原样式）
DEFAULT_THEME = {
    "primary": (231, 231, 231),    # 画布/背景底色
    "accent": (255, 140, 0),       # 强调色/按钮色
    "text": (0, 0, 0),             # 主文字色
    "sub_text": (85, 85, 85),      # 副文字色
    "card_bg": (247, 247, 250),    # 卡片/信息块背景色
}


def _hex_to_rgb(value, fallback):
    """把 '#RRGGBB' / 'RRGGBB' / [r,g,b] 转成 (r,g,b)，失败回退 fallback"""
    if value is None:
        return fallback
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return tuple(int(c) for c in value[:3])
        except (ValueError, TypeError):
            return fallback
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) == 6:
            try:
                return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return fallback
    return fallback


def _resolve_theme(plan: dict | None) -> dict:
    """从 plan.visual_strategy.theme 解析配色，缺失项回退默认值"""
    raw = ((plan or {}).get("visual_strategy") or {}).get("theme") or {}
    return {
        key: _hex_to_rgb(raw.get(key), default)
        for key, default in DEFAULT_THEME.items()
    }


def _ideal_text_on(bg: tuple) -> tuple:
    """根据背景明暗自动返回黑/白文字色，保证可读性"""
    r, g, b = bg[:3]
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return (0, 0, 0) if luminance > 150 else (255, 255, 255)


def _fetch_image(url: str) -> Image.Image:
    r = requests.get(url, headers=HEADERS, timeout=10)
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _enhance_image(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Contrast(img).enhance(1.03)
    img = ImageEnhance.Sharpness(img).enhance(1.10)
    return img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=105, threshold=2))


def _prepare_background(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    prepared = ImageOps.fit(img.convert("RGB"), size, method=Image.LANCZOS, centering=(0.5, 0.5))
    return _enhance_image(prepared)


def _fit_font_size(text: str, max_size: int, min_size: int, max_width: int, draw: ImageDraw.ImageDraw) -> ImageFont.FreeTypeFont:
    """在给定宽度内寻找可容纳文本的最大字号"""
    for size in range(max_size, min_size - 1, -2):
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return font
    return _load_font(min_size)


def _fit_wrapped_text(
    text: str,
    draw: ImageDraw.ImageDraw,
    max_width: int,
    max_lines: int,
    max_size: int,
    min_size: int,
):
    """寻找满足最大宽度和最大行数的字号，并返回换行结果"""
    text = (text or "").strip()
    best_font = _load_font(min_size)
    best_lines = _wrap_by_width(text, best_font, max_width, draw) if text else [""]
    for size in range(max_size, min_size - 1, -2):
        font = _load_font(size)
        lines = _wrap_by_width(text, font, max_width, draw) if text else [""]
        if lines and len(lines) <= max_lines:
            return font, lines
        best_font, best_lines = font, lines
    return best_font, best_lines[:max_lines]


def _darken(img: Image.Image, alpha: int = 120) -> Image.Image:
    """叠一层半透明黑色蒙版，让文字更清晰"""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, alpha))
    base = img.convert("RGBA")
    return Image.alpha_composite(base, overlay).convert("RGB")


def _plan_visual(plan: dict | None) -> dict:
    visual = dict((plan or {}).get("visual_strategy") or {})
    visual.setdefault("layout_mode", "left_text_right_visual")
    visual.setdefault("banner_layout_mode", "")
    visual.setdefault("square_layout_mode", "")
    visual.setdefault("image_strategy", "full_background")
    visual.setdefault("template_id", "generic")
    visual.setdefault("visual_language", "")
    visual.setdefault("style_strength", "medium")
    visual.setdefault("decoration_density", "medium")
    visual.setdefault("overlay_strength", "medium")
    visual.setdefault("replaceable_slots", [])
    visual.setdefault("fixed_elements", [])
    visual.setdefault("text_safe_area", "")
    return visual


def _plan_copy(plan: dict | None) -> dict:
    copy = dict((plan or {}).get("copywriting") or {})
    copy.setdefault("badge", "")
    return copy


def _plan_event(plan: dict | None) -> dict:
    event_info = dict((plan or {}).get("event_info") or {})
    event_info.setdefault("benefits", [])
    event_info.setdefault("event_time", "")
    return event_info


def _draw_badge(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, fill=(224, 151, 92), text_fill=(32, 24, 20)):
    if not text:
        return
    font = _fit_font_size(text[:18], 34, 22, 360, draw)
    bbox = draw.textbbox((0, 0), text[:18], font=font)
    width = bbox[2] - bbox[0] + 44
    height = bbox[3] - bbox[1] + 22
    draw.rounded_rectangle([x, y, x + width, y + height], radius=height // 2, fill=fill)
    draw.text((x + 18, y + 10 - bbox[1]), text[:18], font=font, fill=text_fill)


def _paste_cover_block(canvas: Image.Image, bg: Image.Image, box, radius: int = 24):
    x1, y1, x2, y2 = [int(v) for v in box]
    cover = _prepare_background(bg, (x2 - x1, y2 - y1))
    mask = Image.new("L", (x2 - x1, y2 - y1), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, x2 - x1, y2 - y1], radius=radius, fill=255)
    canvas.paste(cover, (x1, y1), mask)
    ImageDraw.Draw(canvas).rounded_rectangle([x1, y1, x2, y2], radius=radius, outline=(52, 52, 52), width=2)


def _draw_home_fixed_decor(draw: ImageDraw.ImageDraw, w: int, h: int, accent=(235, 156, 89)):
    draw.text((w - 210, 6), ">>>", font=_load_font(54), fill=accent)
    for i in range(4):
        bar_x = w - 92
        bar_y = 144 + i * 18
        draw.rounded_rectangle([bar_x, bar_y, bar_x + 10, bar_y + 10], radius=3, fill=(0, 0, 0))


def _draw_brand_mark(draw: ImageDraw.ImageDraw, x: int, y: int, fill: tuple, text: str = "KUJIALE", size: int = 28):
    draw.text((x, y), text, font=_load_font(size), fill=fill)


def _overlay_alpha(level: str, default: int = 110) -> int:
    mapping = {"light": 72, "medium": default, "strong": 156}
    return mapping.get((level or "").lower(), default)


def _compose_home_vertical_square(bg: Image.Image, title: str, subtitle: str, cta: str, copy_meta: dict, theme: dict) -> Image.Image:
    w, h = SIZES["square"]
    canvas = Image.new("RGB", (w, h), theme["primary"])
    draw = ImageDraw.Draw(canvas)
    margin_x = 68

    draw.text((margin_x, 24), "KUJIALE", font=_load_font(28), fill=theme["text"])
    _draw_home_fixed_decor(draw, w, h, theme["accent"])

    title_font, title_lines = _fit_wrapped_text(
        title, draw,
        max_width=w - margin_x * 2 - 140,
        max_lines=3, max_size=78, min_size=42,
    )
    title_y = 72
    title_gap = sum(title_font.getmetrics()) + 6
    for line in title_lines:
        line_bbox = draw.textbbox((0, 0), line, font=title_font)
        line_x = (w - (line_bbox[2] - line_bbox[0])) / 2
        draw.text((line_x, title_y), line, font=title_font, fill=theme["text"])
        title_y += title_gap

    scene_box = [margin_x, int(h * 0.40), w - margin_x, h - 64]
    _paste_cover_block(canvas, bg, scene_box, radius=30)

    chip_text = (subtitle or "主题内容").strip()
    chip_font = _fit_font_size(chip_text, 32, 18, 340, draw)
    chip_bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
    chip_w = chip_bbox[2] - chip_bbox[0] + 48
    chip_h = chip_bbox[3] - chip_bbox[1] + 24
    chip_x, chip_y = scene_box[0], scene_box[1]
    draw.rounded_rectangle([chip_x, chip_y, chip_x + chip_w, chip_y + chip_h],
                            radius=chip_h // 2, fill=theme["accent"], outline=(52, 52, 52), width=2)
    draw.text((chip_x + 18, chip_y + 11 - chip_bbox[1]), chip_text, font=chip_font,
              fill=_ideal_text_on(theme["accent"]))

    cta_text = cta.strip() or "立即查看"
    cta_font = _fit_font_size(cta_text, 58, 38, 280, draw)
    cta_bbox = draw.textbbox((0, 0), cta_text, font=cta_font)
    cta_w = cta_bbox[2] - cta_bbox[0]
    cta_h = cta_bbox[3] - cta_bbox[1]
    btn_x = scene_box[0] - 30
    btn_y = scene_box[3] - 96
    btn_w = cta_w + 138
    btn_h = cta_h + 42
    draw.rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], fill=theme["text"])
    draw.text((btn_x + 26, btn_y + 18 - cta_bbox[1]), cta_text, font=cta_font,
              fill=_ideal_text_on(theme["text"]))
    circle_d = btn_h - 20
    circle_x = btn_x + btn_w - circle_d - 18
    circle_y = btn_y + 10
    draw.ellipse([circle_x, circle_y, circle_x + circle_d, circle_y + circle_d], fill=theme["accent"])
    arrow_font = _load_font(max(28, circle_d // 2))
    arrow_bbox = draw.textbbox((0, 0), ">", font=arrow_font)
    arrow_x = circle_x + (circle_d - (arrow_bbox[2] - arrow_bbox[0])) / 2
    arrow_y = circle_y + (circle_d - (arrow_bbox[3] - arrow_bbox[1])) / 2 - arrow_bbox[1]
    draw.text((arrow_x, arrow_y), ">", font=arrow_font, fill=_ideal_text_on(theme["accent"]))
    return canvas


def _compose_square_editorial(bg: Image.Image, title: str, subtitle: str, cta: str, copy_meta: dict, theme: dict) -> Image.Image:
    w, h = SIZES["square"]
    canvas = Image.new("RGB", (w, h), theme["primary"])
    draw = ImageDraw.Draw(canvas)
    margin = 64
    left_w = int(w * 0.40)
    image_box = [left_w + 20, 28, w - 28, h - 28]
    _paste_cover_block(canvas, bg, image_box, radius=24)
    draw.rounded_rectangle([margin, 118, left_w - 24, 126], radius=4, fill=theme["accent"])
    _draw_brand_mark(draw, margin, 34, theme["text"], size=28)
    _draw_badge(draw, copy_meta.get("badge", ""), margin, 72, fill=theme["accent"], text_fill=_ideal_text_on(theme["accent"]))
    title_font, title_lines = _fit_wrapped_text(title, draw, left_w - margin - 34, 4, 68, 34)
    y = 178
    gap = sum(title_font.getmetrics()) + 8
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=theme["text"])
        y += gap
    sub_font, sub_lines = _fit_wrapped_text(subtitle or "主题内容", draw, left_w - margin - 34, 3, 30, 20)
    y += 18
    for line in sub_lines:
        draw.text((margin, y), line, font=sub_font, fill=theme["sub_text"])
        y += sum(sub_font.getmetrics()) + 6
    btn_y = h - 124
    btn_h = 64
    btn_w = min(left_w - margin - 10, 260)
    draw.rounded_rectangle([margin, btn_y, margin + btn_w, btn_y + btn_h], radius=btn_h // 2, fill=theme["accent"])
    btn_font = _fit_font_size(cta.strip() or "立即查看", 34, 24, btn_w - 48, draw)
    text = cta.strip() or "立即查看"
    bbox = draw.textbbox((0, 0), text, font=btn_font)
    draw.text((margin + (btn_w - (bbox[2] - bbox[0])) / 2, btn_y + (btn_h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
              text, font=btn_font, fill=_ideal_text_on(theme["accent"]))
    return canvas


def _compose_square_bold(bg: Image.Image, title: str, subtitle: str, cta: str, copy_meta: dict, theme: dict, overlay_strength: str) -> Image.Image:
    w, h = SIZES["square"]
    canvas = _darken(_prepare_background(bg, (w, h)), alpha=_overlay_alpha(overlay_strength, 124))
    draw = ImageDraw.Draw(canvas)
    text_fill = (255, 255, 255)
    sub_fill = (232, 232, 232)
    _draw_brand_mark(draw, 52, 32, text_fill, size=28)
    _draw_badge(draw, copy_meta.get("badge", ""), 52, 72, fill=theme["accent"], text_fill=_ideal_text_on(theme["accent"]))
    title_font, title_lines = _fit_wrapped_text(title, draw, w - 120, 3, 88, 42)
    y = 220
    gap = sum(title_font.getmetrics()) + 10
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        x = (w - (bbox[2] - bbox[0])) / 2
        draw.text((x, y), line, font=title_font, fill=text_fill)
        y += gap
    sub_font, sub_lines = _fit_wrapped_text(subtitle or "主题内容", draw, w - 220, 3, 34, 22)
    y += 12
    for line in sub_lines:
        bbox = draw.textbbox((0, 0), line, font=sub_font)
        x = (w - (bbox[2] - bbox[0])) / 2
        draw.text((x, y), line, font=sub_font, fill=sub_fill)
        y += sum(sub_font.getmetrics()) + 6
    cta_text = cta.strip() or "立即查看"
    btn_font = _fit_font_size(cta_text, 40, 26, 320, draw)
    bbox = draw.textbbox((0, 0), cta_text, font=btn_font)
    btn_w = (bbox[2] - bbox[0]) + 120
    btn_h = (bbox[3] - bbox[1]) + 34
    btn_x = (w - btn_w) // 2
    btn_y = h - 132
    draw.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=btn_h // 2, fill=theme["accent"])
    draw.text((btn_x + (btn_w - (bbox[2] - bbox[0])) / 2, btn_y + (btn_h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
              cta_text, font=btn_font, fill=_ideal_text_on(theme["accent"]))
    return canvas


def _compose_square_showcase(bg: Image.Image, title: str, subtitle: str, cta: str, copy_meta: dict, theme: dict) -> Image.Image:
    w, h = SIZES["square"]
    canvas = Image.new("RGB", (w, h), theme["card_bg"])
    draw = ImageDraw.Draw(canvas)
    _draw_brand_mark(draw, 54, 34, theme["text"], size=28)
    _draw_badge(draw, copy_meta.get("badge", ""), w - 260, 28, fill=theme["accent"], text_fill=_ideal_text_on(theme["accent"]))
    title_font, title_lines = _fit_wrapped_text(title, draw, w - 120, 2, 72, 38)
    y = 100
    gap = sum(title_font.getmetrics()) + 8
    for line in title_lines:
        draw.text((54, y), line, font=title_font, fill=theme["text"])
        y += gap
    chip_font = _fit_font_size(subtitle or "主题内容", 28, 18, 400, draw)
    chip_text = (subtitle or "主题内容").strip()[:18]
    chip_bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
    chip_w = chip_bbox[2] - chip_bbox[0] + 44
    chip_h = chip_bbox[3] - chip_bbox[1] + 20
    draw.rounded_rectangle([54, y + 6, 54 + chip_w, y + 6 + chip_h], radius=chip_h // 2, fill=theme["primary"])
    draw.text((72, y + 16 - chip_bbox[1]), chip_text, font=chip_font, fill=theme["sub_text"])
    image_box = [54, 250, w - 54, h - 158]
    _paste_cover_block(canvas, bg, image_box, radius=32)
    info_y = h - 122
    draw.rounded_rectangle([54, info_y, w - 54, h - 48], radius=28, fill=theme["text"])
    cta_font = _fit_font_size(cta.strip() or "立即查看", 34, 24, 220, draw)
    cta_text = cta.strip() or "立即查看"
    bbox = draw.textbbox((0, 0), cta_text, font=cta_font)
    draw.text((80, info_y + 20 - bbox[1]), cta_text, font=cta_font, fill=_ideal_text_on(theme["text"]))
    draw.text((w - 132, info_y + 12), ">", font=_load_font(46), fill=theme["accent"])
    return canvas


def _compose_banner_editorial(bg: Image.Image, title: str, subtitle: str, cta: str, copy_meta: dict, theme: dict, event_meta: dict) -> Image.Image:
    w, h = SIZES["banner"]
    canvas = Image.new("RGB", (w, h), theme["primary"])
    draw = ImageDraw.Draw(canvas)
    image_box = [int(w * 0.62), 20, w - 24, h - 20]
    _paste_cover_block(canvas, bg, image_box, radius=20)
    _draw_brand_mark(draw, 72, 24, theme["text"], size=30)
    _draw_badge(draw, copy_meta.get("badge", ""), 220, 20, fill=theme["accent"], text_fill=_ideal_text_on(theme["accent"]))
    if event_meta.get("event_time"):
        draw.text((72, 62), event_meta["event_time"][:26], font=_load_font(20), fill=theme["sub_text"])
    title_font = _fit_font_size(title, 64, 34, int(w * 0.46), draw)
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_y = 112
    draw.text((72, title_y), title, font=title_font, fill=theme["text"])
    subtitle_font = _load_font(24)
    subtitle_lines = _wrap_by_width(subtitle or "", subtitle_font, int(w * 0.44), draw)[:2]
    sub_y = title_y + (title_bbox[3] - title_bbox[1]) + 18
    for line in subtitle_lines:
        draw.text((72, sub_y), line, font=subtitle_font, fill=theme["sub_text"])
        sub_y += sum(subtitle_font.getmetrics()) + 4
    btn_w = 200
    btn_h = 54
    btn_x = 72
    btn_y = h - 76
    draw.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=btn_h // 2, fill=theme["accent"])
    btn_font = _fit_font_size(cta.strip() or "立即查看", 28, 22, btn_w - 36, draw)
    text = cta.strip() or "立即查看"
    bbox = draw.textbbox((0, 0), text, font=btn_font)
    draw.text((btn_x + (btn_w - (bbox[2] - bbox[0])) / 2, btn_y + (btn_h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
              text, font=btn_font, fill=_ideal_text_on(theme["accent"]))
    return canvas


def _compose_banner_bold(bg: Image.Image, title: str, subtitle: str, cta: str, copy_meta: dict, theme: dict, event_meta: dict, overlay_strength: str) -> Image.Image:
    w, h = SIZES["banner"]
    canvas = _darken(_prepare_background(bg, (w, h)), alpha=_overlay_alpha(overlay_strength, 116))
    draw = ImageDraw.Draw(canvas)
    text_fill = (255, 255, 255)
    sub_fill = (232, 232, 232)
    _draw_brand_mark(draw, 68, 26, text_fill, size=30)
    _draw_badge(draw, copy_meta.get("badge", ""), w - 300, 20, fill=theme["accent"], text_fill=_ideal_text_on(theme["accent"]))
    if event_meta.get("event_time"):
        draw.text((w - 420, 76), event_meta["event_time"][:26], font=_load_font(20), fill=sub_fill)
    title_font = _fit_font_size(title, 72, 40, int(w * 0.76), draw)
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_x = (w - (title_bbox[2] - title_bbox[0])) / 2
    title_y = 88
    draw.text((title_x, title_y), title, font=title_font, fill=text_fill)
    sub_font = _load_font(22)
    lines = _wrap_by_width(subtitle or "", sub_font, int(w * 0.56), draw)[:2]
    sub_y = title_y + (title_bbox[3] - title_bbox[1]) + 12
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=sub_font)
        draw.text(((w - (bbox[2] - bbox[0])) / 2, sub_y), line, font=sub_font, fill=sub_fill)
        sub_y += sum(sub_font.getmetrics()) + 3
    btn_w = 208
    btn_h = 52
    btn_x = (w - btn_w) // 2
    btn_y = h - 72
    draw.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=btn_h // 2, fill=theme["accent"])
    btn_font = _fit_font_size(cta.strip() or "立即查看", 28, 22, btn_w - 36, draw)
    text = cta.strip() or "立即查看"
    bbox = draw.textbbox((0, 0), text, font=btn_font)
    draw.text((btn_x + (btn_w - (bbox[2] - bbox[0])) / 2, btn_y + (btn_h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
              text, font=btn_font, fill=_ideal_text_on(theme["accent"]))
    return canvas


def _compose_banner_showcase(bg: Image.Image, title: str, subtitle: str, cta: str, copy_meta: dict, theme: dict, event_meta: dict) -> Image.Image:
    w, h = SIZES["banner"]
    canvas = Image.new("RGB", (w, h), theme["card_bg"])
    draw = ImageDraw.Draw(canvas)
    left_card = [28, 22, int(w * 0.38), h - 22]
    right_card = [int(w * 0.41), 22, w - 28, h - 22]
    draw.rounded_rectangle(left_card, radius=24, fill=theme["primary"])
    _paste_cover_block(canvas, bg, right_card, radius=24)
    _draw_brand_mark(draw, left_card[0] + 28, left_card[1] + 22, theme["text"], size=28)
    _draw_badge(draw, copy_meta.get("badge", ""), left_card[0] + 28, left_card[1] + 58, fill=theme["accent"], text_fill=_ideal_text_on(theme["accent"]))
    if event_meta.get("event_time"):
        draw.text((left_card[0] + 28, left_card[1] + 106), event_meta["event_time"][:22], font=_load_font(18), fill=theme["sub_text"])
    title_font = _fit_font_size(title, 44, 28, left_card[2] - left_card[0] - 56, draw)
    title_lines = _wrap_by_width(title, title_font, left_card[2] - left_card[0] - 56, draw)[:2]
    y = left_card[1] + 136
    for line in title_lines:
        draw.text((left_card[0] + 28, y), line, font=title_font, fill=theme["text"])
        y += sum(title_font.getmetrics()) + 6
    sub_font = _load_font(20)
    for line in _wrap_by_width(subtitle or "", sub_font, left_card[2] - left_card[0] - 56, draw)[:3]:
        draw.text((left_card[0] + 28, y), line, font=sub_font, fill=theme["sub_text"])
        y += sum(sub_font.getmetrics()) + 4
    btn_w = 180
    btn_h = 46
    btn_x = left_card[0] + 28
    btn_y = left_card[3] - btn_h - 22
    draw.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=btn_h // 2, fill=theme["text"])
    btn_font = _fit_font_size(cta.strip() or "立即查看", 24, 18, btn_w - 28, draw)
    text = cta.strip() or "立即查看"
    bbox = draw.textbbox((0, 0), text, font=btn_font)
    draw.text((btn_x + (btn_w - (bbox[2] - bbox[0])) / 2, btn_y + (btn_h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
              text, font=btn_font, fill=_ideal_text_on(theme["text"]))
    return canvas


def make_poster(
    cover_url: str,
    title: str,
    subtitle: str,
    cta: str,
    size_key: str = "banner",
    plan: dict = None,
) -> Image.Image:
    """
    cover_url : 背景图 URL（空时用纯色占位）
    title     : 主标题
    subtitle  : 副标题 / 宣传文案
    cta       : 行动号召（如"立即查看"）
    size_key  : "banner"(2560×320) 或 "square"(1160×1016)
    """
    w, h = SIZES[size_key]
    visual = _plan_visual(plan)

    # 背景：有 URL 则下载，否则纯色占位
    if cover_url:
        raw_bg = _fetch_image(cover_url)
        if size_key == "square" and visual.get("template_id") == "home_vertical_promo":
            bg = raw_bg
        else:
            bg = _prepare_background(raw_bg, (w, h))
    else:
        bg = Image.new("RGB", (w, h), (30, 30, 60))

    return _compose(bg, title, subtitle, cta, size_key, plan)


def make_poster_any(bg_source, title, subtitle, cta, size_key="banner", plan: dict = None) -> Image.Image:
    """bg_source 可以是 URL 字符串 或 PIL Image（运营上传图）"""
    if isinstance(bg_source, Image.Image):
        w, h = SIZES[size_key]
        visual = _plan_visual(plan)
        if size_key == "square" and visual.get("template_id") == "home_vertical_promo":
            bg_img = bg_source.convert("RGB")
        else:
            bg_img = _prepare_background(bg_source.convert("RGB"), (w, h))
        return _compose(bg_img, title, subtitle, cta, size_key, plan)
    return make_poster(bg_source, title, subtitle, cta, size_key, plan)


def _compose(bg: Image.Image, title: str, subtitle: str, cta: str, size_key: str, plan: dict = None) -> Image.Image:
    """内部排版逻辑，bg 已是目标尺寸 RGB Image"""
    w, h = bg.size
    source_bg = bg.copy()
    visual = _plan_visual(plan)
    copy_meta = _plan_copy(plan)
    event_meta = _plan_event(plan)
    theme = _resolve_theme(plan)          # ← 参考图主题色（无则用默认值）
    layout_mode = visual.get("layout_mode", "left_text_right_visual")
    image_strategy = visual.get("image_strategy", "full_background")
    template_id = visual.get("template_id", "generic")
    banner_layout_mode = visual.get("banner_layout_mode") or layout_mode
    square_layout_mode = visual.get("square_layout_mode") or layout_mode
    overlay_strength = visual.get("overlay_strength", "medium")

    if size_key == "square":
        if template_id in ("reference_showcase", "home_vertical_promo"):
            return _compose_home_vertical_square(source_bg, title, subtitle, cta, copy_meta, theme)
        if template_id == "reference_editorial":
            return _compose_square_editorial(source_bg, title, subtitle, cta, copy_meta, theme)
        if template_id == "reference_bold":
            return _compose_square_bold(source_bg, title, subtitle, cta, copy_meta, theme, overlay_strength)
        if square_layout_mode == "centered":
            return _compose_square_bold(source_bg, title, subtitle, cta, copy_meta, theme, overlay_strength)
        if square_layout_mode == "top_text_bottom_visual":
            return _compose_square_showcase(source_bg, title, subtitle, cta, copy_meta, theme)
        canvas = Image.new("RGB", (w, h), theme["primary"])
        draw = ImageDraw.Draw(canvas)
        margin_x = 68
        draw.text((margin_x, 24), "KUJIALE", font=_load_font(28), fill=theme["text"])
        _draw_badge(draw, copy_meta.get("badge", ""), margin_x + 148, 18, fill=theme["accent"],
                    text_fill=_ideal_text_on(theme["accent"]))
        draw.text((w - 220, 4), ">>>", font=_load_font(54), fill=theme["accent"])

        if layout_mode == "left_text_right_visual":
            title_font = _load_font(72)
            title_lines = _wrap_by_width(title, title_font, 430, draw)[:3]
            title_y = 110
            for line in title_lines:
                draw.text((margin_x, title_y), line, font=title_font, fill=theme["text"])
                title_y += sum(title_font.getmetrics()) + 6
            chip_text = (subtitle or "主题内容").strip()[:16]
            chip_font = _fit_font_size(chip_text, 32, 24, 320, draw)
            chip_bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
            chip_w = chip_bbox[2] - chip_bbox[0] + 48
            chip_h = chip_bbox[3] - chip_bbox[1] + 24
            draw.rounded_rectangle([margin_x, title_y + 8, margin_x + chip_w, title_y + 8 + chip_h],
                                    radius=chip_h // 2, fill=theme["accent"])
            draw.text((margin_x + 18, title_y + 20 - chip_bbox[1]), chip_text, font=chip_font,
                      fill=_ideal_text_on(theme["accent"]))
            image_box = [470, 150, w - 64, h - 88]
            _paste_cover_block(canvas, bg, image_box, radius=28)
            btn_y = h - 164
            btn_x = margin_x
        else:
            for i in range(4):
                draw.rounded_rectangle([w - 118, 144 + i * 18, w - 108, 154 + i * 18], radius=3, fill=theme["text"])
            title_font = _load_font(74)
            title_lines = _wrap_by_width(title, title_font, w - margin_x * 2 - 80, draw)[:2]
            title_y = 72
            for line in title_lines:
                draw.text((margin_x, title_y), line, font=title_font, fill=theme["text"])
                title_y += sum(title_font.getmetrics()) + 6
            image_x, image_y = margin_x, title_y + 18
            image_h = 626
            _paste_cover_block(canvas, bg, [image_x, image_y, image_x + w - margin_x * 2, image_y + image_h],
                               radius=0 if image_strategy == "full_background" else 24)
            chip_text = (subtitle or "").strip()[:14]
            chip_font = _fit_font_size(chip_text, 34, 24, 300, draw)
            chip_bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
            chip_w = chip_bbox[2] - chip_bbox[0] + 52
            chip_h = chip_bbox[3] - chip_bbox[1] + 28
            draw.rounded_rectangle([image_x, image_y, image_x + chip_w, image_y + chip_h],
                                    radius=chip_h // 2, fill=theme["accent"], outline=(52, 52, 52), width=2)
            draw.text((image_x + 18, image_y + 12 - chip_bbox[1]), chip_text, font=chip_font,
                      fill=_ideal_text_on(theme["accent"]))
            btn_y = image_y + image_h - 132
            btn_x = 32
            if event_meta.get("event_time"):
                draw.text((w - 360, 40), event_meta["event_time"][:24], font=_load_font(24), fill=theme["sub_text"])

        cta_text = cta.strip() or "立即查看"
        cta_font = _fit_font_size(cta_text, 58, 38, 280, draw)
        cta_bbox = draw.textbbox((0, 0), cta_text, font=cta_font)
        cta_w, cta_h = cta_bbox[2] - cta_bbox[0], cta_bbox[3] - cta_bbox[1]
        btn_w, btn_h = cta_w + 138, cta_h + 42
        draw.rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], fill=theme["text"])
        draw.text((btn_x + 26, btn_y + 18 - cta_bbox[1]), cta_text, font=cta_font,
                  fill=_ideal_text_on(theme["text"]))
        circle_d = btn_h - 20
        cx, cy = btn_x + btn_w - circle_d - 18, btn_y + 10
        draw.ellipse([cx, cy, cx + circle_d, cy + circle_d], fill=theme["accent"])
        af = _load_font(max(28, circle_d // 2))
        ab = draw.textbbox((0, 0), ">", font=af)
        draw.text((cx + (circle_d - (ab[2] - ab[0])) / 2, cy + (circle_d - (ab[3] - ab[1])) / 2 - ab[1]),
                  ">", font=af, fill=_ideal_text_on(theme["accent"]))
        return canvas

    # banner（及兜底）
    full_bg = image_strategy == "full_background"
    if template_id == "reference_editorial":
        return _compose_banner_editorial(source_bg, title, subtitle, cta, copy_meta, theme, event_meta)
    if template_id == "reference_bold":
        return _compose_banner_bold(source_bg, title, subtitle, cta, copy_meta, theme, event_meta, overlay_strength)
    if template_id in ("reference_showcase", "home_vertical_promo"):
        return _compose_banner_showcase(source_bg, title, subtitle, cta, copy_meta, theme, event_meta)
    if banner_layout_mode == "centered":
        return _compose_banner_bold(source_bg, title, subtitle, cta, copy_meta, theme, event_meta, overlay_strength)
    if banner_layout_mode == "top_text_bottom_visual":
        return _compose_banner_showcase(source_bg, title, subtitle, cta, copy_meta, theme, event_meta)
    if full_bg:
        bg = _darken(source_bg, alpha=_overlay_alpha(overlay_strength, 120))
    else:
        bg = Image.new("RGB", (w, h), theme["card_bg"])
    draw = ImageDraw.Draw(bg)

    if size_key == "banner":
        text_fill = (255, 255, 255) if full_bg else theme["text"]
        sub_fill = (220, 220, 220) if full_bg else theme["sub_text"]
        brand_font = _load_font(max(28, h // 10))

        if layout_mode == "centered":
            draw.text((w * 0.04, h * 0.12), "KUJIALE", font=brand_font, fill=text_fill)
            _draw_badge(draw, copy_meta.get("badge", ""), int(w * 0.5 - 120), 20,
                        fill=(255, 255, 255) if full_bg else theme["accent"],
                        text_fill=(0, 0, 0) if full_bg else _ideal_text_on(theme["accent"]))
            title_font = _load_font(max(40, h // 6))
            title_bbox = draw.textbbox((0, 0), title, font=title_font)
            draw.text(((w - (title_bbox[2] - title_bbox[0])) / 2, h * 0.30), title, font=title_font, fill=text_fill)
            wrapped = "\n".join(textwrap.wrap(subtitle, 40))
            sub_font = _load_font(max(24, h // 10))
            sub_bbox = draw.multiline_textbbox((0, 0), wrapped, font=sub_font, spacing=4)
            draw.multiline_text(((w - (sub_bbox[2] - sub_bbox[0])) / 2, h * 0.54), wrapped,
                                font=sub_font, fill=sub_fill, spacing=4, align="center")
            btn_anchor = "center"
            if event_meta.get("event_time"):
                ef = _load_font(22)
                eb = draw.textbbox((0, 0), event_meta["event_time"][:24], font=ef)
                draw.text(((w - (eb[2] - eb[0])) / 2, h * 0.16), event_meta["event_time"][:24], font=ef, fill=sub_fill)
        elif layout_mode == "top_text_bottom_visual":
            if full_bg:
                bg = _darken(source_bg, alpha=90)
                draw = ImageDraw.Draw(bg)
            else:
                draw.rectangle([0, 0, w, h], fill=theme["card_bg"])
                _paste_cover_block(bg, source_bg, [int(w * 0.70), 24, int(w * 0.95), h - 24], radius=22)
            draw.text((w * 0.04, 24), "KUJIALE", font=brand_font, fill=text_fill)
            _draw_badge(draw, copy_meta.get("badge", ""), int(w * 0.18), 18,
                        fill=theme["accent"], text_fill=_ideal_text_on(theme["accent"]))
            draw.text((w * 0.04, 92), title, font=_load_font(max(34, h // 5)), fill=text_fill)
            info_bar_y = int(h * 0.63)
            bar_fill = (20, 20, 22) if full_bg else (255, 255, 255)
            draw.rounded_rectangle([int(w * 0.04), info_bar_y, int(w * 0.66), h - 26], radius=20, fill=bar_fill)
            info_fill = (235, 235, 235) if full_bg else theme["sub_text"]
            draw.multiline_text((w * 0.06, info_bar_y + 14),
                                "\n".join(textwrap.wrap(subtitle, 34)),
                                font=_load_font(max(22, h // 11)), fill=info_fill, spacing=4)
            if event_meta.get("event_time"):
                draw.text((w * 0.40, 32), event_meta["event_time"][:24], font=_load_font(20), fill=info_fill)
            btn_anchor = "right"
        else:
            if full_bg:
                draw.text((w * 0.04, h * 0.12), "KUJIALE", font=brand_font, fill=text_fill)
                title_x, title_y = w * 0.04, h * 0.35
            else:
                draw.rectangle([0, 0, w, h], fill=theme["card_bg"])
                _paste_cover_block(bg, source_bg, [int(w * 0.58), 22, int(w * 0.96), h - 22], radius=24)
                draw.text((w * 0.04, h * 0.12), "KUJIALE", font=brand_font, fill=theme["text"])
                title_x, title_y = w * 0.04, h * 0.28
            _draw_badge(draw, copy_meta.get("badge", ""), int(w * 0.04), 22,
                        fill=(255, 255, 255) if full_bg else theme["accent"],
                        text_fill=(0, 0, 0) if full_bg else _ideal_text_on(theme["accent"]))
            draw.text((title_x, title_y), title, font=_load_font(max(36, h // 6)), fill=text_fill)
            draw.text((w * 0.04, h * (0.55 if full_bg else 0.56)),
                      "\n".join(textwrap.wrap(subtitle, 34 if not full_bg else 40)),
                      font=_load_font(max(24, h // 10)), fill=sub_fill)
            btn_anchor = "right" if full_bg else "left"
            if event_meta.get("event_time"):
                draw.text((w * 0.04, h * 0.20), event_meta["event_time"][:24],
                          font=_load_font(20), fill=sub_fill if full_bg else theme["sub_text"])

        cta_text = cta.strip() or "立即查看"
        cta_font = _fit_font_size(cta_text, max(28, h // 10), 24, int(w * 0.16), draw)
        cta_bbox = draw.textbbox((0, 0), cta_text, font=cta_font)
        cta_w, cta_h = cta_bbox[2] - cta_bbox[0], cta_bbox[3] - cta_bbox[1]
        pad_x, pad_y = max(22, w // 120), max(12, h // 22)
        btn_w, btn_h = cta_w + pad_x * 2, cta_h + pad_y * 2
        bottom_safe = max(18, int(h * 0.09))
        btn_x = int((w - btn_w) / 2) if btn_anchor == "center" else \
                int(w * 0.04) if btn_anchor == "left" else int(w * 0.96 - btn_w)
        btn_y = h - bottom_safe - btn_h
        if layout_mode == "top_text_bottom_visual":
            btn_y = h - max(16, int(h * 0.08)) - btn_h
            if btn_anchor == "right":
                btn_x = int(w * 0.66 - btn_w)
        elif layout_mode == "centered":
            btn_y = h - max(18, int(h * 0.10)) - btn_h
        draw.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=btn_h // 2, fill=theme["accent"])
        draw.text((btn_x + (btn_w - cta_w) / 2, btn_y + (btn_h - cta_h) / 2 - cta_bbox[1]),
                  cta_text, font=cta_font, fill=_ideal_text_on(theme["accent"]))
        return bg

    return bg


# ============ 详情页长图 ============

ORANGE = (255, 140, 0)
DARK = (40, 40, 50)
GRAY = (90, 90, 100)
CARD_BG = (247, 247, 250)


def _wrap_by_width(text, font, max_w, draw):
    """按像素宽度换行，支持中英文混排"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        test = cur + ch
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _draw_wrapped(draw, text, font, x, y, max_w, fill, line_gap=10):
    """绘制自动换行文本，返回绘制后的 y 坐标"""
    lines = _wrap_by_width(text, font, max_w, draw)
    asc, desc = font.getmetrics()
    line_h = asc + desc
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h + line_gap
    return y


def make_detail_page(bg_source, detail: dict, width: int = 1080, plan: dict = None) -> Image.Image:
    """
    生成详情页竖向长图：头图区 + 多卖点板块 + CTA
    bg_source : 头图背景（URL字符串 或 PIL Image）
    detail    : ai_writer.generate_detail() 返回的结构化内容
    plan      : poster_plan，用于读取参考图主题色（可选）
    """
    theme = _resolve_theme(plan)
    accent = theme["accent"]
    card_bg = theme["card_bg"]
    dark = theme["text"]
    gray = theme["sub_text"]

    pad = int(width * 0.06)          # 左右留白
    content_w = width - pad * 2
    head_h = 720                     # 头图区高度

    headline = detail.get("headline", "")
    subhead = detail.get("subhead", "")
    sections = detail.get("sections", []) or []
    cta = detail.get("cta", "立即体验")

    # 字体
    f_brand = _load_font(34)
    f_head = _load_font(66)
    f_sub = _load_font(36)
    f_sec_h = _load_font(46)
    f_sec_b = _load_font(32)
    f_cta = _load_font(44)

    # —— 先用临时画布测量各板块高度，算出总高 ——
    probe = ImageDraw.Draw(Image.new("RGB", (width, 10)))
    sec_line_h = sum(f_sec_b.getmetrics()) + 12
    sec_head_h = sum(f_sec_h.getmetrics())
    card_gap = 36
    card_pad = 32

    card_heights = []
    for sec in sections:
        body_lines = _wrap_by_width(sec.get("body", ""), f_sec_b, content_w - card_pad * 2 - 20, probe)
        h = card_pad + sec_head_h + 18 + len(body_lines) * sec_line_h + card_pad
        card_heights.append(h)

    cta_block_h = 200
    total_h = head_h + 40 + sum(card_heights) + card_gap * len(card_heights) + cta_block_h

    # —— 画布 ——
    canvas = Image.new("RGB", (width, total_h), (255, 255, 255))

    # 头图区
    if isinstance(bg_source, Image.Image):
        head_bg = bg_source.convert("RGB").resize((width, head_h), Image.LANCZOS)
    elif bg_source:
        head_bg = _fetch_image(bg_source).resize((width, head_h), Image.LANCZOS)
    else:
        head_bg = Image.new("RGB", (width, head_h), (30, 30, 60))
    head_bg = _darken(head_bg, alpha=110)
    canvas.paste(head_bg, (0, 0))

    d = ImageDraw.Draw(canvas)
    d.text((pad, int(head_h * 0.10)), "KUJIALE 酷家乐", font=f_brand, fill=(255, 255, 255))
    y = int(head_h * 0.34)
    y = _draw_wrapped(d, headline, f_head, pad, y, content_w, (255, 255, 255), line_gap=14)
    y += 12
    _draw_wrapped(d, subhead, f_sub, pad, y, content_w, (235, 235, 235), line_gap=10)

    # 卖点板块
    y = head_h + 40
    for sec, ch in zip(sections, card_heights):
        # 卡片底
        d.rounded_rectangle([pad, y, width - pad, y + ch], radius=20, fill=card_bg)
        # 左侧强调色竖条
        d.rounded_rectangle([pad, y + card_pad, pad + 10, y + ch - card_pad], radius=5, fill=accent)
        tx = pad + card_pad + 20
        ty = y + card_pad
        d.text((tx, ty), sec.get("heading", ""), font=f_sec_h, fill=dark)
        ty += sec_head_h + 18
        _draw_wrapped(d, sec.get("body", ""), f_sec_b, tx, ty, content_w - card_pad * 2 - 20, gray, line_gap=12)
        y += ch + card_gap

    # CTA 按钮（居中）
    cta_text = cta
    cta_bbox = d.textbbox((0, 0), cta_text, font=f_cta)
    cta_w = cta_bbox[2] - cta_bbox[0]
    cta_h = cta_bbox[3] - cta_bbox[1]
    btn_w = cta_w + 120
    btn_h = cta_h + 56
    btn_x = (width - btn_w) // 2
    btn_y = y + 30
    d.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=btn_h // 2, fill=accent)
    d.text((btn_x + (btn_w - cta_w) // 2, btn_y + (btn_h - cta_h) // 2 - cta_bbox[1]), cta_text, font=f_cta, fill=_ideal_text_on(accent))

    return canvas


if __name__ == "__main__":
    # 快速冒烟测试
    cover = "https://i0.hdslb.com/bfs/archive/test.jpg"  # 占位，实际传真实封面URL
    for key in ("banner", "square"):
        img = make_poster(
            cover_url="",           # 空→用纯色占位背景
            title="3分钟解锁异形门衣柜制作密码！",
            subtitle="如何做异形门衣柜？跟着酷家乐王一一步步学会！",
            cta="立即查看",
            size_key=key,
        )
        out = f"test_{key}.jpg"
        img.save(out, quality=90)
        print(f"saved {out}  {img.size}")

    # 详情页长图冒烟
    detail_demo = {
        "headline": "3分钟解锁异形门衣柜",
        "subhead": "跟着酷家乐，从设计到出图一气呵成",
        "sections": [
            {"heading": "智能建模", "body": "拖拽即可生成异形门衣柜模型，无需手动建模，效率提升十倍。"},
            {"heading": "实时渲染", "body": "所见即所得的渲染效果，灯光材质一键调节，方案立等可取。"},
            {"heading": "海量素材", "body": "数百万家居素材库随取随用，覆盖各类风格与户型场景。"},
        ],
        "cta": "立即体验",
    }
    dp = make_detail_page("", detail_demo)
    dp.save("test_detail.jpg", quality=90)
    print(f"saved test_detail.jpg  {dp.size}")
