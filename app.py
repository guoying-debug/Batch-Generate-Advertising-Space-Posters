# -*- coding: utf-8 -*-
import gradio as gr
from dotenv import load_dotenv
import extractor, ai_writer, image_gen, poster_maker

load_dotenv()

MODE1 = "模式一：AI生成背景图"
MODE2 = "模式二：复用封面/素材图"


def step_extract_and_write(url, operator_text):
    """① 提取链接内容 + 生成文案与生图prompt"""
    if not url.strip():
        raise gr.Error("请输入链接")
    content = extractor.extract(url.strip())
    copy = ai_writer.generate(content, operator_text)
    info = f"标题: {content['title']}\n简介: {content['desc'][:60]}\n封面: {content['cover'][:70]}"
    return (
        content["cover"],          # cover_state：模式二默认背景
        info,
        copy["title"],
        copy["subtitle"],
        copy["cta"],
        copy.get("image_prompt", ""),  # 模式一生图描述（可编辑）
    )


def step_make(cover_url, title, subtitle, cta, mode, upload_img, image_prompt):
    """③ 按模式合成两种尺寸海报"""
    if mode == MODE1:
        # 模式一：AI 画背景图（优先用运营手动上传图覆盖）
        if upload_img is not None:
            bg = upload_img
        else:
            if not image_prompt.strip():
                raise gr.Error("模式一需要生图描述，请先点①生成或手动填写")
            bg = image_gen.generate_background(image_prompt.strip())
    else:
        # 模式二：复用封面图，运营上传图则优先
        bg = upload_img if upload_img is not None else cover_url
        if not bg:
            raise gr.Error("没有可用的封面图，请上传图片或改用模式一")

    return (
        poster_maker.make_poster_any(bg, title, subtitle, cta, "banner"),
        poster_maker.make_poster_any(bg, title, subtitle, cta, "square"),
    )


def on_mode_change(mode):
    """切换模式时更新上传框的提示文案"""
    if mode == MODE1:
        return gr.update(label="手动替换背景图（可选，留空则用AI生图）")
    return gr.update(label="手动替换背景图（可选，留空则用链接封面）")


with gr.Blocks(title="酷家乐广告海报生成工具") as demo:
    gr.Markdown("## 酷家乐 · 广告位海报批量生成\n输入链接 → AI 自动出海报 → 审核可手动替换")

    cover_state = gr.State("")

    with gr.Row():
        with gr.Column(scale=1):
            mode = gr.Radio([MODE1, MODE2], value=MODE2, label="生成模式")
            url = gr.Textbox(label="链接（B站/酷家乐活动页/网页）", placeholder="https://...")
            op_text = gr.Textbox(label="运营补充文案（可选）", placeholder="例：3分钟解锁异形门衣柜")
            extract_btn = gr.Button("① 读取链接 + 生成文案", variant="primary")
            info_box = gr.Textbox(label="提取内容", lines=3, interactive=False)

            gr.Markdown("**② 审核区 — 可手动修改后重新合成**")
            title_box    = gr.Textbox(label="主标题")
            subtitle_box = gr.Textbox(label="副标题文案")
            cta_box      = gr.Textbox(label="行动号召")
            prompt_box   = gr.Textbox(label="背景生图描述（模式一生效，可改后重新生成）", lines=2)
            upload_img   = gr.Image(label="手动替换背景图（可选，留空则用链接封面）", type="pil")
            make_btn     = gr.Button("③ 合成海报", variant="primary")

        with gr.Column(scale=2):
            banner_out = gr.Image(label="横幅  2560 × 320", type="pil")
            square_out = gr.Image(label="方图  1160 × 1016", type="pil")

    mode.change(on_mode_change, inputs=[mode], outputs=[upload_img])

    extract_btn.click(
        step_extract_and_write,
        inputs=[url, op_text],
        outputs=[cover_state, info_box, title_box, subtitle_box, cta_box, prompt_box],
    )
    make_btn.click(
        step_make,
        inputs=[cover_state, title_box, subtitle_box, cta_box, mode, upload_img, prompt_box],
        outputs=[banner_out, square_out],
    )

if __name__ == "__main__":
    demo.launch(inbrowser=True)
