# Stage 4: 添加软字幕

> 状态: ✅ 已完成 | 实际代码量: ~600 行 (含 mux.py 270行 + subtitle api 330行 + 测试 700行)

## 1. 设计目标

给视频添加外部字幕文件（SRT/ASS/WebVTT）作为**软字幕轨道**，通过 FFmpeg 流拷贝（`-c copy`）封装，不重编码视频/音频。

**典型场景**：
- 用户有一份 `.srt` 字幕文件 → 封装进 MKV 作为可选字幕轨
- 设置字幕语言元数据（ISO 639-2），供播放器识别
- 输出 MKV（默认，支持所有字幕格式）或 MP4（仅 mov_text）

**文件清单**：

- `processing/core/mux.py` — FFmpeg 封装操作（subprocess 包装）
- `web/api/subtitle.py` — REST 端点（POST 添加、GET 下载）
- `web/templates/subtitle.html` — 字幕添加页面
- `web/templates/subtitle_results.html` — HTMX 片段：结果 + 下载
- `tests/test_mux.py` — 单元测试 + API 测试

## 2. 模块接口

### 2.1 processing/core/mux.py

```python
class MuxError(Exception):
    """封装失败（文件不存在、ffmpeg 未找到、流映射错误等）。"""

@dataclass
class MuxResult:
    """单次封装结果。"""
    input_video: Path           # 输入视频路径
    output_path: Path           # 输出文件路径
    output_size: int            # 输出文件大小（字节）
    subtitle_count: int         # 输出文件的字幕轨总数
    added_track_index: int      # 新添加的字幕轨索引（全局 stream index）
    language: str               # 设置的语言代码，e.g. "eng"

def add_subtitle(
    video_path: Path,
    subtitle_path: Path,
    language: str,
    output_path: Path | None = None,
    container: str = "mkv",      # "mkv" | "mp4"
    ffmpeg_path: str = "ffmpeg",
    timeout: int = 120,
    overwrite: bool = False,
) -> MuxResult:
    """给视频添加外部字幕文件作为软字幕轨道。

    Args:
        video_path: 输入视频文件。
        subtitle_path: 外部字幕文件（.srt / .ass / .vtt）。
        language: ISO 639-2 三字母语言代码，e.g. "eng", "jpn", "zho"。
        output_path: 输出文件路径。None 则自动生成为 {stem}_subtitled.{container}。
        container: 输出容器格式 ("mkv" | "mp4")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        MuxResult 含输出路径、大小和新字幕轨索引。

    Raises:
        MuxError: 输入/输出/ffmpeg 问题。
    """
```

**内部辅助**：

```python
def _build_add_subtitle_args(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    language: str,
    container: str,
    ffmpeg_path: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 添加字幕的命令参数。

    MKV:
      ffmpeg [-y/-n] -i <video> -i <sub> -c copy -map 0 -map 1
             -metadata:s:s:0 language=<lang> <output>.mkv

    MP4:
      ffmpeg [-y/-n] -i <video> -i <sub> -c copy -c:s mov_text
             -map 0 -map 1 -metadata:s:s:0 language=<lang> <output>.mp4
    """

def _validate_subtitle_format(path: Path) -> str:
    """验证字幕文件格式并返回 codec 名称。
    
    支持的格式:
      .srt → subrip
      .ass / .ssa → ass
      .vtt → webvtt
    
    Raises:
        MuxError: 不支持的字幕格式。
    """

def _count_subtitle_streams(video_path: Path, ffprobe_path: str) -> int:
    """探测视频中已有的字幕轨数量（用于计算新轨的 metadata 索引）。
    复用 Stage 2 probe_file。
    """
```

**FFmpeg 命令详解**：

```bash
# MKV（通用 — 所有字幕格式原生支持）
ffmpeg -y \
  -i input.mkv \
  -i subtitle.srt \
  -c copy \                    # 所有流拷贝（视频/音频不重编码）
  -map 0 \                     # 保留原视频所有流
  -map 1 \                     # 加入字幕流
  -metadata:s:s:0 language=eng \  # 设置第一个字幕轨语言
  output.mkv

# MP4（需要转换字幕格式为 mov_text）
ffmpeg -y \
  -i input.mp4 \
  -i subtitle.srt \
  -c copy \                    # 视频/音频流拷贝
  -c:s mov_text \              # 字幕流转为 MP4 兼容的 mov_text
  -map 0 -map 1 \
  -metadata:s:s:0 language=eng \
  output.mp4
```

> **注意**：`-metadata:s:s:0` 中的索引是**全局字幕流索引**（0-based）。如果视频已有 N 个字幕轨，新轨的索引应为 N。

### 2.2 web/api/subtitle.py

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| `POST` | `/api/subtitle/add` | Form: `video_path` / `video_file`, `subtitle_path` / `subtitle_file`, `language`, `container?` | JSON `MuxResult` / HTML fragment (HX-Request) |
| `GET` | `/api/subtitle/download` | query: `?path=` | 文件下载 (`FileResponse`) |

**输入方式**（支持 4 种组合）：

| 视频 | 字幕 | 适用场景 |
|------|------|----------|
| `video_path` (str) | `subtitle_path` (str) | API 调用 / 服务器端 |
| `video_path` (str) | `subtitle_file` (UploadFile) | 本地视频 + 上传字幕 |
| `video_file` (UploadFile) | `subtitle_path` (str) | 上传视频 + 本地字幕 |
| `video_file` (UploadFile) | `subtitle_file` (UploadFile) | 全部上传 |

上传文件保存到 `paths.temp_dir`，处理完成后清理。

**响应 JSON**：

```json
{
  "success": true,
  "result": {
    "input_video": "/media/input/movie.mkv",
    "output_path": "/media/output/movie_subtitled.mkv",
    "output_size": 524288000,
    "subtitle_count": 2,
    "added_track_index": 5,
    "language": "eng",
    "download_url": "/api/subtitle/download?path=/media/output/movie_subtitled.mkv"
  }
}
```

**语言选择**：提供常用语言下拉列表（中/英/日/韩/法/德/西/葡/俄/阿），默认值从 `settings.subtitle.default_language` 读取。高级用户可手动输入任意 ISO 639-2 代码。

**错误处理**：
- 400: 缺少必填参数（视频、字幕、语言）
- 422: 封装失败（ffmpeg 错误、不支持的字幕格式）
- 409: 输出文件已存在（且 overwrite=false）
- 504: 封装超时

**HTMX 交互流**：

```
GET /subtitle → 表单页面
   ↓ 用户填视频路径 + 字幕路径 + 语言 → POST /api/subtitle/add (HX-Request: true)
   ↓ 返回结果片段（含输出信息 + 下载按钮）
   ↓ 用户点击下载 → GET /api/subtitle/download?path=...
```

> **设计决策**：Stage 4 页面**不自动调用 probe**。用户可先去 `/probe` 页面了解视频流信息，再来到 `/subtitle` 添加字幕。保持每个页面职责单一。

### 2.3 模板

**subtitle.html**（继承 base.html）：
- 视频输入区：
  - 路径输入框（文本）
  - 或上传按钮（文件选择器）
  - 二选一，通过 tab 切换
- 字幕输入区：
  - 路径输入框（文本）
  - 或上传按钮（文件选择器）
  - 二选一，通过 tab 切换
- 语言选择器：下拉框（常用语言）+ 自定义输入框
- 容器格式：MKV（默认）/ MP4 单选
- 「添加字幕」提交按钮 → `hx-post="/api/subtitle/add"`
- 结果展示区（初始为空，HTMX 填充）

**subtitle_results.html**（HTMX fragment）：
- 成功：绿色卡片显示
  - 输入视频名
  - 输出文件名 + 大小
  - 添加的字幕轨信息（语言、轨道索引）
  - 下载按钮
- 失败：红色错误提示

### 2.4 页面路由

在 `app.py` 添加：

```python
@app.get("/subtitle")
async def subtitle_page(request: Request):
    return templates.TemplateResponse(request, "subtitle.html", {"version": __version__})
```

### 2.5 路由注册

在 `web/api/__init__.py` 追加：

```python
from web.api.subtitle import router as subtitle_router
router.include_router(subtitle_router, tags=["subtitle"])
```

## 3. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| FFmpeg 调用 | subprocess 直接调用 | 与 probe.py / extract.py 一致，参数简单不依赖 ffmpeg-python |
| 默认容器 | MKV | 原生支持所有字幕格式，无需转换 |
| MP4 字幕 | 强制 `-c:s mov_text` | MP4 只支持 mov_text；FFmpeg 自动从 SRT/ASS 转换 |
| 输出命名 | `{stem}_subtitled.{ext}` | 简洁明了，避免覆盖原文件 |
| 输出目录 | `paths.media_output` | 与 Stage 2/3 一致，下载链接统一 |
| 语言代码 | ISO 639-2 三字母 | 与 FFmpeg metadata 标准一致，与 probe.py 的 BCP47→ISO 映射对接 |
| 视频+字幕输入 | 路径 + 上传双模式 | 灵活适配不同场景；上传文件存 temp_dir |
| 已有字幕处理 | 新轨追加到末尾 | 不覆盖已有字幕轨，metadata 索引自动偏移 |
| metadata 索引 | `-metadata:s:s:<existing_count>` | 精确控制新轨的元数据，避免覆盖已有轨 |
| 不自动 probe | 页面独立 | 保持页面职责单一，用户可自行先去 probe 查看流信息 |

## 4. 与已有 Stage 的关系

```
Stage 2: probe_file() → ProbeResult    (探测有哪些流)
Stage 3: extract_stream() → ExtractResult  (提取某个流)
Stage 4: add_subtitle() → MuxResult    (添加外部字幕轨)
Stage 5: switch_track() → (切换默认轨，追加到 mux.py)
```

**复用点**：
- `_count_subtitle_streams()` 内部调 `probe_file()` 获取已有字幕轨数量
- `_is_within_directory()` 路径穿越防护复用 Stage 3 模式
- API 模式（`_get_templates`, `_get_settings`, `_resolve_path`）与 Stage 2/3 完全一致

**不修改已有 Stage 代码** — 只追加新模块。

## 5. 修改/创建文件

### 新建 (6个)

| 文件 | 估计行数 | 说明 |
|------|----------|------|
| `processing/core/mux.py` | ~150 | 核心模块：add_subtitle + 参数构建 + 格式验证 |
| `web/api/subtitle.py` | ~120 | REST 端点：POST 添加 + GET 下载 + HTMX 检测 + 上传处理 |
| `web/templates/subtitle.html` | ~100 | 添加字幕页面：视频/字幕双输入 + 语言选择 + 容器选择 |
| `web/templates/subtitle_results.html` | ~60 | HTMX 片段：结果卡片 + 下载按钮 |
| `tests/test_mux.py` | ~190 | 测试：核心函数 + API 端点 |

### 修改 (4个)

| 文件 | 改动 |
|------|------|
| `web/api/__init__.py` | 加 2 行：`include_router(subtitle_router)` |
| `app.py` | 加 4 行：`GET /subtitle` 页面路由 |
| `web/templates/base.html` | 导航栏加「字幕管理」链接（或「添加字幕」） |
| `web/static/css/style.css` | ~60 行：表单双模式切换、结果卡片、语言选择器样式 |

## 6. 测试策略

### test_mux.py

| 类 | 测试数 | 覆盖 |
|----|--------|------|
| `TestValidateSubtitleFormat` | 5 | .srt→subrip / .ass→ass / .vtt→webvtt / .txt→MuxError / 无后缀→MuxError |
| `TestBuildAddSubtitleArgs` | 6 | MKV 基本 / MP4 基本 / overwrite / 含空格路径 / 已有 N 个字幕轨的 metadata 索引偏移 / 空语言 |
| `TestAddSubtitle` | 7 | mock subprocess: MKV 正常添加、MP4 正常添加、视频不存在、字幕不存在、字幕格式不支持、ffmpeg 未找到、ffmpeg 非零返回码、超时 |
| `TestAPISubtitle` | 14 | 路径+路径 成功、路径+上传 成功、上传+路径 成功、上传+上传 成功、缺视频 400、缺字幕 400、缺语言 400、视频不存在 422、字幕格式不支持 422、输出冲突 409、HTMX → HTML、GET 下载成功、下载不存在 404、下载路径穿越 403 |
| **合计** | **~32** | |

**测试隔离**：
- `processing/core` 测试 mock `subprocess.run`，不实际调 ffmpeg
- API 测试用独立 FastAPI app + `TestClient`（与 Stage 2/3 一致），mock `add_subtitle`
- 上传文件测试用 `BytesIO` 模拟 `UploadFile`

## 7. 验证

```bash
# 测试
python -m pytest tests/ -v

# Docker
docker compose up -d --build

# 页面
http://localhost:8000/subtitle

# API: 路径模式 (JSON)
curl -X POST http://localhost:8000/api/subtitle/add \
  -F "video_path=/media/input/sample.mkv" \
  -F "subtitle_path=/media/input/subtitle.srt" \
  -F "language=eng"

# API: 路径模式 (HTMX)
curl -X POST http://localhost:8000/api/subtitle/add \
  -H "HX-Request: true" \
  -F "video_path=/media/input/sample.mkv" \
  -F "subtitle_path=/media/input/subtitle.srt" \
  -F "language=jpn"

# API: MP4 输出
curl -X POST http://localhost:8000/api/subtitle/add \
  -F "video_path=/media/input/sample.mp4" \
  -F "subtitle_path=/media/input/subtitle.srt" \
  -F "language=zho" \
  -F "container=mp4"

# API: 上传模式
curl -X POST http://localhost:8000/api/subtitle/add \
  -F "video_file=@sample.mkv" \
  -F "subtitle_file=@subtitle.srt" \
  -F "language=eng"

# API: 下载
curl -O "http://localhost:8000/api/subtitle/download?path=/media/output/sample_subtitled.mkv"

# 错误测试
curl -X POST http://localhost:8000/api/subtitle/add              # → 400
curl -X POST http://localhost:8000/api/subtitle/add \
  -F "video_path=/media/input/sample.mkv" \
  -F "subtitle_path=/media/input/notes.txt" \
  -F "language=eng"                                                # → 422
```

## 8. 安全考虑

- **路径穿越防护**：下载端点复用 `_is_within_directory()` 检查（与 Stage 3 一致）
- **上传文件安全**：校验文件扩展名，只允许 `.srt` / `.ass` / `.ssa` / `.vtt`
- **上传大小限制**：字幕文件限制 10MB（nginx 层或 FastAPI 层），视频暂不限制
- **临时文件清理**：上传的字幕/视频在请求处理完后从 `temp_dir` 删除

---

*预计代码量: core ~150行, api ~120行, 模板 ~160行, CSS ~60行, 测试 ~190行 — 合计 ~680行*
