"""Video-Localizer — AI-powered video subtitle & audio track manager."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Windows 下 pipe/重定向时 stdout 默认用 GBK，强制 UTF-8 避免日志乱码
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("video_localizer")

# 降低第三方库日志噪音
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 共享 Python 环境路径注入
#
# 这些 AI 包体积大、可跨项目复用，安装在外部 venv 中：
#   pytorch-env/  → torch, torchaudio (CUDA)
#   shared-venv/  → faster-whisper, ctranslate2, silero-vad, onnxruntime, …
#
# 此处用代码直接注入 sys.path，不使用 .pth 文件，
# 因为 uvicorn --reload 的 worker 进程可能不会重新读取 .pth。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 配置加载（模块级别，失败立即报错）
# ---------------------------------------------------------------------------

from config import Settings
from config.requirements import auto_configure, check_minimum_requirements, detect_system_info

logger.info("=" * 50)
logger.info("Video-Localizer 启动中...")
logger.info("=" * 50)

# 加载配置
settings = Settings.load()

# 注入共享 Python 环境路径（从本地配置读取，不硬编码绝对路径）
for _sp in settings.paths.shared_site_packages:
    if _sp not in sys.path:
        sys.path.insert(0, _sp)
        logger.info("已注入共享环境路径: %s", _sp)
logger.info("配置加载完成")

# 硬件检测 + 自动选择配置档
info = detect_system_info()
profile_name = auto_configure(settings, info=info)
logger.info("选中配置档: %s", profile_name)

# 最低配置检查（不满足则退出）
failures = check_minimum_requirements(settings, info=info)
if failures:
    logger.error("=" * 50)
    logger.error("不满足最低运行要求，退出。请升级硬件或修改配置。")
    logger.error("=" * 50)
    sys.exit(1)

# 版本号（从 __init__.py 中正则提取，避免 exec()）
__version__ = "0.1.0"
_version_path = Path(__file__).resolve().parent / "__init__.py"
if _version_path.exists():
    try:
        _text = _version_path.read_text(encoding="utf-8")
        _match = re.search(r'__version__\s*=\s*"([^"]+)"', _text)
        if _match:
            __version__ = _match.group(1)
    except Exception:
        logger.debug("无法读取版本号", exc_info=True)


# ---------------------------------------------------------------------------
# 应用与模板
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory="web/templates")


# ---------------------------------------------------------------------------
# 临时文件定时清理
# ---------------------------------------------------------------------------


def _cleanup_temp_files() -> int:
    """清理 temp_dir 中超过 max_age_hours 的临时文件。

    Returns:
        删除的文件数。
    """
    temp_dir = settings.paths.temp_dir
    if not temp_dir.exists():
        return 0

    max_age_seconds = settings.cleanup.max_age_hours * 3600
    now = time.time()
    deleted = 0

    for entry in temp_dir.rglob("*"):
        if not entry.is_file():
            continue
        try:
            age = now - entry.stat().st_mtime
            if age > max_age_seconds:
                entry.unlink(missing_ok=True)
                deleted += 1
                logger.debug("清理过期临时文件: %s (%.1f 小时前)", entry.name, age / 3600)
        except OSError:
            pass

    # 清理空的子目录
    for entry in sorted(temp_dir.rglob("*"), reverse=True):
        if entry.is_dir() and entry != temp_dir:
            try:
                entry.rmdir()  # 只删空目录
            except OSError:
                pass

    if deleted > 0:
        logger.info("临时文件清理完成: 已删除 %d 个文件", deleted)
    return deleted


async def _cleanup_loop(interval_hours: float) -> None:
    """后台定时清理循环。"""
    interval_seconds = interval_hours * 3600

    # 启动后立即清理一次
    logger.info("启动时清理过期临时文件...")
    _cleanup_temp_files()

    while True:
        await asyncio.sleep(interval_seconds)
        logger.debug("定时清理临时文件 (间隔=%.1fh, 最长保留=%.1fh)",
                     settings.cleanup.interval_hours, settings.cleanup.max_age_hours)
        _cleanup_temp_files()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时确保目录存在 + 启停定时清理。"""
    settings.ensure_dirs()
    logger.info("所有目录就绪")

    # 启动定时清理后台任务
    cleanup_task = asyncio.create_task(
        _cleanup_loop(settings.cleanup.interval_hours)
    )

    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            logger.debug("定时清理任务已停止")


app = FastAPI(
    title="Video-Localizer",
    description="AI-powered video subtitle & audio track manager",
    version=__version__,
    lifespan=lifespan,
)

# 静态文件
_static_dir = Path(__file__).resolve().parent / "web" / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

# API 路由（各功能模块在 web/api/ 下注册）
from web.api import router as api_router

app.include_router(api_router)


@app.get("/")
async def index(request: Request):
    """首页。"""
    logger.debug("GET /")
    return templates.TemplateResponse(request, "index.html", {
        "version": __version__,
        "profile": settings.selected_profile,
        "asr": {
            "engine": settings.asr.engine,
            "model_size": settings.asr.model_size,
            "device": settings.asr.device,
        },
        "tts": {"engine": settings.tts.engine},
        "translate": {"engine": settings.translate.engine},
    })


@app.get("/probe")
async def probe_page(request: Request):
    """流探测页面。"""
    logger.debug("GET /probe")
    return templates.TemplateResponse(request, "probe.html", {
        "version": __version__,
    })


@app.get("/extract")
async def extract_page(request: Request):
    """流提取页面。"""
    logger.debug("GET /extract")
    return templates.TemplateResponse(request, "extract.html", {
        "version": __version__,
    })


@app.get("/asr")
async def asr_page(request: Request):
    """语音识别页面。"""
    logger.debug("GET /asr")
    return templates.TemplateResponse(request, "asr.html", {
        "version": __version__,
    })


@app.get("/subtitle")
async def subtitle_page(request: Request):
    """字幕管理页面。"""
    logger.debug("GET /subtitle")
    return templates.TemplateResponse(request, "subtitle.html", {
        "version": __version__,
    })


@app.get("/translate")
async def translate_page(request: Request):
    """翻译页面。"""
    logger.debug("GET /translate")
    return templates.TemplateResponse(request, "translate.html", {
        "version": __version__,
    })


@app.get("/pipeline")
async def pipeline_page(request: Request):
    """端到端流水线页面。"""
    logger.debug("GET /pipeline")
    return templates.TemplateResponse(request, "pipeline.html", {
        "version": __version__,
    })


@app.get("/track")
async def track_page(request: Request):
    """切换默认轨道页面。"""
    logger.debug("GET /track")
    return templates.TemplateResponse(request, "track.html", {
        "version": __version__,
    })


@app.get("/audio")
async def audio_page(request: Request):
    """音频轨管理页面。"""
    logger.debug("GET /audio")
    return templates.TemplateResponse(request, "audio.html", {
        "version": __version__,
    })


@app.get("/api/health")
async def health():
    """健康检查 + 当前配置信息（JSON）。"""
    logger.debug("GET /api/health")
    return {
        "status": "ok",
        **settings.to_safe_dict(version=__version__),
    }


@app.get("/status")
async def status_fragment(request: Request):
    """系统状态 HTML 片段（HTMX 轮询）。"""
    logger.debug("GET /status")
    safe = settings.to_safe_dict(version=__version__)
    return templates.TemplateResponse(request, "_status.html", {
        "version": safe["version"],
        "selected_profile": safe["selected_profile"],
        "asr": safe["asr"],
        "tts": safe["tts"],
        "translate": safe["translate"],
    })
