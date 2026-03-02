from __future__ import annotations


class PipelineError(RuntimeError):
    """Base error for pipeline orchestration failures."""


class ConfigError(PipelineError):
    """Raised when required runtime configuration is missing or invalid."""


class OpenAIResponseError(PipelineError):
    """Raised when OpenAI responses are malformed or unusable."""


class ValidationError(PipelineError):
    """Raised when input/output artifacts fail contract validation."""
