import { useEffect, useRef, useState, useCallback } from "react";

/**
 * useAgentProgress.js — Custom hook for WebSocket-based progress streaming
 *
 * Manages:
 *  - WebSocket lifecycle (open, message, error, close)
 *  - Reconnection logic (up to 3 retries with 2s delay)
 *  - State: { steps, currentStep, percent, isComplete, error, report }
 *
 * Why a custom hook instead of inline logic in ResultsPage?
 * Separating WebSocket logic from rendering keeps the page component focused
 * on presentation. It also makes the hook independently testable (you can
 * mock WebSocket in tests without touching JSX).
 *
 * Why useRef for the socket, not useState?
 * The WebSocket object should not trigger re-renders when assigned — only the
 * derived state values (steps, percent, etc.) should cause re-renders.
 * useRef holds the socket across renders without causing one.
 */

const WS_BASE = "ws://localhost:8000";

// The 6 pipeline steps in order — drives the progress list display
export const PIPELINE_STEPS = [
  { key: "subreddit_discovery", label: "Discovering subreddits",   percent: 17 },
  { key: "thread_fetcher",      label: "Fetching Reddit threads",   percent: 33 },
  { key: "pain_extractor",      label: "Extracting pain points",    percent: 50 },
  { key: "deduplicator",        label: "Removing duplicates",       percent: 67 },
  { key: "ranker",              label: "Ranking by severity",       percent: 83 },
  { key: "report_generator",    label: "Generating report",         percent: 100 },
];

export default function useAgentProgress(jobId, initialReport = null) {
  const [state, setState] = useState({
    completedSteps: [],   // list of step keys that have finished
    currentStep: null,    // key of the currently-running step
    percent: 0,
    isComplete: !!initialReport,
    error: null,
    report: initialReport,
  });

  const wsRef = useRef(null);
  const retriesRef = useRef(0);
  const MAX_RETRIES = 3;
  const RETRY_DELAY_MS = 2000;

  const connect = useCallback(() => {
    if (!jobId) return;

    const url = `${WS_BASE}/ws/${jobId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      retriesRef.current = 0; // reset retry counter on successful connection
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return; // ignore malformed frames
      }

      if (msg.event === "progress") {
        // A pipeline node just finished — update completed steps and percent
        setState((prev) => {
          const step = PIPELINE_STEPS.find((s) => s.key === msg.step);
          const newCompleted = prev.completedSteps.includes(msg.step)
            ? prev.completedSteps
            : [...prev.completedSteps, msg.step];

          // The "current" step is the one immediately after the last completed
          const lastCompletedIdx = PIPELINE_STEPS.findIndex(
            (s) => s.key === msg.step
          );
          const nextStep = PIPELINE_STEPS[lastCompletedIdx + 1]?.key ?? null;

          return {
            ...prev,
            completedSteps: newCompleted,
            currentStep: nextStep,
            percent: step?.percent ?? msg.percent ?? prev.percent,
          };
        });
      } else if (msg.event === "done") {
        // All nodes finished — store report and mark complete
        setState({
          completedSteps: PIPELINE_STEPS.map((s) => s.key),
          currentStep: null,
          percent: 100,
          isComplete: true,
          error: null,
          report: msg.report,
        });
        ws.close();
      } else if (msg.event === "error") {
        setState((prev) => ({
          ...prev,
          error: msg.message || "An unknown error occurred.",
          isComplete: false,
        }));
        ws.close();
      }
    };

    ws.onerror = () => {
      // onerror is always followed by onclose — handle retry there
    };

    ws.onclose = (ev) => {
      // Don't retry if we cleanly closed (code 1000) or if already complete/errored
      if (ev.code === 1000) return;

      setState((prev) => {
        if (prev.isComplete || prev.error) return prev;

        if (retriesRef.current < MAX_RETRIES) {
          retriesRef.current += 1;
          setTimeout(connect, RETRY_DELAY_MS);
        } else {
          return {
            ...prev,
            error: `Lost connection to the server after ${MAX_RETRIES} retries.`,
          };
        }
        return prev;
      });
    };
  }, [jobId]);

  useEffect(() => {
    // If we already have a report (cache hit), skip the WebSocket entirely
    if (initialReport) return;

    connect();

    return () => {
      // Cleanup: close the socket when the component unmounts
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.close(1000, "Component unmounted");
      }
    };
  }, [connect, initialReport]);

  return state;
}
