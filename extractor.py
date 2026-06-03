# -*- coding: utf-8 -*-
"""
从 B站链接 或 通用网页链接 提取：标题、简介、封面图URL、关键词
"""
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"}


def extract_bilibili(bvid: str) -> dict:
    """调 B站 API 提取视频信息"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    data = requests.get(url, headers=HEADERS, timeout=10).json()["data"]
    return {
        "title": data["title"],
        "desc": data.get("desc", "").strip() or data["title"],
        "cover": data["pic"],
        "keywords": [t["tag_name"] for t in data.get("tags", [])][:5],
    }


def extract_webpage(url: str) -> dict:
    """解析通用网页的 og 标签和正文"""
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "html.parser")

    def og(prop):
        tag = soup.find("meta", property=f"og:{prop}") or soup.find("meta", attrs={"name": prop})
        return tag["content"].strip() if tag and tag.get("content") else ""

    title = og("title") or (soup.title.string.strip() if soup.title else "")
    desc = og("description")
    cover = og("image")

    # 没有 og:description 时抓正文前 200 字
    if not desc:
        body = soup.find("body")
        if body:
            desc = re.sub(r"\s+", " ", body.get_text())[:200].strip()

    keywords = [m["content"] for m in soup.find_all("meta", attrs={"name": "keywords"}) if m.get("content")]
    keywords = keywords[0].split(",") if keywords else []

    return {"title": title, "desc": desc, "cover": cover, "keywords": keywords[:5]}


def extract(url: str) -> dict:
    """统一入口：自动识别 B站 或 普通网页"""
    m = re.search(r"bilibili\.com/video/(BV\w+)", url)
    if m:
        return extract_bilibili(m.group(1))
    return extract_webpage(url)


if __name__ == "__main__":
    import sys, json
    result = extract(sys.argv[1] if len(sys.argv) > 1 else "https://www.kujiale.cn/festatic/WxnShkxIPggyujQx")
    print(json.dumps(result, ensure_ascii=False, indent=2))
