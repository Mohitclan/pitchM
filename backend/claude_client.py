"""
PitchMirror — Claude (Anthropic API) for the text work.

Gemini keeps the live-audio jobs (speech-to-text, the practice-mode voice coach);
Claude does the text jobs, where the free Gemini tier kept hitting daily caps and
"high demand" errors:

  REPORT_MODEL (Claude Opus 5.5): post-session analysts, synthesis report,
                                  question-by-question review, skill profile
  FAST_MODEL   (Claude Haiku 5.5): live answer outlines and live conversation checks

Every caller falls back to its Gemini path when Claude is not configured, errors,
times out or refuses, so the app keeps working without an Anthropic key.
"""
from __future__ import annotations

import logging
import os

import anthropic

logger = logging.getLogger(__name__)

REPORT_MODEL = os.getenv("PITCHMIRROR_CLAUDE_REPORT_MODEL", "claude-opus-5-5").strip()
FAST_MODEL = os.getenv("PITCHMIRROR_CLAUDE_FAST_MODEL", "claude-haiku-5-5").strip()
REPORT_TIMEOUT_S = float(os.getenv("PITCHMIRROR_CLAUDE_REPORT_TIMEOUT_S", "150"))
FAST_TIMEOUT_S = float(os.getenv("PITCHMIRROR_CLAUDE_FAST_TIMEOUT_S", "8"))

# Server-side refusal fallback for Opus 5.5: if a safety classifier declines, the API
# re-runs the request on a suitable model inside the same call.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.AsyncAnthropic | None = None


class ClaudeUnavailable(Exception):
    """Claude returned nothing usable (refusal, empty text, or not configured)."""


def claude_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip()) and os.getenv("PITCHMIRROR_USE_CLAUDE", "true").lower() not in {"0", "false", "no"}


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        headers = {}
        workspace = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
        if workspace:
            # The key is a user key not scoped to one workspace; name the workspace explicitly.
            headers["anthropic-workspace-id"] = workspace
        # Fallback paths exist, so don't let the SDK spend long on its own retries.
        _client = anthropic.AsyncAnthropic(default_headers=headers or None, max_retries=1)
    return _client


def _text_of(response) -> str:
    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None) if response.stop_details else None
        raise ClaudeUnavailable(f"refusal ({category})")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ClaudeUnavailable(f"empty response (stop_reason={response.stop_reason})")
    return text


async def report_text(system: str, user: str, *, max_tokens: int = 16000) -> str:
    """Long-form analysis on the report model (adaptive thinking, medium effort)."""
    client = _get_client().with_options(timeout=REPORT_TIMEOUT_S)
    response = await client.beta.messages.create(
        model=REPORT_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium"},
        betas=[_FALLBACK_BETA],
        fallbacks="default",
    )
    usage = response.usage
    logger.info(
        "claude_report model=%s in=%s out=%s",
        response.model, getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"),
    )
    return _text_of(response)


async def fast_text(prompt: str, *, max_tokens: int = 400, timeout: float | None = None) -> str:
    """Short, latency-sensitive answers on the fast model (thinking off, low effort)."""
    client = _get_client().with_options(timeout=timeout or FAST_TIMEOUT_S)
    response = await client.messages.create(
        model=FAST_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "disabled"},
        output_config={"effort": "low"},
    )
    return _text_of(response)
