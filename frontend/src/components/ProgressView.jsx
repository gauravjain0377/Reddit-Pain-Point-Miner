import { useEffect, useState } from "react";
import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { PIPELINE_STEPS } from "../hooks/useAgentProgress";

/**
 * ProgressView.jsx — Animated pipeline progress display
 *
 * Composition:
 *  ┌─ ProgressBar (thin top strip, fills as percent increases)
 *  └─ StepList
 *       └─ StepRow × 6 (done | active | upcoming)
 *
 * Why a separate component?
 * ResultsPage decides WHEN to show the progress view vs. the results dashboard.
 * ProgressView only cares HOW to render the current state — clean separation
 * that makes the transition logic in ResultsPage easy to reason about.
 *
 * Animation strategy:
 * - Done steps: green checkmark, full opacity, static
 * - Active step: indigo spinner (animate-spin), pulsing background (animate-pulse-soft)
 * - Upcoming steps: gray circle outline, reduced opacity, no animation
 * This hierarchy guides the eye naturally downward through the pipeline.
 */

export default function ProgressView({ completedSteps, currentStep, percent, elapsed }) {
  return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center px-4">

      {/* ── Top progress bar ──────────────────────────────────────────────── */}
      {/*
        Fixed to the very top of the viewport. Using inline style for the width
        so we can drive it from the `percent` prop with a smooth CSS transition.
      */}
      <div className="fixed top-0 left-0 right-0 h-1 bg-slate-200 z-50">
        <div
          className="h-full bg-gradient-to-r from-brand-500 to-purple-500 transition-all duration-700 ease-out"
          style={{ width: `${percent}%` }}
        />
      </div>

      {/* ── Central content ───────────────────────────────────────────────── */}
      <div className="w-full max-w-md">

        {/* Logo + heading */}
        <div className="text-center mb-10">
          <p className="text-xs font-semibold text-brand-500 uppercase tracking-widest mb-3">
            Mining Reddit
          </p>
          <h2 className="text-3xl font-display font-bold text-slate-900 mb-2">
            {currentStep
              ? PIPELINE_STEPS.find((s) => s.key === currentStep)?.label ?? "Working…"
              : percent === 100
              ? "Wrapping up…"
              : "Initialising…"}
          </h2>
          {/* Elapsed timer — grows from 0, gives user a sense of progress */}
          <p className="text-sm text-slate-400 font-sans">
            {elapsed}s elapsed · typically 30–60 seconds total
          </p>
        </div>

        {/* ── Step list ─────────────────────────────────────────────────── */}
        <div className="bg-white rounded-2xl shadow-lg shadow-slate-100 border border-slate-100 divide-y divide-slate-50">
          {PIPELINE_STEPS.map((step) => {
            const isDone = completedSteps.includes(step.key);
            const isActive = currentStep === step.key;
            const isUpcoming = !isDone && !isActive;

            return (
              <div
                key={step.key}
                className={[
                  "flex items-center gap-4 px-5 py-4 transition-colors duration-300",
                  isActive ? "bg-brand-50 animate-pulse-soft" : "",
                ].join(" ")}
              >
                {/* Step icon — three possible states */}
                <div className="flex-shrink-0">
                  {isDone ? (
                    // Completed: solid green checkmark
                    <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  ) : isActive ? (
                    // Active: spinning indigo loader
                    <Loader2 className="w-5 h-5 text-brand-500 animate-spin" />
                  ) : (
                    // Upcoming: hollow gray circle
                    <Circle className="w-5 h-5 text-slate-300" />
                  )}
                </div>

                {/* Step label */}
                <span
                  className={[
                    "text-sm font-medium font-sans transition-colors duration-300",
                    isDone   ? "text-slate-700" : "",
                    isActive ? "text-brand-700 font-semibold" : "",
                    isUpcoming ? "text-slate-400" : "",
                  ].join(" ")}
                >
                  {step.label}
                </span>

                {/* Percent badge on the right — only shown for completed steps */}
                {isDone && (
                  <span className="ml-auto text-xs text-emerald-500 font-semibold">
                    {step.percent}%
                  </span>
                )}
                {isActive && (
                  <span className="ml-auto text-xs text-brand-400 font-semibold">
                    Running…
                  </span>
                )}
              </div>
            );
          })}
        </div>

        {/* ── Sub-note ─────────────────────────────────────────────────── */}
        <p className="text-center text-xs text-slate-400 mt-6 font-sans">
          Don't close this tab — the analysis is running in the background.
        </p>
      </div>
    </div>
  );
}
