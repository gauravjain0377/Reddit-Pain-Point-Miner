"""
run_agent.py
─────────────────────────────────────────────────────────────────────────────
CLI entry-point for the Reddit Pain Point Miner agent graph.

Usage
─────
    python backend/run_agent.py "email marketing software"
    python backend/run_agent.py "project management tools" 2>/dev/null

Output
──────
Writes structured JSON to stdout so the output can be piped to files or
consumed by other tools:

    python backend/run_agent.py "CRM software" > report.json

Progress/status messages go to stderr so they do not pollute the JSON output.
"""

from __future__ import annotations

import json
import logging
import sys

# Configure logging to stderr so stdout stays clean JSON.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)


def _print_step_banner(label: str, value: object) -> None:
    """Print a progress banner to stderr during the run."""
    print(f"\n  ▶  {label}: {value}", file=sys.stderr)


def main() -> None:
    # ── Argument validation ──────────────────────────────────────────────────
    if len(sys.argv) < 2:
        print(
            "Usage: python run_agent.py <niche>\n"
            "Example: python run_agent.py \"email marketing software\"",
            file=sys.stderr,
        )
        sys.exit(1)

    niche = sys.argv[1].strip()
    if not niche:
        print("Error: niche argument cannot be empty.", file=sys.stderr)
        sys.exit(1)

    # ── Import here (after logging is configured) ────────────────────────────
    # Late import ensures config validation errors are printed with logging
    # already set up, giving cleaner error messages.
    from agent_graph import run_pipeline  # noqa: PLC0415

    # ── Status banner ────────────────────────────────────────────────────────
    print("\n" + "═" * 60, file=sys.stderr)
    print("  Reddit Pain Point Miner", file=sys.stderr)
    print(f"  Niche: '{niche}'", file=sys.stderr)
    print("═" * 60, file=sys.stderr)

    # ── Run the pipeline ─────────────────────────────────────────────────────
    logger.info("Invoking agent graph for niche: '%s'", niche)

    try:
        final_state = run_pipeline(niche)
    except KeyboardInterrupt:
        print("\n\nAborted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        logger.exception("Pipeline failed with an unexpected error: %s", exc)
        sys.exit(1)

    # ── Handle pipeline-level errors ─────────────────────────────────────────
    if final_state.get("error"):
        print(f"\n❌  Pipeline error: {final_state['error']}", file=sys.stderr)
        # Still emit the partial state as JSON so the caller can inspect it.
        output = {
            "error": final_state["error"],
            "niche": final_state["niche"],
            "job_id": final_state["job_id"],
            "metadata": final_state.get("metadata", {}),
        }
        print(json.dumps(output, indent=2, default=str))
        sys.exit(1)

    # ── Print summary to stderr ───────────────────────────────────────────────
    report = final_state.get("final_report", {})
    meta = final_state.get("metadata", {})

    print("\n" + "─" * 60, file=sys.stderr)
    _print_step_banner("Subreddits mined", meta.get("subreddits_found", "?"))
    _print_step_banner("Threads fetched", meta.get("thread_count", "?"))
    _print_step_banner(
        "Fetch time",
        f"{meta.get('fetch_time_seconds', '?')}s",
    )
    _print_step_banner("Raw pain points", meta.get("raw_pain_point_count", "?"))
    _print_step_banner("After dedup", meta.get("deduped_pain_point_count", "?"))
    _print_step_banner("OpenAI tokens used", meta.get("tokens_used", 0))
    _print_step_banner(
        "Top pain point",
        report.get("top_pain_points", [{}])[0].get("pain_text", "N/A")[:80]
        if report.get("top_pain_points")
        else "None",
    )
    print("─" * 60 + "\n", file=sys.stderr)

    # ── Emit the final report as formatted JSON to stdout ────────────────────
    # default=str handles any non-serialisable types (e.g. enums, Pydantic models)
    print(json.dumps(report, indent=2, default=str))

    print("\n✅  Done. Report written to stdout.\n", file=sys.stderr)


if __name__ == "__main__":
    main()
