# 广告位海报批量生成工具

根据 B 站视频链接、酷家乐活动页或任意网页，自动理解内容并生成广告位海报（横幅 + 方图 + 详情页长图）。

## 已实现功能

### 1. 链接内容提取（extractor.py）

- **B 站视频**：调官方 API 提取标题、简介、封面图、标签
- **普通网页 / 活动页**：解析 OG 标签、完整正文、页面所有图片
- **活动页专属字段**：自动提取活动标题、副标题、活动时间、福利亮点、CTA 按钮文案

### 2. 视频深度理解（media.py）

- 用 yt-dlp 下载视频流（≤10 分钟 / ≤100 MB），同步尝试下载平台字幕
- OpenCV 均匀抽帧（默认 5 帧），跳过纯黑/白帧
- 字幕优先，无字幕则用 faster-whisper（tiny 模型）本地 ASR 转写前 3 分钟

### 3. AI 文案与海报策划（ai_writer.py）

- 视觉模型（GLM-4V-Flash）一次性理解多帧画面，输出视频内容/风格描述
- 封面图语义描述，补充文案生成语境
- 生成结构化 `poster_plan`，包含：主题摘要、海报类型、目标人群、核心卖点、文案（主标题/副标题/CTA/角标）、视觉策略、分尺寸提示词
- 支持按主题从候选图池自动挑图（视觉模型打分，0-100）

### 4. 两种生成模式

#### 模式一：AI 生成背景图（设计案例图片 + 文字形式）

适用场景：需要根据内容主题生成全新背景图的广告位。

流程：输入链接 → 理解内容 → 生成文案和分尺寸提示词 → **Cogview-3-Flash 文生图** → 合成海报

- Banner（2560×320）和方图（1160×1016）分别用各自的提示词生图
- 运营可手动上传单张图覆盖 AI 生图

#### 模式二：复用封面 / 素材图（背景 + 文字形式）

适用场景：有现成图库或视频封面，不需要生图。

流程：输入链接 → 抓取页面图片 + 抽视频帧 → 运营上传图库 → **AI 按主题自动挑最契合的图** → 合成海报

- 图片来源：链接页面图片、视频抽帧、运营上传图库，三者合并为候选池
- 活动页支持 `local_replace` 策略：Banner 用封面原图，方图用挑选图

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

### 7. 审核与手动调整

生成结果可在 UI 中直接修改后重新合成：
- 主标题、副标题、CTA 文案
- Banner / 方图 / 详情页背景生图描述
- `poster_plan` 完整 JSON（支持手改后重新合成）

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
# 可选覆盖默认值
ZHIPUAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
TEXT_MODEL=glm-4-flash-250414
VISION_MODEL=glm-4v-flash
IMAGE_MODEL=cogview-3-flash
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
3. 点击「① 读取链接 + 理解内容 + 生成文案」
4. 选择模式一（AI生图）或模式二（复用封面），点击「③ 合成海报」

**酷家乐活动页 → 活动推广广告位**

1. 粘贴活动链接，如 `https://www.kujiale.com/festatic/duDbbNQfoXJmdDMb`
2. 工具自动提取活动标题、时间、福利、CTA
3. 模式二下会从页面图片中挑选最符合活动主题的图作为背景

---

## 项目结构

```
├── app.py            # Gradio UI 与主流程编排
├── extractor.py      # 链接内容提取（B站API + 通用网页）
├── ai_writer.py      # 文案生成、海报策划、图片打分挑选
├── image_gen.py      # 模式一：Cogview-3-Flash 文生图
├── poster_maker.py   # 海报合成排版（Banner / 方图 / 详情页）
├── media.py          # 视频下载、抽帧、语音转写
├── requirements.txt
└── .env              # API密钥（不入库）
```
