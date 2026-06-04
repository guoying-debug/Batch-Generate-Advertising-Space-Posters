# -*- coding: utf-8 -*-
"""
海报策划与文案生成：
- 统一生成结构化 poster_plan，包含文案、视觉策略、分尺寸提示词
- 保留旧接口 generate / generate_detail 作为兼容层
- 支持从候选图池按主题打分挑选最佳背景图
"""
import os, io, json, re, base64
from openai import OpenAI
from PIL import Image

POSTER_PLAN_SYSTEM = """你是一名资深运营视觉策划师、海报文案专家、AIGC提示词工程师。
你的任务是根据输入的文案、链接摘要、封面图理解、视频理解和尺寸要求，生成可用于广告位海报生产的结构化结果。

请遵守以下要求：
1. 目标不是模仿参考图，而是基于内容理解，输出适合业务投放的海报方案。
2. 背景图必须服务文案表达，且预留文字安全区。
3. 对于家居、设计、装修、空间类主题，优先生成真实可落地的空间场景。
4. 标题、副标题、按钮文案需符合传播语境，不编造虚假价格、时间、活动信息。
5. 最终提示词使用中文，包含主体、场景、构图、光照、风格、色调、材质、细节、文字内容和位置。
6. 若信息不足，可合理补全，但不得偏离主题。

【文字设计规范】final_prompt 中描述海报文字时，必须严格遵守以下四条，避免出图时出现乱码或多余文字：
- 引号不能少：每一处要显示的文字都必须用中文双引号「」或英文引号""完整包裹，例如 主标题"灯光高级设置"。任何没有用引号包裹的内容都不会被当作画面文字，从而防止模型自由发挥生成乱码。
- 顺序很重要：按重要性从高到低描述文字，越重要的越先写。顺序为 主标题 → 副标题 → 角标/标签 → 按钮 → 底部信息。
- 靠前字就大：明确指定字号层级。靠前最重要的文字字号最大（如"顶部中央，巨大主标题"），其后依次减小（"副标题中等字号""底部信息小字号"）。
- 换行断引号：需要换行的长句，用引号把它断成多段分别包裹，例如把一句话写成 "半句话""另半句话"，让模型在引号边界处换行，实现精准换行。
- 除被引号包裹的指定文字外，画面中不得出现任何其他文字、字母、数字或符号，严禁出现乱码、随机文字、水印。

你必须只输出一个合法JSON对象，不要输出额外解释。JSON结构如下：
{
  "topic_summary": "一句话概括主题和转化目标",
  "reference_style_summary": "参考图风格摘要，没有则返回空字符串",
  "poster_type": "海报类型",
  "audience": "目标人群",
  "key_points": ["核心卖点1", "核心卖点2", "核心卖点3"],
  "copywriting": {
    "main_title": "主标题",
    "sub_title": "副标题",
    "cta": "按钮文案",
    "badge": "角标文案，没有则返回空字符串"
  },
  "visual_strategy": {
    "scene": "背景图场景描述",
    "subject": "画面主体描述",
    "composition": "构图策略",
    "layout_mode": "建议版式，如left_text_right_visual、top_text_bottom_visual、centered",
    "image_strategy": "图片策略，如full_background或local_replace",
    "template_id": "模板ID，如home_vertical_promo或generic",
    "replaceable_slots": ["可替换槽位说明1", "可替换槽位说明2"],
    "fixed_elements": ["固定元素说明1", "固定元素说明2"],
    "lighting": "光照氛围",
    "style": "艺术风格",
    "color_palette": "主色调与色彩搭配",
    "texture_details": "材质和细节描述",
    "text_safe_area": "文字安全区建议"
  },
  "size_adaptations": [
    {
      "size": "2560x320",
      "layout": "该尺寸下的版式建议",
      "final_prompt": "可直接用于出图的中文提示词"
    },
    {
      "size": "1160x1016",
      "layout": "该尺寸下的版式建议",
      "final_prompt": "可直接用于出图的中文提示词"
    }
  ],
  "constraints": {
    "must_include_text": ["必须保留文案1", "必须保留文案2"],
    "do_not_include": ["禁用元素1", "禁用元素2"]
  }
}"""


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


def _pil_to_data_url(img: Image.Image, max_side: int = 1280) -> str:
    copy = img.copy()
    copy.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    copy.convert("RGB").save(buf, format="JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def analyze_reference_images(images: list) -> dict:
    """读取参考图，提炼版式、风格、色彩和可复用元素"""
    valid = [im for im in (images or []) if isinstance(im, Image.Image)]
    if not valid:
        return {}
    content = [{
        "type": "text",
        "text": (
            "你是一名海报设计分析师。请综合这些参考图，提炼可复用的视觉规则，"
            "只输出一个JSON对象，字段包括：summary、layout、style、color_palette、subject、"
            "replaceable_slots、fixed_elements、prompt_boost。"
            "其中 summary 是一句话风格摘要；layout 描述版式；style 描述风格；"
            "color_palette 描述主色；subject 描述画面主体类型；"
            "replaceable_slots 和 fixed_elements 为字符串数组；"
            "prompt_boost 为一段可直接拼接到文生图提示词里的中文短句，强调版式、色调、装饰和主体。"
        ),
    }]
    for img in valid[:4]:
        content.append({"type": "image_url", "image_url": {"url": _pil_to_data_url(img)}})
    try:
        r = _client().chat.completions.create(
            model=os.getenv("VISION_MODEL", "glm-4v-flash"),
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            max_tokens=500,
        )
        data = _safe_json_loads(r.choices[0].message.content)
        return {
            "summary": data.get("summary", ""),
            "layout": data.get("layout", ""),
            "style": data.get("style", ""),
            "color_palette": data.get("color_palette", ""),
            "subject": data.get("subject", ""),
            "replaceable_slots": list(data.get("replaceable_slots") or []),
            "fixed_elements": list(data.get("fixed_elements") or []),
            "prompt_boost": data.get("prompt_boost", ""),
        }
    except Exception as e:
        print(f"[ai_writer] 参考图分析失败: {e}")
        return {}


def _safe_json_loads(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            return json.loads(match.group(0))
        raise


def _collect_user_parts(
    content: dict,
    operator_text: str = "",
    video_summary: str = "",
    brand: str = "KUJIALE",
    poster_goal: str = "",
    reference_style: str = "",
    reference_analysis: dict = None,
    sizes: list = None,
) -> list:
    user_parts = [
        f"品牌：{brand or '无'}",
        f"海报目标：{poster_goal or '内容推广'}",
        f"尺寸需求：{','.join(sizes or ['2560x320', '1160x1016'])}",
        f"标题：{content.get('title', '')}",
        f"简介：{content.get('desc') or '无'}",
        f"关键词：{','.join(content.get('keywords', [])) or '无'}",
        f"运营补充：{operator_text or '无'}",
        f"完整正文：{(content.get('full_text') or content.get('desc') or '')[:3000] or '无'}",
    ]
    activity_fields = content.get("activity_fields") or {}
    if activity_fields:
        user_parts.append(f"活动页结构化信息：{json.dumps(activity_fields, ensure_ascii=False)}")
    if reference_style:
        user_parts.append(f"参考风格：{reference_style}")
    if reference_analysis:
        user_parts.append(f"参考图分析：{json.dumps(reference_analysis, ensure_ascii=False)}")
    if video_summary:
        user_parts.append(f"视频内容理解：{video_summary}")
    if content.get("cover"):
        vision_desc = _describe_cover(content["cover"])
        if vision_desc:
            user_parts.append(f"封面图内容：{vision_desc}")
    return user_parts


def _find_size_prompt(plan: dict, size: str, default: str = "") -> str:
    for item in plan.get("size_adaptations", []):
        if item.get("size") == size and item.get("final_prompt"):
            return item["final_prompt"]
    return default


def _clean_copy_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"[【】\[\]（）()]+", "", text)
    return text.strip(" -|｜:：,，。")


def _split_copy_parts(text: str) -> list:
    text = _clean_copy_text(text)
    parts = re.split(r"\s*[-|｜/,:：]\s*", text)
    parts = [_clean_copy_text(p) for p in parts if _clean_copy_text(p)]
    return parts or ([text] if text else [])


def _short_title(text: str, max_len: int = 14) -> str:
    raw = _clean_copy_text(text)
    if len(raw) <= max_len:
        return raw
    parts = [p for p in _split_copy_parts(raw) if p]
    if len(parts) >= 2:
        first, second = parts[0], parts[1]
        if len(first) <= max_len and len(second) <= max_len and len(first) + len(second) <= max_len + 2:
            return f"{first}\n{second}"
    for part in parts:
        if 4 <= len(part) <= max_len:
            return part
    return raw[:max_len]


def _short_subtitle(text: str, max_len: int = 14) -> str:
    raw = _clean_copy_text(text)
    if not raw:
        return ""
    patterns = [
        r"AI仅需\d+分钟",
        r"\d+分钟(?:生成|完成|体验|设计)[^，。]{0,6}",
        r"限时免费体验",
        r"免费体验",
        r"立即体验",
        r"如何做[^，。]{0,8}",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw)
        if m:
            return m.group(0)[:max_len]
    parts = [p for p in _split_copy_parts(raw) if 4 <= len(p) <= max_len]
    if parts:
        return parts[0]
    return raw[:max_len]


def _short_cta(text: str) -> str:
    raw = _clean_copy_text(text)
    if len(raw) <= 6:
        return raw
    rules = [
        ("报名", "立即报名"),
        ("领取", "立即领取"),
        ("免费", "立即体验"),
        ("体验", "立即体验"),
        ("查看", "立即查看"),
        ("了解", "了解更多"),
    ]
    for key, value in rules:
        if key in raw:
            return value
    return "立即查看"


def compact_copywriting(plan: dict) -> dict:
    plan = _normalize_plan(plan)
    copy = plan["copywriting"]
    short_title = _short_title(copy.get("main_title", ""), max_len=14)
    short_subtitle = _short_subtitle(copy.get("sub_title", ""), max_len=14)
    short_cta = _short_cta(copy.get("cta", "立即查看"))
    return {
        "title": short_title or copy.get("main_title", ""),
        "subtitle": short_subtitle or copy.get("sub_title", ""),
        "cta": short_cta or "立即查看",
        "badge": _short_subtitle(copy.get("badge", ""), max_len=8) if copy.get("badge") else "",
    }


def _normalize_plan(plan: dict) -> dict:
    normalized = dict(plan or {})
    normalized.setdefault("topic_summary", "")
    normalized.setdefault("reference_style_summary", "")
    normalized.setdefault("poster_type", "教程知识型海报")
    normalized.setdefault("audience", "家居设计与装修相关用户")
    normalized["key_points"] = list(normalized.get("key_points") or [])[:5]
    while len(normalized["key_points"]) < 3:
        normalized["key_points"].append("突出内容价值与主题表达")

    copywriting = dict(normalized.get("copywriting") or {})
    copywriting.setdefault("main_title", "")
    copywriting.setdefault("sub_title", "")
    copywriting.setdefault("cta", "立即查看")
    copywriting.setdefault("badge", "")
    normalized["copywriting"] = copywriting

    visual = dict(normalized.get("visual_strategy") or {})
    defaults = {
        "scene": "现代简洁的主题化场景背景",
        "subject": "与主题相关的核心主体",
        "composition": "主体明确，预留标题和按钮区域",
        "layout_mode": "left_text_right_visual",
        "image_strategy": "full_background",
        "template_id": "home_vertical_promo",
        "replaceable_slots": [
            "顶部居中主标题文字位",
            "左上角品牌文字位",
            "实景图左上橙框副标题位",
            "实景图左下黑底按钮位",
            "下半部分圆角实景图片区",
        ],
        "fixed_elements": [
            "右上角三个并排橙色箭头",
            "右侧竖向四段黑色短虚线",
            "上半区大面积浅灰白留白背景",
            "下半区圆角边框实景容器",
        ],
        "lighting": "柔和清晰的商业级光线",
        "style": "商业海报风格，专业、现代、适合运营投放",
        "color_palette": "与品牌和主题匹配的统一配色",
        "texture_details": "强调真实材质、空间层次与细节质感",
        "text_safe_area": "保留清晰文字安全区，避免主体遮挡标题和按钮",
    }
    for key, value in defaults.items():
        visual.setdefault(key, value)
    normalized["visual_strategy"] = visual

    normalized["size_adaptations"] = list(normalized.get("size_adaptations") or [])
    constraints = dict(normalized.get("constraints") or {})
    constraints["must_include_text"] = list(constraints.get("must_include_text") or [])
    constraints["do_not_include"] = list(constraints.get("do_not_include") or [])
    normalized["constraints"] = constraints
    event_info = dict(normalized.get("event_info") or {})
    event_info["benefits"] = list(event_info.get("benefits") or [])
    event_info.setdefault("event_time", "")
    normalized["event_info"] = event_info
    return normalized


def _merge_reference_analysis(plan: dict, reference_analysis: dict = None) -> dict:
    plan = _normalize_plan(plan)
    ref = dict(reference_analysis or {})
    if not ref:
        return plan
    if ref.get("summary"):
        plan["reference_style_summary"] = ref["summary"]
    visual = dict(plan.get("visual_strategy") or {})
    if ref.get("layout"):
        visual["composition"] = f"{visual.get('composition', '')}；参考图版式：{ref['layout']}".strip("；")
    if ref.get("style"):
        visual["style"] = f"{visual.get('style', '')}；参考图风格：{ref['style']}".strip("；")
    if ref.get("color_palette"):
        visual["color_palette"] = f"{visual.get('color_palette', '')}；参考图配色：{ref['color_palette']}".strip("；")
    if ref.get("subject"):
        visual["subject"] = f"{visual.get('subject', '')}；参考主体倾向：{ref['subject']}".strip("；")
    replaceable = list(visual.get("replaceable_slots") or [])
    for item in ref.get("replaceable_slots") or []:
        if item and item not in replaceable:
            replaceable.append(item)
    fixed = list(visual.get("fixed_elements") or [])
    for item in ref.get("fixed_elements") or []:
        if item and item not in fixed:
            fixed.append(item)
    visual["replaceable_slots"] = replaceable[:8]
    visual["fixed_elements"] = fixed[:8]
    plan["visual_strategy"] = visual

    boost = ref.get("prompt_boost", "").strip()
    if boost:
        for item in plan.get("size_adaptations", []):
            prompt = item.get("final_prompt", "")
            if prompt and boost not in prompt:
                item["final_prompt"] = f"{prompt} 参考图风格补充：{boost}"[:760]
    return plan


def _fallback_plan(
    content: dict,
    operator_text: str = "",
    video_summary: str = "",
    brand: str = "KUJIALE",
    reference_analysis: dict = None,
    sizes: list = None,
) -> dict:
    title = (operator_text or content.get("title") or "家居设计灵感")[:18]
    activity_fields = content.get("activity_fields") or {}
    desc = (activity_fields.get("sub_title") or content.get("desc") or content.get("title") or "聚焦主题内容，突出案例与实用价值")[:28]
    badge = "快速看懂" if "教程" in f"{title}{desc}{video_summary}" else ""
    scene = "现代家居空间场景，画面干净高级，适合叠加运营文案"
    subject = "与主题相关的核心空间或产品主体，突出真实案例表现"
    if reference_analysis and reference_analysis.get("subject"):
        subject = reference_analysis["subject"]
    cta = activity_fields.get("cta_text") or "立即查看"
    benefits = activity_fields.get("benefits") or []
    event_time = activity_fields.get("event_time") or ""
    ref_boost = (reference_analysis or {}).get("prompt_boost", "")
    main_prompt = (
        f"生成一张{brand}主题商业海报背景图，主题围绕“{title}”。"
        f"画面表现为{scene}，主体为{subject}，构图清晰，预留大面积文字安全区，"
        "光线柔和通透，现代写实3D效果图或照片级商业视觉，色调克制高级。"
        + (f" 参考图风格补充：{ref_boost}" if ref_boost else "")
    )
    sizes = sizes or ["2560x320", "1160x1016"]
    size_adaptations = []
    for size in sizes:
        if size == "2560x320":
            layout = "横向长条构图，主体偏右，左侧为标题和副标题区，右侧保留按钮位置"
            layout_mode = "left_text_right_visual"
        elif size == "1160x1016":
            layout = "竖版上下分层构图，上40%浅灰白大留白，下60%完整圆角边框家装实景"
            layout_mode = "top_text_bottom_visual"
        else:
            layout = "根据尺寸保持主体、主标题和按钮三者不冲突"
            layout_mode = "centered"
        # 按规范：引号包裹每处文字、重要性排序、靠前字大、换行断引号
        title_parts = [f'"{p}"' for p in (title[:9], title[9:18]) if p]
        title_text = "".join(title_parts)
        desc_parts = [f'"{p}"' for p in (desc[:14], desc[14:28]) if p]
        desc_text = "".join(desc_parts)
        final_prompt = (
            f"{main_prompt} 版式为{layout}。"
            "画面文字严格按以下顺序排列："
            f'左上角品牌小字"{brand}"，'
            f"顶部中央巨大主标题{title_text}，"
            f"实景图左上角橙框中等字号副标题{desc_text}，"
            f'实景图左下角黑底按钮大字"{cta}"，'
            + (f'右上角标签小字"{badge}"，' if badge else "")
            + "除上述引号内文字外，画面中不得出现任何其他文字、字母、数字或乱码。"
        )
        if size == "1160x1016":
            final_prompt = (
                f"{main_prompt}"
                " 生成竖版家装产品宣传海报，8K超写实商业渲染。"
                " 画面上40%是纯净浅灰白大留白，下60%完整放置圆角边框主卧实景。"
                " 圆角实景内为轻奢主卧：左侧白色拱形镂空异形衣柜，开放格内置暖黄灯带，格内摆放黑色轻奢包包和书本；"
                " 中间米白色软包双人床，搭配米色床品与抱枕，床侧白色床头柜和小台灯；"
                " 背景为竖向木格栅加天然大理石岩板拼接，浅木地板，浅咖色窗帘。"
                " 固定装饰：右上角三个并排橙色箭头，最右侧四段黑色短虚线。"
                " 可替换槽位仅包括：左上角品牌位、上半区居中主标题位、实景图左上角橙框副标题位、实景图左下角黑底按钮位、下半部分圆角实景图区。"
                f' 文字必须严格限定为左上角品牌小字"{brand}"，顶部中央巨大主标题{title_text}，实景图左上角橙框中等字号副标题{desc_text}，实景图左下角黑底按钮大字"{cta}"。'
                " 除上述引号内文字外，禁止出现任何其他文字、字母、数字、水印和乱码。"
            )
        size_adaptations.append({
            "size": size,
            "layout": layout,
            "final_prompt": final_prompt[:620],
            "layout_mode": layout_mode,
        })

    return _merge_reference_analysis(_normalize_plan({
        "topic_summary": f"围绕“{title}”生成用于内容推广的海报方案",
        "reference_style_summary": (reference_analysis or {}).get("summary", ""),
        "poster_type": "教程知识型海报" if ("教程" in f"{title}{desc}{video_summary}" or video_summary) else "内容推广图",
        "audience": "关注家居设计、装修案例与软件教程的用户",
        "key_points": [
            "标题突出核心主题，增强点击意愿",
            "背景图与内容主题一致，便于运营传播",
            "版式预留清晰文案区，适配广告位投放",
        ],
        "copywriting": {
            "main_title": title,
            "sub_title": desc,
            "cta": cta,
            "badge": badge,
        },
        "visual_strategy": {
            "scene": scene,
            "subject": subject,
            "composition": "主体明确，文字与视觉分区清晰",
            "layout_mode": "top_text_bottom_visual",
            "image_strategy": "local_replace",
            "template_id": "home_vertical_promo",
            "replaceable_slots": [
                "左上角品牌文字位",
                "上半区居中主标题位",
                "实景图左上角橙框副标题位",
                "实景图左下角黑底按钮位",
                "下半部分圆角卧室实景图区",
            ],
            "fixed_elements": [
                "右上角三个并排橙色箭头",
                "最右侧竖向四段黑色短虚线",
                "上40%浅灰白留白背景",
                "下60%圆角边框实景框",
            ],
            "lighting": "柔和暖白或自然光的商业海报光线",
            "style": "现代商业海报，偏照片级写实或高质感家装效果图",
            "color_palette": "浅灰、米白、木色、品牌强调色",
            "texture_details": "强调材质、层次、细节和空间真实感",
            "text_safe_area": "上40%为主标题大留白，下方实景图内仅保留橙框副标题和黑底按钮文字位",
        },
        "size_adaptations": size_adaptations,
        "constraints": {
            "must_include_text": [x for x in [brand, title, desc, cta, event_time] if x],
            "do_not_include": [],
        },
        "event_info": {
            "benefits": benefits,
            "event_time": event_time,
        },
    }), reference_analysis)


def generate_poster_plan(
    content: dict,
    operator_text: str = "",
    video_summary: str = "",
    sizes: list = None,
    brand: str = "KUJIALE",
    poster_goal: str = "",
    reference_style: str = "",
    reference_analysis: dict = None,
) -> dict:
    user_parts = _collect_user_parts(
        content,
        operator_text=operator_text,
        video_summary=video_summary,
        brand=brand,
        poster_goal=poster_goal,
        reference_style=reference_style,
        reference_analysis=reference_analysis,
        sizes=sizes,
    )
    try:
        r = _client().chat.completions.create(
            model=os.getenv("TEXT_MODEL", "glm-4-flash-250414"),
            messages=[
                {"role": "system", "content": POSTER_PLAN_SYSTEM},
                {"role": "user", "content": "\n".join(user_parts)},
            ],
            response_format={"type": "json_object"},
            temperature=0.6,
            max_tokens=1800,
        )
        return _merge_reference_analysis(_safe_json_loads(r.choices[0].message.content), reference_analysis)
    except Exception as e:
        print(f"[ai_writer] 海报方案生成失败，降级: {e}")
        return _fallback_plan(content, operator_text, video_summary, brand=brand, reference_analysis=reference_analysis, sizes=sizes)


def normalize_plan_to_legacy_fields(plan: dict) -> dict:
    plan = _normalize_plan(plan)
    compact = compact_copywriting(plan)
    return {
        "title": compact.get("title", ""),
        "subtitle": compact.get("subtitle", ""),
        "cta": compact.get("cta", "立即查看"),
        "image_prompt": _find_size_prompt(plan, "2560x320", ""),
        "square_prompt": _find_size_prompt(plan, "1160x1016", ""),
        "detail_prompt": _find_size_prompt(plan, "1080x1440", _find_size_prompt(plan, "1160x1016", "")),
    }


def plan_to_detail(plan: dict, content: dict = None, operator_text: str = "") -> dict:
    plan = _normalize_plan(plan)
    copywriting = plan["copywriting"]
    sections = []
    for idx, point in enumerate(plan.get("key_points", [])[:4], start=1):
        sections.append({
            "heading": f"亮点{idx}",
            "body": point if len(point) >= 18 else f"{point}，帮助用户快速理解内容价值并提升点击与转化意愿。",
        })
    if not sections:
        sections = [{"heading": "核心亮点", "body": (content or {}).get("desc", "")[:80] or "围绕主题内容提炼核心信息，强化转化表达。"}]
    headline = copywriting.get("main_title") or operator_text or (content or {}).get("title", "")
    return {
        "headline": headline[:20],
        "subhead": (copywriting.get("sub_title") or (content or {}).get("desc", "") or headline)[:30],
        "sections": sections,
        "cta": copywriting.get("cta", "立即体验")[:6],
        "image_prompt": _find_size_prompt(plan, "1080x1440", _find_size_prompt(plan, "1160x1016", "")),
    }


def describe_frames(frame_data_urls: list) -> str:
    """多帧画面一次性喂视觉模型，输出视频画面内容/风格描述"""
    if not frame_data_urls:
        return ""
    content = [{"type": "text",
                "text": "这是同一个视频的多张关键帧截图。请综合描述视频的主要内容、主题、画面风格和呈现的产品或场景，不超过120字。"}]
    for url in frame_data_urls[:6]:
        content.append({"type": "image_url", "image_url": {"url": url}})
    try:
        r = _client().chat.completions.create(
            model=os.getenv("VISION_MODEL", "glm-4v-flash"),
            messages=[{"role": "user", "content": content}],
            max_tokens=200,
        )
        return r.choices[0].message.content
    except Exception as e:
        print(f"[ai_writer] 多帧理解失败: {e}")
        return ""


def _to_image_url(item) -> str:
    """候选项归一化为 image_url（URL字符串原样返回，PIL转data URL）"""
    if isinstance(item, Image.Image):
        img = item.copy()
        img.thumbnail((768, 768), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    return str(item)


def _score_image(image_url: str, theme: str) -> int:
    """问视觉模型该图与主题契合度 0-100，解析失败返回 0"""
    try:
        r = _client().chat.completions.create(
            model=os.getenv("VISION_MODEL", "glm-4v-flash"),
            messages=[{"role": "user", "content": [
                {"type": "text", "text": f"这张图片作为主题『{theme}』海报背景的契合度有多高？只回一个0到100的整数，不要其他文字。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
            max_tokens=10,
        )
        m = re.search(r"\d+", r.choices[0].message.content)
        return min(100, int(m.group())) if m else 0
    except Exception as e:
        print(f"[ai_writer] 图片打分失败: {e}")
        return 0


def _score_image_with_reason(image_url: str, theme: str) -> dict:
    """
    返回 {"score": int, "reason": str}
    理由尽量简短，突出主体、场景、构图或文字留白等匹配点。
    """
    try:
        r = _client().chat.completions.create(
            model=os.getenv("VISION_MODEL", "glm-4v-flash"),
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    f"请判断这张图片作为主题『{theme}』海报背景的契合度。"
                    "只输出JSON对象，格式为"
                    '{"score": 0-100整数, "reason": "不超过30字，说明为什么更匹配或不匹配"}'
                )},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
            response_format={"type": "json_object"},
            max_tokens=120,
        )
        data = _safe_json_loads(r.choices[0].message.content)
        score = max(0, min(100, int(data.get("score", 0))))
        reason = str(data.get("reason", "")).strip()[:30]
        return {"score": score, "reason": reason or "模型未返回理由"}
    except Exception as e:
        print(f"[ai_writer] 图片评分理由失败: {e}")
        return {"score": _score_image(image_url, theme), "reason": "仅获得基础分数，未生成理由"}


def pick_best_image(candidates: list, theme: str, topk: int = 8):
    """
    从候选图池按与主题契合度挑选最佳。
    candidates: URL字符串 或 PIL.Image 的混合列表
    返回 (best_item, score)；候选为空返回 (None, 0)；全部打分失败返回首张。
    """
    cands = [c for c in candidates if c is not None][:topk]
    if not cands:
        return None, 0
    best, best_score = cands[0], -1
    for item in cands:
        score = _score_image(_to_image_url(item), theme)
        if score > best_score:
            best, best_score = item, score
    return best, max(best_score, 0)


def rank_candidate_images(candidates: list, theme: str, topk: int = 8):
    """
    对候选图批量评分并返回排序结果。
    candidates: [{"item": 原始候选, "source": 来源说明, "label": 标签说明}, ...]
    返回 [{"item":..., "source":..., "label":..., "score":...}, ...]，按分数降序。
    """
    ranked = []
    for cand in (candidates or [])[:topk]:
        item = cand.get("item")
        if item is None:
            continue
        scored = _score_image_with_reason(_to_image_url(item), theme)
        ranked.append({
            "item": item,
            "source": cand.get("source", "候选图"),
            "label": cand.get("label", ""),
            "score": max(scored.get("score", 0), 0),
            "reason": scored.get("reason", ""),
        })
    ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
    return ranked


def generate(content: dict, operator_text: str = "", video_summary: str = "") -> dict:
    plan = generate_poster_plan(content, operator_text, video_summary)
    return normalize_plan_to_legacy_fields(plan)


def _fallback_detail(content: dict, operator_text: str) -> dict:
    """LLM失败时用正文切分兜底，保证UI不崩"""
    full = content.get("full_text") or content.get("desc") or content["title"]
    # 按换行/句号切成段，取前几段做 section
    chunks = [s.strip() for s in re.split(r"[\n。！？]", full) if len(s.strip()) > 12]
    sections = []
    for i, ch in enumerate(chunks[:4]):
        sections.append({"heading": f"亮点{i + 1}", "body": ch[:80]})
    if not sections:
        sections = [{"heading": "产品亮点", "body": (content["desc"] or content["title"])[:80]}]
    return {
        "headline": (operator_text or content["title"])[:20],
        "subhead": (content["desc"] or content["title"])[:30],
        "sections": sections,
        "cta": "立即体验",
        "image_prompt": f"modern interior design scene, {content['title']}, soft natural lighting, professional photography, clean composition",
    }


def generate_detail(content: dict, operator_text: str = "", video_summary: str = "") -> dict:
    """基于结构化海报方案生成详情页内容"""
    try:
        plan = generate_poster_plan(content, operator_text, video_summary, sizes=["2560x320", "1160x1016", "1080x1440"])
        data = plan_to_detail(plan, content, operator_text)
        if not isinstance(data.get("sections"), list) or not data["sections"]:
            raise ValueError("sections 为空")
        return data
    except Exception as e:
        print(f"[ai_writer] 详情页文案生成失败，降级: {e}")
        return _fallback_detail(content, operator_text)


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
        "full_text": "酷家乐灯光高级设置教程。第一步设置主光源。第二步调整环境光。第三步渲染出图。",
    }
    print(json.dumps(generate(demo, "3分钟学会高级灯光"), ensure_ascii=False, indent=2))
    print("---DETAIL---")
    print(json.dumps(generate_detail(demo, "3分钟学会高级灯光"), ensure_ascii=False, indent=2))
