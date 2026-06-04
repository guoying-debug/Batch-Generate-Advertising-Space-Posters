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
        return "现代家居商业海报背景图，构图清晰，光线柔和，预留文字安全区，高级质感"

    # 去掉容易触发风控的强约束和负面文字控制句，仅保留场景与风格信息
    banned_patterns = [
        r"除上述.*?乱码[。；，]?",
        r"不得出现.*?[。；，]?",
        r"禁止出现.*?[。；，]?",
        r"严禁出现.*?[。；，]?",
        r"画面文字严格.*?[。；，]?",
        r"文字必须严格.*?[。；，]?",
        r"所有文字.*?[。；，]?",
        r"仅预留.*?文案位[。；，]?",
        r"按钮大字.*?[。；，]?",
        r"主标题.*?[。；，]?",
        r"副标题.*?[。；，]?",
    ]
    for pattern in banned_patterns:
        prompt = re.sub(pattern, "", prompt)

    prompt = re.sub(r"[\"“”‘’「」]+", "", prompt)
    prompt = re.sub(r"\s+", " ", prompt).strip(" ，。；")

    safe_suffix = " 商业海报背景图，无人物冲突元素，构图干净，预留大面积文字安全区，现代写实，高级质感。"
    compact = (prompt[:220] if len(prompt) > 220 else prompt).strip(" ，。；")
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
    try:
        r = _request_image(client, image_prompt, size)
    except BadRequestError as e:
        msg = str(e)
        if "1301" not in msg and "敏感内容" not in msg and "不安全" not in msg:
            raise
        safe_prompt = _sanitize_prompt_for_image_model(image_prompt)
        print("[image_gen] 命中过滤，已自动使用安全提示词重试")
        r = _request_image(client, safe_prompt, size)
    img_url = r.data[0].url
    resp = requests.get(img_url, timeout=30)
    return _enhance_generated(Image.open(io.BytesIO(resp.content)).convert("RGB"))


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    img = generate_background("modern interior design living room, soft lighting, minimalist style")
    img.save("test_bg.jpg")
    print("saved test_bg.jpg", img.size)
