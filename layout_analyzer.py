# -*- coding: utf-8 -*-
"""参考图布局分析：OCR + 区域聚类 → 结构化布局规范 JSON"""
import json
import math
import os
import traceback
import urllib.request
import numpy as np
from PIL import Image
from typing import List, Tuple


def _pil_to_cv2(img: Image.Image):
    import cv2
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def _scale_bbox(bbox, src_size, dst_size):
    """把 bbox [x1,y1,x2,y2] 从 src_size 比例映射到 dst_size"""
    sw, sh = src_size
    dw, dh = dst_size
    x1, y1, x2, y2 = bbox
    return [
        round(x1 * dw / sw),
        round(y1 * dh / sh),
        round(x2 * dw / sw),
        round(y2 * dh / sh),
    ]


def _bbox_height(bbox):
    return bbox[3] - bbox[1]


def _bbox_center_x(bbox):
    return (bbox[0] + bbox[2]) / 2


def _alignment(bbox, canvas_w):
    cx = _bbox_center_x(bbox)
    if cx < canvas_w * 0.4:
        return "left"
    if cx > canvas_w * 0.6:
        return "right"
    return "center"


def _font_size_from_height(h_px):
    """根据文字块像素高度估算合理字号范围"""
    base = max(12, round(h_px * 0.72))
    return [base, round(base * 1.2)]


def _sample_text_color(img: Image.Image, bbox) -> str:
    """从 bbox 中心区域采样主要文字颜色（返回十六进制）"""
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    r = max(4, min(12, (x2 - x1) // 6))
    region = img.crop((max(0, cx - r), max(0, cy - r), min(img.width, cx + r), min(img.height, cy + r)))
    pixels = list(region.convert("RGB").getdata())
    if not pixels:
        return "#000000"
    avg = tuple(round(sum(c[i] for c in pixels) / len(pixels)) for i in range(3))
    # 如果颜色接近背景，返回反色
    lum = 0.299 * avg[0] + 0.587 * avg[1] + 0.114 * avg[2]
    return "#{:02X}{:02X}{:02X}".format(*avg) if lum < 128 else "#{:02X}{:02X}{:02X}".format(*avg)


_ocr_instance = None


def _debug_report(hypothesis_id: str, location: str, msg: str, data: dict | None = None):
    env_path = os.path.join(".dbg", "paddle-ocr-runtime.env")
    url = "http://127.0.0.1:7777/event"
    session_id = "paddle-ocr-runtime"
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("DEBUG_SERVER_URL="):
                    url = line.split("=", 1)[1].strip()
                elif line.startswith("DEBUG_SESSION_ID="):
                    session_id = line.split("=", 1)[1].strip()
    except Exception:
        pass
    payload = {
        "sessionId": session_id,
        "runId": "pre-fix",
        "hypothesisId": hypothesis_id,
        "location": location,
        "msg": f"[DEBUG] {msg}",
        "data": data or {},
    }
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            ),
            timeout=1,
        ).read()
    except Exception:
        pass

def _get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        from paddleocr import PaddleOCR
        # enable_mkldnn=False：绕开 paddle 3.x 在 Windows CPU 上 oneDNN+PIR 执行器的
        # ConvertPirAttribute2RuntimeAttribute 崩溃；关掉方向分类/文档矫正子模型，只保留检测+识别，提速。
        _ocr_instance = PaddleOCR(
            lang="ch",
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _ocr_instance


def _parse_ocr_result(result) -> List[dict]:
    """兼容 PaddleOCR 3.x (dict) 和 2.x (list) 两种返回格式"""
    zones = []
    if not result:
        return zones

    first = result[0] if isinstance(result, (list, tuple)) else result

    # 3.x: result = [{"rec_texts": [...], "rec_scores": [...], "rec_polys": [...]}]
    if isinstance(first, dict) and "rec_texts" in first:
        texts = first.get("rec_texts") or []
        scores = first.get("rec_scores") or []
        polys = first.get("rec_polys") or []
        for text, conf, points in zip(texts, scores, polys):
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            zones.append({
                "text": text,
                "bbox": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))],
                "confidence": float(conf),
            })
        return zones

    # 2.x: result = [[(points, (text, conf)), ...]]
    lines = first if isinstance(first, list) else result
    for line in (lines or []):
        try:
            points, (text, conf) = line
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            zones.append({
                "text": text,
                "bbox": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))],
                "confidence": float(conf),
            })
        except (TypeError, ValueError):
            continue
    return zones


def _ocr_zones(img: Image.Image) -> List[dict]:
    """
    用 PaddleOCR 识别文字区域。
    返回列表，每项：{text, bbox:[x1,y1,x2,y2], confidence}
    失败时返回空列表（调用方按 confidence=0 回退）
    """
    try:
        ocr = _get_ocr()
        cv2_img = _pil_to_cv2(img)
        try:
            result = ocr.predict(cv2_img)
        except AttributeError:
            # 旧版本无 predict，回退到 ocr()
            result = ocr.ocr(cv2_img)

        zones = _parse_ocr_result(result)
        return zones
    except Exception as e:
        # #region debug-point E:ocr-exception
        _debug_report("E", "layout_analyzer.py:_ocr_zones", "ocr failed", {
            "error_type": type(e).__name__,
            "error": str(e),
            "traceback": traceback.format_exc(limit=6),
        })
        # #endregion
        print(f"[layout_analyzer] OCR 失败，跳过: {e}")
        return []


def _cluster_zones(raw_zones: List[dict], canvas_h: int) -> List[dict]:
    """
    将 OCR 文字块按垂直位置聚类成语义层级：
      - 高度最大的一组 → title
      - 次高一组 → subtitle
      - 靠近底部且短文本 → cta（按钮）
      - 其余 → caption
    """
    if not raw_zones:
        return []

    # 按 bbox 高度降序排列
    sorted_zones = sorted(raw_zones, key=lambda z: _bbox_height(z["bbox"]), reverse=True)
    max_h = _bbox_height(sorted_zones[0]["bbox"])

    labeled = []
    for z in sorted_zones:
        h = _bbox_height(z["bbox"])
        text_len = len(z["text"])
        # 判断是否按钮：短文本（≤8字）+ 高度中等
        if text_len <= 8 and h < max_h * 0.6:
            zone_type = "button"
        elif h >= max_h * 0.75:
            zone_type = "title"
        elif h >= max_h * 0.45:
            zone_type = "subtitle"
        else:
            zone_type = "caption"
        labeled.append({**z, "zone_type": zone_type})

    # 确保 title 只有一项（最高的那个）
    titles = [z for z in labeled if z["zone_type"] == "title"]
    if len(titles) > 1:
        titles[1:] = [{**z, "zone_type": "subtitle"} for z in titles[1:]]
        labeled = [z if z["zone_type"] != "title" else titles[0] for z in labeled]

    return labeled


def _infer_visual_zone(text_bboxes: List[list], canvas_w: int, canvas_h: int) -> dict:
    """推断非文字视觉区：取所有文字区合并后的补集"""
    if not text_bboxes:
        return {"zone_id": "visual_zone", "zone_type": "visual",
                "bbox": [0, 0, canvas_w, canvas_h], "strategy": "full_background"}

    text_x2_max = max(b[2] for b in text_bboxes)
    text_x1_min = min(b[0] for b in text_bboxes)

    # 如果文字区集中在左侧（左文右图）
    if text_x2_max < canvas_w * 0.55:
        return {"zone_id": "visual_zone", "zone_type": "visual",
                "bbox": [text_x2_max, 0, canvas_w, canvas_h], "strategy": "full_background"}
    # 文字区集中在右侧
    if text_x1_min > canvas_w * 0.45:
        return {"zone_id": "visual_zone", "zone_type": "visual",
                "bbox": [0, 0, text_x1_min, canvas_h], "strategy": "full_background"}
    # 居中布局 → 全背景
    return {"zone_id": "visual_zone", "zone_type": "visual",
            "bbox": [0, 0, canvas_w, canvas_h], "strategy": "full_background"}


def _template_summary(visual_zone: dict, canvas_w: int) -> str:
    vx1 = visual_zone["bbox"][0]
    vx2 = visual_zone["bbox"][2]
    if vx1 > canvas_w * 0.4:
        return "左文右图分栏"
    if vx2 < canvas_w * 0.6:
        return "右文左图分栏"
    return "全背景居中"


def _overall_confidence(raw_zones: List[dict]) -> float:
    if not raw_zones:
        return 0.0
    confs = [z.get("confidence", 0) for z in raw_zones]
    return round(sum(confs) / len(confs), 3)


# ── 派生尺寸推导 ────────────────────────────────────────────────────────────

_ZONE_TYPE_ORDER = {"title": 0, "subtitle": 1, "button": 2, "caption": 3}


def derive_layout(banner_spec: dict, target_size: Tuple[int, int]) -> dict:
    """
    从 banner_spec 推导其他尺寸（方图 / 详情页）的布局规范。
    策略：
      - 水平版式 → 垂直版式（左文右图 → 上文下图）
      - bbox 比例从 banner 画布映射到目标画布
    """
    src_w = banner_spec.get("canvas", {}).get("width", 2560)
    src_h = banner_spec.get("canvas", {}).get("height", 320)
    dst_w, dst_h = target_size

    new_zones = []
    for z in banner_spec.get("layout_zones", []):
        if z["zone_type"] == "visual":
            # 视觉区始终铺满目标画布
            new_zones.append({**z, "bbox": [0, 0, dst_w, dst_h]})
            continue

        old = z["bbox"]
        # 原始相对坐标（0~1）
        rx1, ry1, rx2, ry2 = old[0]/src_w, old[1]/src_h, old[2]/src_w, old[3]/src_h

        # banner 是横向，如果原文字区在左半（左文右图），映射到上半（上文下图）
        if src_w > src_h * 2:  # 横向画布
            # x 方向映射到 x，y 方向重新分配（上半留给文字）
            new_x1 = round(rx1 * dst_w)
            new_x2 = round(rx2 * dst_w)
            # 文字区在上半，高度按原始高度占比线性压缩到目标高度的前 50%
            new_y1 = round(ry1 * dst_h * 0.5)
            new_y2 = round(ry2 * dst_h * 0.5)
        else:
            new_x1 = round(rx1 * dst_w)
            new_x2 = round(rx2 * dst_w)
            new_y1 = round(ry1 * dst_h)
            new_y2 = round(ry2 * dst_h)

        new_zones.append({
            **z,
            "bbox": [new_x1, new_y1, new_x2, new_y2],
            "font_size_range": _font_size_from_height(new_y2 - new_y1),
        })

    return {
        "canvas": {"width": dst_w, "height": dst_h},
        "layout_zones": new_zones,
        "confidence": banner_spec.get("confidence", 0),
        "template_summary": banner_spec.get("template_summary", ""),
    }


# ── 公开接口 ────────────────────────────────────────────────────────────────

def analyze_layout(image: Image.Image, target_size: Tuple[int, int] = (2560, 320)) -> dict:
    """
    从参考图提取布局规范。

    Returns:
        {
          "canvas": {"width": w, "height": h},
          "layout_zones": [...],
          "confidence": 0~1,
          "template_summary": "左文右图分栏"
        }
        confidence < 0.5 时建议回退到模板系统
    """
    src_w, src_h = image.size
    dst_w, dst_h = target_size

    raw_zones = _ocr_zones(image)
    confidence = _overall_confidence(raw_zones)

    clustered = _cluster_zones(raw_zones, src_h)

    layout_zones = []
    text_bboxes = []
    zone_counters = {}

    for z in clustered:
        zone_type = z["zone_type"]
        count = zone_counters.get(zone_type, 0) + 1
        zone_counters[zone_type] = count
        zone_id = f"{zone_type}_zone" if count == 1 else f"{zone_type}_zone_{count}"

        scaled = _scale_bbox(z["bbox"], (src_w, src_h), (dst_w, dst_h))
        text_color = _sample_text_color(image, z["bbox"])
        h = scaled[3] - scaled[1]

        entry = {
            "zone_id": zone_id,
            "zone_type": "button" if zone_type == "button" else "text",
            "bbox": scaled,
            "alignment": _alignment(scaled, dst_w),
            "font_size_range": _font_size_from_height(h),
            "text_color": text_color,
            "original_text": z["text"],
        }
        layout_zones.append(entry)
        text_bboxes.append(scaled)

    visual_zone = _infer_visual_zone(text_bboxes, dst_w, dst_h)
    layout_zones.append(visual_zone)

    # 按 zone_type 优先级排序
    layout_zones.sort(key=lambda z: _ZONE_TYPE_ORDER.get(z.get("zone_id", "").split("_")[0], 99))

    return {
        "canvas": {"width": dst_w, "height": dst_h},
        "layout_zones": layout_zones,
        "confidence": confidence,
        "template_summary": _template_summary(visual_zone, dst_w),
    }
