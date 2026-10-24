"""Structured review output, shared by every review backend."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReviewResult(BaseModel):
    same_event: Literal["yes", "no"] = Field(description="Do both contracts pay $1 on the same real-world outcome?")
    settlement_differences: list[str] = Field(
        default_factory=list,
        description="Every way the two contracts could settle differently (ties, postponement, cancellation, sources).",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence the pair is safe to compare as one outcome.")
    reason: str = Field(description="One or two sentences.")


class ReviewRecord(ReviewResult):
    match_id: int
