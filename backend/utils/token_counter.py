"""
utils/token_counter.py
─────────────────────────────────────────────────────────────────────────────
OpenAI token tracking and cost-budget enforcement for the Reddit Pain Point Miner.

Components
──────────
  track_tokens()       — context manager that wraps a single LLM call and
                         returns a TokenUsage record with token counts + cost.

  TokenBudget          — accumulates spend across an entire job run; raises
                         BudgetExceededException before any call that would
                         exceed the configured ceiling.

  BudgetExceededException — raised when the job's cumulative spend would
                            exceed MAX_COST_USD.

Cost constants (GPT-4o, as of mid-2024 — update here when prices change):
    Input:  $5.00 / 1M tokens
    Output: $15.00 / 1M tokens

Log format (costs.log):
    ISO timestamp | job_id | step | input_tokens | output_tokens | cost_usd
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost constants — single source of truth; update when OpenAI changes pricing.
# ---------------------------------------------------------------------------
GPT4O_INPUT_COST_PER_M = 5.00    # USD per 1,000,000 input tokens
GPT4O_OUTPUT_COST_PER_M = 15.00  # USD per 1,000,000 output tokens

# Default maximum spend per job run
DEFAULT_MAX_COST_USD = 1.00

# Path to the append-only costs log
_COSTS_LOG_PATH = Path(__file__).parent.parent / "costs.log"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    """Usage statistics for a single LLM call."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    step: str = ""          # Which pipeline node triggered this call
    job_id: str = ""        # For log correlation


@dataclass
class BudgetSummary:
    """Cumulative spend summary for a whole job run."""
    job_id: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    calls: list[TokenUsage] = field(default_factory=list)
    budget_usd: float = DEFAULT_MAX_COST_USD

    @property
    def remaining_budget_usd(self) -> float:
        return max(0.0, self.budget_usd - self.total_cost_usd)

    @property
    def budget_percent_used(self) -> float:
        if self.budget_usd <= 0:
            return 100.0
        return round(self.total_cost_usd / self.budget_usd * 100, 1)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BudgetExceededException(Exception):
    """
    Raised by TokenBudget.check_budget() before a call that would push
    the job's cumulative cost above the configured ceiling.

    Attributes
    ----------
    job_id          : The job this exception belongs to.
    current_cost    : Accumulated spend so far (USD).
    max_cost        : The configured ceiling (USD).
    """
    def __init__(self, job_id: str, current_cost: float, max_cost: float) -> None:
        self.job_id = job_id
        self.current_cost = current_cost
        self.max_cost = max_cost
        super().__init__(
            f"[job={job_id}] Budget exceeded: ${current_cost:.4f} spent of "
            f"${max_cost:.2f} limit. Halting to prevent runaway costs."
        )


# ---------------------------------------------------------------------------
# Cost calculation helper
# ---------------------------------------------------------------------------

def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    """
    Compute the estimated USD cost for a single GPT-4o call.

    Formula:
        cost = (input_tokens / 1_000_000 * INPUT_RATE)
             + (output_tokens / 1_000_000 * OUTPUT_RATE)
    """
    return (
        input_tokens / 1_000_000 * GPT4O_INPUT_COST_PER_M
        + output_tokens / 1_000_000 * GPT4O_OUTPUT_COST_PER_M
    )


# ---------------------------------------------------------------------------
# Cost log writer
# ---------------------------------------------------------------------------

def _log_cost(usage: TokenUsage) -> None:
    """
    Append a single cost record to costs.log (tab-separated for easy grep/awk).
    Writes to a file next to the backend root.  Failures are swallowed so a
    disk-write error never crashes the pipeline.
    """
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = (
            f"{timestamp}\t"
            f"job={usage.job_id}\t"
            f"step={usage.step}\t"
            f"input={usage.input_tokens}\t"
            f"output={usage.output_tokens}\t"
            f"cost=${usage.estimated_cost_usd:.6f}\n"
        )
        with open(_COSTS_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        logger.warning("Could not write to costs.log: %s", exc)


# ---------------------------------------------------------------------------
# track_tokens() — context manager for a single call
# ---------------------------------------------------------------------------

@contextmanager
def track_tokens(
    step: str = "",
    job_id: str = "",
    budget: Optional["TokenBudget"] = None,
) -> Generator[TokenUsage, None, None]:
    """
    Context manager that captures token usage from an LLM response object.

    Usage::
        with track_tokens(step="pain_extractor", job_id=job_id, budget=tb) as usage:
            response = llm.invoke(messages)
            usage.capture(response)    # call after invoke()

        print(usage.estimated_cost_usd)

    The TokenUsage object is yielded BEFORE the block runs (empty), so callers
    must call usage.capture(response) after the LLM call to populate it.

    If a TokenBudget is passed, check_budget() is called AFTER capturing usage
    to raise BudgetExceededException if the ceiling is crossed.
    """
    usage = TokenUsage(step=step, job_id=job_id)
    yield usage

    # Populate cost from captured usage
    usage.estimated_cost_usd = _calc_cost(usage.input_tokens, usage.output_tokens)
    usage.total_tokens = usage.input_tokens + usage.output_tokens

    _log_cost(usage)
    logger.debug(
        "[track_tokens] step=%s input=%d output=%d cost=$%.5f",
        step, usage.input_tokens, usage.output_tokens, usage.estimated_cost_usd,
    )

    # Register with budget and check ceiling
    if budget is not None:
        budget.record(usage)
        budget.check_budget()


# ---------------------------------------------------------------------------
# TokenBudget — job-level spend tracker
# ---------------------------------------------------------------------------

class TokenBudget:
    """
    Accumulates token usage and estimated cost across all LLM calls in one job.

    Raises BudgetExceededException after any call whose cumulative spend
    would exceed max_cost_usd.  The exception is raised AFTER the call
    completes (not before) so we don't cut off a call mid-flight — but we
    do prevent the NEXT call from starting.

    Usage::
        budget = TokenBudget(job_id="abc", max_cost_usd=1.00)
        for batch in batches:
            budget.check_budget()          # guard before each expensive call
            with track_tokens(..., budget=budget) as usage:
                response = llm.invoke(...)
                usage.capture(response)
    """

    def __init__(self, job_id: str, max_cost_usd: float = DEFAULT_MAX_COST_USD) -> None:
        self.job_id = job_id
        self.max_cost_usd = max_cost_usd
        self._summary = BudgetSummary(job_id=job_id, budget_usd=max_cost_usd)

    def record(self, usage: TokenUsage) -> None:
        """Accumulate one call's usage into the running total."""
        self._summary.total_input_tokens += usage.input_tokens
        self._summary.total_output_tokens += usage.output_tokens
        self._summary.total_cost_usd += usage.estimated_cost_usd
        self._summary.calls.append(usage)
        logger.info(
            "[TokenBudget] job=%s cumulative=%.4f/%.2f USD (%d%% used)",
            self.job_id,
            self._summary.total_cost_usd,
            self.max_cost_usd,
            self._summary.budget_percent_used,
        )

    def check_budget(self) -> None:
        """
        Raise BudgetExceededException if the ceiling has been crossed.
        Call this BEFORE expensive operations to act as a pre-flight guard.
        """
        if self._summary.total_cost_usd > self.max_cost_usd:
            raise BudgetExceededException(
                job_id=self.job_id,
                current_cost=self._summary.total_cost_usd,
                max_cost=self.max_cost_usd,
            )

    def capture_from_response(self, response: object, usage: TokenUsage) -> None:
        """
        Extract token counts from an OpenAI LangChain response object and
        populate the given TokenUsage record.

        LangChain's ChatOpenAI attaches usage in response_metadata["token_usage"].
        Falls back to zero if the key is absent (e.g. mocked responses in tests).
        """
        token_usage = (
            getattr(response, "response_metadata", {})
            .get("token_usage", {})
        )
        usage.input_tokens = token_usage.get("prompt_tokens", 0)
        usage.output_tokens = token_usage.get("completion_tokens", 0)

    @property
    def summary(self) -> BudgetSummary:
        return self._summary

    @property
    def total_cost_usd(self) -> float:
        return self._summary.total_cost_usd

    @property
    def total_tokens(self) -> int:
        return self._summary.total_input_tokens + self._summary.total_output_tokens
