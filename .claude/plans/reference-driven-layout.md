# 参考图驱动布局系统 实现方案

## 一、需求目标
将海报生成从"模板驱动"改造为"参考图驱动布局"：
- **参考图决定**：文字在哪、图在哪、按钮在哪、哪些块能替换
- **运营文案决定**：这些文字位里显示什么
- **系统负责**：图生图复刻视觉关系 + 文案精准替换

## 二、现状分析

### 当前架构的问题

1. **`analyze_reference_images` 输出不具体**
   - 只有分类标签（`template_id`、`layout_mode`）
   - 缺少结构化坐标（标题框、副标题框、CTA 框、主视觉区）

2. **`poster_maker` 全是硬编码模板**
   - 6 个固定排版函数（`_compose_banner_editorial` 等）
   - 所有坐标、字号、留白都是写死的常量
   - 无法处理任意参考图的结构

3. **图生图负责全部画面**
   - 提示词清洗只能"禁止画文字"，但无法控制留白位置
   - 文字区域仍然依赖模板硬编码，和图生图的实际留白不匹配

## 三、目标架构

### 阶段 1：参考图结构化分析（OCR + 布局检测）

**输入**：参考海报图片（1-4张）

**输出**：JSON 布局规范
```json
{
  "canvas": {"width": 2560, "height": 320},
  "layout_zones": [
    {
      "zone_id": "title_zone",
      "zone_type": "text",
   "bbox": [68, 80, 800, 180],
      "alignment": "left",
      "font_size_range": [60, 88],
      "text_color": "#FFFFFF",
      "background_color": null,
      "overlay_alpha": 0.0,
      "original_text": "探索现代家居设计"
  },
    {
      "zone_id": "subtitle_zone",
      "zone_type": "text",
      "bbox": [68, 200, 720, 260],
      "alignment": "left",
      "font_size_range": [24, 32],
    "text_color": "#E8E8E8"
    },
    {
      "zone_id": "cta_zone",
      "zone_type": "button",
      "bbox": [1980, 130, 2460, 210],
      "button_style": "rounded",
      "button_color": "#FF6B35",
      "text_color": "#FFFFFF",
      "font_size": 42,
      "original_text": "立即体验"
    },
    {
      "zone_id": "visual_zone",
      "zone_type": "visual",
      "bbox": [900, 0, 2560, 320],
      "strategy": "full_background"
    }
  ],
  "template_summary": "左文右图分栏",
  "style_hints": {
    "color_palette": {"primary": "#2A3D45", "accent": "#FF6B35"},
    "visual_language": "现代商业海报",
    "decoration_density": "low"
  }
}
```

### 阶段 2：动态排版引擎

**poster_maker 不再依赖固定模板函数，改为：**

```python
def make_poster_dynamic(
    bg_source,
    title: str,
    subtitle: str,
    cta: str,
    layout_spec: dict,  # 来自 analyze_reference_images
    plan: dict = None
) -> Image.Image:
    """根据 layout_spec 动态排版"""
    # 1. 准备背景图（复刻 visual_zone）
    bg = _prepare_background_from_spec(bg_source, layout_spec)
    
    # 2. 遍历 layout_zones，逐个绘制
    for zone in layout_spec.get("layout_zones", []):
        if zone["zone_type"] == "text":
            _draw_text_zone(bg, title if "title" in zone["zone_id"] else subtitle, zone)
        elif zone["zone_type"] == "button":
         _draw_button_zone(bg, cta, zone)
    
    return bg
```

### 阶段 3：图生图只负责背景

**image_gen 的改动：**

- **目标**：图生图只复刻参考图的"视觉区域"，不画文字
- **实现方式**：
  1. 提示词增强：`"留出 {zone_bbox} 区域作为文字安全区，该区域保持干净背景"`
  2. 或：使用 Inpainting 模式（如果代理接口支持 mask 参数）

```python
def generate_background_img2img_with_layout(
    image_prompt: str,
    input_image,
    text_zones: list,  # 从 layout_spec 提取的所有文字/按钮区域
    size: str = "2K"
) -> Image.Image:
    # 构造增强提示词
    safe_zones_desc = _format_safe_zones(text_zones)
    enhanced_prompt = f"{image_prompt}。{safe_zones_desc}"
    
    # 如果代理支持 mask，生成 mask 图
    if _use_inpainting():
        mask = _generate_layout_mask(text_zones, size)
        return _request_proxy_inpainting(enhanced_prompt, input_image, mask, size)
    
    # 回退到提示词约束
    return generate_background_img2img(enhanced_prompt, input_image, size)
```

## 四、实现步骤

### Step 1：OCR + 布局检测（新建 `layout_analyzer.py`）

**工具选型：**
- OCR：PaddleOCR（免费、本地、中文优）或 EasyOCR
- 布局检测：LayoutParser（基于 Detectron2）或简化版本（Hough 直线 + 连通域聚类）

**核心函数：**
```python
def analyze_layout(image: Image.Image, target_size: tuple) -> dict:
    """
    返回：
    - layout_zones: 文字、按钮、视觉区域坐标
    - font_size_hints: 根据 OCR 文字高度估算字号
    - alignment: 根据文字块对齐方式（左/中/右）
    """
    pass
```

### Step 2：改造 `ai_writer.analyze_reference_images()`

**改动：**
1. 保留现有的风格分析（summary、template_id、color_palette 等）
2. **新增**：调用 `layout_analyzer.analyze_layout()` 获取结构化坐标
3. **合并**：将 OCR 结果 + AI 风格分析合并为完整 `layout_spec`

```python
def analyze_reference_images(images: list) -> dict:
    # 1. 现有 AI 风格分析
    style_analysis = _analyze_style_with_vision_model(images)
    
    # 2. OCR + 布局检测（取第一张图为布局基准）
    if images:
        layout_spec = layout_analyzer.analyze_layout(images[0], target_size=(2560, 320))
    else:
        layout_spec = {}
    
    # 3. 合并
    return {
        **style_analysis,  # summary, template_id, color_palette...
        "layout_spec": layout_spec,  # layout_zones, canvas...
    }
```

### Step 3：改造 `poster_maker.make_poster_any()`

**改动：**
1. 废弃所有 `_compose_banner_*` / `_compose_square_*` 固定模板函数
2. 统一使用 `make_poster_dynamic()`

```python
def make_poster_any(bg_source, title, subtitle, cta, size_key="banner", plan: dict = None) -> Image.Image:
    reference_analysis = (plan or {}).get("reference_analysis") or {}
    layout_spec = reference_analysis.get("layout_spec")
    
    if layout_spec and layout_spec.get("layout_zones"):
        # 参考图驱动模式
        return make_poster_dynamic(bg_source, title, subtitle, cta, layout_spec, plan)
    else:
        # 回退到现有模板逻辑（兼容无参考图场景）
        return _compose(bg_source, title, subtitle, cta, size_key, plan)
```

### Step 4：改造 `image_gen` 提示词生成

**目标**：在生图提示词中明确"留白区域"

```python
def _sanitize_prompt_with_layout(prompt: str, text_zones: list) -> str:
    safe_prompt = _sanitize_prompt_for_image_model(prompt)
    
    # 描述文字安全区
    if text_zones:
        zones_desc = "，".join([
            f"左上角 {z['bbox'][:2]} 到 右下角 {z['bbox'][2:]} 区域保持干净背景"
          for z in text_zones if z["zone_type"] in ("text", "button")
        ])
        safe_prompt += f"。{zones_desc}，不在这些区域内绘制复杂主体"
    
    return safe_prompt
```

### Step 5：新增 Banner / 方图 / 详情页的独立 layout_spec

**问题**：一张参考图只能识别一种尺寸的布局，但我们需要生成 3 种尺寸。

**解决方案：**
- 让用户上传 3 种尺寸的参考图（Banner 参考 + 方图参考 + 详情页参考）
- **或**：用 AI 推理从 Banner 布局推导方图布局（缩放 + 版式转换）

```python
def derive_square_layout_from_banner(banner_spec: dict) -> dict:
    """
    从横向 Banner 布局推导方图布局：
    - 左文右图 → 上文下图
    - 文字区域高度比例保持，宽度扩展到全宽
    """
    pass
```

## 五、实现难点与风险

### 难点 1：OCR 识别准确率

**风险**：参考图文字倾斜、艺术字体、低分辨率时 OCR 失败

**缓解方案：**
- 保留回退逻辑：OCR 失败时回到现有模板系统
- UI 层增加"手动调整布局"功能，允许用户在识别结果上微调坐标

### 难点 2：布局检测的准确性

**风险**：参考图排版复杂（文字叠在图上、不规则形状）时检测失败

**缓解方案：**
- 先实现简单场景（左文右图、上文下图、居中）
- 复杂场景先用 AI 分类成已知模板，再应用固定模板

### 难点 3：图生图无法完美留白

**风险**：生图模型不遵守提示词中的"留白区域"约束

**缓解方案：**
- 阶段1：先依赖提示词约束 + 后处理蒙版（在文字区域叠加半透明背景保证可读性）
- 阶段2：如果代理接口支持 Inpainting，使用 mask 精确控制

### 难点 4：多尺寸布局推导

**风险**：从 Banner 推导方图布局时，AI 可能产生不合理的坐标
**缓解方案：**
- 优先建议用户上传对应尺寸的参考图
- 推导逻辑只处理简单的"纵横版式转换"，复杂情况回退到模板

## 六、迭代路线

### MVP（最小可行产品）

**范围**：只支持最简单的参考图
- 左文右图 Banner
- 上文下图方图
- 文字区域必须是矩形
- 背景必须是纯色或简单渐变

**实现**：
1. 新建 `layout_analyzer.py`，实现 OCR + 矩形检测
2. 改造 `analyze_reference_images()`，新增 `layout_spec` 字段
3. 新建 `make_poster_dynamic()` 函数
4. `app.py` 增加"参考图驱动模式"开关

### 迭代 1：支持复杂参考图

- 文字叠在图上（需要蒙版和透明度处理）
- 不规则按钮形状（圆角矩形、胶囊）
- 多层次文字（角标、小字注释）

### 迭代 2：图生图 Inpainting

- 如果代理接口支持 mask 参数，实现精确的文字区域保护
- 背景和文字区域完全解耦

### 迭代 3：多尺寸智能推导

- AI 自动从 Banner 布局推导方图布局
- 用户可在 UI 中预览推导结果并手动调整

## 七、测试计划

### 单元测试

- `layout_analyzer.analyze_layout()` 对 10 张不同风格的参考图
- `derive_square_layout_from_banner()` 的推导逻辑正确性

### 集成测试

- 端到端：上传参考图 → 提取布局 → 生成海报 → 对比坐标精度
- 对比测试：同一内容用"模板模式"vs"参考图模式"生成，人工评估质量

### 回归测试

- 确保无参考图时，回退到现有模板系统不受影响

## 八、需要的外部依赖

```txt
paddleocr>=2.7.0          # OCR 文字识别
layoutparser>=0.3.4       # 布局检测（可选，先不装）
opencv-python>=4.8.0      # 图像处理（已有）
```

## 九、待确认问题

1. **用户会上传哪些类型的参考图？**
   - 是酷家乐自己的历史海报？
   - 还是网上收集的任意海报？
   - 影响 OCR 和布局检测的准确率预期

2. **是否需要支持"一次上传 3 种尺寸的参考图"？**
   - 还是只上传一张 Banner 参考，系统自动推导方图和详情页？

3. **图生图代理接口是否支持 Inpainting（mask 参数）？**
   - 如果支持，可以精确保护文字区域
   - 如果不支持，只能靠提示词约束 + 后处理蒙版

4. **UI 是否需要"手动调整布局"功能？**
   - OCR 识别错误时，用户可以拖拽调整文字框位置和大小

## 十、工作量估算

| 任务 | 工作量 |
|------|--------|
| Step 1：OCR + 布局检测 | 2 天 |
| Step 2：改造 `ai_writer` | 1 天 |
| Step 3：改造 `poster_maker` | 3 天 |
| Step 4：改造 `image_gen` | 1 天 |
| Step 5：多尺寸推导 | 2 天 |
| 测试与调优 | 2 天 |
| **总计** | **11 天** |

---

**下一步：请确认以上方案是否符合预期，我再开始实现。**
