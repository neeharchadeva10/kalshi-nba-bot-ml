"""Review backends.

- SessionBackend: Phase 1. Packets are written to review/review_packets/, reviewed by
  Claude Code inside the working session (no API calls), and results are imported from
  review/review_results/.
- ClaudeAPIBackend: live system. Calls the Claude API with structured output. Refuses to
  run unless matcher.allow_paid_api is true in config (paid service: ask first).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from kpm.agents.matcher.models import ReviewResult
from kpm.agents.matcher.prompt import SYSTEM_PROMPT, user_message


class ReviewBackend(Protocol):
    name: str

    def review(self, pkt: dict) -> ReviewResult: ...


class SessionBackend:
    """Offline exchange: export packets now, import results later."""
    name = "claude-code-session"

    def __init__(self, packet_dir: Path):
        self.packet_dir = packet_dir

    def export(self, packets: list[dict], batch_size: int) -> list[Path]:
        self.packet_dir.mkdir(parents=True, exist_ok=True)
        for old in self.packet_dir.glob("batch_*.jsonl"):  # packets are regenerable; never mix exports
            old.unlink()
        paths = []
        for i in range(0, len(packets), batch_size):
            p = self.packet_dir / f"batch_{i // batch_size:03d}.jsonl"
            with open(p, "w", encoding="utf-8") as fh:
                for pkt in packets[i:i + batch_size]:
                    fh.write(json.dumps(pkt, ensure_ascii=False) + "\n")
            paths.append(p)
        return paths

    def review(self, pkt: dict) -> ReviewResult:
        raise NotImplementedError("Session reviews are done by Claude Code and imported with `kpm review-import`.")


class ClaudeAPIBackend:
    def __init__(self, model: str, allow_paid_api: bool):
        if not allow_paid_api:
            raise PermissionError("Claude API reviews are a paid service; set matcher.allow_paid_api: true "
                                  "in config/settings.yaml only after approving the cost.")
        import anthropic  # optional dependency: pip install -e ".[claude]"

        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env/.env
        self.model = model
        self.name = f"claude-api:{model}"

    def review(self, pkt: dict) -> ReviewResult:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message(pkt)}],
            output_format=ReviewResult,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            # Fail safe: an unusable review routes the pair to manual review.
            return ReviewResult(same_event="no", settlement_differences=["automated review unavailable"],
                                confidence=0.0, reason=f"Claude API returned stop_reason={response.stop_reason}")
        return response.parsed_output
