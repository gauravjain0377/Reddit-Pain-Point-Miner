"""
agent_graph.py
─────────────────────────────────────────────────────────────────────────────
Day 3/4: LangGraph multi-node agent that orchestrates the full pain-point
mining pipeline from niche → final report.

Day 4 addition: every node fires a progress_callback(step_name, percent)
after it completes so the FastAPI WebSocket layer can stream live updates
to the frontend without polling.

Pipeline:
    START
      └─ subreddit_discovery   (Reddit search + GPT-4o suggestions)  [17%]
           ├─ [error] → END
           └─ thread_fetcher   (fetch posts + comments)              [33%]
                └─ pain_extractor   (LLM extraction in batches)      [50%]
                     └─ deduplicator   (Jaccard-based dedup)         [67%]
                          └─ ranker   (score & sort pain points)     [83%]
                               └─ report_generator   (GPT-4o summary)[100%]
                                    └─ END

Design philosophy
─────────────────
Each node owns exactly ONE responsibility and mutates a slice of AgentState.
This makes individual nodes unit-testable in isolation, and lets LangGraph
stream status updates to the UI between every step.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict
from typing import Any, Callable, Optional

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from config import config
from extractor import PainPoint, PainPointExtractor
from reddit_fetcher import RedditFetcher, Thread

logger = logging.getLogger(__name__)


# =============================================================================
# AgentState — the shared mutable context passed between every node.
# TypedDict is used (not a Pydantic model) because LangGraph requires plain
# dicts for its state, while TypedDict gives IDE type-checking for free.
# =============================================================================

class AgentState(TypedDict):
    niche: str                          # User's product niche input
    job_id: str                         # UUID for this pipeline run (for UI tracking)
    discovered_subreddits: list[str]    # Names of subreddits to mine
    threads: list[Thread]               # Fetched Reddit posts with comments
    raw_pain_points: list[PainPoint]    # LLM-extracted pain points (pre-dedup)
    deduped_pain_points: list[PainPoint]# After Jaccard-based deduplication
    ranked_pain_points: list[PainPoint] # Scored, sorted, rank field added
    final_report: dict                  # Structured JSON report for UI/export
    status: str                         # Human-readable current step (streamed to UI)
    error: Optional[str]                # Set if a fatal error occurs; routes to END
    metadata: dict                      # Timing, token counts, thread counts, etc.
    # Day 4: callable(step_name: str, percent: int) → None
    # Stored in state so nodes can fire it without needing a global.
    # LangGraph serialises state as dicts; Callable is not JSON-serialisable,
    # so we store it here only for in-process use (never persisted to disk).
    progress_callback: Any              # Callable[[str, int], None] | None


# =============================================================================
# Shared LLM instance
# temperature=0 for deterministic structured output; gpt-4o for quality.
# Created once at module load to avoid re-initialising on every graph run.
# =============================================================================

_llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,
    api_key=config.OPENAI_API_KEY,
)

# Progress callback helpers

# Node name → completion percentage (fired AFTER the node finishes)
_NODE_PROGRESS: dict[str, int] = {
    "subreddit_discovery": 17,
    "thread_fetcher": 33,
    "pain_extractor": 50,
    "deduplicator": 67,
    "ranker": 83,
    "report_generator": 100,
}


def _noop_callback(step: str, percent: int) -> None:
    """Default no-op callback used when no WebSocket listener is attached."""


def _fire(state: AgentState, node_name: str) -> None:
    """
    Invoke the progress_callback stored in state, if any.
    Swallows all exceptions so a broken callback never crashes the pipeline.
    """
    cb = state.get("progress_callback") or _noop_callback
    percent = _NODE_PROGRESS.get(node_name, 0)
    try:
        cb(node_name, percent)
    except Exception as exc:
        logger.warning("[_fire] progress_callback raised: %s", exc)

# Helper utilities

def _jaccard(a: str, b: str) -> float:
    """
    Compute token-overlap Jaccard similarity between two strings.

    Why Jaccard and not embeddings?
    Embeddings require an extra API call per pair.  For within-category
    comparison (~10–30 pain points per category) this O(n²) keyword check
    is fast, free, and accurate enough for deduplication.
    """
    STOPWORDS = {
        "the", "a", "an", "is", "it", "in", "on", "of", "to", "and", "or",
        "but", "for", "with", "this", "that", "are", "was", "were", "be",
        "been", "have", "has", "they", "their", "we", "our", "you", "your",
    }

    def tokenise(s: str) -> set[str]:
        tokens = re.findall(r"[a-z]+", s.lower())
        return {t for t in tokens if t not in STOPWORDS and len(t) > 2}

    sa, sb = tokenise(a), tokenise(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _broaden_niche(niche: str) -> str:
    """
    Retry heuristic: remove the last word of a multi-word niche to widen the
    search scope.  e.g. "email marketing software" → "email marketing".
    Single-word niches are returned unchanged.
    """
    parts = niche.strip().split()
    return " ".join(parts[:-1]) if len(parts) > 1 else niche


# Node 1 — subreddit_discovery
# Responsibility: find which subreddits contain relevant pain points.
# This is a separate node because subreddit selection is independent of thread
# fetching and can be retried or replaced (e.g. with a curated list) without
# touching the rest of the pipeline.

def subreddit_discovery(state: AgentState) -> AgentState:
    """
    Combines two sources of subreddit signals:
      1. RedditFetcher.discover_subreddits() — PRAW search ranked by subscribers.
      2. GPT-4o suggestion — asks the model to brainstorm 3–5 relevant subreddits
         the PRAW search might miss (niche communities, brand-specific subs, etc.)

    Both lists are merged and deduplicated (case-insensitive).
    If the combined list is empty, sets error and routes the graph to END.
    """
    state["status"] = "Discovering subreddits..."
    _fire(state, "subreddit_discovery")
    niche = state["niche"]
    logger.info("[subreddit_discovery] Niche: '%s'", niche)

    # 1. Reddit search via PRAW
    praw_subs: list[str] = []
    try:
        fetcher = RedditFetcher(niche=niche)
        discovered = fetcher.discover_subreddits(niche, top_n=5)
        praw_subs = [s.name for s in discovered]
        logger.info("[subreddit_discovery] PRAW found: %s", praw_subs)
    except Exception as exc:
        logger.warning("[subreddit_discovery] PRAW discovery failed: %s", exc)

    # 2. GPT-4o supplementary suggestions
    # The LLM knows about niche communities (e.g. r/Mailchimp, r/klaviyo) that
    # PRAW's search may rank poorly because of low subscriber counts.
    gpt_subs: list[str] = []
    try:
        prompt = (
            f"I am researching the niche: '{niche}'.\n"
            "Suggest 3 to 5 Reddit subreddit names (just the names, no r/ prefix) "
            "where users discuss problems, frustrations, or alternatives related to this niche.\n"
            "Focus on subreddits that are active but might be missed by a generic search.\n"
            "Respond with ONLY a JSON array of strings, e.g.: [\"crm\", \"hubspot\", \"salesforce\"]\n"
            "No explanation, no markdown."
        )
        response = _llm.invoke(prompt)
        raw = response.content.strip()
        # Strip markdown fences defensively
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        gpt_subs = json.loads(raw)
        if not isinstance(gpt_subs, list):
            gpt_subs = []
        # Sanitise: strip r/ prefix, whitespace, non-alphanumeric
        gpt_subs = [re.sub(r"^r/", "", s).strip() for s in gpt_subs if s]
        logger.info("[subreddit_discovery] GPT-4o suggested: %s", gpt_subs)
    except Exception as exc:
        logger.warning("[subreddit_discovery] GPT-4o suggestion failed: %s", exc)

    # 3. Merge + deduplicate (case-insensitive, preserve original casing)
    seen: set[str] = set()
    merged: list[str] = []
    for name in praw_subs + gpt_subs:
        key = name.lower()
        if key not in seen and name:
            seen.add(key)
            merged.append(name)

    logger.info("[subreddit_discovery] Final subreddit list (%d): %s", len(merged), merged)

    if not merged:
        state["error"] = (
            f"Could not discover any subreddits for niche '{niche}'. "
            "Try a broader or differently phrased niche."
        )
        logger.error("[subreddit_discovery] %s", state["error"])
        return state

    state["discovered_subreddits"] = merged
    state["metadata"]["subreddits_found"] = len(merged)
    _fire(state, "subreddit_discovery")
    return state

# Node 2 — thread_fetcher
# Responsibility: retrieve Reddit posts and their comments for all subreddits.
# Separated from subreddit_discovery so we can retry with a broader keyword
# without re-running discovery, and from pain_extractor so network I/O is
# isolated from LLM I/O (very different failure modes and latencies).

def thread_fetcher(state: AgentState) -> AgentState:
    """
    Fetches threads from all discovered_subreddits then enriches them with
    comments.  If fewer than 5 threads are found, retries once with a broader
    niche (last word removed) before giving up.

    Timing is recorded in metadata["fetch_time_seconds"] for observability.
    """
    state["status"] = "Fetching Reddit threads..."
    _fire(state, "thread_fetcher")
    niche = state["niche"]
    subreddits = state["discovered_subreddits"]
    t0 = time.monotonic()

    def _fetch(query: str) -> list[Thread]:
        fetcher = RedditFetcher(niche=query)
        raw_threads = fetcher.fetch_threads(
            subreddits=subreddits,
            niche=query,
            max_threads=config.MAX_THREADS,
        )
        if not raw_threads:
            return []
        threads_by_id = {t.post_id: t for t in raw_threads}
        return fetcher.fetch_comments(
            thread_ids=[t.post_id for t in raw_threads],
            max_comments_per_thread=config.MAX_COMMENTS_PER_THREAD,
            threads_by_id=threads_by_id,
        )

    threads = _fetch(niche)
    logger.info("[thread_fetcher] Initial fetch: %d threads", len(threads))

    # Retry with broader keyword if too few threads
    THREAD_MIN = 5
    if len(threads) < THREAD_MIN:
        broader = _broaden_niche(niche)
        if broader != niche:
            logger.warning(
                "[thread_fetcher] Only %d threads found. Retrying with broader niche: '%s'",
                len(threads),
                broader,
            )
            threads = _fetch(broader)
            logger.info("[thread_fetcher] Retry fetch: %d threads", len(threads))

    elapsed = time.monotonic() - t0
    state["threads"] = threads
    state["metadata"]["thread_count"] = len(threads)
    state["metadata"]["fetch_time_seconds"] = round(elapsed, 2)
    _fire(state, "thread_fetcher")

    logger.info(
        "[thread_fetcher] Done: %d threads in %.1fs",
        len(threads),
        elapsed,
    )
    return state


# Node 3 — pain_extractor
# Responsibility: call the LLM to extract structured PainPoint objects.
# Separated because LLM extraction is the most expensive step (many API calls,
# token costs) and must be independently retryable without re-fetching Reddit.
# Token usage is tracked in metadata for cost visibility.

def pain_extractor(state: AgentState) -> AgentState:
    """
    Uses PainPointExtractor (batched GPT-4o calls) to pull structured pain
    points out of every thread.  OpenAI token usage is accumulated and stored
    in metadata["tokens_used"] by monkey-patching the LLM's usage callbacks.

    If extraction yields zero pain points (e.g. all threads are off-topic),
    raw_pain_points will be an empty list — downstream nodes handle that
    gracefully rather than crashing.
    """
    state["status"] = "Extracting pain points..."
    _fire(state, "pain_extractor")
    threads = state["threads"]
    niche = state["niche"]

    logger.info("[pain_extractor] Extracting from %d threads…", len(threads))

    # Token tracking via a lightweight wrapper around the LLM call.
    # We subclass PainPointExtractor's internal LLM to intercept usage metadata.
    total_tokens = 0

    class TokenTrackingLLM:
        """Thin wrapper that delegates to _llm and records token usage."""
        def invoke(self, messages):
            response = _llm.invoke(messages)
            # OpenAI SDK v1 attaches usage to response_metadata
            usage = getattr(response, "response_metadata", {}).get("token_usage", {})
            nonlocal total_tokens
            total_tokens += usage.get("total_tokens", 0)
            return response

    extractor = PainPointExtractor()
    # Swap the extractor's internal LLM with our tracking wrapper
    extractor._llm = TokenTrackingLLM()  # type: ignore[assignment]

    result = extractor.extract(threads, niche=niche)
    pain_points = result.pain_points

    state["raw_pain_points"] = pain_points
    state["metadata"]["tokens_used"] = total_tokens
    state["metadata"]["raw_pain_point_count"] = len(pain_points)
    _fire(state, "pain_extractor")

    logger.info(
        "[pain_extractor] Extracted %d raw pain points. Tokens used: %d",
        len(pain_points),
        total_tokens,
    )
    return state


# Node 4 — deduplicator
# Responsibility: collapse near-duplicate pain points within each category.
# Separated from extraction because deduplication is pure local computation
# (no I/O) and its threshold is a tunable hyperparameter independent of the
# extraction prompt or ranking formula.

def deduplicator(state: AgentState) -> AgentState:
    """
    Groups pain points by category, then compares all pairs within each group
    using Jaccard similarity on pain_text.  When similarity > 0.7:
      - pain_text and verbatim_quote: keep the entry with higher severity
      - mention_count: summed (evidence accumulates)
      - severity: averaged between the two
      - confidence: averaged between the two

    This merge strategy reflects the idea that two similar complaints together
    represent a systemic problem, so we average severity (don't exaggerate) but
    always accumulate mention evidence.
    """
    state["status"] = "Deduplicating pain points..."
    _fire(state, "deduplicator")
    raw = state["raw_pain_points"]
    SIMILARITY_THRESHOLD = 0.7

    # Group by category
    by_category: dict[str, list[PainPoint]] = defaultdict(list)
    for pp in raw:
        by_category[pp.category].append(pp)

    merged_all: list[PainPoint] = []

    for category, points in by_category.items():
        # Union-find style single-pass merge within the category.
        merged: list[PainPoint] = []

        for candidate in points:
            matched = False
            for existing in merged:
                sim = _jaccard(candidate.pain_text, existing.pain_text)
                if sim > SIMILARITY_THRESHOLD:
                    # Determine which entry has richer text (higher severity)
                    if candidate.severity > existing.severity:
                        existing.pain_text = candidate.pain_text
                        existing.verbatim_quote = candidate.verbatim_quote
                        existing.source_url = candidate.source_url

                    # Accumulate evidence
                    existing.mention_count += candidate.mention_count
                    # Average severity and confidence — don't inflate
                    existing.severity = round(
                        (existing.severity + candidate.severity) / 2
                    )
                    existing.confidence = round(
                        (existing.confidence + candidate.confidence) / 2, 3
                    )
                    matched = True
                    break

            if not matched:
                merged.append(candidate.model_copy())

        logger.debug(
            "[deduplicator] Category '%s': %d → %d pain points",
            category,
            len(points),
            len(merged),
        )
        merged_all.extend(merged)

    state["deduped_pain_points"] = merged_all
    state["metadata"]["deduped_pain_point_count"] = len(merged_all)
    _fire(state, "deduplicator")
    logger.info(
        "[deduplicator] %d raw → %d deduped pain points",
        len(raw),
        len(merged_all),
    )
    return state


# Node 5 — ranker
# Responsibility: score and sort pain points into a prioritised list.
# Separated from deduplication because the scoring formula is a business rule
# that product teams will want to tune independently of how we merge duplicates.

def ranker(state: AgentState) -> AgentState:
    """
    Computes a composite score for each pain point:

        score = (severity * 0.4) + (mention_count * 0.3) + (confidence * 10 * 0.3)

    Rationale for weights:
    - severity (40%) — the emotional/impact magnitude of the problem
    - mention_count (30%) — how widespread the problem is across users
    - confidence (30%, scaled ×10 to match the 1–10 range) — signal quality

    Pain points are sorted descending by this score and assigned a 1-indexed
    rank.  Rank is stored as a dynamic attribute (not in the Pydantic model) so
    we don't need to alter the existing PainPoint schema.
    """
    state["status"] = "Ranking pain points..."
    _fire(state, "ranker")
    pain_points = state["deduped_pain_points"]

    def composite_score(pp: PainPoint) -> float:
        return (
            pp.severity * 0.4
            + pp.mention_count * 0.3
            + pp.confidence * 10 * 0.3
        )

    sorted_points = sorted(pain_points, key=composite_score, reverse=True)

    # Add rank as a plain attribute; PainPoint uses model_config allow arbitrary
    # types or we store rank in metadata separately.
    # We use a lightweight approach: wrap each PainPoint dict with a rank key.
    # The ranked list stores PainPoint objects unchanged — rank is communicated
    # via position (index+1), which report_generator uses directly.
    # For downstream consumers that want an explicit rank field, we store a
    # parallel list of (rank, score) in metadata.
    ranked_scores: list[dict] = []
    for i, pp in enumerate(sorted_points, start=1):
        ranked_scores.append({
            "rank": i,
            "pain_text": pp.pain_text[:80],
            "score": round(composite_score(pp), 3),
        })

    state["ranked_pain_points"] = sorted_points
    state["metadata"]["ranked_scores"] = ranked_scores
    _fire(state, "ranker")
    logger.info("[ranker] Ranked %d pain points.", len(sorted_points))
    return state


# Node 6 — report_generator
# Responsibility: synthesise findings into an executive report.
# Separated from ranking because report generation is pure LLM synthesis —
# different prompting, different failure mode, and something a team might want
# to swap out (e.g. use a different model or template) independently.

def report_generator(state: AgentState) -> AgentState:
    """
    Calls GPT-4o to write a 3–4 paragraph executive summary of the top pain
    points, then assembles the final_report dict which the UI and export
    endpoints will consume.

    final_report structure:
    {
        "niche": str,
        "summary": str,                       # GPT-4o executive summary
        "top_pain_points": list[dict],         # Top 10 ranked pain points
        "categories_breakdown": dict[str,int], # Category → count
        "top_quotes": list[dict],              # Top 5 quotes by severity
        "run_metadata": dict,                  # Timing, tokens, counts
    }
    """
    state["status"] = "Generating final report..."
    _fire(state, "report_generator")
    niche = state["niche"]
    ranked = state["ranked_pain_points"]
    top10 = ranked[:10]

    # ── Build a readable summary of top pain points for the GPT-4o prompt ───
    pain_summary_lines = []
    for i, pp in enumerate(top10, 1):
        pain_summary_lines.append(
            f"{i}. [{pp.category}] (severity={pp.severity}, mentions={pp.mention_count}) "
            f"{pp.pain_text}\n   Quote: \"{pp.verbatim_quote[:120]}\""
        )
    pain_summary = "\n".join(pain_summary_lines)

    exec_prompt = (
        f"You are a senior product strategist. A researcher has mined Reddit for pain points "
        f"about the niche: '{niche}'.\n\n"
        f"Here are the top {len(top10)} pain points ranked by impact:\n\n"
        f"{pain_summary}\n\n"
        "Write a 3–4 paragraph executive summary for a product team. Include:\n"
        "1. An overview of the most critical pain theme(s)\n"
        "2. Specific patterns or clusters of complaints\n"
        "3. Recommended focus areas or quick wins\n"
        "Be concise, data-driven, and actionable. Do not use bullet points — "
        "write in flowing prose paragraphs only."
    )

    summary_text = ""
    try:
        response = _llm.invoke(exec_prompt)
        summary_text = response.content.strip()
    except Exception as exc:
        logger.error("[report_generator] GPT-4o summary failed: %s", exc)
        summary_text = (
            f"Executive summary unavailable due to an error: {exc}. "
            "Please review the top pain points listed below directly."
        )

    # categories_breakdown
    cat_counts: dict[str, int] = defaultdict(int)
    for pp in ranked:
        cat_counts[pp.category] += 1

    # ── top_quotes: top 5 by associated pain point severity ─────────────────
    sorted_by_severity = sorted(ranked, key=lambda p: p.severity, reverse=True)
    top_quotes = [
        {
            "quote": pp.verbatim_quote,
            "source_url": pp.source_url,
            "severity": pp.severity,
            "category": pp.category,
        }
        for pp in sorted_by_severity[:5]
    ]

    # Serialise top10 pain points
    top_pain_points_dicts = []
    for i, pp in enumerate(top10, 1):
        d = pp.model_dump()
        d["rank"] = i
        top_pain_points_dicts.append(d)

    state["final_report"] = {
        "niche": niche,
        "job_id": state["job_id"],
        "summary": summary_text,
        "top_pain_points": top_pain_points_dicts,
        "categories_breakdown": dict(cat_counts),
        "top_quotes": top_quotes,
        "run_metadata": {
            **state["metadata"],
            "total_pain_points_found": len(ranked),
        },
    }

    state["status"] = "Complete"
    _fire(state, "report_generator")
    logger.info("[report_generator] Report generated. Total ranked: %d", len(ranked))
    return state


# Conditional edge — after subreddit_discovery
# Routes to END if an error was set (no subreddits found), otherwise continues.

def _route_after_discovery(state: AgentState) -> str:
    """
    Conditional edge function called by LangGraph after subreddit_discovery.

    Returns "thread_fetcher" to continue the pipeline, or END to abort early.
    Early abort prevents wasted API calls when there's nothing to mine.
    """
    if state.get("error"):
        logger.warning(
            "[router] Aborting pipeline — error in discovery: %s", state["error"]
        )
        return END
    return "thread_fetcher"


# Graph assembly

def build_graph() -> StateGraph:
    """
    Constructs and compiles the LangGraph StateGraph.

    Node registration order matches the data-flow dependency order; edges
    encode the exact same dependencies explicitly so LangGraph can validate
    the graph is acyclic before runtime.
    """
    graph = StateGraph(AgentState)

    # Register all nodes
    graph.add_node("subreddit_discovery", subreddit_discovery)
    graph.add_node("thread_fetcher", thread_fetcher)
    graph.add_node("pain_extractor", pain_extractor)
    graph.add_node("deduplicator", deduplicator)
    graph.add_node("ranker", ranker)
    graph.add_node("report_generator", report_generator)

    # Edges
    # START → first node (always)
    graph.add_edge(START, "subreddit_discovery")

    # After discovery: conditional — abort to END on error, else continue
    graph.add_conditional_edges(
        "subreddit_discovery",
        _route_after_discovery,
        {
            "thread_fetcher": "thread_fetcher",
            END: END,
        },
    )

    # Linear pipeline from thread_fetcher onward — each node depends on the
    # previous one's output and there are no parallel branches in this design.
    graph.add_edge("thread_fetcher", "pain_extractor")
    graph.add_edge("pain_extractor", "deduplicator")
    graph.add_edge("deduplicator", "ranker")
    graph.add_edge("ranker", "report_generator")
    graph.add_edge("report_generator", END)

    return graph.compile()



# Public entry-point used by run_agent.py and future FastAPI endpoints

def run_pipeline(
    niche: str,
    job_id: Optional[str] = None,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> AgentState:
    """
    Initialise a fresh AgentState and invoke the compiled graph synchronously.

    Parameters
    ----------
    niche : str
        The product category to mine (e.g. "email marketing software").
    job_id : str, optional
        Pre-assigned UUID (e.g. from the API layer).  Generated if not given.
    progress_callback : callable(step_name: str, percent: int) -> None, optional
        Called after each node completes.  The FastAPI layer wires this to
        a WebSocket broadcaster so the frontend gets live step updates.
        Defaults to a no-op so run_agent.py CLI works unchanged.

    Returns
    -------
    AgentState — the fully populated final state after all nodes have run.
    """
    resolved_job_id = job_id or str(uuid.uuid4())
    initial_state: AgentState = {
        "niche": niche,
        "job_id": resolved_job_id,
        "discovered_subreddits": [],
        "threads": [],
        "raw_pain_points": [],
        "deduped_pain_points": [],
        "ranked_pain_points": [],
        "final_report": {},
        "status": "Initialising...",
        "error": None,
        "metadata": {},
        "progress_callback": progress_callback or _noop_callback,
    }

    app = build_graph()
    logger.info(
        "Starting pipeline for niche: '%s' (job_id=%s, callback=%s)",
        niche,
        resolved_job_id,
        "yes" if progress_callback else "no",
    )
    final_state: AgentState = app.invoke(initial_state)
    return final_state
