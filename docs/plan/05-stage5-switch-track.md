# Stage 5: 切换默认轨道

## 设计目标

实现一个 REST API 与 Web 页面，允许用户切换视频文件中指定类型轨道的默认标记（default disposition）。

典型场景：
- 视频含多条音轨（如日语、英语），将英语音轨设为默认。
- 视频含多条字幕轨（如中、英、日），将中文字幕设为默认。
- 视频含多个视频流（较少见），切换默认视频流。

## 接口设计

### 核心模块

**文件**: `processing/core/mux.py`

新增 `SwitchDefaultResult` 数据类：

```python
@dataclass
class SwitchDefaultResult:
    input_video: Path
    output_path: Path
    output_size: int
    stream_type: str
    stream_index: int
    changed_tracks: list[dict[str, Any]]
```

新增 `switch_default_track()`：

```python
def switch_default_track(
    video_path: Path,
    stream_type: str,
    stream_index: int,
    output_path: Path | None = None,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    container: str = "mkv",
    timeout: int = 120,
    overwrite: bool = False,
) -> SwitchDefaultResult:
    """切换指定类型轨道的默认标记。"""
```

### REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/track/default` | 切换默认轨道 |
| GET  | `/api/track/download?path=` | 下载输出文件 |

**POST /api/track/default 参数**:

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | str | 是 | 媒体文件路径（media_input 内） |
| `stream_type` | str | 是 | `video` / `audio` / `subtitle` |
| `stream_index` | int | 是 | 同类流中的序号（0-based） |
| `container` | str | 否 | 输出容器，`mkv` / `mp4`，默认 `mkv` |

### Web 页面

| 路径 | 模板 |
|---|---|
| `/track` | `web/templates/track.html` |
| `/api/track/default` (HTMX) | `web/templates/track_results.html` |

## 实施要点

### FFmpeg 命令结构

```bash
ffmpeg -y -i input.mkv -map 0 -c copy \
  -disposition:v:0 none \
  -disposition:v:1 default \
  output.mkv
```

- `-map 0 -c copy`：保留所有流，不做重编码。
- `-disposition:<type>:<index>`：设置默认标记。
  - `<type>`: `v` (video), `a` (audio), `s` (subtitle)
  - `<index>`: 同类流中的 0-based 序号
- 目标轨道设为 `default`，同类型其他轨道设为 `none`。

### 校验流程

1. 输入文件存在且为文件。
2. `stream_type` 必须是 `video` / `audio` / `subtitle`。
3. 调用 `probe_file()` 获取流列表，校验 `stream_index` 不越界。
4. 输出容器仅支持 `mkv` / `mp4`。
5. 输出文件冲突处理（`overwrite` 参数）。

### 安全

- 输入路径通过 `web.api.utils._resolve_allowed_path()` 校验，限制在 `media_input` / `temp_dir`。
- 下载路径通过 `_is_within_directory()` 校验，限制在 `media_output`。

### 复用现有模式

- 错误处理复用 `MuxError`。
- Web 端点复用 `_get_settings()` / `_get_templates()` 延迟导入、HTMX/JSON 双响应模式。
- 测试复用 `TestClient` + mock `subprocess.run` + mock `probe_file` 的模式。

## 文件清单

| 文件 | 动作 |
|---|---|
| `processing/core/mux.py` | 新增 `SwitchDefaultResult`、`switch_default_track()`、`_build_switch_default_args()` |
| `web/api/track.py` | 新建，REST 路由 |
| `web/api/__init__.py` | 注册 `track` 路由 |
| `web/templates/track.html` | 新建，Web 表单 |
| `web/templates/track_results.html` | 新建，HTMX 结果片段 |
| `tests/test_track.py` | 新建，单元测试与 API 测试 |

## 验证结果

- [ ] `compileall` 通过。
- [ ] `pytest tests/test_track.py -v` 通过。
- [ ] 手工验证：使用含多音轨/字幕轨的测试视频调用 `/api/track/default`，输出文件默认轨道正确。

## 踩坑记录

- **FFmpeg 选择器**: 必须使用同类流序号（`s:1` 表示第 2 条字幕轨），而不是全局流索引。probe 结果中 `SubtitleStream` 列表的顺序即同类流序号。
- **多默认轨道**: 某些容器允许同类型多条 default 轨道，但通常用户期望只有一条。本实现将目标设为 default，同类型其他轨道显式设为 none。
- **MP4 字幕**: MP4 对字幕支持有限，但 `-c copy` 模式下若原容器支持则可用。默认仍输出 MKV。

---

*计划日期: 2026-06-13*
*状态: 待实现*
