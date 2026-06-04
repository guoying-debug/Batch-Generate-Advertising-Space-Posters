# -*- coding: utf-8 -*-
"""
视频处理：下载 / 抽帧 / 语音转写
- download_video: yt-dlp 下载单文件视频流（含音轨，免 ffmpeg 合并）
- extract_frames: opencv 均匀抽帧，跳过纯黑/白帧
- frames_to_data_urls: 帧转 base64 data URL（供视觉模型）
- get_transcript: 优先用平台字幕，无字幕则 faster-whisper 本地转写
所有函数失败即返回空值，由调用方降级。
"""
import os
import io
import re
import base64
import glob
import tempfile
import shutil

import cv2
from PIL import Image

# 限制：超过这些阈值跳过下载，仅用元数据降级
MAX_DURATION = 600      # 10 分钟
MAX_FILESIZE = 100 * 1024 * 1024  # 100MB
ASR_CLIP_SEC = 180      # ASR 只转写前 3 分钟
MAX_TRANSCRIPT = 2000   # 转写文本上限


def _pick_best_format(formats: list, want_video: bool = False, want_audio: bool = False) -> dict | None:
    candidates = []
    for f in formats or []:
        if want_video and f.get("vcodec") in (None, "none"):
            continue
        if want_audio and f.get("acodec") in (None, "none"):
            continue
        if not want_audio and f.get("acodec") not in (None, "none"):
            pass
        if f.get("protocol") and "m3u8" in str(f.get("protocol")):
            continue
        candidates.append(f)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda x: (
            x.get("height") or 0,
            x.get("abr") or 0,
            x.get("tbr") or 0,
        ),
        reverse=True,
    )[0]


def _download_single(ydl_opts: dict, url: str, format_selector: str, outtmpl: str) -> bool:
    import yt_dlp

    opts = dict(ydl_opts)
    opts["format"] = format_selector
    opts["outtmpl"] = outtmpl
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        print(f"[media] 格式 {format_selector} 下载失败: {e}")
        return False


def download_video(url: str, tmp_dir: str = None) -> dict:
    """
    下载单文件视频流（progressive，含音轨，无需 ffmpeg 合并）+ 尝试下字幕。
    返回 {"video": path_or_None, "subtitle": path_or_None, "duration": int}
    失败返回全 None。
    """
    import yt_dlp

    tmp_dir = tmp_dir or tempfile.mkdtemp(prefix="kjl_media_")
    video_tmpl = os.path.join(tmp_dir, "video.%(ext)s")
    audio_tmpl = os.path.join(tmp_dir, "audio.%(ext)s")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "max_filesize": MAX_FILESIZE,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en"],
        "subtitlesformat": "vtt/srt/best",
        # 反 412 风控：补全浏览器请求头，B站必须带 Referer
        "http_headers": {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Referer": "https://www.bilibili.com/",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        "retries": 3,
    }
    # 可选：用浏览器 cookie 进一步绕过风控（在 .env 设 YTDLP_COOKIES_FROM_BROWSER=chrome/edge/firefox）
    cookies_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER")
    if cookies_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_browser,)
    cookies_file = os.getenv("YTDLP_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        ydl_opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get("duration") or 0
            if duration and duration > MAX_DURATION:
                print(f"[media] 视频时长 {duration}s 超限，跳过下载")
                return {"video": None, "audio": None, "subtitle": None, "duration": duration}
            formats = info.get("formats") or []

            progressive = [
                f for f in formats
                if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")
            ]
            downloaded = False
            if progressive:
                prog = _pick_best_format(progressive, want_video=True, want_audio=True)
                if prog and prog.get("format_id"):
                    downloaded = _download_single(ydl_opts, url, prog["format_id"], video_tmpl)

            if not downloaded:
                video_fmt = _pick_best_format(formats, want_video=True, want_audio=False)
                if video_fmt and video_fmt.get("format_id"):
                    downloaded = _download_single(ydl_opts, url, video_fmt["format_id"], video_tmpl)
                audio_fmt = _pick_best_format(
                    [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")],
                    want_audio=True,
                )
                if audio_fmt and audio_fmt.get("format_id"):
                    _download_single(ydl_opts, url, audio_fmt["format_id"], audio_tmpl)

            if not downloaded:
                raise RuntimeError("未找到可下载的视频格式")
    except Exception as e:
        print(f"[media] 视频下载失败: {e}")
        return {"video": None, "audio": None, "subtitle": None, "duration": 0}

    videos = [f for f in glob.glob(os.path.join(tmp_dir, "video.*"))
              if not f.endswith((".vtt", ".srt"))]
    audios = [f for f in glob.glob(os.path.join(tmp_dir, "audio.*"))
              if not f.endswith((".vtt", ".srt"))]
    subs = glob.glob(os.path.join(tmp_dir, "video.*.vtt")) + \
        glob.glob(os.path.join(tmp_dir, "video.*.srt"))
    if not subs:
        subs = glob.glob(os.path.join(tmp_dir, "audio.*.vtt")) + \
            glob.glob(os.path.join(tmp_dir, "audio.*.srt"))

    return {
        "video": videos[0] if videos else None,
        "audio": audios[0] if audios else None,
        "subtitle": subs[0] if subs else None,
        "duration": duration,
    }


def _is_blank_frame(frame) -> bool:
    """判断是否纯黑/纯白帧（标准差极低）"""
    return frame.std() < 12


def extract_frames(video_path: str, n: int = 5) -> list:
    """opencv 按时长均匀抽 n 帧，跳过纯黑/白帧，返回 PIL.Image 列表"""
    if not video_path or not os.path.exists(video_path):
        return []
    frames = []
    try:
        cap = cv2.VideoCapture(video_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        if total <= 0:
            cap.release()
            return []
        # 多取一些候选位，过滤空帧后取前 n 张
        positions = [int(total * (i + 1) / (n * 2 + 1)) for i in range(n * 2)]
        for pos in positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            if not ok or frame is None or _is_blank_frame(frame):
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(rgb))
            if len(frames) >= n:
                break
        cap.release()
    except Exception as e:
        print(f"[media] 抽帧失败: {e}")
    return frames


def frames_to_data_urls(frames: list, max_side: int = 768) -> list:
    """帧转 base64 data URL（缩放控体积），供视觉模型输入"""
    urls = []
    for im in frames:
        try:
            img = im.copy()
            img.thumbnail((max_side, max_side), Image.LANCZOS)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode()
            urls.append(f"data:image/jpeg;base64,{b64}")
        except Exception as e:
            print(f"[media] 帧编码失败: {e}")
    return urls


def _parse_subtitle(path: str) -> str:
    """解析 vtt/srt 字幕为纯文本"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except Exception:
        return ""
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.isdigit() or line.upper() == "WEBVTT":
            continue
        # 跳过时间轴行 00:00:01.000 --> 00:00:03.000
        if "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line)  # 去标签
        lines.append(line)
    # 去重相邻重复行（自动字幕常见）
    out = []
    for l in lines:
        if not out or out[-1] != l:
            out.append(l)
    return " ".join(out)[:MAX_TRANSCRIPT]


def get_transcript(media: dict) -> str:
    """
    取视频语音文本：优先平台字幕，无则 faster-whisper 本地转写。
    media: download_video() 的返回 dict
    """
    # 1) 平台字幕
    sub = media.get("subtitle")
    if sub and os.path.exists(sub):
        text = _parse_subtitle(sub)
        if text.strip():
            return text

    # 2) ASR 转写
    audio = media.get("audio")
    video = media.get("video")
    asr_source = audio if audio and os.path.exists(audio) else video
    if not asr_source or not os.path.exists(asr_source):
        return ""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(
            os.getenv("ASR_MODEL", "tiny"),
            device="cpu",
            compute_type="int8",
        )
        segments, _ = model.transcribe(
            asr_source,
            language=None,            # 自动检测中英
            clip_timestamps=f"0,{ASR_CLIP_SEC}",
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text[:MAX_TRANSCRIPT]
    except Exception as e:
        print(f"[media] ASR 转写失败: {e}")
        return ""


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8")
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/BV1GJ411x7h7"
    print("下载中...")
    media = download_video(test_url)
    print(json.dumps({k: (v if k != "video" else os.path.basename(v) if v else None)
                      for k, v in media.items()}, ensure_ascii=False))
    if media["video"]:
        frames = extract_frames(media["video"], n=5)
        print(f"抽到 {len(frames)} 帧")
        transcript = get_transcript(media)
        print(f"转写 {len(transcript)} 字: {transcript[:120]}")
