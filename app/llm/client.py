from __future__ import annotations

import json
import base64
import os
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.config import load_settings


@dataclass(frozen=True)
class LLMJsonResponse:
    raw_text: str
    data: dict


class LLMClient:
    """Thin boundary for structured LLM calls.

    The concrete OpenAI implementation lands in the Generation/Debugging phase.
    """

    def complete_json(self, *, prompt: str, schema_name: str) -> LLMJsonResponse:
        raise NotImplementedError("LLM integration is implemented in the next phase.")

    def complete_json_with_image(
        self,
        *,
        prompt: str,
        image_path: str | Path,
        schema_name: str,
    ) -> LLMJsonResponse:
        raise NotImplementedError("Vision JSON completion is not implemented for this client.")


class OpenAIJsonClient(LLMClient):
    def __init__(self, settings: Settings | None = None, *, provider: str = "text") -> None:
        self.settings = settings or load_settings()
        if provider not in {"text", "vision"}:
            raise ValueError(f"Unsupported LLM provider role: {provider!r}")
        self.provider = provider

    @classmethod
    def for_vision(cls, settings: Settings | None = None) -> "OpenAIJsonClient":
        return cls(settings=settings, provider="vision")

    def _provider_config(self) -> tuple[str | None, str, str | None]:
        if self.provider == "vision":
            return (
                self.settings.vision_llm_api_key,
                self.settings.vision_llm_model or "",
                self.settings.vision_llm_base_url,
            )
        return (
            self.settings.openai_api_key,
            self.settings.openai_model,
            self.settings.openai_base_url,
        )

    def complete_json(self, *, prompt: str, schema_name: str) -> LLMJsonResponse:
        api_key, model, base_url = self._provider_config()
        if not api_key:
            raise RuntimeError(
                f"{self.provider.upper()} LLM API key is not configured. Add it to .env, "
                "or run with --mock-llm."
            )
        if not model:
            raise RuntimeError(f"{self.provider.upper()} LLM model is not configured in .env.")

        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError("openai package is not installed. Run: pip install -r requirements.txt") from exc

        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(
            **client_kwargs,
            timeout=float(os.getenv(f"{self.provider.upper()}_LLM_TIMEOUT_SECONDS", os.getenv("LLM_TIMEOUT_SECONDS", "90"))),
            max_retries=int(os.getenv(f"{self.provider.upper()}_LLM_MAX_RETRIES", os.getenv("LLM_MAX_RETRIES", "1"))),
        )
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You return only valid JSON. Do not include markdown fences, "
                            "comments, or explanatory text."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
        except Exception as exc:
            raise RuntimeError(
                _format_openai_error(
                    exc,
                    model=model,
                    base_url=base_url,
                )
            ) from exc
        raw_text = response.choices[0].message.content or ""
        return LLMJsonResponse(raw_text=raw_text, data=_parse_json_object(raw_text, schema_name))

    def complete_json_with_image(
        self,
        *,
        prompt: str,
        image_path: str | Path,
        schema_name: str,
    ) -> LLMJsonResponse:
        api_key, model, base_url = self._provider_config()
        if not api_key:
            raise RuntimeError(
                f"{self.provider.upper()} LLM API key is not configured. Add it to .env."
            )
        if not model:
            raise RuntimeError(f"{self.provider.upper()} LLM model is not configured in .env.")

        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError("openai package is not installed. Run: pip install -r requirements.txt") from exc

        image = Path(image_path).read_bytes()
        image_url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(
            **client_kwargs,
            timeout=float(os.getenv(f"{self.provider.upper()}_LLM_TIMEOUT_SECONDS", os.getenv("LLM_TIMEOUT_SECONDS", "90"))),
            max_retries=int(os.getenv(f"{self.provider.upper()}_LLM_MAX_RETRIES", os.getenv("LLM_MAX_RETRIES", "1"))),
        )
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You inspect browser screenshots and return only valid JSON. "
                            "Do not include markdown fences, comments, or explanatory text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
        except Exception as exc:
            raise RuntimeError(
                _format_openai_error(
                    exc,
                    model=model,
                    base_url=base_url,
                )
            ) from exc
        raw_text = response.choices[0].message.content or ""
        return LLMJsonResponse(raw_text=raw_text, data=_parse_json_object(raw_text, schema_name))


def _parse_json_object(raw_text: str, schema_name: str) -> dict:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM did not return valid JSON for {schema_name}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"LLM returned non-object JSON for {schema_name}.")
    return data


def _format_openai_error(exc: Exception, *, model: str, base_url: str | None) -> str:
    provider = base_url or "OpenAI default API"
    status_code = getattr(exc, "status_code", None)
    error_code = getattr(exc, "code", None)
    if status_code == 401 or error_code == "invalid_api_key":
        return (
            f"LLM authentication failed for {provider}: API key is invalid, expired, "
            "or does not belong to this provider. Please update .env."
        )
    if status_code == 404 or error_code == "model_not_found":
        return (
            f"LLM model is unavailable for {provider}: {model!r}. "
            "Set OPENAI_MODEL/DASHSCOPE_MODEL/DEEPSEEK_MODEL to a model available to your key."
        )
    if status_code:
        return f"LLM API request failed for {provider} with status {status_code}: {type(exc).__name__}"
    return f"LLM API request failed for {provider}: {type(exc).__name__}: {exc}"
