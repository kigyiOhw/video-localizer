# Stage 2: FFprobe 流探测

> 状态: 规划中 | 预计代码量: ~590 行

## 1. 设计目标

上传视频文件或指定文件路径 → ffprobe 探测 → 展示所有流信息（视频/音频/字幕），含编码、分辨率、帧率、声道、语言等。

- `processing/core/probe.py` — FFprobe 包装层（dataclass + subprocess + JSON 解析）
- `web/api/probe.py` — REST 端点（薄封装）
- `web/templates/probe.html` — Web 上传页面 + 结果展示（HTMX fragment）
- `tests/test_probe.py` — 单元测试 + API 测试

## 2. 模块接口

### 2.1 processing/core/probe.py

```python
class ProbeError(Exception):
    """探测失败（文件不存在、ffprobe 找不到、超时、格式不支持等）。"""

@dataclass
class StreamBase:
    index: int          # 流索引 (0-based)
    codec: str          # codec_name, e.g. "h264", "aac", "subrip"
    codec_long: str | None  # codec_long_name, e.g. "H.264 / AVC"
    codec_type: str     # "video" | "audio" | "subtitle"
    language: str | None    # ISO 639-2, e.g. "jpn", "eng"
    disposition: dict   # default/forced/dub 等
    tags: dict          # 元数据标签

@dataclass
class VideoStream(StreamBase):
    width: int
    height: int
    pix_fmt: str        # e.g. "yuv420p"
    bitrate: int | None
    fps: str            # avg_frame_rate, e.g. "24000/1001"
    fps_float: float | None
    duration: float | None
    bit_depth: int

@dataclass
class AudioStream(StreamBase):
    sample_rate: int    # Hz
    channels: int
    channel_layout: str  # e.g. "stereo", "5.1"
    bitrate: int | None
    duration: float | None

@dataclass
class SubtitleStream(StreamBase):
    duration: float | None

@dataclass
class FormatInfo:
    filename: str
    format_name: str    # e.g. "mp4", "matroska"
    format_long: str | None
    size_bytes: int
    duration: float | None
    bitrate: int | None

@dataclass
class ProbeResult:
    format: FormatInfo
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]
    subtitle_streams: list[SubtitleStream]

def probe_file(path: Path, ffprobe_path: str = "ffprobe", timeout: int = 30) -> ProbeResult
    """探测媒体文件，返回结构化流信息。"""

def parse_ffprobe_output(raw: dict) -> ProbeResult
    """解析 ffprobe -print_format json 输出为 ProbeResult。"""
```

内部辅助：`_parse_float(v)`, `_parse_fps(fps_str)`, `_format_duration(seconds)`, `_format_size(bytes)`, `_safe_filename(name)`

### 2.2 web/api/probe.py

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| `POST` | `/api/probe` | multipart: `file` 或 `file_path` | JSON `ProbeResult` / HTML fragment (HX-Request) |
| `GET` | `/api/probe` | query: `?file_path=` | JSON `ProbeResult` |

错误处理：
- 400: 未提供 file 或 file_path
- 422: 探测失败（文件不存在 / 格式不支持 / ffprobe 错误）

HTMX 检测：`request.headers.get("hx-request") == "true"` → 返回 `probe_results.html` 片段。

### 2.3 模板

- `GET /probe` → `web/templates/probe.html`（继承 base.html）：上传表单 + 路径输入
- HTMX POST `/api/probe` → `web/templates/probe_results.html`（片段）：文件信息卡片 + 按类型分组的流卡片

## 3. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| ffprobe 调用 | subprocess 直接调用 | 与 `requirements.py` 的 nvidia-smi 检测模式一致 |
| 流数据结构 | 分层 dataclass (StreamBase → Video/Audio/Subtitle) | 类型安全，模板按类型渲染 |
| 文件输入 | 上传 + 路径参数双模式 | Web UI 上传方便；API 直接路径调用高效 |
| 响应格式 | HX-Request 头检测 | HTMX 返回 HTML，普通请求返回 JSON，一个端点两种行为 |
| 循环引用 | `web/api/probe.py` 内 lazy import settings | 避免 app→web.api→web.api.probe→app 循环 |
| API 结构 | `web/api/__init__.py` 根 APIRouter + 子模块 | 后续 Stage 直接追加子路由 |

## 4. 修改的现有文件

| 文件 | 改动 |
|------|------|
| `web/api/__init__.py` | 创建 APIRouter + include probe 子路由 |
| `app.py` | include api_router；加 `GET /probe` 页面路由 |
| `web/templates/base.html` | 导航栏加「流探测」链接 |
| `web/static/css/style.css` | 加表单、流卡片、错误提示、加载指示器样式 |

> 注: `config/__init__.py` 的 `FFmpegConfig.ffprobe_executable` 和 `config/settings.yaml` 的 `ffprobe_executable` 在 Stage 1 已内置，无需修改。

## 5. 循环引用处理

```
app.py → web/api/__init__.py → web/api/probe.py
                                          ↓ (lazy import inside function body)
                                 from app import settings, templates
```

`web/api/probe.py` 不在模块顶层 import app，而在端点函数体内延迟导入，避免循环。

## 6. 测试策略

### test_probe.py

| 类 | 覆盖 |
|----|------|
| `TestParseFFprobeOutput` | valid JSON → ProbeResult；无流/纯音频/"und"语言/字段缺失 |
| `TestProbeFile` | mock subprocess: 正常/ffprobe 未找到/非零返回码/超时/JSON 异常 |
| `TestAPIProbe` | POST 上传/POST 路径/缺参 400/探测错误 422/HTMX 头→HTML |

测试隔离：API 测试用独立 FastAPI app（不导入 app.py），避免模块级配置加载副作用。

## 7. 验证

```bash
# 测试
python -m pytest tests/ -v

# Docker
docker compose up -d --build

# 页面
http://localhost:8000/probe

# API
curl -X POST http://localhost:8000/api/probe -F "file_path=/media/input/sample.mp4"
curl -X POST http://localhost:8000/api/probe -H "HX-Request: true" -F "file_path=/media/input/sample.mp4"

# 错误
curl -X POST http://localhost:8000/api/probe              # → 400
curl -X POST http://localhost:8000/api/probe -F "file_path=/nonexistent"  # → 422
```
