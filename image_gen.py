# -*- coding: utf-8 -*-
"""模式一：支持文生图和图生图，返回 PIL Image"""
import base64
import io
import json
import os
import re
import time
import urllib.request

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


def _enhance_generated(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Contrast(img).enhance(1.04)
    img = ImageEnhance.Sharpness(img).enhance(1.08)
    return img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=95, threshold=2))


def _sanitize_prompt_for_image_model(prompt: str, layout_zones: list = None) -> str:
    """清洗提示词 + 注入文字安全区约束
    layout_zones: 从 layout_spec 传入的文字/按钮区域列表
    """
    prompt = (prompt or "").strip()
    if not prompt:
        prompt = "现代家居室内场景，柔和自然光，简洁大气"

    banned_words = [
        "标题", "主标题", "副标题", "文案", "文字", "按钮", "logo", "LOGO",
        "KUJIALE", "酷家乐", "平台", "立即体验", "立即查看", "广告语", "标语",
        "界面截图", "UI", "屏幕文字", "海报文字", "字体", "排版",
    ]
    for word in banned_words:
        prompt = prompt.replace(word, "")

    prompt = re.sub(r"(不得|禁止|严禁|不要|无需)[^，。；\n]{0,20}", "", prompt)
    prompt = re.sub(r"[\"""\'\'「」]+", "", prompt)
    prompt = re.sub(r"\s+", " ", prompt).strip(" ，。；")

    safe_suffix = (
        " 纯背景视觉图，画面中不包含任何文字、字母、数字、水印、logo、"
        "招牌、按钮、UI界面或屏幕截图。构图干净留白，现代写实，高级质感。"
    )

    # 有 layout_zones 时注入安全区位置约束
    if layout_zones:
        text_zones = [z for z in layout_zones if z.get("zone_type") in ("text", "button")]
        hints = []
        for z in text_zones[:3]:
            b = z["bbox"]
            hints.append(f"画面 {b[0]}-{b[2]} 横向区域保持简洁背景")
        if hints:
            safe_suffix += " " + "，".join(hints) + "。"

    compact = (prompt[:180] if len(prompt) > 180 else prompt).strip(" ，。；")
    return compact + safe_suffix


def _pil_to_data_url(img: Image.Image, fmt: str = "JPEG") -> str:
    buf = io.BytesIO()
    rgb = img.convert("RGB")
    rgb.save(buf, format=fmt, quality=95)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _source_to_image(source) -> Image.Image:
    if isinstance(source, Image.Image):
        return source.convert("RGB")
    if isinstance(source, str):
        if source.startswith("http://") or source.startswith("https://"):
            resp = requests.get(source, timeout=30)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
        if source.startswith("data:image/"):
            _, b64 = source.split(",", 1)
            return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    raise TypeError("input_image 只支持 PIL.Image、图片 URL 或 data URL")


def _get_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise KeyError(f"缺少环境变量：{' / '.join(names)}")


def _get_optional_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


# #region debug-point A:proxy-config
def _debug_report(hypothesis_id: str, msg: str, data=None, run_id: str = "pre-fix"):
    payload = {
        "sessionId": "image-proxy-config",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": "image_gen.py",
        "msg": f"[DEBUG] {msg}",
        "data": data or {},
        "ts": int(time.time() * 1000),
    }
    debug_env = ".dbg/image-proxy-config.env"
    url = "http://127.0.0.1:7777/event"
    session_id = "image-proxy-config"
    try:
        with open(debug_env, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("DEBUG_SERVER_URL="):
                    url = line.split("=", 1)[1].strip()
                elif line.startswith("DEBUG_SESSION_ID="):
                    session_id = line.split("=", 1)[1].strip()
        payload["sessionId"] = session_id
    except Exception:
        pass
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            ),
            timeout=2,
        ).read()
    except Exception:
        pass


# #endregion


def _as_bool(value: str, default: bool = False) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _get_proxy_base_url() -> str:
    base_url = _get_env("IMAGE_PROXY_BASE_URL").rstrip("/")
    if "/v1/images/generations" in base_url:
        return base_url
    return f"{base_url}/v1/images/generations"


def _get_proxy_root_url() -> str:
    base_url = _get_env("IMAGE_PROXY_BASE_URL").rstrip("/")
    for suffix in ("/v1/images/generations", "/v1/chat/completions", "/v1"):
        if base_url.endswith(suffix):
            return base_url[: -len(suffix)]
    return base_url


def _get_proxy_img2img_url() -> str:
    return _get_optional_env(
        "IMAGE_PROXY_IMG2IMG_URL",
        default=f"{_get_proxy_root_url()}/v1/chat/completions",
    )


def _get_proxy_status_url(task_id: str) -> str:
    template = _get_optional_env("IMAGE_PROXY_STATUS_URL_TEMPLATE")
    if template:
        return template.format(task_id=task_id)
    return f"{_get_proxy_base_url().rstrip('/')}/{task_id}"


def _get_proxy_api_key() -> str:
    return _get_env("IMAGE_PROXY_API_KEY")


def _get_proxy_headers(include_google_key: bool = False) -> dict:
    api_key = _get_proxy_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if include_google_key:
        headers["x-goog-api-key"] = api_key
    return headers


def _get_proxy_model() -> str:
    return _get_optional_env(
        "IMAGE_PROXY_MODEL",
        default="nano-banana-pro",
    )


def _use_async_proxy() -> bool:
    return _as_bool(_get_optional_env("IMAGE_PROXY_ASYNC", default="false"), default=False)


def _size_to_aspect_ratio(size: str) -> str:
    text = (size or "").strip().lower()
    mapping = {
        "1440x720": "16:9",
        "1024x1024": "1:1",
        "1160x1016": "1:1",
        "1080x1440": "3:4",
        "2560x320": "21:9",
        "1k": "1:1",
        "2k": "16:9",
        "4k": "16:9",
    }
    if text in mapping:
        return mapping[text]
    if "x" in text:
        try:
            width_text, height_text = text.split("x", 1)
            width = int(width_text)
            height = int(height_text)
        except ValueError:
            return "1:1"
        known = [
            ("1:1", 1.0),
            ("2:3", 2 / 3),
            ("3:2", 3 / 2),
            ("3:4", 3 / 4),
            ("4:3", 4 / 3),
            ("4:5", 4 / 5),
            ("5:4", 5 / 4),
            ("9:16", 9 / 16),
            ("16:9", 16 / 9),
            ("21:9", 21 / 9),
        ]
        ratio = width / max(height, 1)
        return min(known, key=lambda item: abs(item[1] - ratio))[0]
    return "1:1"


def _size_to_generation_params(size: str) -> dict:
    params = {
        "aspect_ratio": _size_to_aspect_ratio(size),
    }
    normalized = (size or "").strip().upper()
    if normalized in {"1K", "2K", "4K"}:
        params["size"] = normalized
    return params


def _source_to_generation_input(source):
    if isinstance(source, str):
        if source.startswith("http://") or source.startswith("https://"):
            return source
        if source.startswith("data:image/"):
            return source
    return _pil_to_data_url(_source_to_image(source))


def _extract_image_url(data: dict) -> str:
    if isinstance(data, dict):
        # OpenAI 格式: {"data": [{"url": ...} | {"b64_json": ...}]}
        images = data.get("data")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                for key in ("url", "b64_json", "revised_prompt"):
                    if first.get(key):
                        return first[key]
            if isinstance(first, str):
                return first
        for key in ("url", "image_url", "result_url"):
            if data.get(key):
                return data[key]
        gemini_ref = _extract_gemini_inline_image_ref(data)
        if gemini_ref:
            return gemini_ref
    summary = list(data.keys())[:12] if isinstance(data, dict) else type(data).__name__
    raise RuntimeError(f"生图返回异常，未找到图片字段: {summary}")


def _extract_task_id(data: dict) -> str:
    for key in ("TaskID", "task_id", "id"):
        value = data.get(key)
        if value:
            return str(value)
    raise RuntimeError(f"异步生图未返回任务 ID: {data}")


def _extract_chat_image_ref(data: dict) -> str:
    try:
        message = (((data or {}).get("choices") or [])[0] or {}).get("message") or {}
    except Exception as exc:
        raise RuntimeError("图生图返回异常，choices.message 结构不可用") from exc

    images = message.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            image_url = first.get("image_url")
            if isinstance(image_url, dict) and image_url.get("url"):
                return image_url["url"]
            if first.get("url"):
                return first["url"]

    content = message.get("content")
    if isinstance(content, str):
        match = re.search(r"\((data:image/[^)]+)\)", content)
        if match:
            return match.group(1)
        match = re.search(r"\((https?://[^)\s]+)\)", content)
        if match:
            return match.group(1)
        if content.startswith("data:image/"):
            return content
        if content.startswith("http://") or content.startswith("https://"):
            return content
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                image_url = part.get("image_url") or {}
                if isinstance(image_url, dict) and image_url.get("url"):
                    return image_url["url"]
            if part.get("type") == "output_text":
                text = part.get("text") or ""
                match = re.search(r"\((data:image/[^)]+)\)", text)
                if match:
                    return match.group(1)
                match = re.search(r"\((https?://[^)\s]+)\)", text)
                if match:
                    return match.group(1)

    gemini_ref = _extract_gemini_inline_image_ref(data)
    if gemini_ref:
        return gemini_ref

    summary = list(data.keys())[:12] if isinstance(data, dict) else type(data).__name__
    raise RuntimeError(f"图生图返回异常，未找到图片字段: {summary}")


def _extract_gemini_inline_image_ref(data: dict) -> str:
    """兼容 Gemini 原始图片响应，提取 inlineData 中的 base64 图片。"""
    if not isinstance(data, dict):
        return ""

    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return ""

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if isinstance(content, dict):
            parts = content.get("parts") or []
        elif isinstance(content, list):
            parts = content
        else:
            parts = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or {}
            b64 = inline.get("data")
            mime = inline.get("mimeType") or "image/png"
            if b64:
                return f"data:{mime};base64,{b64}"
    return ""


def _request_proxy_image(prompt: str, image=None, size: str = "2K") -> str:
    create_url = _get_proxy_base_url()
    payload = {
        "model": _get_proxy_model(),
        "prompt": prompt,
        "n": 1,
        "response_format": "url",
    }
    payload.update(_size_to_generation_params(size))
    if image is not None:
        payload["image"] = _source_to_generation_input(image)

    use_async = _use_async_proxy()
    # #region debug-point A:request-create
    _debug_report(
        "A",
        "准备发起图片生成请求",
        {
            "create_url": create_url,
            "model": payload.get("model"),
            "use_async": use_async,
            "aspect_ratio": payload.get("aspect_ratio"),
            "size": payload.get("size"),
            "has_image": "image" in payload,
            "image_kind": ("url" if isinstance(payload.get("image"), str) and payload.get("image", "").startswith(("http://", "https://")) else ("data_url" if "image" in payload else "none")),
        },
    )
    # #endregion
    if use_async:
        create_url = f"{create_url}?async=true"

    resp = requests.post(
        create_url,
        headers=_get_proxy_headers(),
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    # #region debug-point B:create-response
    _debug_report(
        "B",
        "创建图片任务返回",
        {
            "status_code": resp.status_code,
            "top_level_keys": list(data.keys())[:12] if isinstance(data, dict) else [],
            "has_data": isinstance(data, dict) and "data" in data,
            "has_task_id": isinstance(data, dict) and any(k in data for k in ("TaskID", "task_id", "id")),
        },
    )
    # #endregion

    if not use_async:
        return _extract_image_url(data)

    task_id = _extract_task_id(data)
    poll_interval = float(_get_optional_env("IMAGE_PROXY_POLL_INTERVAL", default="2"))
    poll_timeout = float(_get_optional_env("IMAGE_PROXY_POLL_TIMEOUT", default="180"))
    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        status_url = _get_proxy_status_url(task_id)
        # #region debug-point C:poll-request
        _debug_report(
            "C",
            "轮询异步图片任务",
            {"task_id": task_id, "status_url": status_url},
        )
        # #endregion
        status_resp = requests.get(
            status_url,
            headers=_get_proxy_headers(),
            timeout=60,
        )
        status_resp.raise_for_status()
        status_data = status_resp.json()
        status = str(status_data.get("Status") or status_data.get("status") or "").upper()
        # #region debug-point C:poll-response
        _debug_report(
            "C",
            "异步任务状态返回",
            {
                "task_id": task_id,
                "status_code": status_resp.status_code,
                "status": status,
                "top_level_keys": list(status_data.keys())[:12] if isinstance(status_data, dict) else [],
            },
        )
        # #endregion
        if status == "SUCCESS":
            return _extract_image_url(status_data)
        if status == "FAILURE":
            # #region debug-point C:poll-failure
            _debug_report(
                "C",
                "异步任务失败",
                {"task_id": task_id, "fail_reason": status_data.get("FailReason"), "status": status},
            )
            # #endregion
            raise RuntimeError(f"生图失败: {status_data.get('FailReason') or status_data}")
        time.sleep(poll_interval)
    raise TimeoutError(f"异步生图超时: {task_id}")


def _request_proxy_chat_img2img(prompt: str, image, size: str = "2K") -> str:
    image_input = _source_to_generation_input(image)
    request_url = _get_proxy_img2img_url()
    payload = {
        "model": _get_proxy_model(),
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_input}},
                ],
            }
        ],
    }

    # #region debug-point D:chat-request
    _debug_report(
        "D",
        "准备发起图生图 chat/completions 请求",
        {
            "request_url": request_url,
            "model": payload.get("model"),
            "size": size,
            "image_kind": "url" if isinstance(image_input, str) and image_input.startswith(("http://", "https://")) else "data_url",
        },
    )
    # #endregion

    resp = requests.post(
        request_url,
        headers=_get_proxy_headers(include_google_key=True),
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()

    # #region debug-point D:chat-response
    _debug_report(
        "D",
        "图生图 chat/completions 返回",
        {
            "status_code": resp.status_code,
            "top_level_keys": list(data.keys())[:12] if isinstance(data, dict) else [],
            "has_choices": isinstance(data, dict) and "choices" in data,
        },
    )
    # #endregion
    return _extract_chat_image_ref(data)


def _download_generated_image(image_ref: str) -> Image.Image:
    if image_ref.startswith("data:image/"):
        _, b64 = image_ref.split(",", 1)
        return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    resp = requests.get(image_ref, timeout=60)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def generate_background(image_prompt: str, size: str = "1440x720", layout_zones: list = None) -> Image.Image:
    """
    image_prompt : ai_writer.generate() 返回的 image_prompt 字段
    size         : 兼容旧调用；内部会转换成代理接口的 aspect_ratio/size
    layout_zones : 可选，文字安全区列表（来自 layout_spec）
    """
    safe_prompt = _sanitize_prompt_for_image_model(image_prompt, layout_zones)
    try:
        image_ref = _request_proxy_image(safe_prompt, size=size)
    except Exception as e:
        # #region debug-point E:text2img-error
        _debug_report("E", "文生图请求异常", {"error": str(e), "size": size})
        # #endregion
        if "敏感" not in str(e) and "安全" not in str(e):
            raise
        fallback_prompt = _sanitize_prompt_for_image_model("")
        print("[image_gen] 命中过滤，已自动使用无文字安全提示词重试")
        image_ref = _request_proxy_image(fallback_prompt, size=size)
    return _enhance_generated(_download_generated_image(image_ref))


def generate_background_img2img(image_prompt: str, input_image, size: str = "2K", layout_zones: list = None) -> Image.Image:
    """使用代理图片接口图生图。input_image 支持 PIL.Image、URL、data URL。"""
    safe_prompt = _sanitize_prompt_for_image_model(image_prompt, layout_zones)
    try:
        image_ref = _request_proxy_image(safe_prompt, image=input_image, size=size)
    except Exception as e:
        # #region debug-point D:img2img-error
        _debug_report("D", "图生图请求异常", {"error": str(e), "size": size})
        # #endregion
        raise
    return _enhance_generated(_download_generated_image(image_ref))


def _build_inpaint_mask(ref_img: Image.Image, layout_zones: list) -> Image.Image:
    """根据 layout_zones 生成蒙版：文字/按钮区=黑色(重绘)，其余=白色(保留)"""
    mask = Image.new("L", ref_img.size, 255)
    draw = ImageDraw.Draw(mask)
    for z in (layout_zones or []):
        if z.get("zone_type") in ("text", "button"):
            x1, y1, x2, y2 = z["bbox"]
            pad = max(6, int((y2 - y1) * 0.1))
            rx1 = max(0, min(x1 - pad, ref_img.width - 1))
            ry1 = max(0, min(y1 - pad, ref_img.height - 1))
            rx2 = max(rx1 + 1, min(x2 + pad, ref_img.width))
            ry2 = max(ry1 + 1, min(y2 + pad, ref_img.height))
            draw.rectangle([rx1, ry1, rx2, ry2], fill=0)
    return mask


def _get_proxy_edits_url() -> str:
    return f"{_get_proxy_root_url()}/v1/images/edits"


def _compact_response_text(resp: requests.Response, limit: int = 200) -> str:
    text = (getattr(resp, "text", "") or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def generate_background_template_recompose(
    reference_image,
    title: str,
    subtitle: str,
    cta: str,
    plan: dict,
    layout_zones: list = None,
) -> Image.Image:
    """
    模式2专用：以参考图为风格基准做图生图，复刻其背景风格与配色。
    reference_image: 参考海报模板（PIL.Image / URL / data URL）
    layout_zones: 兼容旧签名，当前实现未使用
    """
    ref_img = _source_to_image(reference_image)
    ref_img.thumbnail((1024, 1024), Image.LANCZOS)

    visual = (plan or {}).get("visual_strategy") or {}
    ref_analysis = (plan or {}).get("reference_analysis") or {}
    style_kw = ref_analysis.get("prompt_boost", "") or visual.get("visual_language", "")

    prompt = (
        f"参考这张图片的背景风格、装饰元素和配色方案，重新生成一张海报背景。"
        f"标题文字为「{title}」，副标题为「{subtitle}」，按钮文字为「{cta}」。"
        f"保持整体版式协调统一。{style_kw}"
    ).strip()

    image_ref = _request_proxy_image(prompt, image=ref_img, size="2K")
    return _enhance_generated(_download_generated_image(image_ref))


def generate_background_auto(
    image_prompt: str,
    input_image=None,
    text_size: str = "1440x720",
    image_size: str = "2K",
) -> Image.Image:
    """
    有输入图时优先图生图；否则回退到现有文生图。
    """
    if input_image is not None:
        return generate_background_img2img(image_prompt, input_image, size=image_size)
    return generate_background(image_prompt, size=text_size)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    img = generate_background("modern interior design living room, soft lighting, minimalist style")
    img.save("test_bg.jpg")
    print("saved test_bg.jpg", img.size)
