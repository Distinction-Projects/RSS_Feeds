from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from .cache_sqlite import SQLiteOpenAICache
from .errors import OpenAIResponseError


@dataclass(slots=True)
class OpenAIResult:
    parsed: dict[str, Any]
    response_id: str | None
    usage: dict[str, Any] | None
    cache_hit: bool
    cache_key: str
    request_hash: str
    prompt_hash: str
    user_prompt_hash: str
    response_hash: str
    latency_ms: int


@dataclass(slots=True)
class PromptAuditRecord:
    run_id: str
    purpose: str
    model: str
    cache_key: str
    prompt_ref: str
    prompt_hash: str
    prompt_body: str
    response_ref: str
    response_hash: str
    response_body: str
    article_id: str | None = None
    lens_name: str | None = None
    rubric_name: str | None = None


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise OpenAIResponseError("OpenAI returned empty content.")

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        return parsed

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate

    raise OpenAIResponseError("Could not parse a JSON object from OpenAI content.")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif hasattr(item, "text") and isinstance(item.text, str):
                parts.append(item.text)
        return "\n".join(parts)
    return str(content or "")


def usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        dumped = usage.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(usage, "dict"):
        dumped = usage.dict()
        if isinstance(dumped, dict):
            return dumped
    return {"value": str(usage)}


class OpenAIService:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: int,
        cache: SQLiteOpenAICache | None,
    ):
        self.client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self.cache = cache

    def chat_json(
        self,
        *,
        run_id: str,
        purpose: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        metadata: dict[str, Any] | None = None,
    ) -> OpenAIResult:
        request_payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        request_json = _stable_json(request_payload)
        request_hash = hash_text(request_json)
        cache_key = hash_text(f"{model}:{request_hash}")

        prompt_hash = hash_text(messages[0].get("content", "") if messages else "")
        user_prompt_hash = hash_text(messages[1].get("content", "") if len(messages) > 1 else "")

        if self.cache is not None:
            cached = self.cache.get_cached(cache_key)
            if isinstance(cached, dict):
                parsed_cached = cached.get("parsed")
                if isinstance(parsed_cached, dict):
                    response_hash = hash_text(_stable_json(parsed_cached))
                    self.cache.log_openai_call(
                        run_id=run_id,
                        purpose=purpose,
                        model=model,
                        cache_key=cache_key,
                        cache_hit=True,
                        request_hash=request_hash,
                        prompt_hash=prompt_hash,
                        user_prompt_hash=user_prompt_hash,
                        response_hash=response_hash,
                        latency_ms=0,
                        error=None,
                    )
                    if metadata:
                        self._write_prompt_audit(
                            run_id=run_id,
                            purpose=purpose,
                            model=model,
                            cache_key=cache_key,
                            messages=messages,
                            parsed=parsed_cached,
                            response_id=cached.get("response_id"),
                            metadata=metadata,
                        )
                    return OpenAIResult(
                        parsed=parsed_cached,
                        response_id=cached.get("response_id"),
                        usage=cached.get("usage")
                        if isinstance(cached.get("usage"), dict)
                        else None,
                        cache_hit=True,
                        cache_key=cache_key,
                        request_hash=request_hash,
                        prompt_hash=prompt_hash,
                        user_prompt_hash=user_prompt_hash,
                        response_hash=response_hash,
                        latency_ms=0,
                    )

        start = time.monotonic()
        response_hash = ""
        try:
            request_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
            result = self.client.chat.completions.create(**request_kwargs)
            choice = result.choices[0] if result.choices else None
            content = ""
            if choice is not None and choice.message is not None:
                content = _content_to_text(choice.message.content)
            parsed = _extract_json_object(content)
            response_hash = hash_text(_stable_json(parsed))
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - start) * 1000)
            if self.cache is not None:
                self.cache.log_openai_call(
                    run_id=run_id,
                    purpose=purpose,
                    model=model,
                    cache_key=cache_key,
                    cache_hit=False,
                    request_hash=request_hash,
                    prompt_hash=prompt_hash,
                    user_prompt_hash=user_prompt_hash,
                    response_hash="",
                    latency_ms=latency_ms,
                    error=f"{type(exc).__name__}: {exc}",
                )
            raise OpenAIResponseError(
                f"OpenAI request failed: {type(exc).__name__}: {exc}"
            ) from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        response_id = getattr(result, "id", None)
        usage = usage_to_dict(getattr(result, "usage", None))
        cache_payload = {
            "parsed": parsed,
            "response_id": response_id,
            "usage": usage,
        }

        if self.cache is not None:
            self.cache.set_cached(
                cache_key=cache_key,
                model=model,
                request_hash=request_hash,
                response_payload=cache_payload,
            )
            self.cache.log_openai_call(
                run_id=run_id,
                purpose=purpose,
                model=model,
                cache_key=cache_key,
                cache_hit=False,
                request_hash=request_hash,
                prompt_hash=prompt_hash,
                user_prompt_hash=user_prompt_hash,
                response_hash=response_hash,
                latency_ms=latency_ms,
                error=None,
            )
            if metadata:
                self._write_prompt_audit(
                    run_id=run_id,
                    purpose=purpose,
                    model=model,
                    cache_key=cache_key,
                    messages=messages,
                    parsed=parsed,
                    response_id=response_id,
                    metadata=metadata,
                )

        return OpenAIResult(
            parsed=parsed,
            response_id=response_id,
            usage=usage,
            cache_hit=False,
            cache_key=cache_key,
            request_hash=request_hash,
            prompt_hash=prompt_hash,
            user_prompt_hash=user_prompt_hash,
            response_hash=response_hash,
            latency_ms=latency_ms,
        )

    def _write_prompt_audit(
        self,
        *,
        run_id: str,
        purpose: str,
        model: str,
        cache_key: str,
        messages: list[dict[str, str]],
        parsed: dict[str, Any],
        response_id: str | None,
        metadata: dict[str, Any],
    ) -> None:
        if self.cache is None:
            return

        system_prompt = messages[0].get("content", "") if messages else ""
        user_prompt = messages[1].get("content", "") if len(messages) > 1 else ""
        prompt_body = _stable_json({"system": system_prompt, "user": user_prompt})
        prompt_hash = hash_text(prompt_body)
        response_body = _stable_json(parsed)
        response_hash = hash_text(response_body)

        self.cache.record_prompt_audit(
            run_id=run_id,
            purpose=purpose,
            model=model,
            cache_key=cache_key,
            prompt_ref=f"sqlite://prompt_audit/{run_id}/{cache_key}",
            prompt_hash=prompt_hash,
            prompt_body=prompt_body,
            response_ref=f"openai://chat.completions/{response_id or 'cached'}",
            response_hash=response_hash,
            response_body=response_body,
            article_id=_optional_text(metadata.get("article_id")),
            lens_name=_optional_text(metadata.get("lens_name")),
            rubric_name=_optional_text(metadata.get("rubric_name")),
        )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
