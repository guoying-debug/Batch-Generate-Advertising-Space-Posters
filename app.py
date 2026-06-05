# -*- coding: utf-8 -*-
import json
import os
import re
import gradio as gr
from dotenv import load_dotenv
import extractor, ai_writer, image_gen, poster_maker, media

load_dotenv()

MODE1 = "模式一：智能生成背景图（有图走图生图，无图走文生图）"
MODE2 = "模式二：复用封面/素材图（自动挑图）"

VIDEO_RE = re.compile(r"(bilibili\.com/video/|youtube\.com|youtu\.be|v\.qq\.com|douyin\.com)")
TEMPLATE_FILE = os.path.join(os.path.dirname(__file__), "poster_plan_templates.json")


def _is_video(url: str) -> bool:
    return bool(VIDEO_RE.search(url))


def _clean_url(raw: str) -> str:
    """从粘贴文本中提取真实 URL，兼容 APP/微信分享的『【标题】 链接』格式"""
    raw = (raw or "").strip()
    # 1) 直接抓 http(s) 链接（URL 不含中文和中文标点，据此切断尾部多余文字）
    m = re.search(r"https?://[^\s一-鿿，。、！？；：（）【】「」『』]+", raw)
    if m:
        return m.group(0).rstrip("，。！？、）】")
    # 2) 兜底：文本里只有 BV 号时拼成标准 B 站链接
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    if m:
        return f"https://www.bilibili.com/video/{m.group(1)}"
    return raw


def _load_templates() -> dict:
    if not os.path.exists(TEMPLATE_FILE):
        return {}
    try:
        with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[app] 模板读取失败: {e}")
        return {}


def _save_templates(data: dict):
    with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _template_choices():
    return sorted(_load_templates().keys())


def _reference_info_text(reference_analysis: dict) -> str:
    if not reference_analysis:
        return "未上传参考图"
    parts = [
        f"风格摘要: {reference_analysis.get('summary', '')}",
        f"模板: {reference_analysis.get('template_id', '')}",
        f"版式签名: {reference_analysis.get('layout_signature', '')}",
        f"版式: {reference_analysis.get('layout', '')}",
        f"版式判断: {reference_analysis.get('layout_reason', '')}",
        f"横幅版式: {reference_analysis.get('banner_layout_mode', '')}",
        f"方图版式: {reference_analysis.get('square_layout_mode', '')}",
        f"风格: {reference_analysis.get('style', '')}",
        f"视觉语言: {reference_analysis.get('visual_language', '')}",
        f"配色: {reference_analysis.get('color_palette', '')}",
        f"主体: {reference_analysis.get('subject', '')}",
    ]
    return "\n".join([p for p in parts if p.strip().split(':', 1)[-1].strip()]) or "已读取参考图"


def step_extract_and_write(url, operator_text, gallery_files, reference_files):
    """① 提取链接内容（含视频多帧+语音理解） + 生成文案"""
    if not url.strip():
        raise gr.Error("请输入链接")
    url = _clean_url(url)
    content = extractor.extract(url)

    video_summary = ""
    frames = []
    notes = []

    # 视频链接：下载 + 抽帧 + 多帧理解 + 语音转写
    if _is_video(url):
        notes.append("检测到视频链接，正在理解内容...")
        m = media.download_video(url)
        if m.get("video"):
            frames = media.extract_frames(m["video"], n=5)
            frame_desc = ai_writer.describe_frames(media.frames_to_data_urls(frames)) if frames else ""
            transcript = media.get_transcript(m)
            parts = []
            if frame_desc:
                parts.append(f"画面：{frame_desc}")
            if transcript:
                parts.append(f"语音内容：{transcript}")
            video_summary = "\n".join(parts)
            notes.append(f"已理解 {len(frames)} 帧画面 / 转写 {len(transcript)} 字")
        else:
            notes.append("视频下载失败或超限，已降级用封面+元数据")

    reference_images = _load_gallery(reference_files)
    reference_analysis = ai_writer.analyze_reference_images(reference_images)

    poster_goal = "教程推广" if _is_video(url) else "活动推广"
    poster_plan = ai_writer.generate_poster_plan(
        content,
        operator_text,
        video_summary,
        sizes=["2560x320", "1160x1016", "1080x1440"],
        brand="KUJIALE",
        poster_goal=poster_goal,
        reference_style="若上传参考图，则参考图决定海报的版式、配色、视觉语言和装饰节奏；若未上传，则按内容自动规划。",
        reference_analysis=reference_analysis,
    )
    copy = ai_writer.normalize_plan_to_legacy_fields(poster_plan)
    detail = ai_writer.plan_to_detail(poster_plan, content, operator_text)

    gallery_images = _load_gallery(gallery_files)
    candidate_entries = _build_candidate_entries(content, frames, gallery_images)
    theme = _theme_from_plan(poster_plan, copy["title"], detail)
    ranked_candidates = ai_writer.rank_candidate_images(candidate_entries, theme, topk=8)
    candidate_choices = _candidate_choice_list(ranked_candidates)
    link_images = content.get("images", [])
    gallery_count = len(gallery_files) if gallery_files else 0
    activity_fields = content.get("activity_fields") or {}
    visual = poster_plan.get("visual_strategy", {})
    info = (
        f"标题: {content['title']}\n"
        f"抓到正文 {len(content.get('full_text',''))} 字 / 链接图片 {len(link_images)} 张 / "
        f"视频帧 {len(frames)} 张 / 运营图库 {gallery_count} 张\n"
        f"海报类型: {poster_plan.get('poster_type','')}\n"
        f"主题摘要: {poster_plan.get('topic_summary','')}\n"
        f"模板ID: {visual.get('template_id','')}\n"
        f"参考图风格摘要: {poster_plan.get('reference_style_summary', '')}\n"
        f"可替换槽位: {'、'.join(visual.get('replaceable_slots', [])[:5])}\n"
        f"固定元素: {'、'.join(visual.get('fixed_elements', [])[:4])}\n"
        f"活动标题: {activity_fields.get('main_title','')}\n"
        f"活动时间: {activity_fields.get('event_time','')}\n"
        + ("\n".join(notes) if notes else "")
    )

    return (
        content["cover"],
        info,
        copy["title"],
        copy["subtitle"],
        copy["cta"],
        copy.get("image_prompt", ""),
        copy.get("image_render_prompt", copy.get("image_prompt", "")),
        detail,
        detail.get("image_prompt", copy.get("image_prompt", "")),
        copy.get("detail_render_prompt", detail.get("image_prompt", copy.get("image_prompt", ""))),
        copy.get("square_prompt", copy.get("image_prompt", "")),
        copy.get("square_render_prompt", copy.get("square_prompt", copy.get("image_prompt", ""))),
        json.dumps(poster_plan, ensure_ascii=False, indent=2),
        _reference_info_text(reference_analysis),
        "\n".join(activity_fields.get("benefits", [])),
        activity_fields.get("event_time", ""),
        activity_fields.get("cta_text", ""),
        _candidate_gallery_value(ranked_candidates),
        gr.update(choices=candidate_choices, value=(candidate_choices[0] if candidate_choices else None)),
            _ranking_reason_text(ranked_candidates),
        ranked_candidates,             # candidates_state
        video_summary,                 # video_summary_state
        poster_plan,                   # poster_plan_state
        reference_analysis,            # reference_analysis_state
        (_pick_note(ranked_candidates[0], "系统预选最高分") if ranked_candidates else "暂无可评分候选图"),
    )


def _load_gallery(gallery_files):
    """把运营上传的多图文件读成 PIL.Image 列表"""
    images = []
    if not gallery_files:
        return images
    from PIL import Image
    for f in gallery_files:
        path = f.name if hasattr(f, "name") else f
        try:
            images.append(Image.open(path).convert("RGB"))
        except Exception as e:
            print(f"[app] 图库读取失败 {path}: {e}")
    return images


def _build_candidate_entries(content, frames, gallery_images):
    entries = []
    seen_urls = set()
    for idx, img in enumerate(gallery_images or [], start=1):
        entries.append({"item": img, "source": "运营图库", "label": f"运营图库{idx}"})
    for idx, url in enumerate(content.get("images", []) or [], start=1):
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        source = "链接封面" if idx == 1 and content.get("cover") == url else "页面图片"
        entries.append({"item": url, "source": source, "label": f"{source}{idx}"})
    for idx, frame in enumerate(frames or [], start=1):
        entries.append({"item": frame, "source": "视频抽帧", "label": f"视频抽帧{idx}"})
    return entries


def _theme_from_plan(plan, title, detail):
    visual = (plan or {}).get("visual_strategy", {})
    return " ".join([
        (plan or {}).get("topic_summary", ""),
        title or "",
        (detail or {}).get("headline", ""),
        visual.get("subject", ""),
        visual.get("scene", ""),
    ]).strip() or "酷家乐家居设计"


def _candidate_gallery_value(ranked_candidates):
    gallery_value = []
    for idx, cand in enumerate(ranked_candidates or [], start=1):
        caption = f"{idx}. {cand.get('source', '候选图')} | {cand.get('score', 0)}分"
        gallery_value.append((cand.get("item"), caption))
    return gallery_value


def _candidate_choice_list(ranked_candidates):
    choices = []
    for idx, cand in enumerate(ranked_candidates or [], start=1):
        label = f"{idx}. {cand.get('source', '候选图')} | {cand.get('score', 0)}分 | {cand.get('label', '')}"
        choices.append(label)
        cand["choice_label"] = label
    return choices


def _ranking_reason_text(ranked_candidates, topn: int = 5):
    lines = []
    for idx, cand in enumerate((ranked_candidates or [])[:topn], start=1):
        lines.append(
            f"Top{idx} | 来源:{cand.get('source', '候选图')} | 分数:{cand.get('score', 0)} | 理由:{cand.get('reason', '无')}"
        )
    return "\n".join(lines) if lines else "暂无排序理由"


def _pick_note(cand, selection_mode: str):
    if not cand:
        return "未命中候选图"
    return (
        f"来源:{cand.get('source','候选图')} | 分数:{cand.get('score',0)} | "
        f"理由:{cand.get('reason', '无')} | 选择结果:{selection_mode}"
    )


def _generate_mode1_background(render_prompt: str, source_img, text_size: str, image_size: str = "2K"):
    prompt = (render_prompt or "").strip()
    if source_img is not None:
        try:
            return image_gen.generate_background_img2img(prompt, source_img, size=image_size), "图生图"
        except Exception as e:
            print(f"[app] 图生图失败，准备回退: {e}")
            if prompt:
                try:
                    return image_gen.generate_background(prompt, size=text_size), "图生图失败，已回退文生图"
                except Exception as fallback_e:
                    print(f"[app] 文生图回退失败，直接使用原图: {fallback_e}")
            return source_img, "图生图失败，已直接使用原图"
    if not prompt:
        raise gr.Error("缺少生图提示词，无法执行模式一。请先点①生成内容或手动填写安全生图提示词")
    try:
        return image_gen.generate_background(prompt, size=text_size), "文生图"
    except Exception as e:
        raise gr.Error(f"模式一背景生成失败：{e}") from e


def _parse_plan_text(plan_text, poster_plan_state):
    if plan_text and str(plan_text).strip():
        try:
            return ai_writer._normalize_plan(json.loads(plan_text))
        except Exception as e:
            raise gr.Error(f"poster_plan JSON 解析失败: {e}")
    return poster_plan_state or {}


def on_candidate_select(ranked_candidates_state, evt: gr.SelectData):
    ranked = ranked_candidates_state or []
    index = evt.index[0] if isinstance(evt.index, tuple) else evt.index
    if index is None or index < 0 or index >= len(ranked):
        return gr.update(), "候选图选择无效"
    cand = ranked[index]
    return gr.update(value=cand.get("choice_label")), _pick_note(cand, "已点选待生成")


def save_plan_template(template_name, plan_text):
    name = (template_name or "").strip()
    if not name:
        raise gr.Error("请输入模板名称")
    plan = _parse_plan_text(plan_text, {})
    data = _load_templates()
    data[name] = plan
    _save_templates(data)
    choices = _template_choices()
    return gr.update(choices=choices, value=name), f"已保存模板：{name}"


def load_plan_template(template_name):
    name = (template_name or "").strip()
    if not name:
        raise gr.Error("请选择模板")
    data = _load_templates()
    plan = data.get(name)
    if not plan:
        raise gr.Error("未找到对应模板")
    legacy = ai_writer.normalize_plan_to_legacy_fields(plan)
    compact = ai_writer.compact_copywriting(plan)
    detail = ai_writer.plan_to_detail(plan)
    event_info = plan.get("event_info", {})
    reference_analysis = {
        "summary": plan.get("reference_style_summary", ""),
        "template_id": ((plan.get("visual_strategy") or {}).get("template_id", "")),
        "layout": ((plan.get("visual_strategy") or {}).get("composition", "")),
        "style": ((plan.get("visual_strategy") or {}).get("style", "")),
        "banner_layout_mode": ((plan.get("visual_strategy") or {}).get("banner_layout_mode", "")),
        "square_layout_mode": ((plan.get("visual_strategy") or {}).get("square_layout_mode", "")),
        "visual_language": ((plan.get("visual_strategy") or {}).get("visual_language", "")),
        "color_palette": ((plan.get("visual_strategy") or {}).get("color_palette", "")),
        "subject": ((plan.get("visual_strategy") or {}).get("subject", "")),
    }
    return (
        json.dumps(plan, ensure_ascii=False, indent=2),
        compact.get("title", ""),
        compact.get("subtitle", ""),
        compact.get("cta", "立即查看"),
        legacy.get("image_prompt", ""),
        legacy.get("image_render_prompt", legacy.get("image_prompt", "")),
        legacy.get("square_prompt", ""),
        legacy.get("square_render_prompt", legacy.get("square_prompt", "")),
        legacy.get("detail_prompt", ""),
        legacy.get("detail_render_prompt", legacy.get("detail_prompt", "")),
        _reference_info_text(reference_analysis),
        "\n".join(event_info.get("benefits", [])),
        event_info.get("event_time", ""),
        compact.get("cta", "立即查看"),
        detail,
        f"已加载模板：{name}",
        plan,
        reference_analysis,
    )


def step_make(cover_url, title, subtitle, cta, mode, upload_img, image_prompt, image_render_prompt,
              square_prompt, square_render_prompt, detail_state, detail_prompt, detail_render_prompt, candidates_state, gallery_files,
              candidate_choice, poster_plan_text, poster_plan_state):
    """③ 合成 banner + 方图 + 详情页长图"""
    detail = detail_state or {}
    pick_note = ""
    plan = _parse_plan_text(poster_plan_text, poster_plan_state)
    legacy = ai_writer.normalize_plan_to_legacy_fields(plan)
    banner_prompt = image_prompt.strip() or legacy.get("image_prompt", "")
    banner_render_prompt = image_render_prompt.strip() or legacy.get("image_render_prompt", banner_prompt)
    square_prompt = square_prompt.strip() or legacy.get("square_prompt", banner_prompt)
    square_render_prompt = square_render_prompt.strip() or legacy.get("square_render_prompt", square_prompt or banner_render_prompt)
    detail_prompt = detail_prompt.strip() or legacy.get("detail_prompt", square_prompt)
    detail_render_prompt = detail_render_prompt.strip() or legacy.get("detail_render_prompt", detail_prompt or square_render_prompt)
    visual = plan.get("visual_strategy", {})
    ranked_candidates = candidates_state or []

    used_ai_gen = False
    generation_source = None

    if mode == MODE1:
        # 智能生成：有图优先图生图，无图再文生图
        if upload_img is not None:
            generation_source = upload_img
            banner_bg, banner_method = _generate_mode1_background(banner_render_prompt, generation_source, text_size="1440x720")
            square_bg, square_method = _generate_mode1_background(square_render_prompt or banner_render_prompt, generation_source, text_size="1024x1024")
            used_ai_gen = banner_method != "图生图失败，已直接使用原图" or square_method != "图生图失败，已直接使用原图"
            pick_note = (
                "来源:手动上传 | 分数:人工指定 | "
                f"Banner:{banner_method} | 方图:{square_method} | 选择结果:已基于上传图处理背景"
            )
        else:
            selected = next((c for c in ranked_candidates if c.get("choice_label") == candidate_choice), None)
            chosen = selected or (ranked_candidates[0] if ranked_candidates else None)
            if chosen is not None:
                generation_source = chosen["item"]
                banner_bg, banner_method = _generate_mode1_background(banner_render_prompt, generation_source, text_size="1440x720")
                square_bg, square_method = _generate_mode1_background(square_render_prompt or banner_render_prompt, generation_source, text_size="1024x1024")
                used_ai_gen = banner_method != "图生图失败，已直接使用原图" or square_method != "图生图失败，已直接使用原图"
                selection_mode = "手动点选使用" if selected else "系统预选最高分（智能背景）"
                pick_note = f"{_pick_note(chosen, selection_mode)} | Banner:{banner_method} | 方图:{square_method}"
            else:
                banner_bg, banner_method = _generate_mode1_background(banner_render_prompt, None, text_size="1440x720")
                square_bg, square_method = _generate_mode1_background(square_render_prompt or banner_render_prompt, None, text_size="1024x1024")
                used_ai_gen = True
                pick_note = (
                    "来源:AI安全生图提示词 | 分数:不适用 | "
                    f"Banner:{banner_method} | 方图:{square_method} | 选择结果:无候选图，已直接生成背景"
                )
    else:
        # 模式二：图库优先 → 候选池，按主题自动挑最佳
        if upload_img is not None:
            banner_bg = upload_img
            square_bg = upload_img
            pick_note = "来源:手动上传 | 分数:人工指定 | 选择结果:使用上传图生成"
        else:
            selected = next((c for c in ranked_candidates if c.get("choice_label") == candidate_choice), None)
            chosen = selected or (ranked_candidates[0] if ranked_candidates else None)
            if chosen is None and cover_url:
                chosen = {"item": cover_url, "source": "链接封面", "score": 0, "label": "链接封面"}
            if chosen is None:
                raise gr.Error("没有可用的候选图，请上传图片或改用模式一")
            if visual.get("image_strategy") == "local_replace" and cover_url:
                banner_bg = cover_url
                square_bg = chosen["item"]
            else:
                banner_bg = chosen["item"]
                square_bg = chosen["item"]
            selection_mode = "手动点选使用" if selected else "系统自动使用最高分"
            pick_note = _pick_note(chosen, selection_mode)

    # 详情页头图背景：模式一优先沿用同一输入图策略，失败时自动复用 Banner 背景
    if used_ai_gen and detail_render_prompt and detail_render_prompt.strip():
        try:
            detail_bg, detail_method = _generate_mode1_background(
                detail_render_prompt.strip(),
                generation_source if mode == MODE1 else None,
                text_size="1440x720",
            )
            if mode == MODE1:
                pick_note = f"{pick_note} | 详情页:{detail_method}"
        except Exception as e:
            print(f"[app] 详情页背景生成失败，复用 Banner: {e}")
            detail_bg = banner_bg
            if mode == MODE1:
                pick_note = f"{pick_note} | 详情页:生成失败，已复用Banner背景"
    else:
        detail_bg = banner_bg

    return (
        poster_maker.make_poster_any(banner_bg, title, subtitle, cta, "banner", plan),
        poster_maker.make_poster_any(square_bg, title, subtitle, cta, "square", plan),
        poster_maker.make_detail_page(detail_bg, detail, plan=plan),
        pick_note or "合成完成",
    )


def on_mode_change(mode):
    if mode == MODE1:
        return (
            gr.update(label="手动指定背景图（可选；有图走图生图，无图才文生图）"),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )
    return (
        gr.update(label="手动指定背景图（可选，留空则从图库/候选自动挑选）"),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
    )


def _compare_reference_note(slot_name: str, reference_analysis: dict, plan: dict, copy: dict, has_reference: bool) -> str:
    visual = (plan or {}).get("visual_strategy", {})
    prefix = f"{slot_name}: 参考图驱动" if has_reference else f"{slot_name}: 基线样式（未上传参考图）"
    lines = [
        prefix,
        f"模板: {visual.get('template_id', '')}",
        f"版式签名: {reference_analysis.get('layout_signature', '') if reference_analysis else ''}",
        f"横幅版式: {visual.get('banner_layout_mode', '') or visual.get('layout_mode', '')}",
        f"方图版式: {visual.get('square_layout_mode', '') or visual.get('layout_mode', '')}",
        f"版式判断: {reference_analysis.get('layout_reason', '') if reference_analysis else ''}",
        f"视觉语言: {visual.get('visual_language', '')}",
        f"风格强度: {visual.get('style_strength', '')}",
        f"装饰密度: {visual.get('decoration_density', '')}",
        f"蒙版强度: {visual.get('overlay_strength', '')}",
        f"配色: {visual.get('color_palette', '')}",
        f"标题: {copy.get('title', '')}",
        f"副标题: {copy.get('subtitle', '')}",
        f"按钮: {copy.get('cta', '')}",
        f"参考图摘要: {reference_analysis.get('summary', '') if reference_analysis else '无'}",
    ]
    return "\n".join([line for line in lines if line.strip().split(':', 1)[-1].strip() or "基线样式" in line])


def _compare_layout_key(plan: dict, reference_analysis: dict) -> str:
    visual = (plan or {}).get("visual_strategy", {})
    return "|".join([
        visual.get("template_id", "") or "generic",
        reference_analysis.get("layout_signature", "") if reference_analysis else "",
        visual.get("banner_layout_mode", "") or visual.get("layout_mode", ""),
        visual.get("square_layout_mode", "") or visual.get("layout_mode", ""),
    ])


def _compare_conflict_warnings(slot_results: list) -> list:
    warnings = []
    active = [item for item in slot_results if item.get("has_reference")]
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            left = active[i]
            right = active[j]
            if left.get("layout_key") != right.get("layout_key"):
                continue
            warnings.append(
                f"警告：{left['slot_name']} 与 {right['slot_name']} 被判成同一模板/版式组合（{left['layout_key']}），"
                "这次对比主要只会看到配色或细节差异，版式对比无效。请换一组版式差异更大的参考图。"
            )
    return warnings


def _compare_slot_outputs(slot_name: str, content: dict, operator_text: str, poster_goal: str, bg_source, reference_files):
    reference_images = _load_gallery(reference_files)
    has_reference = bool(reference_images)
    reference_analysis = ai_writer.analyze_reference_images(reference_images)
    plan = ai_writer.generate_poster_plan(
        content,
        operator_text,
        "",
        sizes=["2560x320", "1160x1016"],
        brand="KUJIALE",
        poster_goal=poster_goal,
        reference_style="对比测试中若上传参考图，则必须由参考图决定海报的模板、版式、视觉语言与装饰关系。",
        reference_analysis=reference_analysis,
    )
    copy = ai_writer.normalize_plan_to_legacy_fields(plan)
    analysis_text = (
        _reference_info_text(reference_analysis)
        if has_reference else
        "未上传参考图，本列作为基线样式。"
    )
    note_text = _compare_reference_note(slot_name, reference_analysis, plan, copy, has_reference)
    banner = poster_maker.make_poster_any(bg_source, copy["title"], copy["subtitle"], copy["cta"], "banner", plan)
    square = poster_maker.make_poster_any(bg_source, copy["title"], copy["subtitle"], copy["cta"], "square", plan)
    return analysis_text, note_text, banner, square, {
        "slot_name": slot_name,
        "has_reference": has_reference,
        "layout_key": _compare_layout_key(plan, reference_analysis),
    }


def generate_style_comparison(url, operator_text, compare_bg, ref_a_files, ref_b_files, ref_c_files):
    if not (url or "").strip():
        raise gr.Error("请输入链接")
    url = _clean_url(url)
    content = extractor.extract(url)
    poster_goal = "教程推广" if _is_video(url) else "活动推广"
    bg_source = compare_bg if compare_bg is not None else (content.get("cover") or "")
    bg_desc = "上传底图" if compare_bg is not None else ("链接封面" if content.get("cover") else "纯色占位")
    summary = (
        f"对比主题: {content.get('title', '')}\n"
        f"统一底图来源: {bg_desc}\n"
        f"对比说明: 三列使用同一条内容和同一张底图，仅切换参考图风格控制，便于直接比较模板、版式和视觉语言差异。"
    )
    slot_outputs = []
    for slot_name, files in (
        ("A组", ref_a_files),
        ("B组", ref_b_files),
        ("C组", ref_c_files),
    ):
        slot_outputs.append(_compare_slot_outputs(slot_name, content, operator_text, poster_goal, bg_source, files))
    warnings = _compare_conflict_warnings([item[4] for item in slot_outputs])
    if warnings:
        summary = summary + "\n" + "\n".join(warnings)
    outputs = [summary]
    for analysis_text, note_text, banner, square, meta in slot_outputs:
        if warnings:
            related = [w for w in warnings if meta["slot_name"] in w]
            if related:
                note_text = note_text + "\n" + "\n".join(related)
        outputs.extend([analysis_text, note_text, banner, square])
    return tuple(outputs)


with gr.Blocks(title="酷家乐广告海报生成工具") as demo:
    gr.Markdown(
        "## 酷家乐 · 广告位海报批量生成\n"
        "输入链接 → 视频多帧+语音理解 → 自动挑图/图生图/文生图 → 出海报+详情页"
    )

    cover_state = gr.State("")
    detail_state = gr.State({})
    candidates_state = gr.State([])
    video_summary_state = gr.State("")
    poster_plan_state = gr.State({})
    reference_analysis_state = gr.State({})

    with gr.Row():
        with gr.Column(scale=1):
            mode = gr.Radio([MODE1, MODE2], value=MODE2, label="生成模式")
            url = gr.Textbox(label="链接（B站视频/酷家乐活动页/网页）", placeholder="https://...")
            op_text = gr.Textbox(label="运营补充文案（可选）", placeholder="例：3分钟解锁异形门衣柜")
            gallery = gr.File(label="图片库（可选，多张，自动挑最贴合的）",
                              file_count="multiple", file_types=["image"])
            reference_gallery = gr.File(label="参考图（可选，多张，用于学习版式和风格）",
                                        file_count="multiple", file_types=["image"])
            extract_btn = gr.Button("① 读取链接 + 理解内容 + 生成文案", variant="primary")
            info_box = gr.Textbox(label="提取与理解结果", lines=5, interactive=False)

            gr.Markdown("**② 审核区 — 可手动修改后重新合成**")
            title_box    = gr.Textbox(label="主标题（Banner/方图用）")
            subtitle_box = gr.Textbox(label="副标题文案")
            cta_box      = gr.Textbox(label="行动号召")
            prompt_box   = gr.Textbox(label="Banner海报策划提示词（审核用）", lines=2)
            render_prompt_box = gr.Textbox(label="Banner安全生图提示词（模式一实际使用）", lines=2)
            square_prompt_box = gr.Textbox(label="方图海报策划提示词（审核用）", lines=2)
            square_render_prompt_box = gr.Textbox(label="方图安全生图提示词（模式一实际使用）", lines=2)
            detail_prompt_box = gr.Textbox(label="详情页海报策划提示词（审核用）", lines=3)
            detail_render_prompt_box = gr.Textbox(label="详情页安全生图提示词（模式一实际使用）", lines=3)
            plan_box = gr.Textbox(label="poster_plan（可审核和手改 JSON）", lines=18)
            reference_box = gr.Textbox(label="参考图分析结果", lines=6, interactive=False)
            gr.Markdown("**活动页字段命中结果**")
            benefits_box = gr.Textbox(label="benefits", lines=4, interactive=False)
            event_time_box = gr.Textbox(label="event_time", interactive=False)
            activity_cta_box = gr.Textbox(label="cta_text", interactive=False)
            gr.Markdown("**模板管理**")
            template_name_box = gr.Textbox(label="模板名称")
            template_dropdown = gr.Dropdown(label="已保存模板", choices=_template_choices(), allow_custom_value=True)
            with gr.Row():
                save_template_btn = gr.Button("保存当前模板")
                load_template_btn = gr.Button("加载选中模板")
            upload_img   = gr.Image(label="手动指定背景图（可选；模式一有图走图生图，模式二直接排版）", type="pil")
            make_btn     = gr.Button("③ 合成海报", variant="primary")

        with gr.Column(scale=2):
            pick_box = gr.Textbox(label="挑图结果", lines=1, interactive=False, visible=True)
            candidate_gallery = gr.Gallery(label="模式2候选图库（点击图片可选中）", columns=4, rows=2, height=320, object_fit="contain", visible=True)
            candidate_choice = gr.Radio(label="当前候选图选择", choices=[], interactive=True, visible=True)
            rank_reason_box = gr.Textbox(label="TopN 排序理由", lines=6, interactive=False, visible=True)
            banner_out = gr.Image(label="横幅  2560 × 320", type="pil")
            square_out = gr.Image(label="方图  1160 × 1016", type="pil")
            detail_out = gr.Image(label="详情页长图  1080 × 动态", type="pil")

    gr.Markdown("---")
    gr.Markdown("## 风格对比测试页\n同一条内容、同一张底图，分别套 3 组参考图，直接比较模板识别、版式变化和预览差异。")
    with gr.Row():
        with gr.Column(scale=1):
            compare_url = gr.Textbox(label="对比链接", placeholder="https://...")
            compare_op_text = gr.Textbox(label="对比补充文案（可选）", placeholder="例：强调现代轻奢、活动感或教程感")
            compare_bg = gr.Image(label="统一底图（可选，建议上传同一张图做公平对比）", type="pil")
            compare_ref_a = gr.File(label="A组参考图（可空，留空即基线样式）", file_count="multiple", file_types=["image"])
            compare_ref_b = gr.File(label="B组参考图", file_count="multiple", file_types=["image"])
            compare_ref_c = gr.File(label="C组参考图", file_count="multiple", file_types=["image"])
            compare_btn = gr.Button("生成风格对比", variant="primary")
            compare_summary_box = gr.Textbox(label="对比说明", lines=4, interactive=False)
        with gr.Column(scale=3):
            with gr.Row():
                with gr.Column():
                    compare_a_ref_box = gr.Textbox(label="A组参考图分析", lines=7, interactive=False)
                    compare_a_note_box = gr.Textbox(label="A组风格结果", lines=10, interactive=False)
                    compare_a_banner = gr.Image(label="A组 Banner", type="pil")
                    compare_a_square = gr.Image(label="A组 方图", type="pil")
                with gr.Column():
                    compare_b_ref_box = gr.Textbox(label="B组参考图分析", lines=7, interactive=False)
                    compare_b_note_box = gr.Textbox(label="B组风格结果", lines=10, interactive=False)
                    compare_b_banner = gr.Image(label="B组 Banner", type="pil")
                    compare_b_square = gr.Image(label="B组 方图", type="pil")
                with gr.Column():
                    compare_c_ref_box = gr.Textbox(label="C组参考图分析", lines=7, interactive=False)
                    compare_c_note_box = gr.Textbox(label="C组风格结果", lines=10, interactive=False)
                    compare_c_banner = gr.Image(label="C组 Banner", type="pil")
                    compare_c_square = gr.Image(label="C组 方图", type="pil")

    mode.change(on_mode_change, inputs=[mode], outputs=[upload_img, pick_box, candidate_gallery, candidate_choice, rank_reason_box])

    extract_btn.click(
        step_extract_and_write,
        inputs=[url, op_text, gallery, reference_gallery],
        outputs=[cover_state, info_box, title_box, subtitle_box, cta_box,
                 prompt_box, render_prompt_box, detail_state, detail_prompt_box, detail_render_prompt_box, square_prompt_box, square_render_prompt_box, plan_box, reference_box,
                 benefits_box, event_time_box, activity_cta_box,
                 candidate_gallery, candidate_choice, rank_reason_box, candidates_state, video_summary_state, poster_plan_state, reference_analysis_state,
                 pick_box],
    )
    candidate_gallery.select(
        on_candidate_select,
        inputs=[candidates_state],
        outputs=[candidate_choice, pick_box],
    )
    save_template_btn.click(
        save_plan_template,
        inputs=[template_name_box, plan_box],
        outputs=[template_dropdown, pick_box],
    )
    load_template_btn.click(
        load_plan_template,
        inputs=[template_dropdown],
        outputs=[plan_box, title_box, subtitle_box, cta_box, prompt_box, render_prompt_box, square_prompt_box, square_render_prompt_box,
                 detail_prompt_box, detail_render_prompt_box, reference_box, benefits_box, event_time_box, activity_cta_box,
                 detail_state, pick_box, poster_plan_state, reference_analysis_state],
    )
    make_btn.click(
        step_make,
        inputs=[cover_state, title_box, subtitle_box, cta_box, mode, upload_img,
                prompt_box, render_prompt_box, square_prompt_box, square_render_prompt_box, detail_state, detail_prompt_box, detail_render_prompt_box,
                candidates_state, gallery, candidate_choice, plan_box, poster_plan_state],
        outputs=[banner_out, square_out, detail_out, pick_box],
    )
    compare_btn.click(
        generate_style_comparison,
        inputs=[compare_url, compare_op_text, compare_bg, compare_ref_a, compare_ref_b, compare_ref_c],
        outputs=[
            compare_summary_box,
            compare_a_ref_box, compare_a_note_box, compare_a_banner, compare_a_square,
            compare_b_ref_box, compare_b_note_box, compare_b_banner, compare_b_square,
            compare_c_ref_box, compare_c_note_box, compare_c_banner, compare_c_square,
        ],
    )

if __name__ == "__main__":
    demo.launch(inbrowser=True)
