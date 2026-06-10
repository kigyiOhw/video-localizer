# Stage 3: 流提取

> 状态: 规划中 | 预计代码量: ~550 行

## 1. 设计目标

从媒体文件中提取单个流（视频/音频/字幕），以 FFmpeg `-c copy`（流拷贝，无重编码）输出为独立文件。

**典型场景**：
- 提取视频中的某条音轨 → `.mka` / `.aac` 文件
- 提取视频中的字幕轨 → `.srt` / `.ass` 文件
- 提取纯视频轨（去音轨） → `.mkv` 文件

**文件清单**：

- `processing/core/extract.py` — FFmpeg 流提取（subprocess 包装）
- `web/api/extract.py` — REST 端点（POST 提取、GET 下载）
- `web/templates/extract.html` — 提取页面：探测 → 选择流 → 提取
- `web/templates/extract_results.html` — HTMX 片段：提取结果 + 下载链接
- `tests/test_extract.py` — 单元测试 + API 测试

## 2. 模块接口

### 2.1 processing/core/extract.py

```python
class ExtractError(Exception):
    """提取失败（流不存在、ffmpeg 未找到、超时、输出已存在等）。"""

@dataclass
class ExtractResult:
    """单次提取结果。"""
    stream_index: int
    stream_type: str          # "video" | "audio" | "subtitle"
    codec: str                # 原始 codec，e.g. "aac", "subrip"
    output_path: Path         # 输出文件路径
    output_size: int          # 输出文件大小（字节）
    duration: float | None    # 秒

def extract_stream(
    input_path: Path,
    output_path: Path,
    stream_index: int,
    stream_type: str = "audio",  # "video" | "audio" | "subtitle"
    ffmpeg_path: str = "ffmpeg",
    timeout: int = 120,
    overwrite: bool = False,
) -> ExtractResult:
    """从媒体文件中提取单个流。

    Args:
        input_path: 输入媒体文件。
        output_path: 输出文件路径（后缀决定容器格式）。
        stream_index: 要提取的流索引（0-based，在同类流中的序号）。
        stream_type: 流类型选择器 ("video" | "audio" | "subtitle")。
        ffmpeg_path: ffmpeg 可执行文件路径或名称。
        timeout: 子进程超时秒数。
        overwrite: 是否覆盖已存在的输出文件。

    Returns:
        ExtractResult 含输出路径和大小。

    Raises:
        ExtractError: 输入/输出/ffmpeg 问题。
    """

def extract_multiple(
    input_path: Path,
    output_dir: Path,
    streams: list[tuple[int, str, str]],  # [(index, type, extension)]
    ffmpeg_path: str = "ffmpeg",
    timeout: int = 300,
) -> list[ExtractResult]:
    """批量提取多个流。

    Args:
        streams: [(stream_index, stream_type, output_extension), ...]
                 例: [(0, "audio", "mka"), (0, "subtitle", "srt")]
    """
```

**内部辅助**：

```python
def _build_ffmpeg_args(
    input_path: Path,
    output_path: Path,
    stream_index: int,
    stream_type: str,
    overwrite: bool,
) -> list[str]:
    """构建 ffmpeg 命令参数。

    - 视频: -map 0:v:<index> -c:v copy -an -sn
    - 音频: -map 0:a:<index> -c:a copy -vn -sn
    - 字幕: -map 0:s:<index> -c:s copy -vn -an
    """

def _suggest_extension(codec: str, stream_type: str) -> str:
    """根据 codec 推荐输出扩展名。

    视频: h264→mp4, hevc→mkv, vp9→webm, av1→mkv
    音频: aac→m4a, mp3→mp3, opus→opus, flac→flac, pcm_*→wav
    字幕: subrip→srt, ass→ass, webvtt→vtt, mov_text→srt
    """

def _detect_codec(input_path: Path, ffprobe_path: str, stream_index: int, stream_type: str) -> str:
    """探测单个流的 codec（提取前快速验证流是否存在）。"""
```

### 2.2 web/api/extract.py

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| `POST` | `/api/extract` | Form: `file_path`, `stream_index`, `stream_type`, `output_ext?` | JSON `ExtractResult` / HTML fragment (HX-Request) |
| `GET` | `/api/extract/download` | query: `?path=` | 文件下载 (`FileResponse`) |

**流选择器 API**：

```
POST /api/extract
  file_path: "/media/input/movie.mkv"
  stream_index: 0
  stream_type: "audio"    → 提取第一条音轨
  output_ext: "mka"       → 可选，不传则自动推断

响应 JSON:
{
  "success": true,
  "result": {
    "stream_index": 0,
    "stream_type": "audio",
    "codec": "aac",
    "output_path": "/media/output/movie_audio_0.mka",
    "output_size": 5242880,
    "duration": 123.456,
    "download_url": "/api/extract/download?path=/media/output/movie_audio_0.mka"
  },
  // 附带完整流信息 (复用 Stage 2 ProbeResult)
  "stream_info": {
    "index": 0, "codec": "aac", "language": "jpn",
    "channels": 2, "sample_rate": 48000, ...
  }
}
```

**输出路径策略**：

```
/media/output/
  ├── <original_name>_video_0.mkv
  ├── <original_name>_audio_0.mka     (日语)
  ├── <original_name>_audio_1.mka     (英语)
  ├── <original_name>_subtitle_0.srt  (中文)
  └── <original_name>_subtitle_1.ass  (日语)
```

命名规则：`{stem}_{type}_{index}.{ext}`

**错误处理**：
- 400: 缺少必填参数
- 404: 流不存在（index 越界或类型无匹配）
- 409: 输出文件已存在（且 overwrite=false）
- 422: 提取失败（ffmpeg 错误）
- 504: 提取超时

**HTMX 交互流**：

```
GET /extract → 探测页面
   ↓ 用户填路径 → POST /api/probe (HX-Request: true) → 展示流列表
   ↓ 用户勾选流 → POST /api/extract (HX-Request: true) → 展示提取结果
   ↓ 用户点击下载 → GET /api/extract/download?path=...
```

> **设计决策**：提取页面复用 Stage 2 的 probe API 获取流列表，不做重复的路径输入。用户在同一个页面完成"探测 → 选择 → 提取"闭环。

### 2.3 模板

**extract.html**（继承 base.html）：
- 路径输入区（复用 probe 的 `.path-input-group` 样式）
- 「探测」按钮 → `hx-post="/api/probe"` → 填充流选择区
- 流选择区（初始为空，HTMX 填充）：
  - 视频/音频/字幕分组展示
  - 每条流前有 checkbox
  - 每条流显示输出扩展名建议
- 「提取选中流」按钮 → `hx-post="/api/extract"` → 填充结果区
- 结果区（初始为空，HTMX 填充）

**extract_results.html**（HTMX fragment）：
- 成功：卡片列表，每个提取成功的流显示文件信息 + 下载按钮
- 失败：错误消息
- 部分成功：显示成功项 + 失败项

### 2.4 页面路由

在 `app.py` 添加：

```python
@app.get("/extract")
async def extract_page(request: Request):
    return templates.TemplateResponse(request, "extract.html", {"version": __version__})
```

### 2.5 路由注册

在 `web/api/__init__.py` 追加：

```python
from web.api.extract import router as extract_router
router.include_router(extract_router, tags=["extract"])
```

## 3. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| FFmpeg 调用 | subprocess 直接调用 | 与 probe.py 一致，`-c copy` 参数简单、不需要 ffmpeg-python 的 builder |
| 默认模式 | `-c copy` 流拷贝 | 无质量损失、速度快；重编码是 Stage 8 的事 |
| 输出命名 | `{stem}_{type}_{index}.{ext}` | 避免覆盖，可识别流来源 |
| 输出目录 | `paths.media_output` | 与 Docker 卷映射一致，下载链接直指 `/media/output/` |
| 流选择器 | `stream_index` + `stream_type` | 与 ffmpeg `-map 0:a:0` 的索引语义一致；index 是同类流中的序号，非全局 index |
| API 设计 | 单流提取 + 批量提取 | POST `/api/extract` 支持单流和批量（`streams[]` 数组），减少 HTTP 往返 |
| 下载方式 | `GET /api/extract/download?path=` | 直接返回 `FileResponse`，浏览器自动触发下载 |
| 文件冲突 | overwrite 显式声明 | 默认不覆盖，避免误删 |
| 扩展名推断 | 自动推断 + 手动指定 | 减少用户决策负担，但保留高级用户控制权 |
| 探测复用 | 提取页复用 probe API | 不重复造轮子；提取页的"选择流"环节就是 probe 结果的交互版 |

## 4. 与 Stage 2 的关系

```
Stage 2: probe_file() → ProbeResult   (探测有哪些流)
Stage 3: extract_stream() → ExtractResult   (把某个流抽出来)
```

**复用点**：
- `extract.html` 页面内嵌 probe 功能 — `hx-post="/api/probe"` 获取流列表
- `_detect_codec()` 内部调 `probe_file()` 验证流存在
- API 响应中的 `stream_info` 字段使用 Stage 2 的 `VideoStream` / `AudioStream` / `SubtitleStream` 序列化

**不修改 Stage 2 代码** — 只追加新模块。

## 5. 修改/创建文件

### 新建 (6个)

| 文件 | 估计行数 | 说明 |
|------|----------|------|
| `processing/core/extract.py` | ~180 | 核心模块：extract_stream + extract_multiple + 辅助函数 |
| `web/api/extract.py` | ~120 | REST 端点：POST 提取 + GET 下载 + HTMX 检测 |
| `web/templates/extract.html` | ~90 | 提取页面：路径输入 → 流选择 → 提取 |
| `web/templates/extract_results.html` | ~70 | HTMX 片段：提取结果卡片 + 下载链接 |
| `tests/test_extract.py` | ~200 | 测试：核心函数 + API 端点 |

### 修改 (4个)

| 文件 | 改动 |
|------|------|
| `web/api/__init__.py` | 加 2 行：`include_router(extract_router)` |
| `app.py` | 加 4 行：`GET /extract` 页面路由 |
| `web/templates/base.html` | 导航栏加「流提取」链接 |
| `web/static/css/style.css` | ~80 行：提取结果卡片、下载按钮、复选框样式 |

## 6. 测试策略

### test_extract.py

| 类 | 测试数 | 覆盖 |
|----|--------|------|
| `TestSuggestExtension` | 8 | 视频/音频/字幕常见 codec → 后缀映射 |
| `TestBuildFFmpegArgs` | 6 | 视频/音频/字幕/overwrite = true/false |
| `TestExtractStream` | 7 | mock subprocess: 正常提取、文件不存在、流索引越界、输出已存在、ffmpeg 未找到、非零返回码、超时 |
| `TestExtractMultiple` | 3 | 全部成功、部分失败（某流不存在）、空流列表 |
| `TestAPIExtract` | 12 | POST 提取成功、缺参 400、流不存在 404、文件冲突 409、提取失败 422、HTMX → HTML、GET 下载成功、下载文件不存在 404、下载路径越界 403 |
| **合计** | **~36** | |

**测试隔离**：
- `processing/core` 测试 mock `subprocess.run`，不实际调 ffmpeg
- API 测试用独立 FastAPI app + `TestClient`（与 Stage 2 一致），mock `extract_stream`
- 路径安全测试：验证下载端点拒绝 `/media/output/../` 类路径穿越

## 7. 验证

```bash
# 测试
python -m pytest tests/ -v

# Docker
docker compose up -d --build

# 页面
http://localhost:8000/extract

# API: 单流提取
curl -X POST http://localhost:8000/api/extract \
  -F "file_path=/media/input/sample.mkv" \
  -F "stream_index=0" \
  -F "stream_type=audio"

# API: 批量提取
curl -X POST http://localhost:8000/api/extract \
  -F "file_path=/media/input/sample.mkv" \
  -F "streams[0][index]=0" -F "streams[0][type]=audio" -F "streams[0][ext]=mka" \
  -F "streams[1][index]=0" -F "streams[1][type]=subtitle" -F "streams[1][ext]=srt"

# API: 下载
curl -O http://localhost:8000/api/extract/download?path=/media/output/sample_audio_0.mka

# 错误测试
curl -X POST http://localhost:8000/api/extract                         # → 400
curl -X POST http://localhost:8000/api/extract \
  -F "file_path=/media/input/sample.mkv" -F "stream_index=99" \
  -F "stream_type=audio"                                               # → 404
```

## 8. 安全考虑

- **路径穿越防护**：下载端点校验 `output_path` 必须在 `settings.paths.media_output` 子树内
- **文件覆盖保护**：默认 `overwrite=False`，防止误覆盖已提取文件
- **输出大小限制**：不在 Stage 3 实现（`-c copy` 不重编码，输出≈输入流大小），后续 Stage 可加配额

---

*预计代码量: core ~180行, api ~120行, 模板 ~160行, CSS ~80行, 测试 ~200行 — 合计 ~740行*
