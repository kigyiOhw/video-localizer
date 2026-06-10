# Stage 9: ASR 语音识别（前置实施）

> 状态: 实施中 | 预计代码量: ~550 行

## 1. 设计目标

视频/音频 → 提取音轨 → faster-whisper 语音识别 → SRT 字幕文件。

这是 AI 流水线的第一步，后续接 Stage 10（翻译）和 Stage 11（字幕生成）。

## 2. 整体流程

```
GET /asr → 页面上传文件/指定路径
    ↓
POST /api/asr/transcribe
    ↓
1. 探测媒体 (Stage 2 probe)
2. 提取音轨 (Stage 3 extract) → .m4a 临时文件
3. faster-whisper 转写 → segments 列表
4. segments → SRT 文本
5. 返回 JSON: {segments, srt_text, language}
```

## 3. 模块接口

### 3.1 engines/asr/engine.py

```python
class ASREngine(ABC):
    """ASR 引擎抽象基类（策略模式）。"""

    @abstractmethod
    def transcribe(
        self, audio_path: Path, language: str | None = None
    ) -> list[ASRSegment]: ...

    @abstractmethod
    def detect_language(self, audio_path: Path) -> str: ...

@dataclass
class ASRSegment:
    start: float      # 起始秒数
    end: float        # 结束秒数
    text: str         # 转写文本
    confidence: float # 置信度 0-1
```

### 3.2 engines/asr/whisper_local.py

```python
class WhisperLocalEngine(ASREngine):
    """faster-whisper 本地实现。"""

    def __init__(self, model_size: str, device: str, compute_type: str):
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio_path, language=None) -> list[ASRSegment]:
        segments, info = self._model.transcribe(str(audio_path), language=language, ...)
        return [ASRSegment(seg.start, seg.end, seg.text.strip(), ...) for seg in segments]

    def detect_language(self, audio_path) -> str:
        segments, info = self._model.transcribe(str(audio_path), ...)
        return info.language
```

### 3.3 web/api/asr.py

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| `POST` | `/api/asr/transcribe` | Form: `file_path`, `language?`, `model_size?` | JSON 转写结果 |
| `GET` | `/api/asr/transcribe` | query: 同上 | JSON |

响应格式：
```json
{
  "success": true,
  "language": "ja",
  "duration": 120.5,
  "segments": [
    {"start": 0.0, "end": 2.5, "text": "こんにちは", "confidence": 0.98}
  ],
  "srt": "1\n00:00:00,000 --> 00:00:02,500\nこんにちは\n\n...",
  "stats": {
    "model": "medium",
    "device": "cpu",
    "compute_type": "int8",
    "elapsed_seconds": 45.2
  }
}
```

### 3.4 辅助函数

```python
def segments_to_srt(segments: list[ASRSegment]) -> str:
    """转写片段 → SRT 格式文本。"""

def extract_audio_for_asr(input_path: Path, output_dir: Path) -> Path:
    """从视频提取音轨，返回音频文件路径。复用 Stage 3 extract_stream。"""
```

## 4. 文件清单

| 操作 | 文件 | 行数 |
|------|------|------|
| 新建 | `engines/asr/engine.py` | ~40 |
| 新建 | `engines/asr/whisper_local.py` | ~100 |
| 新建 | `web/api/asr.py` | ~120 |
| 新建 | `web/templates/asr.html` | ~100 |
| 新建 | `web/templates/asr_results.html` | ~60 |
| 新建 | `tests/test_asr.py` | ~130 |
| 修改 | `web/api/__init__.py` | +2 行 |
| 修改 | `app.py` | +7 行 |
| 修改 | `web/templates/base.html` | +1 行 |

## 5. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| GPU Worker | 暂不分离 | Docker 容器无 GPU，先用 CPU 模式跑；Worker 架构后续加 |
| 模型加载 | 单例懒加载 | 首次请求加载模型（几秒），后续请求复用 |
| 语言检测 | faster-whisper 自动检测 | 不强制用户选语言 |
| 音频提取 | 复用 Stage 3 | 提取第一个音轨（默认轨），支持指定索引 |
| 临时文件 | `paths.temp_dir` | 提取的音频用完即删 |
| SRT 生成 | 内置函数 | pysrt 太重量，直接格式化字符串 |

## 6. 验证

```bash
# 测试
py -m pytest tests/ -v

# 页面
http://localhost:8000/asr

# API (CPU 模式，medium 模型 ~5-10x 实时)
curl -X POST http://localhost:8000/api/asr/transcribe \
  -F "file_path=/media/input/sample.mp4" \
  -F "language=auto"
```
