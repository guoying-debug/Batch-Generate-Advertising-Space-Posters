# 广告位海报批量生成工具

根据 B 站视频链接、酷家乐活动页或任意网页，自动理解内容并生成广告位海报（横幅 + 方图 + 详情页长图）。

![alt text](image.png)

## 已实现功能

### 1. 链接内容提取（extractor.py）

- **B 站视频**：调官方 API 提取标题、简介、封面图、标签
- **普通网页 / 活动页**：解析 OG 标签、完整正文、页面所有图片
- **活动页专属字段**：自动提取活动标题、副标题、活动时间、福利亮点、CTA 按钮文案
- **URL 清洗**：自动从微信 / APP 分享文本（如「【标题】 链接」）中提取真实 URL，支持直接粘贴分享文字

### 2. 视频深度理解（media.py）

- 用 yt-dlp 下载视频流（≤10 分钟 / ≤100 MB），同步尝试下载平台字幕
- OpenCV 均匀抽帧（默认 5 帧），跳过纯黑/白帧
- 字幕优先，无字幕则用 faster-whisper（tiny 模型）本地 ASR 转写前 3 分钟
- **视频下载失败自动降级**：封面图 + 元数据照样生成海报，不影响主流程

### 3. AI 文案与海报策划（ai_writer.py）

- 视觉模型（GLM-4V-Flash）一次性理解多帧画面，输出视频内容/风格描述
- 封面图语义描述，补充文案生成语境
- **参考图驱动设计（核心架构升级）**：
  - **AI 风格分析**：识别参考图的模板 ID、版式签名、视觉语言、配色方案
  - **OCR 布局提取（PaddleOCR）**：精确识别参考图中文字区域坐标、字号、颜色、对齐方式，生成结构化 `layout_spec` JSON
  - **动态排版引擎**：按 `layout_spec` 的 `layout_zones` 把运营文案精准放入参考图的对应位置，无需手动调整
  - **自动尺寸推导**：从 Banner 参考图自动推导出方图和详情页的布局规范（横向 → 纵向版式转换）
  - **置信度机制**：OCR 识别置信度 < 0.5 时自动回退到固定模板系统，保证鲁棒性
- 生成结构化 `poster_plan`，包含：主题摘要、海报类型、目标人群、核心卖点、文案（主标题/副标题/CTA/角标）、视觉策略（`template_id` / `banner_layout_mode` / `square_layout_mode` / `style_strength` / `decoration_density` / `overlay_strength`）、分尺寸提示词、**layout_specs**（含 banner/square/detail 三种尺寸的坐标规范）
- 支持按主题从候选图池自动挑图（视觉模型打分，0-100），返回 TopN 排序理由
- **文字设计规范**（防止生图出现乱码/多余文字）：
  - 画面文字用引号包裹，防止模型自由发挥生成乱码
  - 按重要性排序：主标题 → 副标题 → 角标 → 按钮 → 底部信息
  - 明确字号层级：巨大主标题 → 中等副标题 → 小字底部信息
  - 长句断成多段实现精准换行

### 4. 两种生成模式

#### 模式一：智能生成背景图（有图走图生图，无图走文生图）

适用场景：希望保留原图主体/构图关系并让 AI 重新生成更统一的背景，或无底图时直接生成全新背景。

流程：

- 有输入图时：输入链接 → 理解内容 → 生成文案和分尺寸提示词 → **代理接口图生图**（`chat/completions` 格式）→ 合成海报
- 无输入图时：输入链接 → 理解内容 → 生成文案和分尺寸提示词 → **代理接口文生图**（`images/generations` 格式）→ 合成海报

- 输入图来源以运营手动上传图为主；未上传时自动从候选图池中取最高分图，再无候选则直接文生图
- Banner（2560×320）和方图（1160×1016）分别按各自提示词生成
- 图生图失败时自动回退到文生图；若文生图也失败，则直接复用原图继续合成，避免流程中断
- 详情页头图背景同步生成，失败时自动复用 Banner 背景
- 支持异步任务模式（`IMAGE_PROXY_ASYNC=true`），自动轮询直到生图完成

#### 模式二：复用封面 / 素材图（背景 + 文字形式）

适用场景：有现成图库或视频封面，不需要生图。

流程：输入链接 → 抓取页面图片 + 抽视频帧 → 运营上传图库 → **AI 按主题自动挑最契合的图** → 合成海报

- 图片来源：链接页面图片、视频抽帧、运营上传图库，三者合并为候选池
- 活动页支持 `local_replace` 策略：Banner 用封面原图，方图用挑选图
- 无任何候选图时报错提示，避免生成空白海报

### 5. 海报排版合成（poster_maker.py）

输出三种规格：

| 规格 | 尺寸 | 说明 |
|------|------|------|
| 横幅 Banner | 2560 × 320 | 网站顶部广告位 |
| 方图 | 1160 × 1016 | 信息流 / 落地页模块 |
| 详情页长图 | 1080 × 动态高度 | 头图 + 多卖点卡片 + CTA |

排版特性：
- 根据 `layout_mode` 自适应三种版式：`left_text_right_visual` / `top_text_bottom_visual` / `centered`
- 支持角标、活动时间、福利列表渲染
- 文字自适应字号（保证不超出安全区）
- 全背景模式叠半透明蒙版保证文字可读性

### 6. 模板管理

- 将当前 `poster_plan` JSON 保存为命名模板（`poster_plan_templates.json`）
- 下次直接加载模板，跳过 AI 生成步骤，快速复用风格
- 加载时自动还原文案、提示词、活动字段、参考图风格信息

### 7. 风格对比测试

- 同一条内容 + 同一张底图，分别套 **三组参考图**，并排输出 Banner 和方图
- 展示每组的模板识别结果、版式签名、视觉语言、配色等详细分析
- 自动检测同版式冲突：若两组被判为同一模板/版式，给出明确警告，提示更换参考图

### 8. 审核与手动调整

生成结果可在 UI 中直接修改后重新合成：
- 主标题、副标题、CTA 文案
- Banner / 方图 / 详情页海报策划提示词（审核用）和安全生图提示词（实际使用）分开展示
- `poster_plan` 完整 JSON（支持手改后重新合成）
- 候选图库：点击图片即可切换用于合成的底图

---

## 快速开始

### 环境要求

```
Python 3.10+
```

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置环境变量

新建 `.env` 文件：

```env
ZHIPUAI_API_KEY=your_key_here
# 可选覆盖默认值（文本/视觉模型仍走智谱）
ZHIPUAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
TEXT_MODEL=glm-4-flash-250414
VISION_MODEL=glm-4v-flash

# 模式一生图（文生图 + 图生图统一走代理接口，兼容 OpenAI images/generations 或 chat/completions 格式）
IMAGE_PROXY_BASE_URL=https://your-proxy/v1/images/generations
IMAGE_PROXY_API_KEY=your_proxy_key_here
IMAGE_PROXY_MODEL=your-model-name          # 默认 nano-banana-pro
IMAGE_PROXY_IMG2IMG_URL=                   # 可选，图生图单独接口；缺省用 /v1/chat/completions
IMAGE_PROXY_STATUS_URL_TEMPLATE=           # 可选，异步任务查询地址模板，含 {task_id}
IMAGE_PROXY_ASYNC=false                    # 异步模式：true 时开启任务轮询
IMAGE_PROXY_POLL_INTERVAL=2                # 轮询间隔（秒）
IMAGE_PROXY_POLL_TIMEOUT=180              # 最长等待（秒）

ASR_MODEL=tiny
```

### 启动

```bash
python app.py
```

浏览器自动打开 Gradio 界面。

---

## 使用示例

**B 站视频 → 教程推广海报**

1. 粘贴 B 站链接，如 `https://www.bilibili.com/video/BV11C411H7PM/`
2. 可选填运营补充文案，如「3分钟解锁异形门衣柜」
3. 可选上传参考海报（多张），AI 将学习其版式和风格
4. 点击「① 读取链接 + 理解内容 + 生成文案」
5. 选择模式一（智能生成背景图）或模式二（复用封面）
6. 模式一可上传一张背景图作为底图：有图时走图生图，无图时自动文生图
7. 点击「③ 合成海报」

**酷家乐活动页 → 活动推广广告位**

1. 粘贴活动链接，如 `https://www.kujiale.com/festatic/duDbbNQfoXJmdDMb`
2. 工具自动提取活动标题、时间、福利、CTA
3. 模式二下会从页面图片中挑选最符合活动主题的图作为背景
4. 模式一可上传一张底图做图生图，得到更统一的广告视觉

**风格对比测试**

1. 在页面下方「风格对比测试页」填入链接
2. 上传同一张底图（保证公平对比）
3. 分别给 A/B/C 三组上传不同风格的参考海报（A 组可留空作为基线）
4. 点击「生成风格对比」，并排查看三组版式差异

---

## 项目结构

```
├── app.py              # Gradio UI 与主流程编排
├── extractor.py        # 链接内容提取（B站API + 通用网页）
├── ai_writer.py        # 文案生成、海报策划、图片打分挑选、参考图风格分析
├── layout_analyzer.py  # 参考图 OCR 布局提取，生成结构化 layout_spec，并推导各尺寸版式
├── image_gen.py        # 模式一：代理接口文生图 + 图生图（含异步轮询）
├── poster_maker.py     # 海报合成排版（Banner / 方图 / 详情页，含动态排版引擎）
├── media.py            # 视频下载、抽帧、语音转写
├── requirements.txt
└── .env                # API密钥（不入库）
```

进入虚拟环境

```powershell
.\venv\Scripts\Activate.ps1
```
