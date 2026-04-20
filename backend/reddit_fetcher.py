"""
reddit_fetcher.py
─────────────────────────────────────────────────────────────────────────────
Core Reddit data-fetching module for the Reddit Pain Point Miner.

This module is intentionally dependency-minimal (only PRAW + Pydantic) so it
can be imported and tested independently of the LangChain / LangGraph layers
that will be added in later iterations.

Architecture overview
─────────────────────
    RedditFetcher
        ├── discover_subreddits(niche)          → list[SubredditInfo]
        ├── fetch_threads(subreddits, niche)    → list[Thread]
        └── fetch_comments(thread_ids)          → list[Thread]  (with comments)

    Pydantic models
        Comment   — a single Reddit comment (top-level or reply)
        Thread    — a Reddit post, optionally with a list of Comment objects
        SubredditInfo — lightweight descriptor of a subreddit
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import praw
import prawcore.exceptions
from pydantic import BaseModel, Field

from config import config

# ---------------------------------------------------------------------------
# Logging — use the standard library so callers can configure it however they
# like (StreamHandler, FileHandler, JSON handler, etc.).
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic models
# =============================================================================

class Comment(BaseModel):
    """
    Represents a single Reddit comment (any depth level, though we only
    collect top-level and their direct replies = depth 0 and depth 1).

    Fields
    ──────
    comment_id  : Reddit's base-36 comment ID (e.g. "kzq3a7b")
    body        : Raw comment text.  Deleted/removed comments are filtered
                  upstream and never reach this model.
    score       : Net upvote count at fetch time.  Can be negative.
    depth       : 0 = direct reply to the post, 1 = reply to a top-level
                  comment.  We cap at depth 1 to keep data manageable.
    post_title  : Title of the parent Reddit post — denormalised here so each
                  Comment is self-contained and can be passed to the LLM
                  without needing to carry the parent Thread along.
    post_url    : Full Reddit URL of the parent post (e.g. reddit.com/r/…/…).
    """

    comment_id: str
    body: str
    score: int
    depth: int = Field(ge=0, le=1, description="0 = top-level, 1 = second-level")
    post_title: str
    post_url: str


class Thread(BaseModel):
    """
    Represents a Reddit post (submission) and its associated comments.

    Fields
    ──────
    post_id      : Reddit's base-36 submission ID (e.g. "t3_abc123").
    title        : Post title — often the richest pain-point signal.
    selftext     : Body text of the post (empty string for link posts).
    url          : Canonical Reddit URL of the post.
    subreddit    : Display name of the subreddit (without r/).
    score        : Net upvote count at fetch time.
    num_comments : Total comment count reported by Reddit (may exceed the
                   number of Comment objects we actually fetched, because we
                   apply max_comments_per_thread and skip deleted content).
    comments     : Flattened list of Comment objects (top-level + replies).
                   Empty list until fetch_comments() is called.
    """

    post_id: str
    title: str
    selftext: str
    url: str
    subreddit: str
    score: int
    num_comments: int
    comments: list[Comment] = Field(default_factory=list)


class SubredditInfo(BaseModel):
    """
    Lightweight descriptor of a subreddit — returned by discover_subreddits().
    """

    name: str           # Display name, e.g. "projectmanagement"
    title: str          # Full title, e.g. "Project Management"
    description: str    # Public description (truncated)
    subscribers: int    # Subscriber count at fetch time
    url: str            # Full Reddit URL, e.g. "/r/projectmanagement/"


# =============================================================================
# Retry decorator helpers
# =============================================================================

# Sentinel values that Reddit uses to mark deleted/removed content.
_DELETED_BODIES = {"[deleted]", "[removed]"}

# How long to wait (in seconds) between retry attempts.
# Attempt 0 (first failure) → 2 s, attempt 1 → 4 s, attempt 2 → 8 s.
_BACKOFF_DELAYS = [2, 4, 8]
_MAX_RETRIES = 3


def _call_with_backoff(fn, *args, **kwargs):
    """
    Call `fn(*args, **kwargs)` with exponential-backoff retry on
    `prawcore.exceptions.TooManyRequests`.

    Why a helper function instead of a decorator?
    Because we need to retry individual PRAW *iterator* exhaustion points
    (e.g. inside a for-loop over listing.hot()), not entire method calls.
    A helper is easier to inline at the exact PRAW call site.

    Raises the original exception if all retries are exhausted.
    """
    for attempt, delay in enumerate(_BACKOFF_DELAYS):
        try:
            return fn(*args, **kwargs)
        except prawcore.exceptions.TooManyRequests:
            if attempt == _MAX_RETRIES - 1:
                logger.error("Rate limit hit — all %d retries exhausted.", _MAX_RETRIES)
                raise
            logger.warning(
                "Reddit rate limit hit (attempt %d/%d).  Waiting %ds…",
                attempt + 1,
                _MAX_RETRIES,
                delay,
            )
            time.sleep(delay)
    # Unreachable — loop always re-raises on the last iteration.
    raise RuntimeError("Unexpected fall-through in _call_with_backoff")


# =============================================================================
# RedditFetcher
# =============================================================================

class RedditFetcher:
    """
    Encapsulates all Reddit data-fetching logic for a given product niche.

    Usage example
    ─────────────
        fetcher = RedditFetcher(niche="CRM software")
        subs    = fetcher.discover_subreddits("CRM software")
        threads = fetcher.fetch_threads(
                      subreddits=[s.name for s in subs],
                      niche="CRM software",
                      max_threads=config.MAX_THREADS,
                  )
        threads = fetcher.fetch_comments(
                      thread_ids=[t.post_id for t in threads],
                      max_comments_per_thread=config.MAX_COMMENTS_PER_THREAD,
                      threads_by_id={t.post_id: t for t in threads},
                  )
    """

    def __init__(
        self,
        niche: str,
        subreddits: Optional[list[str]] = None,
    ) -> None:
        """
        Parameters
        ──────────
        niche       : The product category or keyword the user is researching
                      (e.g. "project management software", "CRM software").
        subreddits  : Optional explicit list of subreddit names to search.
                      If None, `discover_subreddits()` should be called first.
        """
        self.niche = niche
        self.subreddits = subreddits or []

        # Build the PRAW read-only Reddit instance.
        # read_only=True means we never need user credentials (username /
        # password); we only use the app credentials (client_id / secret).
        # This is the correct OAuth flow for bots that only read public data.
        self._reddit = praw.Reddit(
            client_id=config.REDDIT_CLIENT_ID,
            client_secret=config.REDDIT_CLIENT_SECRET,
            user_agent=config.REDDIT_USER_AGENT,
            # Explicitly set read-only mode.  PRAW defaults to this when no
            # username/password is provided, but being explicit is clearer.
            ratelimit_seconds=60,  # Wait up to 60 s for rate-limit window reset
        )
        logger.info("RedditFetcher initialised for niche: '%s'", niche)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def discover_subreddits(self, niche: str, top_n: int = 5) -> list[SubredditInfo]:
        """
        Search Reddit for the most relevant subreddits for `niche` and return
        the top `top_n` by subscriber count.

        Why subscriber count?
        ─────────────────────
        PRAW's subreddits.search() returns results ordered by Reddit's own
        relevance ranking, but relevance doesn't guarantee a large, active
        community.  Sorting by subscriber count ensures we prioritise
        communities where there are enough users to surface meaningful pain
        points.  A niche community with 500 members often has richer signal
        than a generic one with 500k members, but that trade-off is left to
        the caller — we simply return the top N and let downstream logic decide.

        Parameters
        ──────────
        niche  : The search query string.
        top_n  : How many subreddits to return (default 5).

        Returns
        ───────
        list[SubredditInfo] sorted descending by subscriber count.
        """
        logger.info("Discovering subreddits for: '%s'", niche)

        # PRAW's subreddits.search() returns a generator of Subreddit objects.
        # We request more than we need (top_n * 4) so that after sorting by
        # subscribers we still have enough candidates if some have 0 subscribers
        # (private/banned subs can appear in search results).
        raw_results = _call_with_backoff(
            lambda: list(self._reddit.subreddits.search(niche, limit=top_n * 4))
        )

        results: list[SubredditInfo] = []
        for sub in raw_results:
            try:
                # Accessing .subscribers or .public_description may trigger a
                # network call if the Subreddit object is lazy-loaded — wrap in
                # the backoff helper to handle transient rate limits.
                info = SubredditInfo(
                    name=sub.display_name,
                    title=sub.title or "",
                    # Truncate description to 300 chars to keep logs readable.
                    description=(sub.public_description or "")[:300],
                    subscribers=sub.subscribers or 0,
                    url=f"https://www.reddit.com{sub.url}",
                )
                results.append(info)
            except Exception as exc:
                # Some subreddits are restricted / quarantined; skip them
                # gracefully rather than crashing the entire discovery phase.
                logger.warning("Skipping subreddit '%s': %s", getattr(sub, "display_name", "?"), exc)

        # Sort descending by subscribers so the most active communities come first.
        results.sort(key=lambda s: s.subscribers, reverse=True)
        top = results[:top_n]

        logger.info(
            "Discovered %d subreddits: %s",
            len(top),
            [s.name for s in top],
        )
        return top

    def fetch_threads(
        self,
        subreddits: list[str],
        niche: str,
        max_threads: int = 50,
    ) -> list[Thread]:
        """
        Search each subreddit for posts related to `niche`, combining results
        from two PRAW listing endpoints:
            • subreddit.search(sort="hot")         — recent, actively discussed posts
            • subreddit.search(sort="top", time_filter="year") — high-signal posts from
                                                                  the past year

        Why two endpoints?
        ──────────────────
        "Hot" surfaces recency; "top (past year)" surfaces posts that generated
        the most engagement (and therefore the most comments / pain points).
        Together they give a balanced corpus without requiring us to scroll
        through months of cold posts.

        Deduplication
        ─────────────
        Posts can appear in both hot and top listings.  We deduplicate on
        `post_id` (Reddit's base-36 submission ID) using a set, so each post
        appears exactly once in the returned list regardless of how many
        subreddits or listings it was found in.

        Parameters
        ──────────
        subreddits  : List of subreddit display names to search (without 'r/').
        niche       : Search query string.
        max_threads : Total cap on the number of Thread objects to return.

        Returns
        ───────
        list[Thread] — deduplicated, without comments (call fetch_comments next).
        """
        logger.info(
            "Fetching threads for niche='%s' across subreddits: %s",
            niche,
            subreddits,
        )

        seen_ids: set[str] = set()       # For O(1) deduplication
        threads: list[Thread] = []

        # We ask each listing for (max_threads) posts.  After deduplication the
        # total will be less than max_threads * len(subreddits) * 2, but we'll
        # trim to max_threads at the end.
        per_listing_limit = max(10, max_threads // max(len(subreddits), 1))

        for sub_name in subreddits:
            subreddit = self._reddit.subreddit(sub_name)

            # Define the two listing generators we want to exhaust.
            # We use lambdas so that the generator is created (and the HTTP
            # request is made) lazily inside _call_with_backoff.
            listings = [
                ("hot", lambda s=subreddit: s.search(
                    niche, sort="hot", limit=per_listing_limit
                )),
                ("top_year", lambda s=subreddit: s.search(
                    niche, sort="top", time_filter="year", limit=per_listing_limit
                )),
            ]

            for listing_name, listing_fn in listings:
                try:
                    # Materialise the generator into a list inside the backoff
                    # wrapper so that any TooManyRequests raised during iteration
                    # is caught and retried.
                    posts = _call_with_backoff(lambda fn=listing_fn: list(fn()))
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch %s listing for r/%s: %s",
                        listing_name, sub_name, exc,
                    )
                    continue

                for post in posts:
                    if post.id in seen_ids:
                        continue  # Already collected from another listing/subreddit

                    seen_ids.add(post.id)

                    thread = Thread(
                        post_id=post.id,
                        title=post.title,
                        # selftext is "" for link posts; "[removed]" for removed ones.
                        # We normalise removed body text to an empty string.
                        selftext=(post.selftext if post.selftext not in _DELETED_BODIES else ""),
                        url=f"https://www.reddit.com{post.permalink}",
                        subreddit=post.subreddit.display_name,
                        score=post.score,
                        num_comments=post.num_comments,
                    )
                    threads.append(thread)

                    if len(threads) >= max_threads:
                        logger.info("Reached max_threads limit (%d).  Stopping.", max_threads)
                        return threads

        logger.info("Fetched %d unique threads.", len(threads))
        return threads

    def fetch_comments(
        self,
        thread_ids: list[str],
        max_comments_per_thread: int = 30,
        threads_by_id: Optional[dict[str, Thread]] = None,
    ) -> list[Thread]:
        """
        For each thread ID, fetch its top-level comments and their direct
        replies (depth 0 and 1), then attach them to the corresponding Thread.

        Why only depth 0 and 1?
        ────────────────────────
        Deeper comment threads are usually meta-discussion (jokes, off-topic
        replies) rather than raw pain points.  Keeping depth ≤ 1 also keeps
        the number of tokens sent to the LLM bounded.

        MoreComments expansion
        ──────────────────────
        PRAW represents collapsed comment sections as `MoreComments` objects.
        We call `replace_more(limit=0)` to skip all of them — this means we
        only get the comments that Reddit loaded in the initial request.
        Using `limit=None` would expand all collapsed sections but can result
        in dozens of extra HTTP requests per post, causing rate-limit issues.
        `limit=0` is the pragmatic choice for bulk fetching.

        Parameters
        ──────────
        thread_ids             : List of post IDs to fetch comments for.
        max_comments_per_thread: Cap on the number of Comment objects per Thread.
        threads_by_id          : Optional dict mapping post_id → Thread, so we
                                 can attach comments in-place.  If None, new
                                 Thread stubs are created (without post metadata).

        Returns
        ───────
        list[Thread] — same objects as threads_by_id.values(), now with
        comments populated.  Order matches the input thread_ids list.
        """
        threads_by_id = threads_by_id or {}
        result_threads: list[Thread] = []

        for post_id in thread_ids:
            # Retrieve the corresponding Thread object if we have it;
            # otherwise create a minimal stub so comments are still returned.
            thread = threads_by_id.get(post_id)
            if thread is None:
                logger.debug("No Thread object for post_id=%s; creating stub.", post_id)
                thread = Thread(
                    post_id=post_id,
                    title="",
                    selftext="",
                    url=f"https://www.reddit.com/comments/{post_id}",
                    subreddit="",
                    score=0,
                    num_comments=0,
                )

            logger.debug("Fetching comments for post_id=%s ('%s')", post_id, thread.title[:60])

            try:
                submission = _call_with_backoff(
                    lambda pid=post_id: self._reddit.submission(id=pid)
                )

                # Collapse MoreComments nodes without making extra HTTP calls.
                # This is the key performance trade-off: we miss some comments
                # but avoid potentially hundreds of extra API requests per post.
                _call_with_backoff(lambda s=submission: s.comments.replace_more(limit=0))

            except Exception as exc:
                logger.warning("Could not fetch comments for post %s: %s", post_id, exc)
                result_threads.append(thread)
                continue

            comments: list[Comment] = []
            comment_count = 0

            # Iterate over top-level comments (depth 0).
            for top_comment in submission.comments:
                if comment_count >= max_comments_per_thread:
                    break

                # Skip deleted / removed top-level comments.
                if top_comment.body in _DELETED_BODIES:
                    continue

                comments.append(
                    Comment(
                        comment_id=top_comment.id,
                        body=top_comment.body,
                        score=top_comment.score,
                        depth=0,
                        post_title=thread.title,
                        post_url=thread.url,
                    )
                )
                comment_count += 1

                # Iterate over the direct replies to this top-level comment
                # (depth 1).  We intentionally do NOT go deeper.
                for reply in top_comment.replies:
                    if comment_count >= max_comments_per_thread:
                        break

                    # PRAW can return MoreComments objects in the replies list
                    # even after replace_more(limit=0) in some edge cases.
                    # Check the type explicitly to avoid AttributeError on .body.
                    if not hasattr(reply, "body"):
                        continue

                    if reply.body in _DELETED_BODIES:
                        continue

                    comments.append(
                        Comment(
                            comment_id=reply.id,
                            body=reply.body,
                            score=reply.score,
                            depth=1,
                            post_title=thread.title,
                            post_url=thread.url,
                        )
                    )
                    comment_count += 1

            # Attach the collected comments to the Thread in-place.
            thread.comments = comments
            logger.debug(
                "  → %d comments collected for post_id=%s", len(comments), post_id
            )
            result_threads.append(thread)

        return result_threads


# =============================================================================
# __main__ — smoke test / demo
# =============================================================================

if __name__ == "__main__":
    """
    Quick integration test that exercises all three public methods.

    Run with:
        python backend/reddit_fetcher.py

    Expected output (numbers will vary):
        ✅  Discovered 5 subreddits
        ✅  Fetched N threads
        ✅  Fetched comments
        --- Summary ---
        r/CRM            | 12 threads | 340 comments total
        …
    """
    import json

    # Configure basic logging so we can see what's happening.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    TEST_NICHE = "CRM software"

    print(f"\n{'='*60}")
    print(f"  Reddit Pain Point Miner — Smoke Test")
    print(f"  Niche: '{TEST_NICHE}'")
    print(f"{'='*60}\n")

    fetcher = RedditFetcher(niche=TEST_NICHE)

    # ── Step 1: Discover subreddits ──────────────────────────────────────────
    print("Step 1 — Discovering subreddits…")
    subs = fetcher.discover_subreddits(TEST_NICHE, top_n=5)
    print(f"✅  Discovered {len(subs)} subreddits:\n")
    for s in subs:
        print(f"   r/{s.name:<30} {s.subscribers:>10,} subscribers")
    print()

    # ── Step 2: Fetch threads ────────────────────────────────────────────────
    # Use a small cap so the smoke test completes quickly.
    MAX_T = 20
    print(f"Step 2 — Fetching up to {MAX_T} threads…")
    threads = fetcher.fetch_threads(
        subreddits=[s.name for s in subs],
        niche=TEST_NICHE,
        max_threads=MAX_T,
    )
    print(f"✅  Fetched {len(threads)} unique threads.\n")

    # ── Step 3: Fetch comments ───────────────────────────────────────────────
    MAX_C = 10  # keep it small for the smoke test
    print(f"Step 3 — Fetching up to {MAX_C} comments per thread…")
    threads_by_id = {t.post_id: t for t in threads}
    threads_with_comments = fetcher.fetch_comments(
        thread_ids=[t.post_id for t in threads],
        max_comments_per_thread=MAX_C,
        threads_by_id=threads_by_id,
    )
    print(f"✅  Comments fetched.\n")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("─" * 60)
    print(f"{'SUBREDDIT':<20} {'THREADS':>7}  {'TOTAL COMMENTS':>14}  {'AVG SCORE':>9}")
    print("─" * 60)

    # Group threads by subreddit for the summary table.
    from collections import defaultdict
    by_sub: dict[str, list[Thread]] = defaultdict(list)
    for t in threads_with_comments:
        by_sub[t.subreddit].append(t)

    for sub_name, sub_threads in sorted(by_sub.items()):
        total_comments = sum(len(t.comments) for t in sub_threads)
        avg_score = sum(t.score for t in sub_threads) // max(len(sub_threads), 1)
        print(
            f"r/{sub_name:<18} {len(sub_threads):>7}  {total_comments:>14}  {avg_score:>9}"
        )

    print("─" * 60)
    total_c = sum(len(t.comments) for t in threads_with_comments)
    print(f"\n{'TOTAL':<20} {len(threads_with_comments):>7}  {total_c:>14}")

    # ── Print a sample comment so we can see real data ────────────────────────
    sample_threads = [t for t in threads_with_comments if t.comments]
    if sample_threads:
        sample_thread = sample_threads[0]
        sample_comment = sample_thread.comments[0]
        print(f"\n── Sample comment from r/{sample_thread.subreddit} ──")
        print(f"   Post : {sample_thread.title[:80]}")
        print(f"   Score: {sample_comment.score}")
        print(f"   Depth: {sample_comment.depth}")
        print(f"   Body :\n{sample_comment.body[:400]}")

    print("\n✅  Smoke test complete.\n")
