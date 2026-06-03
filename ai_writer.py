# -*- coding: utf-8 -*-
"""
文案生成：
- 有封面图时先用视觉模型理解图片内容，再生成文案
- 无封面图时直接用文本模型生成文案
返回: {"title":..., "subtitle":..., "cta":..., "image_prompt":...}
"""
import os, json
from openai import OpenAI

SYSTEM = """你是资深广告文案，为酷家乐(KUJIALE)生成海报文案。
根据内容输出JSON（只输出JSON，不要多余文字）：
- title: 主标题，吸引点击，≤15字
- subtitle: 副标题宣传语，≤25字
- cta: 行动号召，≤5字（如"立即查看""立即领取"）
- image_prompt: 用于AI生图的英文描述，体现主题氛围，约20词"""


def _client():
    return OpenAI(
        api_key=os.environ["ZHIPUAI_API_KEY"],
        base_url=os.getenv("ZHIPUAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
    )


def _describe_cover(cover_url: str) -> str:
    """用视觉模型描述封面图，补充语义信息"""
    try:
        r = _client().chat.completions.create(
            model=os.getenv("VISION_MODEL", "glm-4v-flash"),
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "简要描述这张图片的主题、风格和画面内容，不超过60字"},
                {"type": "image_url", "image_url": {"url": cover_url}},
            ]}],
            max_tokens=80,
        )
        return r.choices[0].message.content
    except Exception as e:
        print(f"[ai_writer] 视觉模型失败: {e}")
        return ""


def generate(content: dict, operator_text: str = "") -> dict:
    user_parts = [
        f"标题：{content['title']}",
        f"简介：{content['desc'] or '无'}",
        f"关键词：{','.join(content.get('keywords', []))}",
        f"运营补充：{operator_text or '无'}",
    ]

    # 有封面图时先做视觉理解
    if content.get("cover"):
        vision_desc = _describe_cover(content["cover"])
        if vision_desc:
            user_parts.append(f"封面图内容：{vision_desc}")

    try:
        r = _client().chat.completions.create(
            model=os.getenv("TEXT_MODEL", "glm-4-flash-250414"),
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "\n".join(user_parts)},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=200,
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        print(f"[ai_writer] 文案生成失败，降级: {e}")
        title = (operator_text or content["title"])[:15]
        return {
            "title": title,
            "subtitle": (content["desc"] or content["title"])[:25],
            "cta": "立即查看",
            "image_prompt": f"modern interior design background, {content['title']}",
        }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv()
    demo = {
        "title": "酷家乐-灯光的高级设置",
        "desc": "",
        "cover": "http://i1.hdslb.com/bfs/archive/fca5150c8337d6688f3f0b9ec2f0e138ab74a037.jpg",
        "keywords": ["室内设计", "效果图"],
    }
    print(json.dumps(generate(demo, "3分钟学会高级灯光"), ensure_ascii=False, indent=2))
