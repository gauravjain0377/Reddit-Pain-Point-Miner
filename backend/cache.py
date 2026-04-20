"""
cache.py
─────────────────────────────────────────────────────────────────────────────
SQLite-backed cache for completed analysis results.

Why SQLite?
- Zero-dependency persistence (built into Python's stdlib)
- Survives server restarts, unlike an in-memory dict
- Single-file deployment — no Redis/Postgres to set up
- Sufficient for single-instance dev/staging deployments

Schema
──────
    analysis_cache
        id          INTEGER PRIMARY KEY AUTOINCREMENT
        cache_key   TEXT UNIQUE     ← SHA-256 of normalised niche string
        niche       TEXT            ← original niche (for human readability)
        result_json TEXT            ← JSON-serialised final_report dict
        created_at  TIMESTAMP
        expires_at  TIMESTAMP       ← created_at + TTL (default 24 hours)

All datetimes stored as ISO-8601 strings in UTC.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants — easy to override per deployment
# ---------------------------------------------------------------------------
_DB_PATH = Path(__file__).parent / "analysis_cache.db"
_DEFAULT_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# CacheStats — returned by get_stats() for the health endpoint
# ---------------------------------------------------------------------------

@dataclass
class CacheStats:
    hit_rate: float             # 0.0–1.0; populated externally by the API layer
    total_entries: int          # Live (non-expired) entries in the DB
    oldest_entry_age_hours: float  # Age of the oldest live entry, in hours


# ---------------------------------------------------------------------------
# AnalysisCache
# ---------------------------------------------------------------------------

class AnalysisCache:
    """
    Thread-safe SQLite cache for analysis results.

    Thread safety:
        SQLite supports multiple readers and one writer.  We use
        check_same_thread=False and rely on the GIL for the single-process
        FastAPI app.  For a multi-process deployment, switch to WAL mode or
        a proper cache store (Redis).

    Usage::
        cache = AnalysisCache()
        result = cache.get("email marketing")
        if result is None:
            result = run_pipeline(...)
            cache.set("email marketing", result)
    """

    def __init__(self, db_path: Path = _DB_PATH, ttl_hours: int = _DEFAULT_TTL_HOURS) -> None:
        self._db_path = db_path
        self._ttl = timedelta(hours=ttl_hours)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # dict-like row access
        self._init_schema()
        logger.info("AnalysisCache initialised at %s (TTL=%dh)", db_path, ttl_hours)

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Create the table if it doesn't already exist (idempotent)."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key   TEXT UNIQUE NOT NULL,
                niche       TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL
            )
        """)
        # Index on cache_key for O(1) lookups and expires_at for cleanup sweeps
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_key ON analysis_cache(cache_key)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_expires_at ON analysis_cache(expires_at)"
        )
        self._conn.commit()

    # ── Cache key ─────────────────────────────────────────────────────────────

    @staticmethod
    def _make_key(niche: str) -> str:
        """
        Normalise the niche string and return its SHA-256 hex digest.

        Normalisation (lowercase + strip) ensures "CRM Software" and
        "crm software" hit the same cache entry.
        """
        normalised = niche.lower().strip()
        return hashlib.sha256(normalised.encode()).hexdigest()

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, niche: str) -> Optional[dict]:
        """
        Look up a cached result for the given niche.

        Returns the deserialized result dict if a non-expired entry exists,
        or None if there is no entry or the entry has expired.

        Expired entries are NOT deleted on read (lazy expiry) — cleanup_expired()
        handles bulk removal.
        """
        key = self._make_key(niche)
        now = datetime.now(timezone.utc).isoformat()

        row = self._conn.execute(
            "SELECT result_json FROM analysis_cache WHERE cache_key = ? AND expires_at > ?",
            (key, now),
        ).fetchone()

        if row is None:
            logger.debug("Cache MISS for niche='%s'", niche)
            return None

        logger.info("Cache HIT for niche='%s'", niche)
        return json.loads(row["result_json"])

    def set(self, niche: str, result: dict) -> None:
        """
        Store or replace a result for the given niche.

        Uses INSERT OR REPLACE so repeat runs for the same niche refresh
        the TTL clock rather than accumulating stale rows.
        """
        key = self._make_key(niche)
        now = datetime.now(timezone.utc)
        expires = now + self._ttl

        self._conn.execute(
            """
            INSERT OR REPLACE INTO analysis_cache
                (cache_key, niche, result_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                key,
                niche,
                json.dumps(result, default=str),
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        self._conn.commit()
        logger.info(
            "Cache SET for niche='%s' (expires %s UTC)",
            niche,
            expires.strftime("%Y-%m-%d %H:%M"),
        )

    def invalidate(self, niche: str) -> bool:
        """
        Delete the cache entry for the given niche, if one exists.

        Returns True if an entry was deleted, False if nothing was found.
        This is exposed as the DELETE /api/cache/{niche} endpoint.
        """
        key = self._make_key(niche)
        cursor = self._conn.execute(
            "DELETE FROM analysis_cache WHERE cache_key = ?", (key,)
        )
        self._conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Cache INVALIDATED for niche='%s'", niche)
        else:
            logger.debug("Cache invalidate: no entry found for niche='%s'", niche)
        return deleted

    def cleanup_expired(self) -> int:
        """
        Delete all expired entries from the cache table.

        Returns the number of rows deleted.
        Call this periodically (e.g. on startup, or via a background task)
        to prevent the SQLite file from growing unboundedly.
        """
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM analysis_cache WHERE expires_at <= ?", (now,)
        )
        self._conn.commit()
        count = cursor.rowcount
        if count:
            logger.info("Cache cleanup: removed %d expired entries.", count)
        return count

    def get_stats(self) -> CacheStats:
        """
        Return aggregate statistics about the current live cache entries.

        hit_rate is always 0.0 here — the caller (API layer) is responsible for
        maintaining a hit/miss counter and passing it in if needed.  We keep the
        DB layer focused on storage, not operational metrics.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Count of non-expired entries
        total_row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM analysis_cache WHERE expires_at > ?", (now,)
        ).fetchone()
        total = total_row["cnt"] if total_row else 0

        # Age of the oldest live entry
        oldest_row = self._conn.execute(
            "SELECT MIN(created_at) as oldest FROM analysis_cache WHERE expires_at > ?",
            (now,),
        ).fetchone()
        oldest_age_hours = 0.0
        if oldest_row and oldest_row["oldest"]:
            oldest_dt = datetime.fromisoformat(oldest_row["oldest"])
            if oldest_dt.tzinfo is None:
                oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
            oldest_age_hours = (
                datetime.now(timezone.utc) - oldest_dt
            ).total_seconds() / 3600

        return CacheStats(
            hit_rate=0.0,
            total_entries=total,
            oldest_entry_age_hours=round(oldest_age_hours, 2),
        )

    def close(self) -> None:
        """Explicitly close the SQLite connection (useful for testing teardown)."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton — shared by all API routes via FastAPI dependency
# ---------------------------------------------------------------------------
cache: AnalysisCache = AnalysisCache()
