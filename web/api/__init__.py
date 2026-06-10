"""API 路由模块。

各功能路由（probe / subtitle / asr / tts / translate / pipeline）在此注册。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

# Stage 2: 流探测
from web.api.probe import router as probe_router  # noqa: E402

router.include_router(probe_router, tags=["probe"])

# Stage 3: 流提取
from web.api.extract import router as extract_router  # noqa: E402

router.include_router(extract_router, tags=["extract"])

# Stage 9: ASR 语音识别
from web.api.asr import router as asr_router  # noqa: E402

router.include_router(asr_router, tags=["asr"])

# 后续 Stage 在此追加：
# router.include_router(subtitle_router, tags=["subtitle"])
# ...
