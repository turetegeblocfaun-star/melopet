from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings
from .schemas import MoodClassificationOutput, PetCopyOutput

T = TypeVar("T", bound=BaseModel)


@dataclass(slots=True)
class ModelCallResult:
    output: BaseModel | None
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    latency_ms: int
    status: str


@dataclass(slots=True)
class ImageCallResult:
    image_bytes: bytes | None
    provider: str
    model: str
    status: str


class ModelGateway:
    def classify_moods(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> ModelCallResult:
        del payload, idempotency_key
        return ModelCallResult(None, "disabled", "rule-only", "none", "none", 0, 0, 0, 0, "disabled")

    def generate_copy(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> ModelCallResult:
        del payload, idempotency_key
        return ModelCallResult(None, "disabled", "template", "none", "none", 0, 0, 0, 0, "disabled")

    def generate_image(self, prompt: str, *, size: str = "1024x1024", idempotency_key: str | None = None) -> ImageCallResult:
        del prompt, size, idempotency_key
        return ImageCallResult(None, "disabled", "rule-only", "disabled")


class OpenAICompatibleGateway(ModelGateway):
    def __init__(self, settings: Settings):
        self.settings = settings

    def _call(
        self,
        *,
        model_id: str,
        prompt_file: str,
        prompt_version: str,
        payload: dict[str, Any],
        output_type: type[T],
        idempotency_key: str | None,
    ) -> ModelCallResult:
        prompt = (self.settings.prompts_path / prompt_file).read_text(encoding="utf-8")
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        last_error = "invalid_output"
        for attempt in range(self.settings.model_max_retries + 1):
            if attempt:
                messages.append(
                    {
                        "role": "user",
                        "content": "上一响应未通过 JSON Schema。只修正格式并输出合法 JSON；不要新增事实。",
                    }
                )
            try:
                with httpx.Client(timeout=self.settings.model_timeout_seconds) as client:
                    headers = {"Authorization": f"Bearer {self.settings.model_api_key}"}
                    if idempotency_key:
                        headers["Idempotency-Key"] = idempotency_key
                    response = client.post(
                        f"{self.settings.model_base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json={
                            "model": model_id,
                            "messages": messages,
                            "temperature": 0,
                            "response_format": {"type": "json_object"},
                            "tools": [],
                        },
                    )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                output = output_type.model_validate_json(content)
                if getattr(output, "prompt_version", None) != prompt_version:
                    last_error = "prompt_version_mismatch"
                    continue
                usage = body.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens", 0))
                output_tokens = int(usage.get("completion_tokens", 0))
                estimated_cost = (
                    input_tokens * self.settings.model_input_cost_per_1m + output_tokens * self.settings.model_output_cost_per_1m
                ) / 1_000_000
                return ModelCallResult(
                    output,
                    "openai_compatible",
                    model_id,
                    prompt_version,
                    prompt_hash,
                    input_tokens,
                    output_tokens,
                    estimated_cost,
                    int(response.elapsed.total_seconds() * 1000),
                    "ok",
                )
            except (
                httpx.HTTPError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
                ValidationError,
            ) as exc:
                last_error = type(exc).__name__
        return ModelCallResult(
            None,
            "openai_compatible",
            model_id,
            prompt_version,
            prompt_hash,
            0,
            0,
            0,
            0,
            f"fallback:{last_error}",
        )

    def classify_moods(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> ModelCallResult:
        return self._call(
            model_id=self.settings.mood_model_id,
            prompt_file=f"{self.settings.prompt_mood_version}.txt",
            prompt_version=self.settings.prompt_mood_version,
            payload=payload,
            output_type=MoodClassificationOutput,
            idempotency_key=idempotency_key,
        )

    def generate_copy(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> ModelCallResult:
        return self._call(
            model_id=self.settings.copy_model_id,
            prompt_file=f"{self.settings.prompt_copy_version}.txt",
            prompt_version=self.settings.prompt_copy_version,
            payload=payload,
            output_type=PetCopyOutput,
            idempotency_key=idempotency_key,
        )

    def generate_image(self, prompt: str, *, size: str = "1024x1024", idempotency_key: str | None = None) -> ImageCallResult:
        provider = self.settings.image_provider
        if not self.settings.image_enabled:
            return ImageCallResult(None, provider, self.settings.image_model_id, "disabled")
        key = self.settings.image_api_key or self.settings.model_api_key
        if provider == "openai_compatible":
            return self._generate_image_openai(prompt, size, key, idempotency_key)
        if provider == "dashscope":
            return self._generate_image_dashscope(prompt, size, key, idempotency_key)
        return ImageCallResult(None, provider, self.settings.image_model_id, "fallback")

    def _generate_image_openai(
        self,
        prompt: str,
        size: str,
        key: str,
        idempotency_key: str | None,
    ) -> ImageCallResult:
        headers = {"Authorization": f"Bearer {key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(timeout=self.settings.model_timeout_seconds) as client:
                response = client.post(
                    f"{self.settings.model_base_url.rstrip('/')}/images/generations",
                    headers=headers,
                    json={
                        "model": self.settings.image_model_id,
                        "prompt": prompt,
                        "n": 1,
                        "size": size,
                        "response_format": "b64_json",
                    },
                )
                response.raise_for_status()
                body = response.json()
                item = body["data"][0]
                encoded = item.get("b64_json")
                if encoded:
                    return ImageCallResult(base64.b64decode(encoded), "openai_compatible", self.settings.image_model_id, "ok")
                url = item.get("url")
                if url:
                    image_response = httpx.get(url, timeout=self.settings.model_timeout_seconds)
                    image_response.raise_for_status()
                    return ImageCallResult(image_response.content, "openai_compatible", self.settings.image_model_id, "ok")
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            pass
        return ImageCallResult(None, "openai_compatible", self.settings.image_model_id, "fallback")

    def _generate_image_dashscope(self, prompt: str, size: str, key: str, idempotency_key: str | None) -> ImageCallResult:
        del idempotency_key
        base = self.settings.image_base_url.rstrip("/")
        size = size.replace("x", "*")  # DashScope native API expects 1024*1024
        headers = {"Authorization": f"Bearer {key}", "X-DashScope-Async": "enable"}
        try:
            with httpx.Client(timeout=self.settings.model_timeout_seconds) as client:
                submit = client.post(
                    f"{base}/api/v1/services/aigc/text2image/image-synthesis",
                    headers=headers,
                    json={
                        "model": self.settings.image_model_id,
                        "input": {"prompt": prompt},
                        "parameters": {"size": size, "n": 1},
                    },
                )
                submit.raise_for_status()
                task_id = submit.json()["output"]["task_id"]
            for _attempt in range(40):
                time.sleep(2)
                with httpx.Client(timeout=self.settings.model_timeout_seconds) as client:
                    task = client.get(f"{base}/api/v1/tasks/{task_id}", headers={"Authorization": f"Bearer {key}"})
                    task.raise_for_status()
                    status = task.json().get("output", {}).get("task_status")
                if status == "SUCCEEDED":
                    url = task.json()["output"]["results"][0]["url"]
                    image_response = httpx.get(url, timeout=self.settings.model_timeout_seconds)
                    image_response.raise_for_status()
                    return ImageCallResult(image_response.content, "dashscope", self.settings.image_model_id, "ok")
                if status in ("FAILED", "CANCELED"):
                    return ImageCallResult(None, "dashscope", self.settings.image_model_id, "fallback")
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            pass
        return ImageCallResult(None, "dashscope", self.settings.image_model_id, "fallback")


def build_model_gateway(settings: Settings) -> ModelGateway:
    if settings.model_provider == "disabled":
        return ModelGateway()
    if settings.model_provider == "openai_compatible" and settings.model_enabled:
        return OpenAICompatibleGateway(settings)
    if settings.model_provider == "openai_compatible":
        raise ValueError("MODEL_CONFIGURATION_INCOMPLETE")
    raise ValueError("MODEL_PROVIDER_UNSUPPORTED")
