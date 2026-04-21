import { ExternalLink } from "lucide-react";
import { CATEGORY_COLORS } from "./ResultsDashboard";

/**
 * PainPointCard.jsx — individual pain point display card.
 *
 * Visual anatomy:
 *   ┌─ severity bar (4px left stripe, color by severity tier)
 *   │  ┌─ rank circle
 *   │  │  pain_text heading
 *   │  │  category badge · severity dots · confidence
 *   │  │  ── blockquote (verbatim_quote + View on Reddit link) ──
 *   │  │  X mentions across threads
 *   └──┘
 *
 * Staggered entrance: each card delays by 60ms × index, creating a
 * cascade effect as the page loads rather than a jarring all-at-once reveal.
 *
 * Also exports: PainPointSkeleton — 5 gray animated placeholder cards shown
 * during the brief transition between progress complete and data available.
 */

// Severity tier → left-edge stripe color
function severityColor(s) {
  if (s >= 9) return "bg-red-500";
  if (s >= 7) return "bg-orange-400";
  if (s >= 4) return "bg-yellow-400";
  return "bg-emerald-400";
}

// Dot severity scale — N filled dots out of 10
function SeverityDots({ severity }) {
  return (
    <div className="flex items-center gap-0.5" title={`Severity ${severity}/10`}>
      {Array.from({ length: 10 }).map((_, i) => (
        <div
          key={i}
          className={[
            "w-2 h-2 rounded-full",
            i < severity
              ? severity >= 9
                ? "bg-red-500"
                : severity >= 7
                ? "bg-orange-400"
                : severity >= 4
                ? "bg-yellow-400"
                : "bg-emerald-400"
              : "bg-slate-200",
          ].join(" ")}
        />
      ))}
    </div>
  );
}

export default function PainPointCard({ painPoint: pp, index }) {
  const catColor = CATEGORY_COLORS[pp.category] ?? CATEGORY_COLORS["Other"];

  return (
    <div
      className="flex rounded-2xl bg-white border border-slate-100 shadow-sm
                 hover:shadow-md hover:border-slate-200 transition-all duration-200
                 animate-fade-in overflow-hidden"
      style={{ animationDelay: `${index * 60}ms`, animationFillMode: "both", opacity: 0 }}
    >
      {/* Left severity stripe */}
      <div className={`w-1 flex-shrink-0 ${severityColor(pp.severity)}`} />

      {/* Card body */}
      <div className="flex-1 p-5 min-w-0">

        {/* Row 1: rank + pain_text */}
        <div className="flex items-start gap-3 mb-3">
          <span className="flex-shrink-0 w-7 h-7 rounded-full bg-slate-100 text-slate-500
                           text-xs font-bold flex items-center justify-center mt-0.5">
            {pp.rank}
          </span>
          <p className="text-base font-medium text-slate-800 leading-snug font-sans">
            {pp.pain_text}
          </p>
        </div>

        {/* Row 2: category + severity dots + confidence */}
        <div className="flex flex-wrap items-center gap-3 mb-4 pl-10">
          <span className={`text-xs font-semibold px-2.5 py-1 rounded-full ${catColor.bg} ${catColor.text}`}>
            {pp.category}
          </span>
          <div className="flex items-center gap-2">
            <SeverityDots severity={pp.severity} />
            <span className="text-xs text-slate-400 font-sans">{pp.severity}/10</span>
          </div>
          <span className="text-xs text-slate-400 font-sans">
            {Math.round((pp.confidence ?? 0) * 100)}% confidence
          </span>
        </div>

        {/* Blockquote */}
        <div className="pl-10">
          <blockquote className="border-l-2 border-brand-200 pl-4 mb-3">
            <p className="text-sm text-slate-500 italic leading-relaxed font-sans">
              "{pp.verbatim_quote}"
            </p>
            {pp.source_url && (
              <a
                href={pp.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 mt-2 text-xs text-brand-500
                           hover:text-brand-700 font-medium transition-colors"
              >
                View on Reddit
                <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </blockquote>

          <p className="text-xs text-slate-400 font-sans">
            <span className="font-semibold text-slate-600">{pp.mention_count}</span>{" "}
            mention{pp.mention_count !== 1 ? "s" : ""} across threads
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * PainPointSkeleton — animated gray placeholder card.
 *
 * Shown during the ~50ms transition between progress completion and
 * the real cards fading in. Uses Tailwind's animate-pulse for the
 * shimmer effect.
 */
export function PainPointSkeleton({ index = 0 }) {
  return (
    <div
      className="flex rounded-2xl bg-white border border-slate-100 overflow-hidden
                 animate-pulse"
      style={{ animationDelay: `${index * 80}ms` }}
    >
      {/* Left stripe placeholder */}
      <div className="w-1 bg-slate-200 flex-shrink-0" />

      <div className="flex-1 p-5 space-y-3">
        {/* Rank + title line */}
        <div className="flex items-start gap-3">
          <div className="w-7 h-7 rounded-full bg-slate-200 flex-shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="h-4 bg-slate-200 rounded-md w-4/5" />
            <div className="h-4 bg-slate-200 rounded-md w-2/3" />
          </div>
        </div>

        {/* Badges row */}
        <div className="flex gap-2 pl-10">
          <div className="h-5 w-16 bg-slate-200 rounded-full" />
          <div className="h-5 w-24 bg-slate-200 rounded-full" />
        </div>

        {/* Blockquote */}
        <div className="pl-10 space-y-2">
          <div className="h-3 bg-slate-100 rounded w-full" />
          <div className="h-3 bg-slate-100 rounded w-5/6" />
          <div className="h-3 bg-slate-100 rounded w-3/4" />
        </div>
      </div>
    </div>
  );
}
