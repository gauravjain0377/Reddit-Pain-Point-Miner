"""
api/main.py
─────────────────────────────────────────────────────────────────────────────
FastAPI application for the Reddit Pain Point Miner backend.

Endpoints
─────────
  POST   /api/analyze          — start a new analysis job (cache-aware)
  GET    /api/status/{job_id}  — poll current job status
  GET    /api/result/{job_id}  — retrieve completed report
  WS     /ws/{job_id}          — live progress stream via WebSocket
  DELETE /api/cache/{niche}    — invalidate a cached niche
  GET    /api/health           — health check

Architecture
────────────
Jobs run in a ThreadPoolExecutor (via run_in_executor) so the async FastAPI
event loop is never blocked.  Each job stores its state in the in-memory JOBS
dict.  WebSocket clients subscribe to a per-job asyncio.Queue; the background
thread pushes progress events into the queue via call_soon_threadsafe.

Why a queue per job instead of direct WebSocket.send() from a thread?
Because WebSocket.send() is a coroutine — you cannot call it from a synchronous
thread without a running event loop reference.  Posting to an asyncio.Queue is
the correct thread-safe bridge between the sync pipeline and the async WebSocket
handler.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Ensure backend/ is on sys.path when running from the api/ subdirectory ──
_BACKEND_DIR = Path(__file__).parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent_graph import run_pipeline
from cache import cache as analysis_cache

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# FastAPI app setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Reddit Pain Point Miner API",
    description="Mine Reddit for product pain points using a LangGraph agent.",
    version="0.4.0",
)

# CORS — allow the Vite dev server (and localhost variants) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite default
        "http://127.0.0.1:5173",
        "http://localhost:3000",   # Create-React-App fallback
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory job registry
# ---------------------------------------------------------------------------
# Structure per job:
#   {
#       "job_id":       str,
#       "niche":        str,
#       "status":       "queued" | "running" | "complete" | "error",
#       "current_step": str,          # last completed node name
#       "progress_percent": int,      # 0–100
#       "report":       dict | None,  # populated on completion
#       "error":        str | None,
#       "created_at":   str,          # ISO timestamp
#       "from_cache":   bool,
#   }
JOBS: dict[str, dict] = {}

# Per-job asyncio queues — WebSocket handlers drain these
# Key: job_id, Value: asyncio.Queue of JSON-serialisable dicts
_WS_QUEUES: dict[str, asyncio.Queue] = {}

# Thread pool for running the synchronous LangGraph pipeline
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pipeline")

# Cache hit/miss counters for the health endpoint hit_rate calculation
_CACHE_HITS = 0
_CACHE_MISSES = 0


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    niche: str = Field(..., min_length=2, max_length=200, description="Product niche to research")
    max_threads: int = Field(default=50, ge=1, le=500)
    use_cache: bool = Field(default=True, description="Return cached result if available")


class AnalyzeResponse(BaseModel):
    job_id: str
    status: str
    from_cache: bool = False
    report: Optional[dict] = None  # only set on immediate cache hit


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    current_step: str
    progress_percent: int
    error: Optional[str] = None
    created_at: str


# ---------------------------------------------------------------------------
# Helper: push an event into the per-job WS queue from any thread
# ---------------------------------------------------------------------------

def _push_ws_event(loop: asyncio.AbstractEventLoop, job_id: str, event: dict) -> None:
    """
    Thread-safe bridge: post an event dict into the job's asyncio.Queue.
    Called from the pipeline background thread; uses call_soon_threadsafe
    so the queue.put_nowait() runs on the event loop thread.
    """
    q = _WS_QUEUES.get(job_id)
    if q is None:
        return
    # call_soon_threadsafe schedules the coroutine on the event loop safely
    loop.call_soon_threadsafe(q.put_nowait, event)


# ---------------------------------------------------------------------------
# Pipeline runner — executed in the thread pool
# ---------------------------------------------------------------------------

def _run_pipeline_in_thread(
    job_id: str,
    niche: str,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Runs run_pipeline() synchronously in a worker thread.
    Updates JOBS and pushes WebSocket events as the pipeline progresses.

    The progress_callback is a simple closure that:
    1. Updates the JOBS dict (safe: GIL protects dict writes in CPython)
    2. Pushes a WS event via the event loop
    """
    JOBS[job_id]["status"] = "running"

    def progress_callback(step_name: str, percent: int) -> None:
        JOBS[job_id]["current_step"] = step_name
        JOBS[job_id]["progress_percent"] = percent
        _push_ws_event(loop, job_id, {
            "event": "progress",
            "step": step_name,
            "percent": percent,
        })

    try:
        final_state = run_pipeline(
            niche=niche,
            job_id=job_id,
            progress_callback=progress_callback,
        )

        if final_state.get("error"):
            # Pipeline-level error (e.g. no subreddits found)
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = final_state["error"]
            _push_ws_event(loop, job_id, {
                "event": "error",
                "message": final_state["error"],
            })
        else:
            report = final_state.get("final_report", {})
            JOBS[job_id]["status"] = "complete"
            JOBS[job_id]["report"] = report
            JOBS[job_id]["progress_percent"] = 100

            # Cache the result for future requests
            analysis_cache.set(niche, report)

            _push_ws_event(loop, job_id, {
                "event": "done",
                "report": report,
            })
            logger.info("[API] Job %s complete for niche='%s'", job_id, niche)

    except Exception as exc:
        logger.exception("[API] Job %s crashed: %s", job_id, exc)
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(exc)
        _push_ws_event(loop, job_id, {
            "event": "error",
            "message": f"Internal pipeline error: {exc}",
        })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/analyze", response_model=AnalyzeResponse, status_code=202)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """
    Start a new pain-point analysis for the given niche.

    Flow:
    1. If use_cache=True and a fresh cache entry exists → return immediately
       with from_cache=True and the full report (no job launched).
    2. Otherwise → create a job, launch it in the thread pool, return job_id
       immediately so the client can open the WebSocket.

    Why 202 Accepted?
    The pipeline takes 1–3 minutes.  202 signals "accepted but not yet done"
    which is more honest than 200 (which implies completion).
    """
    global _CACHE_HITS, _CACHE_MISSES

    niche = req.niche.strip()

    # ── Cache check ──────────────────────────────────────────────────────────
    if req.use_cache:
        cached = analysis_cache.get(niche)
        if cached:
            _CACHE_HITS += 1
            logger.info("[API] Cache hit for niche='%s'", niche)
            return AnalyzeResponse(
                job_id=cached.get("job_id", str(uuid.uuid4())),
                status="complete",
                from_cache=True,
                report=cached,
            )
        _CACHE_MISSES += 1

    # ── Launch new job ───────────────────────────────────────────────────────
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    JOBS[job_id] = {
        "job_id": job_id,
        "niche": niche,
        "status": "queued",
        "current_step": "queued",
        "progress_percent": 0,
        "report": None,
        "error": None,
        "created_at": now,
        "from_cache": False,
    }

    # Create a fresh asyncio.Queue for this job's WebSocket events
    _WS_QUEUES[job_id] = asyncio.Queue()

    # Get the running event loop BEFORE handing off to the thread executor.
    # The executor thread will capture this loop via closure to push WS events.
    loop = asyncio.get_event_loop()

    # run_in_executor runs _run_pipeline_in_thread in the ThreadPoolExecutor
    # without blocking the event loop.  We do NOT await the future so this
    # endpoint returns immediately.
    loop.run_in_executor(
        _EXECUTOR,
        _run_pipeline_in_thread,
        job_id,
        niche,
        loop,
    )

    logger.info("[API] Launched job %s for niche='%s'", job_id, niche)
    return AnalyzeResponse(job_id=job_id, status="queued")


@app.get("/api/status/{job_id}", response_model=JobStatusResponse)
async def get_status(job_id: str) -> JobStatusResponse:
    """
    Poll the current status of a running or completed job.

    progress_percent mapping:
      0   — queued / not yet started
      17  — subreddit_discovery complete
      33  — thread_fetcher complete
      50  — pain_extractor complete
      67  — deduplicator complete
      83  — ranker complete
      100 — report_generator complete / done
    """
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        current_step=job["current_step"],
        progress_percent=job["progress_percent"],
        error=job.get("error"),
        created_at=job["created_at"],
    )


@app.get("/api/result/{job_id}")
async def get_result(job_id: str) -> dict:
    """
    Retrieve the final report for a completed job.

    Returns 404 while the job is still running — the client should either
    poll /api/status or wait for the WebSocket "done" event before calling this.
    """
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job["status"] != "complete":
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' is not yet complete (status='{job['status']}').",
        )

    return job["report"]


@app.websocket("/ws/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str) -> None:
    """
    WebSocket endpoint for live progress streaming.

    Protocol:
      Client connects after POST /api/analyze returns a job_id.
      Server sends JSON messages:
        {"event": "progress", "step": str, "percent": int}  — after each node
        {"event": "done",     "report": {...}}               — on completion
        {"event": "error",    "message": str}                — on failure

    The server drains the per-job asyncio.Queue (populated by the pipeline
    thread via _push_ws_event) and forwards each message to the WebSocket.

    Connection lifecycle:
      - If job_id doesn't exist: send error and close immediately.
      - If job is already complete (e.g. client reconnects): send done immediately.
      - Otherwise: stream events until done/error, then close.
    """
    await websocket.accept()

    # ── Guard: unknown job ───────────────────────────────────────────────────
    job = JOBS.get(job_id)
    if job is None:
        await websocket.send_json({"event": "error", "message": f"Unknown job '{job_id}'"})
        await websocket.close()
        return

    # ── Fast path: job already complete ─────────────────────────────────────
    if job["status"] == "complete":
        await websocket.send_json({"event": "done", "report": job["report"]})
        await websocket.close()
        return

    if job["status"] == "error":
        await websocket.send_json({"event": "error", "message": job.get("error", "Unknown error")})
        await websocket.close()
        return

    # ── Stream events from the queue ─────────────────────────────────────────
    q = _WS_QUEUES.get(job_id)
    if q is None:
        # Queue was never created (race condition, very unlikely)
        q = asyncio.Queue()
        _WS_QUEUES[job_id] = q

    try:
        while True:
            # Wait for the next event with a 180s timeout (max expected pipeline duration)
            try:
                event = await asyncio.wait_for(q.get(), timeout=180.0)
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "event": "error",
                    "message": "Pipeline timed out after 180 seconds.",
                })
                break

            await websocket.send_json(event)

            # Stop streaming once the terminal event arrives
            if event.get("event") in ("done", "error"):
                break

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected from job %s", job_id)
    except Exception as exc:
        logger.warning("[WS] Unexpected error for job %s: %s", job_id, exc)
        try:
            await websocket.send_json({"event": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        # Clean up the queue to free memory (the job result stays in JOBS)
        _WS_QUEUES.pop(job_id, None)


@app.delete("/api/cache/{niche}", status_code=200)
async def invalidate_cache(niche: str) -> dict:
    """
    Invalidate the cache entry for a given niche.

    Useful when you want to force a fresh analysis despite a valid cache entry
    (e.g. the niche landscape has shifted significantly).

    Returns {"deleted": true} if an entry was found and removed,
            {"deleted": false} if no entry existed.
    """
    deleted = analysis_cache.invalidate(niche)
    return {"deleted": deleted, "niche": niche}


@app.get("/api/health")
async def health() -> dict:
    """
    Basic health-check endpoint.

    Returns server status, active job count, cache stats, and cost hit-rate
    so ops tooling can monitor the service without querying internal state.
    """
    stats = analysis_cache.get_stats()
    total_requests = _CACHE_HITS + _CACHE_MISSES
    hit_rate = round(_CACHE_HITS / total_requests, 3) if total_requests > 0 else 0.0

    active_jobs = sum(
        1 for j in JOBS.values() if j["status"] in ("queued", "running")
    )

    return {
        "status": "ok",
        "cache_entries": stats.total_entries,
        "cache_hit_rate": hit_rate,
        "oldest_cache_entry_age_hours": stats.oldest_entry_age_hours,
        "active_jobs": active_jobs,
        "total_jobs_seen": len(JOBS),
    }


# ---------------------------------------------------------------------------
# Startup / shutdown lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    """Clean up expired cache entries on every server start."""
    removed = analysis_cache.cleanup_expired()
    logger.info("[startup] Cleaned up %d expired cache entries.", removed)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Gracefully shut down the thread pool."""
    logger.info("[shutdown] Shutting down thread pool executor…")
    _EXECUTOR.shutdown(wait=False)
    analysis_cache.close()
