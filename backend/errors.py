"""
errors.py
─────────────────────────────────────────────────────────────────────────────
Centralised domain exception hierarchy for the Reddit Pain Point Miner.

All custom exceptions carry:
  - message  : human-readable description (shown to the user in the API response)
  - code     : machine-readable slug (used by the frontend to route error UX)
  - http_status : the HTTP status code the FastAPI handler should return

Design rationale
────────────────
Having a single module for all exceptions means:
1. The FastAPI exception handlers register them all in one place.
2. Pipeline code can raise a typed exception without importing FastAPI.
3. Tests can assert on the exception type, not on a string message.
"""

from __future__ import annotations


class PainMinerError(Exception):
    """Base class for all domain exceptions."""
    message: str = "An unexpected error occurred."
    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.__class__.message
        super().__init__(self.message)

    def to_dict(self) -> dict:
        """Serialise to the standard API error shape: {error, message, code}."""
        return {
            "error": True,
            "message": self.message,
            "code": self.code,
        }


class RedditFetchError(PainMinerError):
    """
    Raised when PRAW fails to fetch subreddits or threads after all retries
    (e.g. Reddit API is down, credentials revoked, or rate-limit exhausted).
    """
    message = "Failed to fetch data from Reddit after multiple retries."
    code = "reddit_fetch_error"
    http_status = 503  # Service Unavailable — the upstream (Reddit) is at fault


class ExtractionError(PainMinerError):
    """
    Raised when GPT-4o returns malformed JSON on two consecutive extraction
    attempts, making it impossible to produce structured pain points.
    """
    message = "GPT-4o returned invalid output twice in a row. Try again shortly."
    code = "extraction_error"
    http_status = 502  # Bad Gateway — the upstream (OpenAI) is misbehaving


class BudgetExceededError(PainMinerError):
    """
    Raised when the estimated OpenAI cost for a job exceeds MAX_COST_USD.
    Prevents runaway spending on unusually large or complex niches.
    """
    message = "This analysis exceeded the per-job cost limit. Try a more specific niche."
    code = "budget_exceeded"
    http_status = 402  # Payment Required — appropriate for a cost-limit violation


class NicheNotFoundError(PainMinerError):
    """
    Raised when zero relevant subreddits are discovered AND zero threads are
    found after retrying with a broader keyword. The niche is too obscure for
    Reddit to have meaningful discussions about it.
    """
    message = (
        "No relevant Reddit communities or discussions found for this niche. "
        "Try a broader term (e.g. 'CRM software' instead of 'Salesforce CPQ integrations')."
    )
    code = "niche_not_found"
    http_status = 404
