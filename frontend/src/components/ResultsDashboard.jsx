import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  Search, Clock, MessageSquare, Layers,
  SortDesc, SortAsc, X, Zap,
} from "lucide-react";
import PainPointCard from "./PainPointCard";
import CategoryChart from "./CategoryChart";
import SummaryCard from "./SummaryCard";
import ExportBar from "./ExportBar";

/**
 * ResultsDashboard.jsx — top-level layout for the completed report.
 *
 * Layout:
 *   ┌──────────────────────────────────────────┐
 *   │ TopBar (niche h1 + metadata pills)        │
 *   ├──────────────────────────────────────────┤
 *   │ ExportBar                                 │
 *   ├─────────────────────────┬────────────────┤
 *   │ SummaryCard             │                │
 *   │ FilterBar               │ Sticky sidebar │
 *   │ PainPointCard × N       │ CategoryChart  │
 *   │                         │ TopQuotes      │
 *   └─────────────────────────┴────────────────┘
 *
 * State owned here (not in children) because filtering/sorting affects
 * both the card list count AND the category chart highlight.
 */

// Ordered category colors — consistent across card badges and chart bars
export const CATEGORY_COLORS = {
  "Pricing":          { bg: "bg-red-100",    text: "text-red-700",    bar: "#fca5a5" },
  "UX/Design":        { bg: "bg-violet-100", text: "text-violet-700", bar: "#c4b5fd" },
  "Performance":      { bg: "bg-orange-100", text: "text-orange-700", bar: "#fdba74" },
  "Missing Feature":  { bg: "bg-blue-100",   text: "text-blue-700",   bar: "#93c5fd" },
  "Customer Support": { bg: "bg-pink-100",   text: "text-pink-700",   bar: "#f9a8d4" },
  "Onboarding":       { bg: "bg-yellow-100", text: "text-yellow-700", bar: "#fde68a" },
  "Integration":      { bg: "bg-teal-100",   text: "text-teal-700",   bar: "#99f6e4" },
  "Reliability":      { bg: "bg-amber-100",  text: "text-amber-700",  bar: "#fcd34d" },
  "Documentation":    { bg: "bg-sky-100",    text: "text-sky-700",    bar: "#7dd3fc" },
  "Other":            { bg: "bg-slate-100",  text: "text-slate-600",  bar: "#cbd5e1" },
};

function fmt(n) {
  if (n == null) return "—";
  return Number(n).toLocaleString();
}

export default function ResultsDashboard({ report, fromCache }) {
  const navigate = useNavigate();

  const [activeCategory, setActiveCategory] = useState(null); // null = all
  const [sortBy, setSortBy] = useState("severity");            // "severity" | "mentions"

  const allPoints = report?.top_pain_points ?? [];
  const meta      = report?.run_metadata ?? {};

  // ── Filtered + sorted pain points ────────────────────────────────────────
  const visiblePoints = useMemo(() => {
    let pts = activeCategory
      ? allPoints.filter((p) => p.category === activeCategory)
      : allPoints;

    return [...pts].sort((a, b) =>
      sortBy === "severity"
        ? b.severity - a.severity
        : b.mention_count - a.mention_count
    );
  }, [allPoints, activeCategory, sortBy]);

  // Toggle category filter — clicking the active one clears it
  function handleCategoryClick(cat) {
    setActiveCategory((prev) => (prev === cat ? null : cat));
  }

  return (
    <div className="min-h-screen bg-slate-50">

      {/* ── Completed progress bar (solid green) ─────────────────────────── */}
      <div className="fixed top-0 left-0 right-0 h-1 bg-emerald-400 z-50" />

      {/* ── Top bar ──────────────────────────────────────────────────────── */}
      <header className="bg-white border-b border-slate-100 sticky top-1 z-40 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex flex-col sm:flex-row sm:items-center gap-3">

          {/* Logo mark */}
          <div className="flex items-center gap-2 mr-4">
            <div className="w-7 h-7 bg-brand-600 rounded-lg flex items-center justify-center flex-shrink-0">
              <Zap className="w-3.5 h-3.5 text-white" fill="white" />
            </div>
          </div>

          {/* Niche headline */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-lg font-bold text-slate-900 font-display truncate">
                {report?.niche ?? "Analysis"}
              </h1>
              {fromCache && (
                <span className="text-xs font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
                  ⚡ cached
                </span>
              )}
            </div>

            {/* Metadata pills */}
            <div className="flex flex-wrap items-center gap-3 mt-1">
              <Pill icon={<Layers className="w-3 h-3" />} label={`${fmt(meta.thread_count ?? meta.threads_analyzed)} threads`} />
              <Pill icon={<MessageSquare className="w-3 h-3" />} label={`${fmt(meta.comments_analyzed ?? "—")} comments`} />
              <Pill icon={<Clock className="w-3 h-3" />} label={`${meta.fetch_time_seconds ?? meta.duration_seconds ?? "—"}s`} />
              <Pill icon={<Search className="w-3 h-3" />} label={`${fmt(meta.tokens_used)} tokens`} />
            </div>
          </div>

          {/* New Search button */}
          <button
            onClick={() => navigate("/")}
            className="flex-shrink-0 flex items-center gap-2 px-4 py-2 rounded-xl
                       bg-gradient-to-r from-brand-600 to-purple-600 text-white
                       text-sm font-semibold hover:from-brand-700 hover:to-purple-700
                       transition-all duration-200 shadow-sm"
          >
            <Search className="w-3.5 h-3.5" />
            New Search
          </button>
        </div>
      </header>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-8">

        {/* Export bar */}
        <ExportBar report={report} />

        {/* Two-column grid: main (70%) + sidebar (30%) */}
        <div className="mt-6 flex flex-col lg:flex-row gap-8 items-start">

          {/* ── Left: summary + filter bar + cards ───────────────────────── */}
          <div className="flex-1 min-w-0">

            <SummaryCard summary={report?.summary} />

            {/* Filter / sort bar */}
            <div className="flex items-center justify-between mt-6 mb-4 flex-wrap gap-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-slate-700">
                  {visiblePoints.length} pain point{visiblePoints.length !== 1 ? "s" : ""}
                </span>
                {activeCategory && (
                  <button
                    onClick={() => setActiveCategory(null)}
                    className="flex items-center gap-1 text-xs px-2 py-1 rounded-full
                               bg-brand-100 text-brand-700 hover:bg-brand-200 transition-colors"
                  >
                    <X className="w-3 h-3" />
                    {activeCategory}
                  </button>
                )}
              </div>

              {/* Sort toggle */}
              <div className="flex items-center gap-1 bg-white border border-slate-200 rounded-lg p-1">
                <SortButton
                  active={sortBy === "severity"}
                  onClick={() => setSortBy("severity")}
                  icon={<SortDesc className="w-3.5 h-3.5" />}
                  label="By Severity"
                />
                <SortButton
                  active={sortBy === "mentions"}
                  onClick={() => setSortBy("mentions")}
                  icon={<SortAsc className="w-3.5 h-3.5" />}
                  label="By Mentions"
                />
              </div>
            </div>

            {/* Pain point cards list */}
            <div className="space-y-4">
              {visiblePoints.length === 0 ? (
                <div className="bg-white rounded-2xl border border-slate-100 p-10 text-center">
                  <p className="text-slate-400 text-sm">No pain points in this category.</p>
                </div>
              ) : (
                visiblePoints.map((pp, i) => (
                  <PainPointCard key={`${pp.rank}-${i}`} painPoint={pp} index={i} />
                ))
              )}
            </div>
          </div>

          {/* ── Right: sticky sidebar ─────────────────────────────────────── */}
          <aside className="lg:w-80 xl:w-96 flex-shrink-0 lg:sticky lg:top-24 space-y-6">
            <CategoryChart
              breakdown={report?.categories_breakdown ?? {}}
              activeCategory={activeCategory}
              onCategoryClick={handleCategoryClick}
            />
            <TopQuotesSidebar quotes={report?.top_quotes ?? []} />
          </aside>
        </div>
      </div>
    </div>
  );
}

// ── Small helper sub-components ───────────────────────────────────────────────

function Pill({ icon, label }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-slate-500 font-sans">
      {icon}
      {label}
    </span>
  );
}

function SortButton({ active, onClick, icon, label }) {
  return (
    <button
      onClick={onClick}
      className={[
        "flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md transition-all duration-150",
        active
          ? "bg-brand-600 text-white shadow-sm"
          : "text-slate-500 hover:text-slate-700",
      ].join(" ")}
    >
      {icon}
      {label}
    </button>
  );
}

function TopQuotesSidebar({ quotes }) {
  if (!quotes?.length) return null;
  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-4">
        Top Quotes
      </h3>
      <div className="space-y-4">
        {quotes.map((q, i) => (
          <div key={i} className="border-l-2 border-brand-300 pl-3">
            <p className="text-sm text-slate-600 italic leading-relaxed font-sans line-clamp-4">
              "{q.quote}"
            </p>
            <div className="flex items-center justify-between mt-2">
              <span className={[
                "text-xs font-semibold px-2 py-0.5 rounded-full",
                CATEGORY_COLORS[q.category]?.bg ?? "bg-slate-100",
                CATEGORY_COLORS[q.category]?.text ?? "text-slate-600",
              ].join(" ")}>
                {q.category}
              </span>
              <span className="text-xs text-slate-400">sev. {q.severity}/10</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
