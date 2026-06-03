# -*- coding: utf-8 -*-
"""
海报合成：背景图 + 文案排版 → 输出指定尺寸图片
"""
import io
import textwrap
import requests
from PIL import Image, ImageDraw, ImageFont

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


def _fetch_image(url: str) -> Image.Image:
    r = requests.get(url, headers=HEADERS, timeout=10)
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _darken(img: Image.Image, alpha: int = 120) -> Image.Image:
    """叠一层半透明黑色蒙版，让文字更清晰"""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, alpha))
    base = img.convert("RGBA")
    return Image.alpha_composite(base, overlay).convert("RGB")


def make_poster(
    cover_url: str,
    title: str,
    subtitle: str,
    cta: str,
    size_key: str = "banner",
) -> Image.Image:
    """
    cover_url : 背景图 URL（空时用纯色占位）
    title     : 主标题
    subtitle  : 副标题 / 宣传文案
    cta       : 行动号召（如"立即查看"）
    size_key  : "banner"(2560×320) 或 "square"(1160×1016)
    """
    w, h = SIZES[size_key]

    # 背景：有 URL 则下载，否则纯色占位
    if cover_url:
        bg = _fetch_image(cover_url).resize((w, h), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (w, h), (30, 30, 60))

    return _compose(bg, title, subtitle, cta, size_key)


def make_poster_any(bg_source, title, subtitle, cta, size_key="banner") -> Image.Image:
    """bg_source 可以是 URL 字符串 或 PIL Image（运营上传图）"""
    if isinstance(bg_source, Image.Image):
        w, h = SIZES[size_key]
        bg_img = bg_source.convert("RGB").resize((w, h), Image.LANCZOS)
        return _compose(bg_img, title, subtitle, cta, size_key)
    return make_poster(bg_source, title, subtitle, cta, size_key)


def _compose(bg: Image.Image, title: str, subtitle: str, cta: str, size_key: str) -> Image.Image:
    """内部排版逻辑，bg 已是目标尺寸 RGB Image"""
    w, h = bg.size
    bg = _darken(bg)
    draw = ImageDraw.Draw(bg)

    brand_font = _load_font(max(28, h // 10))
    draw.text((w * 0.04, h * 0.12), "KUJIALE", font=brand_font, fill=(255, 255, 255, 220))

    title_size = max(36, h // 6) if size_key == "banner" else max(60, h // 12)
    draw.text((w * 0.04, h * 0.35), title, font=_load_font(title_size), fill="white")

    sub_size = max(24, h // 10) if size_key == "banner" else max(38, h // 20)
    wrap_width = 40 if size_key == "banner" else 28
    wrapped = "\n".join(textwrap.wrap(subtitle, wrap_width))
    sub_y = h * 0.58 if size_key == "banner" else h * 0.52
    draw.text((w * 0.04, sub_y), wrapped, font=_load_font(sub_size), fill=(220, 220, 220))

    cta_font = _load_font(max(28, h // 9))
    cta_text = f">>> {cta}"
    cta_bbox = draw.textbbox((0, 0), cta_text, font=cta_font)
    cta_w = cta_bbox[2] - cta_bbox[0]
    cta_x = w - cta_w - w * 0.04
    cta_y = h * 0.4
    pad = 12
    draw.rectangle([cta_x - pad, cta_y - pad, cta_x + cta_w + pad, cta_y + (cta_bbox[3] - cta_bbox[1]) + pad], fill=(255, 140, 0))
    draw.text((cta_x, cta_y), cta_text, font=cta_font, fill="white")
    return bg


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
