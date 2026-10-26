from __future__ import annotations

from dataclasses import dataclass

import httpx

from melopet_backend.config import Settings
from melopet_backend.model_gateway import OpenAICompatibleGateway


@dataclass
class _Elapsed:
    def total_seconds(self) -> float:
        return 0.123


class _Response:
    def __init__(self, content: str):
        self._content = content
        self.elapsed = _Elapsed()

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": self._content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }


class _FakeClient:
    responses: list[str] = []
    requests: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url: str, *, headers: dict, json: dict):
        self.requests.append({"url": url, "headers": headers, "json": json})
        return _Response(self.responses.pop(0))


def _model_settings() -> Settings:
    return Settings(
        model_provider="openai_compatible",
        model_base_url="https://model.invalid/v1",
        model_api_key="test-key",
        mood_model_id="mood-test",
        copy_model_id="copy-test",
        model_daily_budget=1,
        model_input_cost_per_1m=2,
        model_output_cost_per_1m=4,
    )


def test_model_gateway_has_no_tools_and_validates_schema(monkeypatch):
    _FakeClient.requests = []
    _FakeClient.responses = [
        '{"status":"ok","task_type":"song_mood_classification","items":['
        '{"trackRef":"track_1","proposedPrimaryMoodId":"joy_bright","score":0.9,'
        '"evidenceSummary":["genre: pop"],"reasonCode":"MATCHED"}],"warnings":[],'
        '"promptVersion":"MP-MOOD-CLASSIFY-001@1.1.0"}'
    ]
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    gateway = OpenAICompatibleGateway(_model_settings())
    result = gateway.classify_moods(
        {
            "task_type": "song_mood_classification",
            "taxonomyVersion": "0.1",
            "taxonomy": [],
            "sourcePolicy": {"allowedSignalFields": ["genre"]},
            "tracks": [{"trackRef": "track_1", "signals": {"genre": "pop"}, "sourceRefs": []}],
        },
        idempotency_key="task_1:mood",
    )
    assert result.status == "ok"
    assert result.output is not None
    assert result.output.items[0].proposed_primary_mood_id == "joy_bright"
    assert result.estimated_cost == (100 * 2 + 20 * 4) / 1_000_000
    assert len(result.prompt_hash) == 64
    sent = _FakeClient.requests[0]["json"]
    assert sent["tools"] == []
    assert sent["temperature"] == 0
    assert _FakeClient.requests[0]["headers"]["Idempotency-Key"] == "task_1:mood"


def test_model_gateway_repairs_format_once_then_falls_back(monkeypatch):
    _FakeClient.requests = []
    _FakeClient.responses = ["not-json", "still-not-json"]
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    gateway = OpenAICompatibleGateway(_model_settings())
    result = gateway.generate_copy(
        {
            "task_type": "pet_copy_generation",
            "locale": "zh-CN",
            "moodProfile": {"percentages": {}, "unknownRate": 0},
            "petSpec": {"candidateId": "pet_1", "traitBindings": []},
            "limits": {"nameChars": 8, "introChars": 60, "lineChars": 24, "lineCount": 5},
        }
    )
    assert result.output is None
    assert result.status.startswith("fallback:")
    assert len(_FakeClient.requests) == 2
    assert "只修正格式" in _FakeClient.requests[1]["json"]["messages"][-1]["content"]
