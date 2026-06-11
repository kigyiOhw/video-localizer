"""远程 LLM 翻译引擎。

通过 OpenAI 兼容 API（/v1/chat/completions）调用远程 LLM 进行字幕翻译。
支持的 API 提供商：OpenAI、DeepSeek、Groq、Together AI 等。
"""

from __future__ import annotations

import json
import logging
from typing import Iterator

import httpx

from engines.translate.engine import TranslateEngine, TranslateSegment, lang_code_to_name

logger = logging.getLogger("video_localizer.translate.llm")

# ---------------------------------------------------------------------------
# 翻译 Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是一个专业的字幕翻译专家。请将以下字幕片段翻译成{target_language}。

要求：
1. 保持原意，表达自然，适合字幕显示（简洁、一目了然）
2. 只返回一个 JSON 数组，每个元素是一条翻译结果，顺序与输入严格一致
3. 不要添加任何解释、注释或额外文本
4. 示例格式：["译文1", "译文2", "译文3"]"""

_USER_PROMPT_TEMPLATE = """\
{numbered_lines}"""


class LLMTranslateEngine(TranslateEngine):
    """远程 LLM 翻译引擎（OpenAI 兼容 API）。

    通过 httpx 直接调用 /v1/chat/completions 端点，
    支持所有兼容 OpenAI API 的服务。
    """

    def __init__(
        self,
        api_base: str = "",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        temperature: float = 0.1,
        batch_size: int = 20,
    ) -> None:
        """初始化引擎（客户端延迟创建）。

        Args:
            api_base: OpenAI 兼容 API 地址（如 https://api.openai.com/v1）。
            api_key: API 密钥。空字符串时不发送 Authorization 头（用于 Ollama）。
            model: 模型名称。
            temperature: 翻译温度（0-2，越低越忠实原文）。
            batch_size: 每批翻译的片段数。
        """
        self._api_base = api_base.rstrip("/") if api_base else ""
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._batch_size = batch_size
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # 客户端
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.Client:
        """延迟创建 HTTP 客户端。"""
        if self._client is None:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.Client(
                base_url=self._api_base,
                headers=headers,
                timeout=60.0,
            )
        return self._client

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _build_system_prompt(self, target_language: str) -> str:
        """构建系统指令。

        Args:
            target_language: 目标语言名称（如 "Chinese"）。
        """
        return _SYSTEM_PROMPT.format(target_language=target_language)

    @staticmethod
    def _build_user_prompt(segments: list[TranslateSegment]) -> str:
        """构建用户消息 —— 编号的原文列表。

        Args:
            segments: 待翻译的片段。

        Returns:
            编号行文本，如 "[1] Hello\n[2] World"。
        """
        lines = [
            f"[{i + 1}] {seg.source_text}"
            for i, seg in enumerate(segments)
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 单批翻译
    # ------------------------------------------------------------------

    def _translate_batch(
        self,
        segments: list[TranslateSegment],
        target_language: str,
    ) -> list[str]:
        """翻译一个批次的片段。

        Args:
            segments: 当前批次的片段。
            target_language: 目标语言名称。

        Returns:
            翻译后的文本列表，顺序与输入一致。

        Raises:
            httpx.HTTPStatusError: API 返回错误状态码。
            ValueError: 响应无法解析。
        """
        client = self._get_client()
        if not self._api_base:
            raise ValueError(
                "未配置 API 地址 (translate.api_base)。"
                "请在 config/settings.local.yaml 中设置，"
                "或设置 OPENAI_API_KEY 环境变量后使用默认 API。"
            )

        system_prompt = self._build_system_prompt(target_language)
        user_prompt = self._build_user_prompt(segments)

        logger.debug("翻译批次: %d 条, 目标语言=%s, 模型=%s",
                     len(segments), target_language, self._model)

        response = client.post(
            "/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self._temperature,
            },
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"].strip()

        return self._parse_response(content, len(segments))

    def _parse_response(self, content: str, expected_count: int) -> list[str]:
        """解析 LLM 响应为翻译文本列表。

        优先尝试 JSON 数组解析，失败时降级为逐行解析。

        Args:
            content: LLM 返回的文本内容。
            expected_count: 期望的翻译条数。

        Returns:
            翻译文本列表，长度 = expected_count。
        """
        # 路径 1: JSON 数组
        try:
            # 去掉可能的 markdown 代码块包裹
            cleaned = content
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                # 去掉第一行（```json）和最后一行（```）
                cleaned = "\n".join(lines[1:-1])
            result = json.loads(cleaned)
            if isinstance(result, list) and all(isinstance(item, str) for item in result):
                # 补齐或截断到期望长度
                while len(result) < expected_count:
                    result.append("")
                return result[:expected_count]
        except (json.JSONDecodeError, ValueError):
            pass

        # 路径 2: 降级逐行解析
        logger.debug("JSON 解析失败，降级为逐行解析")
        lines = content.strip().split("\n")
        result: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # 尝试去掉行首编号 [N] 或 N.
            import re
            match = re.match(r"^(?:\[\d+\]\s*|\d+\.\s*|[-*]\s*)", stripped)
            if match:
                stripped = stripped[match.end():]
            # 去掉可能的引号包裹
            if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ('"', "'"):
                stripped = stripped[1:-1]
            result.append(stripped)

        while len(result) < expected_count:
            result.append("")
        return result[:expected_count]

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def translate(
        self,
        segments: list[TranslateSegment],
        target_language: str,
        source_language: str = "",
    ) -> list[TranslateSegment]:
        """同步批量翻译。

        Args:
            segments: 待翻译的片段列表。
            target_language: 目标语言名称或 ISO 639-3 代码。
            source_language: 源语言（未使用，保留接口兼容）。

        Returns:
            翻译后的 TranslateSegment 列表。
        """
        if not segments:
            return []

        # 将代码转为名称（如果传的是代码）
        lang_name = lang_code_to_name(target_language)

        result: list[TranslateSegment] = []
        for i in range(0, len(segments), self._batch_size):
            batch = segments[i:i + self._batch_size]
            translations = self._translate_batch(batch, lang_name)
            for seg, text in zip(batch, translations):
                result.append(TranslateSegment(
                    start=seg.start,
                    end=seg.end,
                    source_text=seg.source_text,
                    translated_text=text.strip() if text else "",
                ))

        logger.info("翻译完成: %d 条, 目标语言=%s", len(result), lang_name)
        return result

    def translate_stream(
        self,
        segments: list[TranslateSegment],
        target_language: str,
        source_language: str = "",
    ) -> Iterator[list[TranslateSegment]]:
        """流式翻译生成器 —— 逐批 yield。

        Args:
            segments: 待翻译的片段列表。
            target_language: 目标语言名称或 ISO 639-3 代码。
            source_language: 源语言（未使用）。

        Yields:
            每批翻译完成的 TranslateSegment 列表。
        """
        if not segments:
            return

        lang_name = lang_code_to_name(target_language)

        for i in range(0, len(segments), self._batch_size):
            batch = segments[i:i + self._batch_size]
            translations = self._translate_batch(batch, lang_name)
            yield [
                TranslateSegment(
                    start=seg.start,
                    end=seg.end,
                    source_text=seg.source_text,
                    translated_text=text.strip() if text else "",
                )
                for seg, text in zip(batch, translations)
            ]
