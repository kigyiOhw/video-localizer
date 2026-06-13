# Stage 7: 音频轨管理 + 音画同步

## 设计目标

提供 REST API 与 Web UI，让用户可以管理视频中的音频轨并调整音画同步：

- 为视频追加外部音频轨。
- 替换指定音频轨。
- 移除指定音频轨。
- 静音指定音频轨（或全部音频轨）。
- 调整音频同步偏移（正 = 延后，负 = 提前）。
- 调整音频速度（变速不变调）。

典型场景：
- 视频只有日语配音，追加一条中文配音轨。
- 替换质量不佳的音轨为重新编码的版本。
- 删除不需要的音轨以减小文件体积。
- 配音整体比画面晚 0.5 秒，需要整体提前。
- 音频时长与视频不匹配，需要轻微加速/减速。

## 接口设计

### 核心模块

**文件**: `processing/core/audio.py`

新增 `AudioTrackError` 异常与 `AudioTrackResult` 数据类：

```python
@dataclass
class AudioTrackResult:
    input_video: Path
    output_path: Path
    output_size: int
    operation: str
    audio_index: int | None
    extra: dict[str, Any]
```

新增 6 个公共函数：

| 函数 | 作用 |
|---|---|
| `add_audio_track()` | 追加外部音频轨 |
| `replace_audio_track()` | 替换指定音频轨 |
| `remove_audio_track()` | 移除指定音频轨 |
| `mute_audio_track()` | 静音音频轨 |
| `adjust_audio_sync()` | 调整同步偏移 |
| `adjust_audio_speed()` | 调整速度 |

### REST API

**文件**: `web/api/audio.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/audio/add` | 追加音频轨 |
| POST | `/api/audio/replace` | 替换音频轨 |
| POST | `/api/audio/remove` | 移除音频轨 |
| POST | `/api/audio/mute` | 静音音频轨 |
| POST | `/api/audio/sync` | 同步偏移 |
| POST | `/api/audio/speed` | 速度调整 |
| GET  | `/api/audio/download?path=` | 下载输出文件 |

**POST /api/audio/add 参数**:

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | str | 是 | 视频文件路径 |
| `audio_path` | str | 是 | 外部音频文件路径 |
| `language` | str | 否 | ISO 639-2 三字母代码，默认 `und` |
| `set_default` | bool | 否 | 是否设为默认音轨，默认 `false` |
| `container` | str | 否 | 输出容器，`mkv` / `mp4`，默认 `mkv` |

其他 POST 端点参数类似，根据操作需要 `audio_index`、`offset_seconds`、`speed_ratio` 等。

### Web 页面

| 路径 | 模板 |
|---|---|
| `/audio` | `web/templates/audio.html` |
| `/api/audio/*` (HTMX) | `web/templates/audio_results.html` |

## 实施要点

### FFmpeg 命令结构

**追加音频轨**:

```bash
ffmpeg -y -i video.mkv -i audio.m4a -map 0 -map 1 -c copy \
  -metadata:s:a:N language=chi \
  -disposition:a:N default \
  output.mkv
```

其中 `N` 为新音频轨的同类序号（等于原视频音频流总数）。

**替换音频轨**:

```bash
ffmpeg -y -i video.mkv -i audio.m4a \
  -map 0:v \
  -map 0:a:0 ... -map 0:a:<idx-1> \
  -map 1:a \
  -map 0:a:<idx+1> ... \
  -map 0:s \
  -c copy -metadata:s:a:<idx> language=chi \
  output.mkv
```

**移除音频轨**:

```bash
ffmpeg -y -i video.mkv \
  -map 0:v \
  -map 0:a:0 ... -map 0:a:<idx-1> \
  -map 0:a:<idx+1> ... \
  -map 0:s \
  -c copy output.mkv
```

**静音音频轨**:

```bash
ffmpeg -y -i video.mkv \
  -filter_complex "[0:a:<idx>]volume=0[muted]" \
  -map 0:v -map "[muted]" -map 0:a:<others>? -map 0:s \
  -c:v copy -c:a copy \
  output.mkv
```

**同步偏移**:

```bash
ffmpeg -y -i video.mkv -itsoffset 0.5 -i video.mkv \
  -map 0:v -map 0:s \
  -map 1:a:<idx> \
  -map 0:a:<others>? \
  -c copy output.mkv
```

正偏移使音频延后，负偏移使音频提前。

**速度调整**:

```bash
ffmpeg -y -i video.mkv \
  -filter_complex "[0:a:<idx>]atempo=1.05[speeded]" \
  -map 0:v -map "[speeded]" -map 0:a:<others>? -map 0:s \
  -c:v copy -c:a aac \
  output.mkv
```

`atempo` 单次范围 0.5–2.0，超出时链式使用多个 `atempo`。

### 校验流程

1. 输入视频存在且为文件。
2. 外部音频文件存在且为文件（如需要）。
3. `audio_index` 在有效范围内（通过 `probe_file` 校验）。
4. 输出容器仅支持 `mkv` / `mp4`。
5. 输出文件冲突处理（`overwrite`）。

### 安全

- 输入路径通过 `web.api.utils._resolve_allowed_path()` 校验，限制在 `media_input` / `temp_dir`。
- 下载路径通过 `_is_within_directory()` 校验，限制在 `media_output`。

### 复用现有模式

- `processing/core/probe.py` 的 `probe_file()` 与 `AudioStream`。
- `processing/core/mux.py` 的 `MuxError`、subprocess 处理、输出路径生成。
- `web/api/track.py` 的 `_get_settings()` / `_get_templates()` 延迟导入、HTMX/JSON 双响应。
- `web/templates/track.html` / `track_results.html` 的表单与结果模板结构。

## 文件清单

| 文件 | 动作 |
|---|---|
| `processing/core/audio.py` | 新建，核心函数 |
| `web/api/audio.py` | 新建，REST 路由 |
| `web/api/__init__.py` | 注册 `audio` 路由 |
| `web/templates/audio.html` | 新建，Web 表单 |
| `web/templates/audio_results.html` | 新建，HTMX 结果片段 |
| `tests/test_audio.py` | 新建，单元测试与 API 测试 |
| `app.py` | 添加 `/audio` 页面路由 |
| `web/templates/base.html` | 添加导航入口 |

## 验证结果

- [ ] `compileall` 通过。
- [ ] `pytest tests/test_audio.py -v` 通过（待环境就绪后运行）。
- [ ] 手工验证：使用含多音轨视频调用各 `/api/audio/*` 端点，输出轨道、元数据、同步、速度正确。

## 踩坑记录

- **`-map` 显式选择**: 添加/替换/移除音频轨时必须显式映射所有流，避免 FFmpeg 自动选择导致非预期结果。
- **音频重编码边界**: 静音和速度调整必须使用滤镜，会强制音频重编码；视频流仍应保持 `-c:v copy`。
- **同步偏移方向**: `-itsoffset` 作用于第二路输入，正值使该输入的流延后；对用户来说即“音频延后”。
- **`atempo` 范围**: 单次 `atempo` 只接受 0.5–2.0，超出范围需要链式多个 `atempo` 滤镜。

---

*计划日期: 2026-06-14*
*状态: 计划中*
