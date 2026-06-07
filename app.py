"""Video-Localizer — AI-powered video subtitle & audio track manager."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

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
# 配置加载（模块级别，失败立即报错）
# ---------------------------------------------------------------------------

from config import Settings
from config.requirements import auto_configure, check_minimum_requirements, detect_system_info

logger.info("=" * 50)
logger.info("Video-Localizer 启动中...")
logger.info("=" * 50)

# 加载配置
settings = Settings.load()
logger.info("配置加载完成")

# 硬件检测 + 自动选择配置档
info = detect_system_info()
profile_name = auto_configure(settings)
logger.info("选中配置档: %s", profile_name)

# 最低配置检查
warnings = check_minimum_requirements(settings)
if warnings:
    for w in warnings:
        logger.warning("⚠ %s", w)

# 版本号
_version_path = Path(__file__).resolve().parent / "__init__.py"
__version__ = "0.1.0"
if _version_path.exists():
    try:
        _scope: dict[str, str] = {}
        exec(_version_path.read_text(encoding="utf-8"), _scope)
        __version__ = _scope.get("__version__", __version__)
    except Exception:
        logger.debug("无法读取版本号", exc_info=True)


# ---------------------------------------------------------------------------
# 应用与模板
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory="templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时确保目录存在。"""
    settings.ensure_dirs()
    logger.info("所有目录就绪")
    yield


app = FastAPI(
    title="Video-Localizer",
    description="AI-powered video subtitle & audio track manager",
    version=__version__,
    lifespan=lifespan,
)

# 静态文件
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


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
