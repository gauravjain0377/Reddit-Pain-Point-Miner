"""
tests/test_full_pipeline.py
─────────────────────────────────────────────────────────────────────────────
Pytest test suite for the Reddit Pain Point Miner pipeline.

All external calls (Reddit API, OpenAI) are mocked so these tests run
offline, quickly, and without spending API credits.

Test strategy
─────────────
1. test_full_pipeline_happy_path  — mock PRAW + GPT-4o, run the full graph,
                                    assert final_report structure is correct.
2. test_cache_hit                 — store a result, assert get() returns it.
3. test_cache_miss                — assert get() returns None for unknown niche.
4. test_deduplicator_merges       — 5 pain points where 2 are near-duplicates;
                                    assert they merge into 4 entries.
5. test_ranker_scoring            — assert ranking formula produces correct order.

Run with:
    cd backend
    pytest tests/ -v
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure backend/ is importable when running pytest from the project root ──
_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ── Stub heavy optional dependencies before importing our modules ─────────────
# praw and prawcore may not be installed in a minimal CI environment
for _mod in ("praw", "prawcore", "prawcore.exceptions"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# Provide the TooManyRequests exception prawcore code expects
_prawcore_exc = types.ModuleType("prawcore.exceptions")
_prawcore_exc.TooManyRequests = type("TooManyRequests", (Exception,), {})
sys.modules["prawcore.exceptions"] = _prawcore_exc

# langchain, langchain_core, and langchain_openai stubs
_lc = types.ModuleType("langchain")
_lc.__path__ = []  # make it a package
_lc_op = types.ModuleType("langchain.output_parsers")
_lc_op.PydanticOutputParser = MagicMock
_lc_p = types.ModuleType("langchain.prompts")
_lc_p.ChatPromptTemplate = MagicMock
_lc_p.SystemMessagePromptTemplate = MagicMock
_lc_p.HumanMessagePromptTemplate = MagicMock
sys.modules["langchain"] = _lc
sys.modules["langchain.output_parsers"] = _lc_op
sys.modules["langchain.prompts"] = _lc_p

_lc_core = types.ModuleType("langchain_core")
_lc_core.__path__ = []
_lc_core_op = types.ModuleType("langchain_core.output_parsers")
_lc_core_op.PydanticOutputParser = MagicMock
sys.modules["langchain_core"] = _lc_core
sys.modules["langchain_core.output_parsers"] = _lc_core_op

_lco = types.ModuleType("langchain_openai")
_lco.ChatOpenAI = MagicMock
sys.modules["langchain_openai"] = _lco

# langgraph stubs
for _mod in ("langgraph", "langgraph.graph"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# ── Now import our modules ─────────────────────────
from extractor import PainPoint
from cache import AnalysisCache


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_pain_points() -> list[PainPoint]:
    """Five pain points — two are near-duplicates on Pricing."""
    return [
        PainPoint(
            pain_text="CSV export is locked behind an expensive Business plan paywall.",
            severity=8,
            category="Pricing",
            verbatim_quote=(
                "why the hell is CSV export behind a paywall we are a 3-person startup "
                "and cannot afford $99/mo just to get our data out of the tool"
            ),
            source_url="https://reddit.com/r/CRM/comments/abc1",
            mention_count=4,
            confidence=0.92,
        ),
        PainPoint(
            pain_text="Exporting data to CSV requires an expensive paid plan, blocking small teams.",
            severity=7,
            category="Pricing",
            verbatim_quote=(
                "we tried to export CSV and hit a paywall, total BS for a startup that "
                "just wants their own data back from the platform they pay for monthly"
            ),
            source_url="https://reddit.com/r/CRM/comments/abc2",
            mention_count=3,
            confidence=0.88,
        ),
        PainPoint(
            pain_text="The mobile app crashes on Android 14 when opening contact records.",
            severity=9,
            category="Performance",
            verbatim_quote=(
                "the app crashes every single time I try to open a contact on my Pixel 8 "
                "running Android 14 this has been happening since the last update"
            ),
            source_url="https://reddit.com/r/CRM/comments/abc3",
            mention_count=12,
            confidence=0.97,
        ),
        PainPoint(
            pain_text="There is no native Slack integration; users must use Zapier workarounds.",
            severity=6,
            category="Integration",
            verbatim_quote=(
                "why is there no Slack integration in 2024 we had to set up a janky "
                "Zapier workflow and it breaks every other week"
            ),
            source_url="https://reddit.com/r/CRM/comments/abc4",
            mention_count=7,
            confidence=0.85,
        ),
        PainPoint(
            pain_text="Customer support takes over 5 days to respond to critical bugs.",
            severity=8,
            category="Customer Support",
            verbatim_quote=(
                "opened a ticket 5 days ago about data sync breaking and still no response "
                "not even an acknowledgement this is completely unacceptable"
            ),
            source_url="https://reddit.com/r/CRM/comments/abc5",
            mention_count=5,
            confidence=0.91,
        ),
    ]


@pytest.fixture
def tmp_cache(tmp_path) -> AnalysisCache:
    """An AnalysisCache instance backed by a temp SQLite file."""
    db = tmp_path / "test_cache.db"
    c = AnalysisCache(db_path=db, ttl_hours=24)
    yield c
    c.close()


@pytest.fixture
def sample_report() -> dict:
    return {
        "niche": "CRM Software",
        "job_id": "test-job-001",
        "summary": "Users face significant pricing and performance issues.",
        "top_pain_points": [
            {
                "rank": 1,
                "pain_text": "CSV export is locked behind a paywall.",
                "severity": 8,
                "category": "Pricing",
                "verbatim_quote": "why is CSV export behind a paywall we are a startup",
                "source_url": "https://reddit.com/r/CRM/abc1",
                "mention_count": 4,
                "confidence": 0.92,
            }
        ],
        "categories_breakdown": {"Pricing": 2, "Performance": 1},
        "top_quotes": [],
        "run_metadata": {
            "thread_count": 3,
            "tokens_used": 1500,
            "fetch_time_seconds": 8.2,
        },
    }


# =============================================================================
# Test 1 — Full pipeline happy path
# =============================================================================

class TestFullPipeline:
    """
    Run the complete agent graph with mocked Reddit + OpenAI.

    Strategy:
      - Patch RedditFetcher.discover_subreddits → returns 2 fake subreddits
      - Patch RedditFetcher.fetch_threads → returns 3 fake Thread objects
      - Patch RedditFetcher.fetch_comments → returns the same threads (with comments)
      - Patch ChatOpenAI.invoke → returns fake extraction JSON then fake summary
    """

    def _make_thread(self, post_id: str, title: str) -> Any:
        """Build a minimal Thread-like object without importing reddit_fetcher."""
        from reddit_fetcher import Comment, Thread
        return Thread(
            post_id=post_id,
            title=title,
            selftext="Example post body for testing.",
            url=f"https://reddit.com/r/CRM/comments/{post_id}",
            subreddit="CRM",
            score=100,
            num_comments=5,
            comments=[
                Comment(
                    comment_id=f"{post_id}_c1",
                    body=(
                        "The pricing is absolutely ridiculous. $99/month just for CSV "
                        "export is insane for a startup team we cannot afford that."
                    ),
                    score=80,
                    depth=0,
                    post_title=title,
                    post_url=f"https://reddit.com/r/CRM/comments/{post_id}",
                ),
            ],
        )

    def _fake_gpt_response(self, content: str) -> MagicMock:
        resp = MagicMock()
        resp.content = content
        resp.response_metadata = {"token_usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}}
        return resp

    def test_final_report_has_required_keys(self):
        """Full pipeline produces a report with all expected top-level keys."""
        fake_threads = [
            self._make_thread("t1", "Why is HubSpot pricing so expensive?"),
            self._make_thread("t2", "CRM comparison: HubSpot vs Salesforce"),
            self._make_thread("t3", "HubSpot CSV export locked behind paywall"),
        ]

        fake_extraction_json = json.dumps({
            "pain_points": [
                {
                    "pain_text": "CSV export is behind a paywall.",
                    "severity": 8,
                    "category": "Pricing",
                    "verbatim_quote": (
                        "the pricing is absolutely ridiculous $99/month just for CSV "
                        "export is insane for a startup team we cannot afford that"
                    ),
                    "source_url": "https://reddit.com/r/CRM/comments/t1",
                    "mention_count": 3,
                    "confidence": 0.91,
                }
            ]
        })
        fake_summary = "Users consistently report that pricing tiers are misaligned with small team budgets."

        gpt_call_count = 0

        def fake_invoke(messages):
            nonlocal gpt_call_count
            gpt_call_count += 1
            # First N calls are extraction; last call is the executive summary
            if gpt_call_count == 1:
                return self._fake_gpt_response(fake_extraction_json)
            # Subreddit suggestion call
            if gpt_call_count == 2:
                return self._fake_gpt_response('["CRM", "salesforce", "hubspot"]')
            return self._fake_gpt_response(fake_summary)

        from reddit_fetcher import SubredditInfo

        fake_subs = [
            SubredditInfo(
                name="CRM",
                title="CRM Software",
                description="CRM discussions",
                subscribers=50000,
                url="https://reddit.com/r/CRM/",
            )
        ]

        with (
            patch("reddit_fetcher.RedditFetcher.discover_subreddits", return_value=fake_subs),
            patch("reddit_fetcher.RedditFetcher.fetch_threads", return_value=fake_threads),
            patch("reddit_fetcher.RedditFetcher.fetch_comments", return_value=fake_threads),
            patch("langchain_openai.ChatOpenAI") as MockLLM,
        ):
            mock_llm_instance = MockLLM.return_value
            mock_llm_instance.invoke.side_effect = fake_invoke

            # Patch the module-level _llm used in agent_graph
            with patch("agent_graph._llm", mock_llm_instance):
                from agent_graph import run_pipeline
                result = run_pipeline(niche="CRM Software")

        report = result.get("final_report", {})
        required_keys = {
            "niche", "summary", "top_pain_points",
            "categories_breakdown", "top_quotes", "run_metadata",
        }
        assert required_keys.issubset(report.keys()), (
            f"Missing keys: {required_keys - report.keys()}"
        )
        assert report["niche"] == "CRM Software"
        assert isinstance(report["top_pain_points"], list)
        assert isinstance(report["categories_breakdown"], dict)


# =============================================================================
# Test 2 & 3 — Cache hit / miss
# =============================================================================

class TestCache:

    def test_cache_miss_returns_none(self, tmp_cache):
        """get() returns None for a niche that was never stored."""
        result = tmp_cache.get("some obscure niche nobody has searched")
        assert result is None

    def test_cache_set_then_get_returns_result(self, tmp_cache, sample_report):
        """set() then get() with the same niche returns the stored report."""
        tmp_cache.set("CRM Software", sample_report)
        retrieved = tmp_cache.get("CRM Software")
        assert retrieved is not None
        assert retrieved["niche"] == "CRM Software"
        assert retrieved["job_id"] == "test-job-001"

    def test_cache_is_case_insensitive(self, tmp_cache, sample_report):
        """'CRM Software' and 'crm software' share the same cache entry."""
        tmp_cache.set("CRM Software", sample_report)
        assert tmp_cache.get("crm software") is not None
        assert tmp_cache.get("CRM SOFTWARE") is not None

    def test_cache_invalidate_removes_entry(self, tmp_cache, sample_report):
        """invalidate() deletes the entry; subsequent get() returns None."""
        tmp_cache.set("CRM Software", sample_report)
        assert tmp_cache.get("CRM Software") is not None

        deleted = tmp_cache.invalidate("CRM Software")
        assert deleted is True
        assert tmp_cache.get("CRM Software") is None

    def test_cache_invalidate_returns_false_for_unknown(self, tmp_cache):
        """invalidate() returns False when the niche was never stored."""
        assert tmp_cache.invalidate("definitely not in cache") is False

    def test_cache_stats_total_entries(self, tmp_cache, sample_report):
        """get_stats() reflects the correct live entry count."""
        assert tmp_cache.get_stats().total_entries == 0
        tmp_cache.set("CRM Software", sample_report)
        assert tmp_cache.get_stats().total_entries == 1


# =============================================================================
# Test 4 — Deduplicator
# =============================================================================

class TestDeduplicator:
    """
    The deduplicator groups by category and merges pairs with Jaccard > 0.7.
    The two Pricing pain points above share significant word overlap (CSV, export,
    paywall, startup) and should merge into a single entry.
    """

    def test_near_duplicates_merge(self, sample_pain_points):
        from agent_graph import deduplicator, AgentState

        state: AgentState = {
            "niche": "CRM Software",
            "job_id": "test",
            "discovered_subreddits": [],
            "threads": [],
            "raw_pain_points": sample_pain_points,
            "deduped_pain_points": [],
            "ranked_pain_points": [],
            "final_report": {},
            "status": "",
            "error": None,
            "metadata": {},
            "progress_callback": None,
        }

        result = deduplicator(state)
        deduped = result["deduped_pain_points"]

        # 5 raw → should be 4 after merging the 2 near-duplicate Pricing entries
        assert len(deduped) == 4, (
            f"Expected 4 deduped pain points, got {len(deduped)}: "
            + str([p.pain_text[:50] for p in deduped])
        )

    def test_merged_mention_count_is_summed(self, sample_pain_points):
        """The merged entry's mention_count = sum of both originals (4 + 3 = 7)."""
        from agent_graph import deduplicator, AgentState

        state: AgentState = {
            "niche": "CRM Software",
            "job_id": "test",
            "discovered_subreddits": [],
            "threads": [],
            "raw_pain_points": sample_pain_points,
            "deduped_pain_points": [],
            "ranked_pain_points": [],
            "final_report": {},
            "status": "",
            "error": None,
            "metadata": {},
            "progress_callback": None,
        }

        result = deduplicator(state)
        pricing_points = [
            p for p in result["deduped_pain_points"]
            if p.category == "Pricing"
        ]
        assert len(pricing_points) == 1
        merged = pricing_points[0]
        assert merged.mention_count == 7, (
            f"Expected mention_count=7 (4+3), got {merged.mention_count}"
        )

    def test_non_duplicates_preserved(self, sample_pain_points):
        """Performance, Integration, and Customer Support entries are not merged."""
        from agent_graph import deduplicator, AgentState

        state: AgentState = {
            "niche": "CRM Software",
            "job_id": "test",
            "discovered_subreddits": [],
            "threads": [],
            "raw_pain_points": sample_pain_points,
            "deduped_pain_points": [],
            "ranked_pain_points": [],
            "final_report": {},
            "status": "",
            "error": None,
            "metadata": {},
            "progress_callback": None,
        }

        result = deduplicator(state)
        categories = [p.category for p in result["deduped_pain_points"]]
        assert "Performance" in categories
        assert "Integration" in categories
        assert "Customer Support" in categories


# =============================================================================
# Test 5 — Ranker scoring formula
# =============================================================================

class TestRanker:
    """
    Scoring formula: score = severity*0.4 + mention_count*0.3 + confidence*10*0.3

    For our fixture:
      Performance:      9*0.4 + 12*0.3 + 0.97*10*0.3 = 3.6 + 3.6 + 2.91 = 10.11
      Customer Support: 8*0.4 + 5*0.3  + 0.91*10*0.3 = 3.2 + 1.5  + 2.73 = 7.43
      Pricing (rank 1): 8*0.4 + 4*0.3  + 0.92*10*0.3 = 3.2 + 1.2  + 2.76 = 7.16
      Integration:      6*0.4 + 7*0.3  + 0.85*10*0.3 = 2.4 + 2.1  + 2.55 = 7.05

    Expected rank order: Performance > Customer Support > Pricing > Integration
    """

    def test_highest_composite_score_ranks_first(self, sample_pain_points):
        from agent_graph import ranker, deduplicator, AgentState

        state: AgentState = {
            "niche": "CRM Software",
            "job_id": "test",
            "discovered_subreddits": [],
            "threads": [],
            "raw_pain_points": sample_pain_points,
            "deduped_pain_points": [],
            "ranked_pain_points": [],
            "final_report": {},
            "status": "",
            "error": None,
            "metadata": {},
            "progress_callback": None,
        }

        deduped_state = deduplicator(state)
        deduped_state["ranked_pain_points"] = []
        ranked_state = ranker(deduped_state)

        ranked = ranked_state["ranked_pain_points"]
        assert len(ranked) > 0
        # Performance entry (severity=9, mentions=12, confidence=0.97) must be first
        top = ranked[0]
        assert top.category == "Performance", (
            f"Expected Performance to rank first, got {top.category} "
            f"(severity={top.severity}, mentions={top.mention_count})"
        )

    def test_ranked_list_is_sorted_descending(self, sample_pain_points):
        """Each entry's score must be >= the next entry's score."""
        from agent_graph import ranker, deduplicator, AgentState

        def score(pp):
            return pp.severity * 0.4 + pp.mention_count * 0.3 + pp.confidence * 10 * 0.3

        state: AgentState = {
            "niche": "CRM Software",
            "job_id": "test",
            "discovered_subreddits": [],
            "threads": [],
            "raw_pain_points": sample_pain_points,
            "deduped_pain_points": [],
            "ranked_pain_points": [],
            "final_report": {},
            "status": "",
            "error": None,
            "metadata": {},
            "progress_callback": None,
        }

        deduped_state = deduplicator(state)
        deduped_state["ranked_pain_points"] = []
        ranked_state = ranker(deduped_state)
        ranked = ranked_state["ranked_pain_points"]

        scores = [score(p) for p in ranked]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Score inversion at index {i}: {scores[i]:.3f} < {scores[i+1]:.3f}"
            )
