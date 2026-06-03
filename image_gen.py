# -*- coding: utf-8 -*-
"""模式一：调 Cogview-3-Flash 根据 image_prompt 生成背景图，返回 PIL Image"""
import os, io, requests
from PIL import Image
from openai import OpenAI


def generate_background(image_prompt: str, size: str = "1440x720") -> Image.Image:
    """
    image_prompt : ai_writer.generate() 返回的 image_prompt 字段
    size         : 智谱支持的尺寸，默认 1440x720（宽图适合 banner）
    """
    client = OpenAI(
        api_key=os.environ["ZHIPUAI_API_KEY"],
        base_url=os.getenv("ZHIPUAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
    )
    r = client.images.generate(
        model=os.getenv("IMAGE_MODEL", "cogview-3-flash"),
        prompt=image_prompt,
        size=size,
        n=1,
    )
    img_url = r.data[0].url
    resp = requests.get(img_url, timeout=30)
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    img = generate_background("modern interior design living room, soft lighting, minimalist style")
    img.save("test_bg.jpg")
    print("saved test_bg.jpg", img.size)
