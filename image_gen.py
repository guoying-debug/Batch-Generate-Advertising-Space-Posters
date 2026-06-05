# -*- coding: utf-8 -*-
"""模式一：调 Cogview-3-Flash 根据 image_prompt 生成背景图，返回 PIL Image"""
import os, io, re, requests
from PIL import Image, ImageEnhance, ImageFilter
from openai import OpenAI, BadRequestError


def _enhance_generated(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Contrast(img).enhance(1.04)
    img = ImageEnhance.Sharpness(img).enhance(1.08)
    return img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=95, threshold=2))


def _sanitize_prompt_for_image_model(prompt: str) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        prompt = "现代家居室内场景，柔和自然光，简洁大气"

    # 删除会诱导模型在背景里画文字的词汇
    banned_words = [
        "标题", "主标题", "副标题", "文案", "文字", "按钮", "logo", "LOGO",
        "KUJIALE", "酷家乐", "平台", "立即体验", "立即查看", "广告语", "标语",
        "界面截图", "UI", "屏幕文字", "海报文字", "字体", "排版",
    ]
    for word in banned_words:
        prompt = prompt.replace(word, "")

    # 去掉负面约束句式（容易触发风控）
    prompt = re.sub(r"(不得|禁止|严禁|不要|无需)[^，。；\n]{0,20}", "", prompt)
    prompt = re.sub(r"[\"“”‘’「」]+", "", prompt)
    prompt = re.sub(r"\s+", " ", prompt).strip(" ，。；")

    safe_suffix = (
        " 纯背景视觉图，画面中不包含任何文字、字母、数字、水印、logo、"
        "招牌、按钮、UI界面或屏幕截图。构图干净留白，现代写实，高级质感。"
    )
    compact = (prompt[:180] if len(prompt) > 180 else prompt).strip(" ，。；")
    return compact + safe_suffix


def _request_image(client: OpenAI, prompt: str, size: str):
    return client.images.generate(
        model=os.getenv("IMAGE_MODEL", "cogview-3-flash"),
        prompt=prompt,
        size=size,
        n=1,
    )


def generate_background(image_prompt: str, size: str = "1440x720") -> Image.Image:
    """
    image_prompt : ai_writer.generate() 返回的 image_prompt 字段
    size         : 智谱支持的尺寸，默认 1440x720（宽图适合 banner）
    """
    client = OpenAI(
        api_key=os.environ["ZHIPUAI_API_KEY"],
        base_url=os.getenv("ZHIPUAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
    )
    safe_prompt = _sanitize_prompt_for_image_model(image_prompt)
    try:
        r = _request_image(client, safe_prompt, size)
    except BadRequestError as e:
        msg = str(e)
        if "1301" not in msg and "敏感内容" not in msg and "不安全" not in msg:
            raise
        fallback_prompt = _sanitize_prompt_for_image_model("")
        print("[image_gen] 命中过滤，已自动使用无文字安全提示词重试")
        r = _request_image(client, fallback_prompt, size)
    img_url = r.data[0].url
    resp = requests.get(img_url, timeout=30)
    return _enhance_generated(Image.open(io.BytesIO(resp.content)).convert("RGB"))


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    img = generate_background("modern interior design living room, soft lighting, minimalist style")
    img.save("test_bg.jpg")
    print("saved test_bg.jpg", img.size)
