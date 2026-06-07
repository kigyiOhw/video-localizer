# Stage 1: Web 框架与配置系统

> 状态: ✅ 已完成 (2026-06-07) | 实际代码量: ~340 行

## 1. 设计目标

`docker compose up -d` → 浏览器 `localhost:8000` 显示首页。

- Docker 化开发环境
- FastAPI + Jinja2 + HTMX Web 交互层
- **两层配置体系**：通用默认提交 Git，个人路径 gitignored
- **硬件检测 + 配置档匹配**：自动适配不同配置的电脑
- **最低要求拦截**：不满足直接退出并提示
- 所有模块目录可导入

## 2. 部署架构

**默认：Docker**。也支持直接在宿主机运行。

```
┌─ 方式一: Docker (默认) ─────────────────────────────┐
│ docker compose up -d                                  │
│                                                       │
│ Container (python:3.14-slim + FFmpeg)                 │
│ ├── uvicorn app:app --reload  (:8000)                 │
│ ├── 挂载: ./:/app, <media>:/media, <models>:/models   │
│ └── 无 GPU                                            │
│                                                       │
│ 宿主机 Worker (GPU 直连, Stage 9 起)                   │
│ └── python worker.py  →  faster-whisper CUDA          │
└───────────────────────────────────────────────────────┘

┌─ 方式二: 直接运行 (无 Docker 时) ────────────────────┐
│ pip install -r requirements.txt                        │
│ python app.py                    → :8000              │
│ python worker.py (Stage 9 起)    → :9001              │
│                                                       │
│ 前提: 系统已安装 Python 3.14+ 和 FFmpeg               │
└───────────────────────────────────────────────────────┘
```

## 3. 文件清单

```
video-localizer/
├── Dockerfile
├── docker-compose.yml
├── .env.example              # 提交 Git
├── .env                      # gitignored
├── requirements.txt
├── __init__.py               # __version__
├── app.py                    # FastAPI 入口 + startup 检测
│
├── config/
│   ├── __init__.py            # Settings 数据类 + 两层加载 + 配置档解析
│   ├── requirements.py        # 硬件检测 + 最低要求校验
│   ├── settings.yaml          # 通用默认 (提交 Git)
│   └── settings.local.yaml    # 个人覆盖 (gitignored)
│
├── web/api/__init__.py
├── web/templates/{base.html, index.html}
├── web/static/css/style.css
├── tests/{__init__.py, conftest.py, test_config.py, test_requirements.py}
└── 8 个空包标记
```

## 4. 接口设计

### 4.1 配置体系总览

```
settings.yaml (提交)           settings.local.yaml (gitignored)
├── requirements  最低要求      ├── paths  真实路径覆盖
├── profiles  硬件配置档        ├── engines  引擎覆盖
├── engines  引擎选择链         └── ...  任意字段覆盖
├── paths  容器内默认路径
├── asr/tts/translate  各段默认值
└── ffmpeg

          ↓ 深度合并 ↓
       HardwareProfile.detect()
          ↓
    匹配最佳配置档 → 应用引擎参数
          ↓
    最低要求校验 → 不满足 → 退出 + 提示
          ↓
       Settings 实例
```

### 4.2 `config/settings.yaml` 结构 (提交 Git)

```yaml
# 最低运行要求 (不满足→退出)
requirements:
  min_ram_gb: 4
  min_vram_gb: 2
  min_disk_free_gb: 10
  min_python: "3.13"
  required_tools: [ffmpeg, ffprobe]

# 硬件配置档 (自动匹配最佳，按 VRAM 从高到低)
profiles:
  gpu_ultra:      # VRAM ≥ 16GB (RTX 4090/5090 等)
    asr:
      model_size: "large-v3-turbo"
      device: "cuda"
      compute_type: "float16"
    tts:
      engine: "xtts"           # 本地 XTTS-v2，高质量
    translate:
      engine: "llm_local"      # 本地 LLM，离线翻译
  gpu_high:       # VRAM 8-16GB (主流中高端 GPU)
    asr:
      model_size: "large-v3"
      device: "cuda"
      compute_type: "int8_float16"
    tts:
      engine: "edge_tts"
    translate:
      engine: "llm"
  gpu_medium:     # VRAM 4-8GB (入门级 GPU)
    asr:
      model_size: "medium"
      device: "cuda"
      compute_type: "int8"
    tts:
      engine: "edge_tts"
    translate:
      engine: "llm"
  gpu_low:        # VRAM 2-4GB (入门独显 / 旧卡)
    asr:
      model_size: "small"
      device: "cuda"
      compute_type: "int8"
    tts:
      engine: "edge_tts"
    translate:
      engine: "none"           # 显存不足，翻译不可用
  cpu:            # 无 GPU / VRAM < 2GB
    asr:
      model_size: "tiny"
      device: "cpu"
      compute_type: "int8"
    tts:
      engine: "edge_tts"
    translate:
      engine: "none"

# 引擎选择链 (每类可指定多个后端，按顺序回退)
engines:
  asr:
    default: "auto"      # auto=根据配置档自动选
    fallback: ["whisper_local", "whisper_api", "none"]
  translate:
    default: "auto"
    fallback: ["llm_local", "llm", "none"]
  tts:
    default: "auto"
    fallback: ["xtts", "edge_tts", "none"]

# 路径 (容器内默认)
paths:
  model_root: "/models"
  media_input: "/media/input"
  media_output: "/media/output"
  temp_dir: "/media/temp"

# 其余段使用 profile 选定的值作为默认，local.yaml 可覆盖
asr:
  beam_size: 5
  vad_filter: true
  word_timestamps: true

subtitle:
  default_language: "zh"
  format: "srt"

translate:
  llm_provider: "deepseek"
  llm_model: "deepseek-chat"

tts:
  voice: "zh-CN-XiaoxiaoNeural"

ffmpeg:
  executable: "ffmpeg"
```

### 4.3 `config/requirements.py` — 硬件检测与校验

```python
@dataclass
class HardwareProfile:
    """当前机器硬件能力。"""
    cpu_cores: int
    ram_total_gb: float
    gpu_name: str | None       # None = 无 GPU
    vram_total_gb: float       # 0 = 无 GPU
    cuda_available: bool
    cuda_version: str | None
    disk_free_gb: float
    tools_available: dict[str, bool]  # {"ffmpeg": True, "ffprobe": True}

    @classmethod
    def detect(cls) -> "HardwareProfile": ...
    #   CPU: os.cpu_count()
    #   RAM: psutil.virtual_memory()
    #   GPU: ctranslate2.get_cuda_device_count() + nvidia-smi 解析
    #   Disk: shutil.disk_usage()
    #   Tools: shutil.which("ffmpeg") ...

def check_requirements(profile: HardwareProfile, reqs: dict) -> list[str]:
    """返回不满足的项列表，全部满足返回空列表。"""
    # 比较 profile 各项与 requirements 配置

def select_profile(profile: HardwareProfile, profiles: dict) -> dict:
    """根据硬件从 profiles 中选最佳配置档。
    
    匹配规则 (VRAM 从高到低):
      ≥ 16GB → gpu_ultra    (RTX 4090/5090, 本地 TTS+翻译)
      8-16GB → gpu_high     (RTX 4070+, large-v3)
      4-8GB  → gpu_medium   (RTX 3060/4060, medium)
      2-4GB  → gpu_low      (入门独显, small)
      < 2GB  → cpu          (纯 CPU, tiny)
    """
    if not profile.cuda_available:
        return profiles["cpu"]
    vram = profile.vram_total_gb
    if vram >= 16:
        return profiles["gpu_ultra"]
    elif vram >= 8:
        return profiles["gpu_high"]
    elif vram >= 4:
        return profiles["gpu_medium"]
    elif vram >= 2:
        return profiles["gpu_low"]
    else:
        return profiles["cpu"]
```

### 4.4 启动时的校验流程 (`app.py` startup 事件)

```
1. Settings.load()           → 合并 YAML
2. HardwareProfile.detect()  → 检测硬件
3. check_requirements()      → 不满足?
   ├── 有失败项 → 打印清单 → sys.exit(1)
   └── 全部通过 ↓
4. select_profile()          → 匹配配置档
5. 配置档参数覆盖 Settings 默认值 (仍可被 local.yaml 覆盖)
6. 记录最终生效的配置到日志
7. /api/health 返回配置摘要 (不含路径)
```

**不满足时退出示例**:

```
[Video-Localizer] 硬件检测完成:
  CPU: 4核, RAM: 3.8GB, GPU: 无, 磁盘: 50GB

[ERROR] 不满足最低运行要求:
  ✗ 内存不足: 需要 4GB, 当前 3.8GB
  ✗ VRAM不足: 需要 2GB, 当前 0GB

请升级硬件或使用 CPU 最低配置档 (修改 settings.local.yaml)。
```

### 4.5 Web 入口 (`app.py`)

```python
app = FastAPI(title="Video-Localizer")

@app.on_event("startup")
def startup():
    settings = Settings.load()
    profile = HardwareProfile.detect()
    failures = check_requirements(profile, settings.requirements)
    if failures:
        print(...)  # 打印失败清单
        sys.exit(1)
    selected = select_profile(profile, settings.profiles)
    settings.apply_profile(selected)
    app.state.settings = settings
    app.state.profile = profile

@app.get("/")               # 首页
@app.get("/api/health")     # 健康检查 + 配置摘要
```

### 4.6 首页 (`web/templates/index.html`)

功能入口卡片 + 当前检测到的硬件信息和匹配的配置档。

### 4.7 Docker 配置

- **Dockerfile**: `python:3.14-slim` + FFmpeg + pip install
- **docker-compose.yml**: volumes 从 `.env` 读取挂载路径
- `.env.example` (提交): `MEDIA_DIR=./media`, `MODEL_DIR=./models`
- `.env` (gitignored): 个人实际路径

### 4.8 新增依赖

```
fastapi, uvicorn[standard], python-multipart, httpx
python-dotenv   # .env 加载
psutil          # 硬件信息检测
```

## 5. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 硬件检测 | psutil + ctranslate2 + shutil | 无需额外系统依赖，跨平台 |
| 配置档匹配 | 5 档 VRAM 阈值分段 | 覆盖从 CPU 到 RTX 5090，每档 ASR/TTS/翻译独立配置 |
| 最低要求 | 启动时硬拦截 | 比运行到一半崩掉好的多 |
| 引擎回退 | fallback 链 + `none` | 每类引擎独立回退，`none` 表示该功能不可用 |
| `auto` 模式 | 先配配置档，再走 fallback | 先定硬件级别，再选引擎实现 |
| Docker | 默认选项，非强制 | 降低门槛，没有 Docker 也能跑 |
| 其余同前版 | | |

## 6. 验证

### 自动化

| 测试 | 预期 |
|------|------|
| `HardwareProfile.detect()` | 返回有效 CPU/RAM/GPU 信息 |
| 旗舰卡检测 (VRAM 24GB) | `select_profile` → `gpu_ultra` |
| 高配检测 (VRAM 12GB) | `select_profile` → `gpu_high` |
| 中配检测 (VRAM 6GB) | `select_profile` → `gpu_medium` |
| 低配检测 (VRAM 3GB) | `select_profile` → `gpu_low` |
| 无 GPU 检测 | `select_profile` → `cpu` |
| `gpu_ultra` 配置 | asr=large-v3-turbo, tts=xtts, translate=llm_local |
| `cpu` 配置 | asr=tiny, tts=edge_tts, translate=none |
| 不满足要求 | `check_requirements` 返回非空列表 |
| 满足要求 | `check_requirements` 返回空列表 |
| `settings.local.yaml` 覆盖 | 覆盖配置档参数 |
| `/api/health` | 200 + 配置摘要 (不含路径) |

### 手动

```bash
docker compose up -d --build
curl http://localhost:8000/api/health
# → {"status":"ok","profile":"gpu_high","engines":{"asr":"whisper_local",...}}
```

## 7. 注意事项

- 硬件检测跑在宿主机 Worker 侧（GPU 信息在容器内不可见），容器内只做 CPU/RAM/Disk 检测
- `profiles` 生效顺序: `settings.yaml profiles` → `select_profile()` 选定 → `settings.local.yaml` 覆盖
- 文档只引用配置键名，不出现具体路径
- 现有 `docs/01-05` 后续改为引用键名后可从 `.gitignore` 移除

---

*下一步: 实施 Stage 1*
