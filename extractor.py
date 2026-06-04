# -*- coding: utf-8 -*-
"""
从 B站链接 或 通用网页链接 提取：标题、简介、封面图URL、关键词
升级：新增 full_text（完整正文）和 images（页面图片URL列表）
"""
import re
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}
MAX_TEXT = 4000   # 正文字符上限
MAX_IMGS = 20     # 图片URL上限


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

    return {
        "title": title,
        "desc": desc,
        "cover": cover,
        "keywords": keywords[:5],
        "full_text": full_text,
        "images": images,
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
