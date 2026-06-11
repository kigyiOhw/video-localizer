"""翻译模块测试。"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from engines.translate.engine import (
    TranslateSegment,
    lang_code_to_name,
    srt_to_segments,
    translated_segments_to_srt,
)
from engines.translate.llm import LLMTranslateEngine


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

SAMPLE_SRT = """\
1
00:00:00,000 --> 00:00:02,000
Hello world

2
00:00:02,000 --> 00:00:04,500
How are you

3
00:00:04,500 --> 00:00:07,000
I am fine thank you
"""


def make_segments(n: int = 3) -> list[TranslateSegment]:
    """创建测试用翻译片段。"""
    texts = ["Hello world", "How are you", "I am fine thank you", "Good bye", "See you later"]
    return [
        TranslateSegment(
            start=i * 2.0,
            end=i * 2.0 + 1.5,
            source_text=texts[i % len(texts)],
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# TestTranslateSegment
# ---------------------------------------------------------------------------


class TestTranslateSegment:
    """TranslateSegment 数据类测试。"""

    def test_create(self) -> None:
        seg = TranslateSegment(0.0, 2.0, "Hello", "你好")
        assert seg.start == 0.0
        assert seg.end == 2.0
        assert seg.source_text == "Hello"
        assert seg.translated_text == "你好"

    def test_default_translated_text(self) -> None:
        seg = TranslateSegment(0.0, 1.0, "Hello")
        assert seg.translated_text == ""


# ---------------------------------------------------------------------------
# TestLangCodeToName
# ---------------------------------------------------------------------------


class TestLangCodeToName:
    """语言代码映射测试。"""

    def test_known_codes(self) -> None:
        assert lang_code_to_name("zho") == "Chinese"
        assert lang_code_to_name("eng") == "English"
        assert lang_code_to_name("jpn") == "Japanese"

    def test_unknown_code_returns_itself(self) -> None:
        assert lang_code_to_name("xyz") == "xyz"

    def test_empty_string(self) -> None:
        assert lang_code_to_name("") == ""


# ---------------------------------------------------------------------------
# TestTranslatedSegmentsToSRT
# ---------------------------------------------------------------------------


class TestTranslatedSegmentsToSRT:
    """translated_segments_to_srt 测试。"""

    def test_basic(self) -> None:
        segs = [
            TranslateSegment(0.0, 2.5, "Hello", "你好"),
            TranslateSegment(3.0, 5.5, "World", "世界"),
        ]
        srt = translated_segments_to_srt(segs)
        lines = srt.split("\n")
        assert lines[0] == "1"
        assert "00:00:00,000 --> 00:00:02,500" in lines[1]
        assert lines[2] == "你好"
        assert lines[3] == ""
        assert lines[4] == "2"

    def test_empty(self) -> None:
        assert translated_segments_to_srt([]) == ""

    def test_uses_translated_text(self) -> None:
        """SRT 使用 translated_text 字段，不是 source_text。"""
        segs = [TranslateSegment(0.0, 1.0, "original", "translated")]
        srt = translated_segments_to_srt(segs)
        assert "translated" in srt
        assert "original" not in srt

    def test_falls_back_to_source(self) -> None:
        """translated_text 为空时回退到 source_text。"""
        segs = [TranslateSegment(0.0, 1.0, "source only")]
        srt = translated_segments_to_srt(segs)
        assert "source only" in srt


# ---------------------------------------------------------------------------
# TestSRTParse
# ---------------------------------------------------------------------------


class TestSRTParse:
    """SRT 解析测试。"""

    def test_valid_srt(self) -> None:
        segments = srt_to_segments(SAMPLE_SRT)
        assert len(segments) == 3
        assert segments[0].source_text == "Hello world"
        assert segments[0].start == 0.0
        assert segments[1].source_text == "How are you"
        assert segments[2].source_text == "I am fine thank you"

    def test_empty_srt(self) -> None:
        assert srt_to_segments("") == []
        assert srt_to_segments("   ") == []

    def test_invalid_srt_raises(self) -> None:
        """无效 SRT 文本不抛异常但返回空列表。pysrt 对无效输入返回 0 条记录。"""
        segments = srt_to_segments("not valid srt content at all")
        assert segments == []


# ---------------------------------------------------------------------------
# TestLLMTranslateEngine
# ---------------------------------------------------------------------------


class TestLLMTranslateEngine:
    """LLMTranslateEngine 测试（mock httpx）。"""

    @pytest.fixture
    def engine(self) -> LLMTranslateEngine:
        return LLMTranslateEngine(
            api_base="https://api.test.com/v1",
            api_key="test-key",
            model="test-model",
            temperature=0.0,
            batch_size=2,
        )

    def _mock_response(self, content: str, status_code: int = 200):
        """创建 mock httpx 响应。"""
        resp = mock.Mock()
        resp.status_code = status_code
        resp.json.return_value = {
            "choices": [{"message": {"content": content}}],
        }
        resp.raise_for_status = mock.Mock()
        if status_code >= 400:
            resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        return resp

    def test_translate_json_array(self, engine: LLMTranslateEngine) -> None:
        """JSON 数组响应 → 正确解析。"""
        segs = make_segments(2)
        mock_client = mock.Mock()
        mock_client.post.return_value = self._mock_response('["你好世界", "你好吗"]')
        engine._client = mock_client

        result = engine.translate(segs, "Chinese")
        assert len(result) == 2
        assert result[0].translated_text == "你好世界"
        assert result[1].translated_text == "你好吗"

    def test_translate_line_fallback(self, engine: LLMTranslateEngine) -> None:
        """非 JSON 响应 → 降级逐行解析。"""
        segs = make_segments(2)
        mock_client = mock.Mock()
        mock_client.post.return_value = self._mock_response(
            "1. 你好世界\n2. 你好吗"
        )
        engine._client = mock_client

        result = engine.translate(segs, "Chinese")
        assert len(result) == 2
        assert result[0].translated_text == "你好世界"

    def test_translate_empty_segments(self, engine: LLMTranslateEngine) -> None:
        """空输入 → 空输出。"""
        result = engine.translate([], "Chinese")
        assert result == []

    def test_translate_stream(self, engine: LLMTranslateEngine) -> None:
        """流式翻译 → 逐批 yield。"""
        segs = make_segments(4)  # batch_size=2 → 2 batches
        mock_client = mock.Mock()
        mock_client.post.side_effect = [
            self._mock_response('["你好世界", "你好吗"]'),
            self._mock_response('["我很好谢谢", "再见"]'),
        ]
        engine._client = mock_client

        batches = list(engine.translate_stream(segs, "Chinese"))
        assert len(batches) == 2
        assert len(batches[0]) == 2
        assert len(batches[1]) == 2
        assert batches[0][0].translated_text == "你好世界"
        assert batches[1][1].translated_text == "再见"

    def test_translate_large_batch(self, engine: LLMTranslateEngine) -> None:
        """片段数超过 batch_size → 分批处理。"""
        segs = make_segments(5)  # batch_size=2 → 3 batches
        mock_client = mock.Mock()
        mock_client.post.side_effect = [
            self._mock_response('["a", "b"]'),
            self._mock_response('["c", "d"]'),
            self._mock_response('["e"]'),
        ]
        engine._client = mock_client

        result = engine.translate(segs, "Chinese")
        assert len(result) == 5
        assert mock_client.post.call_count == 3

    def test_no_api_base_raises(self, engine: LLMTranslateEngine) -> None:
        """未配置 api_base → ValueError。"""
        engine._api_base = ""
        with pytest.raises(ValueError, match="未配置 API 地址"):
            engine.translate(make_segments(1), "Chinese")

    def test_prompt_contains_target_language(self, engine: LLMTranslateEngine) -> None:
        """系统 prompt 包含目标语言。"""
        prompt = engine._build_system_prompt("Japanese")
        assert "Japanese" in prompt
        assert "字幕翻译" in prompt

    def test_user_prompt_format(self) -> None:
        """用户 prompt 是编号列表。"""
        segs = [
            TranslateSegment(0.0, 1.0, "Hello"),
            TranslateSegment(1.0, 2.0, "World"),
        ]
        prompt = LLMTranslateEngine._build_user_prompt(segs)
        assert "[1] Hello" in prompt
        assert "[2] World" in prompt

    def test_json_with_markdown_wrapper(self, engine: LLMTranslateEngine) -> None:
        """JSON 被 markdown 代码块包裹 → 正确剥离。"""
        result = engine._parse_response(
            '```json\n["a", "b"]\n```', 2,
        )
        assert result == ["a", "b"]

    def test_parse_response_pads_short(self, engine: LLMTranslateEngine) -> None:
        """返回条数不足 → 空字符串补齐。"""
        result = engine._parse_response('["a"]', 3)
        assert result == ["a", "", ""]

    def test_parse_response_truncates_long(self, engine: LLMTranslateEngine) -> None:
        """返回条数过多 → 截断。"""
        result = engine._parse_response('["a", "b", "c", "d"]', 2)
        assert result == ["a", "b"]

    def test_code_to_name_conversion(self, engine: LLMTranslateEngine) -> None:
        """ISO 639-3 代码自动转为语言名。"""
        segs = make_segments(1)
        mock_client = mock.Mock()
        mock_client.post.return_value = self._mock_response('["测试"]')
        engine._client = mock_client

        # 传 "zho" 代码，内部应转为 "Chinese"
        engine.translate(segs, "zho")
        call_args = mock_client.post.call_args[1]["json"]
        system_msg = call_args["messages"][0]["content"]
        assert "Chinese" in system_msg


# ---------------------------------------------------------------------------
# TestLLMLocalTranslateEngine
# ---------------------------------------------------------------------------


class TestLLMLocalTranslateEngine:
    """LLMLocalTranslateEngine 测试。"""

    @pytest.fixture
    def engine(self):
        from engines.translate.llm_local import LLMLocalTranslateEngine
        return LLMLocalTranslateEngine()

    def test_defaults(self, engine) -> None:
        """Ollama 默认参数正确。"""
        assert engine._api_base == "http://localhost:11434/v1"
        assert engine._api_key == ""
        assert engine._model == "qwen2.5:7b"
        assert engine._batch_size == 15

    def test_inherits_translate(self, engine) -> None:
        """继承父类翻译逻辑。"""
        segs = make_segments(1)
        mock_client = mock.Mock()
        mock_client.post.return_value = mock.Mock()
        mock_client.post.return_value.status_code = 200
        mock_client.post.return_value.json.return_value = {
            "choices": [{"message": {"content": '["测试"]'}}],
        }
        mock_client.post.return_value.raise_for_status = mock.Mock()
        engine._client = mock_client

        result = engine.translate(segs, "Chinese")
        assert len(result) == 1
        assert result[0].translated_text == "测试"

    def test_no_auth_header(self, engine) -> None:
        """Ollama 不发送 Authorization 头。"""
        client = engine._get_client()
        assert "Authorization" not in client.headers


# ---------------------------------------------------------------------------
# TestAPITranslate
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app_translate() -> FastAPI:
    """创建独立的测试 FastAPI app（含 translate 路由）。"""
    from fastapi.templating import Jinja2Templates

    app = FastAPI()

    template_dir = Path(__file__).resolve().parent.parent / "web" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))

    from web.api import router as api_router
    app.include_router(api_router)

    @app.get("/translate", response_model=None)
    async def translate_page(request: Request):
        return templates.TemplateResponse(request, "translate.html", {"version": "test"})

    return app


@pytest.fixture
def client_translate(test_app_translate: FastAPI) -> TestClient:
    return TestClient(test_app_translate)


class TestAPITranslate:
    """翻译 API 端点测试。"""

    @pytest.fixture
    def mock_engine(self):
        """Mock 翻译引擎。"""
        from engines.translate.engine import TranslateEngine

        engine = mock.Mock(spec=TranslateEngine)
        engine.translate.return_value = [
            TranslateSegment(0.0, 2.0, "Hello", "你好"),
            TranslateSegment(2.0, 4.0, "World", "世界"),
        ]
        engine.translate_stream.return_value = iter([
            [TranslateSegment(0.0, 2.0, "Hello", "你好")],
            [TranslateSegment(2.0, 4.0, "World", "世界")],
        ])
        return engine

    def test_get_translate_page(self, client_translate: TestClient) -> None:
        """GET /translate → HTML。"""
        resp = client_translate.get("/translate")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_post_missing_srt(self, client_translate: TestClient) -> None:
        """POST 无 SRT → 400。"""
        resp = client_translate.post("/api/translate")
        assert resp.status_code == 400

    def test_post_success_json(self, client_translate: TestClient, mock_engine) -> None:
        """POST 成功 → JSON。"""
        with mock.patch("web.api.translate._get_engine", return_value=mock_engine):
            resp = client_translate.post("/api/translate", data={
                "srt_text": SAMPLE_SRT,
                "target_language": "Chinese",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert len(data["segments"]) == 2
            assert data["segments"][0]["translated_text"] == "你好"

    def test_post_htmx_returns_html(self, client_translate: TestClient, mock_engine) -> None:
        """HX-Request 头 → HTML fragment。"""
        with mock.patch("web.api.translate._get_engine", return_value=mock_engine):
            resp = client_translate.post(
                "/api/translate",
                data={"srt_text": SAMPLE_SRT, "target_language": "Chinese"},
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "翻译完成" in resp.text

    def test_post_htmx_error_html(self, client_translate: TestClient, mock_engine) -> None:
        """HX-Request + 错误 → HTML 错误。"""
        mock_engine.translate.side_effect = ValueError("翻译失败")
        with mock.patch("web.api.translate._get_engine", return_value=mock_engine):
            resp = client_translate.post(
                "/api/translate",
                data={"srt_text": SAMPLE_SRT, "target_language": "Chinese"},
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "翻译失败" in resp.text

    def test_post_engine_error(self, client_translate: TestClient, mock_engine) -> None:
        """引擎错误 → 422。"""
        mock_engine.translate.side_effect = ValueError("API key invalid")
        with mock.patch("web.api.translate._get_engine", return_value=mock_engine):
            resp = client_translate.post("/api/translate", data={
                "srt_text": SAMPLE_SRT,
                "target_language": "Chinese",
            })
            assert resp.status_code == 422

    def test_translate_stream_sse(self, client_translate: TestClient, mock_engine) -> None:
        """SSE 流式 → 事件类型正确。

        progress 事件仅在翻译耗时超过 0.3s 心跳间隔时出现，
        mock 引擎瞬间返回，不会触发心跳。
        """
        with mock.patch("web.api.translate._get_engine", return_value=mock_engine):
            resp = client_translate.post("/api/translate/stream", data={
                "srt_text": SAMPLE_SRT,
                "target_language": "Chinese",
            })
            assert resp.status_code == 200
            text = resp.text
            assert "event: status" in text
            assert "event: translated" in text
            assert "event: done" in text

    def test_translate_stream_engine_error(self, client_translate: TestClient, mock_engine) -> None:
        """SSE 引擎错误 → error 事件。"""
        mock_engine.translate_stream.side_effect = ValueError("API error")
        with mock.patch("web.api.translate._get_engine", return_value=mock_engine):
            resp = client_translate.post("/api/translate/stream", data={
                "srt_text": SAMPLE_SRT,
                "target_language": "Chinese",
            })
            assert resp.status_code == 200
            assert "event: error" in resp.text
            assert "API error" in resp.text

    def test_srt_file_path(self, client_translate: TestClient, mock_engine, tmp_path: Path) -> None:
        """SRT 文件路径模式。"""
        srt_file = tmp_path / "test.srt"
        srt_file.write_text(SAMPLE_SRT, encoding="utf-8")

        with mock.patch("web.api.translate._get_engine", return_value=mock_engine):
            resp = client_translate.post("/api/translate", data={
                "srt_path": str(srt_file),
                "target_language": "Chinese",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
