"""Pydantic request models."""

from typing import Any

from pydantic import BaseModel, Field


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="v1", max_length=64)


class CasesImport(BaseModel):
    text: str | None = None
    cases: list[dict[str, Any]] | None = None
    format: str = Field(default="auto", pattern="^(auto|json|jsonl)$")


class TargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern="^(mock|http|openai-compatible)$")
    config: dict[str, Any] = Field(default_factory=dict)


class ProbeRequest(BaseModel):
    input: str = Field(default="你好，请用一句话介绍你自己。", max_length=20000)


class RunCreate(BaseModel):
    dataset_id: int
    target_id: int
    concurrency: int = Field(default=4, ge=1, le=64)
    retries: int = Field(default=1, ge=0, le=5)
    timeout_s: float = Field(default=30.0, gt=0, le=600)
    judge: dict[str, Any] = Field(default_factory=lambda: {"kind": "rule", "mode": "mock"})
