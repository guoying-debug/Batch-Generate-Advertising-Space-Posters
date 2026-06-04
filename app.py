# -*- coding: utf-8 -*-
import json
import os
import re
import gradio as gr
from dotenv import load_dotenv
import extractor, ai_writer, image_gen, poster_maker, media

load_dotenv()

MODE1 = "模式一：AI生成背景图"
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
        f"版式: {reference_analysis.get('layout', '')}",
        f"风格: {reference_analysis.get('style', '')}",
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
        reference_style="现代家居教程海报，设计案例图片加文字，适合运营广告位",
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
        "layout": ((plan.get("visual_strategy") or {}).get("composition", "")),
        "style": ((plan.get("visual_strategy") or {}).get("style", "")),
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
    """③ 合成 banner + 方图 + 详情页长图（模式二自动挑图）"""
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

    if mode == MODE1:
        # AI 生图（运营手动上传单图优先）
        if upload_img is not None:
            banner_bg = upload_img
            square_bg = upload_img
            pick_note = "来源:手动上传 | 分数:人工指定 | 选择结果:使用上传图生成"
        else:
            if not banner_render_prompt:
                raise gr.Error("模式一需要生图描述，请先点①生成或手动填写")
            banner_bg = image_gen.generate_background(banner_render_prompt, size="1440x720")
            square_bg = image_gen.generate_background(square_render_prompt or banner_render_prompt, size="1024x1024")
            pick_note = "来源:AI安全生图提示词 | 分数:不适用 | 选择结果:使用AI生成背景"
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

    # 详情页头图背景
    if mode == MODE1 and detail_render_prompt and detail_render_prompt.strip():
        detail_bg = image_gen.generate_background(detail_render_prompt.strip(), size="1440x720")
    else:
        detail_bg = banner_bg

    return (
        poster_maker.make_poster_any(banner_bg, title, subtitle, cta, "banner", plan),
        poster_maker.make_poster_any(square_bg, title, subtitle, cta, "square", plan),
        poster_maker.make_detail_page(detail_bg, detail),
        pick_note or "合成完成",
    )


def on_mode_change(mode):
    if mode == MODE1:
        return gr.update(label="手动替换背景图（可选，留空则用AI生图）")
    return gr.update(label="手动指定背景图（可选，留空则从图库/候选自动挑选）")


with gr.Blocks(title="酷家乐广告海报生成工具") as demo:
    gr.Markdown("## 酷家乐 · 广告位海报批量生成\n输入链接 → 视频多帧+语音理解 → 自动挑图 → 出海报+详情页")

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
            upload_img   = gr.Image(label="手动指定背景图（可选，留空则从图库/候选自动挑选）", type="pil")
            make_btn     = gr.Button("③ 合成海报", variant="primary")

        with gr.Column(scale=2):
            pick_box = gr.Textbox(label="挑图结果", lines=1, interactive=False)
            candidate_gallery = gr.Gallery(label="模式2候选图库（点击图片可选中）", columns=4, rows=2, height=320, object_fit="contain")
            candidate_choice = gr.Radio(label="当前候选图选择", choices=[], interactive=True)
            rank_reason_box = gr.Textbox(label="TopN 排序理由", lines=6, interactive=False)
            banner_out = gr.Image(label="横幅  2560 × 320", type="pil")
            square_out = gr.Image(label="方图  1160 × 1016", type="pil")
            detail_out = gr.Image(label="详情页长图  1080 × 动态", type="pil")

    mode.change(on_mode_change, inputs=[mode], outputs=[upload_img])

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

if __name__ == "__main__":
    demo.launch(inbrowser=True)
