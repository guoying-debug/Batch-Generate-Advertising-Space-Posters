# -*- coding: utf-8 -*-
"""
从 B站链接 或 通用网页链接 提取：标题、简介、封面图URL、关键词
升级：新增活动页专属字段、full_text（完整正文）和 images（页面图片URL列表）
"""
import re
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}
MAX_TEXT = 4000   # 正文字符上限
MAX_IMGS = 20     # 图片URL上限


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _pick_first(items: list) -> str:
    for item in items:
        item = _clean_text(item)
        if item:
            return item
    return ""


def _guess_cta_text(soup: BeautifulSoup) -> str:
    candidates = []
    for el in soup.find_all(["a", "button", "span", "div"]):
        text = _clean_text(el.get_text(" ", strip=True))
        if 2 <= len(text) <= 16 and any(k in text for k in ("立即", "马上", "领取", "报名", "查看", "了解", "参与", "预约", "点击", "体验")):
            candidates.append(text)
    return _pick_first(candidates)


def _extract_benefits(full_text: str) -> list:
    segments = []
    for line in re.split(r"[\n。！？；]", full_text or ""):
        line = _clean_text(line)
        if 8 <= len(line) <= 40:
            if any(k in line for k in ("免费", "福利", "优惠", "限时", "报名", "领取", "抽奖", "课程", "活动", "教程", "方案", "直播", "训练营", "体验", "升级")):
                segments.append(line)
    dedup = []
    for seg in segments:
        if seg not in dedup:
            dedup.append(seg)
    return dedup[:4]


def _extract_event_time(full_text: str) -> str:
    patterns = [
        r"\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*(?:至|-|到)\s*\d{4}[./-]\d{1,2}[./-]\d{1,2}",
        r"\d{1,2}[./-]\d{1,2}\s*(?:至|-|到)\s*\d{1,2}[./-]\d{1,2}",
        r"\d{4}年\d{1,2}月\d{1,2}日\s*(?:至|-|到)\s*\d{4}年?\d{1,2}月\d{1,2}日",
        r"\d{1,2}月\d{1,2}日\s*(?:至|-|到)\s*\d{1,2}月\d{1,2}日",
        r"(?:活动时间|报名时间|直播时间|截止时间)[:：]\s*([^\n]{4,30})",
    ]
    for pattern in patterns:
        match = re.search(pattern, full_text or "")
        if not match:
            continue
        return _clean_text(match.group(1) if match.lastindex else match.group(0))
    return ""


def _extract_activity_fields(soup: BeautifulSoup, title: str, desc: str, full_text: str) -> dict:
    headings = [_clean_text(el.get_text(" ", strip=True)) for el in soup.find_all(["h1", "h2", "h3"])]
    headings = [h for h in headings if 2 <= len(h) <= 40]
    paras = [_clean_text(el.get_text(" ", strip=True)) for el in soup.find_all(["p", "li"])]
    paras = [p for p in paras if 8 <= len(p) <= 80]
    main_title = _pick_first([title] + headings)
    subtitle = _pick_first([desc] + paras[:6])
    benefits = _extract_benefits("\n".join(paras) or full_text)
    cta = _guess_cta_text(soup)
    event_time = _extract_event_time(full_text)
    return {
        "main_title": main_title,
        "sub_title": subtitle,
        "benefits": benefits,
        "cta_text": cta,
        "event_time": event_time,
    }


def extract_bilibili(bvid: str) -> dict:
    """调 B站 API 提取视频信息"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    data = requests.get(url, headers=HEADERS, timeout=10).json()["data"]
    return {
        "title": data["title"],
        "desc": data.get("desc", "").strip() or data["title"],
        "cover": data["pic"],
        "keywords": [t["tag_name"] for t in data.get("tags", [])][:5],
        "full_text": data.get("desc", "").strip() or data["title"],
        "images": [data["pic"]],
        "activity_fields": {
            "main_title": data["title"],
            "sub_title": data.get("desc", "").strip()[:60],
            "benefits": [],
            "cta_text": "",
            "event_time": "",
        },
    }


def _extract_full_text(soup: BeautifulSoup) -> str:
    """提取页面完整正文，优先 article/main，回退到最长文本块"""
    for tag in ("article", "main"):
        container = soup.find(tag)
        if container:
            break
    else:
        # 找内容最长的 div/section
        candidates = soup.find_all(["div", "section"])
        container = max(candidates, key=lambda t: len(t.get_text()), default=soup.body)

    parts = []
    for el in container.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = el.get_text(" ", strip=True)
        if text and len(text) > 10:
            parts.append(text)

    full = "\n".join(parts)
    return full[:MAX_TEXT]


def _extract_images(soup: BeautifulSoup, base_url: str) -> list:
    """提取页面所有有效图片URL（过滤小图标和base64）"""
    seen = set()
    result = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if not src or src.startswith("data:"):
            continue
        abs_url = urljoin(base_url, src)
        # 过滤明显是图标的小图（URL含icon/logo/avatar/1x1等特征）
        lower = abs_url.lower()
        if any(x in lower for x in ("icon", "logo", "avatar", "1x1", "pixel", "blank", "placeholder")):
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            result.append(abs_url)
        if len(result) >= MAX_IMGS:
            break
    return result


def extract_webpage(url: str) -> dict:
    """深度解析单页：og标签 + 完整正文 + 所有图片URL"""
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "html.parser")

    def og(prop):
        tag = soup.find("meta", property=f"og:{prop}") or soup.find("meta", attrs={"name": prop})
        return tag["content"].strip() if tag and tag.get("content") else ""

    title = og("title") or (soup.title.string.strip() if soup.title else "")
    desc = og("description")
    cover = og("image")
    if cover:
        cover = urljoin(url, cover)

    if not desc:
        body = soup.find("body")
        if body:
            desc = re.sub(r"\s+", " ", body.get_text())[:200].strip()

    keywords = [m["content"] for m in soup.find_all("meta", attrs={"name": "keywords"}) if m.get("content")]
    keywords = keywords[0].split(",") if keywords else []

    full_text = _extract_full_text(soup)
    images = _extract_images(soup, url)
    if cover and cover not in images:
        images.insert(0, cover)
    activity_fields = _extract_activity_fields(soup, title, desc, full_text)

    return {
        "title": title,
        "desc": desc,
        "cover": cover,
        "keywords": keywords[:5],
        "full_text": full_text,
        "images": images,
        "activity_fields": activity_fields,
    }


def extract(url: str) -> dict:
    """统一入口：自动识别 B站 或 普通网页"""
    m = re.search(r"bilibili\.com/video/(BV\w+)", url)
    if m:
        return extract_bilibili(m.group(1))
    return extract_webpage(url)


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8")
    result = extract(sys.argv[1] if len(sys.argv) > 1 else "https://www.kujiale.cn/festatic/WxnShkxIPggyujQx")
    print(json.dumps(result, ensure_ascii=False, indent=2))
