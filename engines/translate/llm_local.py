"""本地 LLM 翻译引擎（Ollama）。

通过 Ollama 的 OpenAI 兼容端点（v0.1.24+）进行本地翻译。
无网络依赖，数据不离开本机。
"""

from __future__ import annotations

from engines.translate.llm import LLMTranslateEngine


class LLMLocalTranslateEngine(LLMTranslateEngine):
    """本地 Ollama 翻译引擎。

    继承 LLMTranslateEngine 的全部逻辑，覆盖默认参数为 Ollama 配置。
    Ollama 从 v0.1.24 起原生支持 OpenAI 兼容的 /v1/chat/completions 端点，
    因此无需额外的 ollama Python 包。
    """

    def __init__(
        self,
        api_base: str = "http://localhost:11434/v1",
        api_key: str = "",
        model: str = "qwen2.5:7b",
        temperature: float = 0.1,
        batch_size: int = 15,
    ) -> None:
        """初始化本地 Ollama 翻译引擎。

        Args:
            api_base: Ollama OpenAI 兼容端点地址。
            api_key: 空字符串（Ollama 不需要认证）。
            model: Ollama 模型名（推荐 Qwen 系列，中英日优秀）。
            temperature: 翻译温度。
            batch_size: 每批翻译片段数（本地模型窗口小，默认 15）。
        """
        super().__init__(
            api_base=api_base,
            api_key=api_key,
            model=model,
            temperature=temperature,
            batch_size=batch_size,
        )
