import { useEffect, useState } from "react";
import { useParams, useLocation, useNavigate } from "react-router-dom";
import { AlertCircle, RefreshCw } from "lucide-react";
import useAgentProgress from "../hooks/useAgentProgress";
import ProgressView from "../components/ProgressView";
import ResultsDashboard from "../components/ResultsDashboard";
import { PainPointSkeleton } from "../components/PainPointCard";
import EmptyState from "../components/EmptyState";
import { saveToHistory } from "./HistoryPage";

/**
 * ResultsPage.jsx — orchestrates the full results lifecycle.
 *
 * States:
 *  1. Running   → <ProgressView> (WebSocket-driven step list)
 *  2. Skeletons → 5 PainPointSkeleton cards during 50ms fade-in transition
 *  3. Empty     → <EmptyState> when report has 0 pain points
 *  4. Complete  → <ResultsDashboard> (full data dashboard)
 *  5. Error     → red error card + "Try again" button
 *
 * Data persistence:
 *  On completion, saves the result to localStorage via saveToHistory()
 *  so it appears in /history.
 */

export default function ResultsPage() {
  const { jobId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();

  const initialReport = location.state?.report ?? null;
  const fromCache = location.state?.fromCache ?? false;

  const { completedSteps, currentStep, percent, isComplete, error, report } =
    useAgentProgress(jobId, initialReport);

  // ── Elapsed timer ─────────────────────────────────────────────────────────
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (isComplete || error) return;
    const id = setInterval(() => setElapsed((s) => Math.min(s + 1, 120)), 1000);
    return () => clearInterval(id);
  }, [isComplete, error]);

  // ── Show skeletons briefly while the results fade in ──────────────────────
  const [showSkeletons, setShowSkeletons] = useState(false);
  const [showResults, setShowResults] = useState(false);

  useEffect(() => {
    if (!isComplete) return;

    // Step 1: save to history
    if (report && jobId) {
      saveToHistory(jobId, report.niche ?? "Unknown", report);
    }

    // Step 2: show skeletons for 400ms so the transition feels intentional
    setShowSkeletons(true);
    const t1 = setTimeout(() => {
      setShowSkeletons(false);
      setShowResults(true);
    }, 400);

    return () => clearTimeout(t1);
  }, [isComplete]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Error state ──────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4 pt-16">
        <div className="w-full max-w-md bg-white rounded-2xl border border-red-100 shadow-lg shadow-red-50 p-8 text-center">
          <div className="w-12 h-12 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <AlertCircle className="w-6 h-6 text-red-500" />
          </div>
          <h2 className="text-xl font-bold text-slate-900 mb-2 font-display">
            Analysis Failed
          </h2>
          <p className="text-sm text-slate-500 font-sans mb-6 leading-relaxed">
            {error}
          </p>
          <button
            onClick={() => navigate("/")}
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl
                       bg-gradient-to-r from-brand-600 to-purple-600 text-white
                       font-semibold text-sm hover:from-brand-700 hover:to-purple-700
                       transition-all duration-200"
          >
            <RefreshCw className="w-4 h-4" />
            Try again
          </button>
        </div>
      </div>
    );
  }

  // ── Progress state ────────────────────────────────────────────────────────
  if (!isComplete && !showSkeletons) {
    return (
      <ProgressView
        completedSteps={completedSteps}
        currentStep={currentStep}
        percent={percent}
        elapsed={elapsed}
      />
    );
  }

  // ── Skeleton transition state ─────────────────────────────────────────────
  if (showSkeletons) {
    return (
      <div className="min-h-screen bg-slate-50 pt-24 pb-16 px-4">
        <div className="max-w-2xl mx-auto space-y-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <PainPointSkeleton key={i} index={i} />
          ))}
        </div>
      </div>
    );
  }

  // ── Empty state ───────────────────────────────────────────────────────────
  const hasResults = (report?.top_pain_points?.length ?? 0) > 0;
  if (!hasResults && showResults) {
    return (
      <div className="min-h-screen bg-slate-50 pt-16">
        <EmptyState niche={report?.niche ?? "this niche"} />
      </div>
    );
  }

  // ── Full results dashboard ────────────────────────────────────────────────
  return (
    <div
      className={[
        "transition-opacity duration-500",
        showResults ? "opacity-100" : "opacity-0",
      ].join(" ")}
    >
      <ResultsDashboard report={report} fromCache={fromCache} />
    </div>
  );
}
