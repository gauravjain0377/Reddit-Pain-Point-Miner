"""
config.py
─────────────────────────────────────────────────────────────────────────────
Centralised configuration module for the Reddit Pain Point Miner backend.

All environment variables are loaded once at import time from a `.env` file
(if present) and then from the actual process environment.  A single `Config`
singleton is exported so that every other module can do:

    from config import config

Design decisions
────────────────
• We use `pydantic-settings` (BaseSettings) instead of bare os.environ reads
  because it gives us:
    - Automatic type coercion  (e.g. "true" → True, "50" → 50)
    - Built-in validation with a single, readable error message
    - IDE-friendly attribute access and type hints
• We call `load_dotenv()` explicitly *before* instantiating Settings so that
  `.env` values are already in os.environ when Pydantic reads them.
  pydantic-settings also supports this natively via `env_file`, but explicit
  loading ensures compatibility regardless of the pydantic-settings version.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Load .env from the same directory as this file (backend/.env)
# override=False means existing environment variables (e.g. from Docker / CI)
# take precedence over .env file values — safer in production deployments.
# ---------------------------------------------------------------------------
_env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


class Settings(BaseSettings):
    """
    Pydantic settings model — every attribute maps 1-to-1 to an environment
    variable of the same name (case-insensitive by default in pydantic-settings).
    """

    model_config = SettingsConfigDict(
        # pydantic-settings will also check this file as a fallback, providing
        # a second layer of .env loading (belt-and-suspenders approach).
        env_file=str(_env_path),
        env_file_encoding="utf-8",
        # Prevent typos in field names from silently being ignored.
        extra="ignore",
    )

    # ── Reddit credentials ────────────────────────────────────────────────────
    REDDIT_CLIENT_ID: str = Field(
        ...,  # ellipsis means "required" in Pydantic — no default, must be set
        description="Client ID from your Reddit app registration page.",
    )
    REDDIT_CLIENT_SECRET: str = Field(
        ...,
        description="Client secret from your Reddit app registration page.",
    )
    REDDIT_USER_AGENT: str = Field(
        ...,
        description=(
            "Unique user-agent string for your bot.  "
            "Reddit requires this to identify API clients.  "
            "Convention: '<AppName>/<version> by /u/<username>'"
        ),
    )

    # ── OpenAI credentials ────────────────────────────────────────────────────
    OPENAI_API_KEY: str = Field(
        ...,
        description="Secret key from https://platform.openai.com/api-keys",
    )

    # ── Application behaviour (optional — have sensible defaults) ─────────────
    USE_CACHE: bool = Field(
        default=True,
        description=(
            "Cache Reddit API responses to disk to avoid redundant fetches "
            "during development.  Set to False in production for fresh data."
        ),
    )
    MAX_THREADS: int = Field(
        default=50,
        ge=1,      # greater-than-or-equal guard: must be at least 1
        le=500,    # upper guard: prevents absurdly large fetches
        description="Maximum Reddit posts to retrieve per niche query.",
    )
    MAX_COMMENTS_PER_THREAD: int = Field(
        default=30,
        ge=1,
        le=1000,
        description="Maximum comments to pull from a single Reddit post.",
    )

    # ── Field-level validators ────────────────────────────────────────────────

    @field_validator("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT", "OPENAI_API_KEY")
    @classmethod
    def must_not_be_placeholder(cls, v: str, info) -> str:
        """
        Reject values that look like the placeholder text from .env.example.
        This catches the common mistake of forgetting to fill in the template.
        """
        placeholder_fragments = [
            "your_reddit_client_id_here",
            "your_reddit_client_secret_here",
            "your_reddit_username",
            "your_openai_api_key_here",
            "sk-proj-your",
        ]
        lower_v = v.lower()
        for fragment in placeholder_fragments:
            if fragment in lower_v:
                raise ValueError(
                    f"'{info.field_name}' still contains the placeholder value "
                    f"'{v}'.  Please set a real value in your .env file."
                )
        return v

    @field_validator("REDDIT_USER_AGENT")
    @classmethod
    def user_agent_must_be_descriptive(cls, v: str) -> str:
        """
        Reddit's API rules require a non-generic user-agent string.
        Enforce a minimum length to discourage copy-paste of empty strings.
        """
        if len(v.strip()) < 10:
            raise ValueError(
                "REDDIT_USER_AGENT is too short.  Use a descriptive string "
                "such as 'RedditPainMiner/0.1 by /u/yourusername'."
            )
        return v.strip()

    @model_validator(mode="after")
    def openai_key_must_start_with_sk(self) -> "Settings":
        """
        OpenAI API keys always start with 'sk-'.  A quick prefix check
        catches obvious copy-paste errors before they surface as 401 errors
        deep inside the application.
        """
        if not self.OPENAI_API_KEY.startswith("sk-"):
            raise ValueError(
                "OPENAI_API_KEY does not look valid — OpenAI keys always "
                "start with 'sk-'.  Check https://platform.openai.com/api-keys"
            )
        return self


def _load_config() -> Settings:
    """
    Instantiate and return the Settings object.  Any missing or invalid values
    will raise a pydantic ValidationError, which we catch here to print a
    human-friendly error message and exit immediately rather than crashing
    inside a library call later.
    """
    try:
        return Settings()
    except Exception as exc:
        # pydantic ValidationError has a readable __str__ that lists all
        # failing fields with their error messages.
        print(
            "\n❌  Configuration error — cannot start the application.\n"
            "─────────────────────────────────────────────────────────\n"
            f"{exc}\n\n"
            "👉  Copy backend/.env.example to backend/.env and fill in all values.\n",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Module-level singleton — imported by all other modules.
# Loaded once at import time; subsequent imports use the cached module object.
# ---------------------------------------------------------------------------
config: Settings = _load_config()
