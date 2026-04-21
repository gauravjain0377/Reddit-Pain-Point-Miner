import { useNavigate } from "react-router-dom";
import { SearchX, Lightbulb } from "lucide-react";

/**
 * EmptyState.jsx — shown when the pipeline completes with 0 pain points.
 *
 * This happens when a niche is too obscure for Reddit to have significant
 * discussion (e.g. "Salesforce CPQ integration edge cases").
 *
 * Design goals:
 * - Empathetic, not alarming — the tool worked; the niche was just too narrow.
 * - Actionable — give concrete broader alternatives they can try immediately.
 * - Consistent aesthetic with the rest of the app.
 */

// Broader alternative suggestions derived from common too-narrow patterns
const BROADER_SUGGESTIONS = [
  "Try removing brand names — use 'CRM software' instead of 'HubSpot'",
  "Use the product category, not a specific feature — 'email marketing' not 'A/B subject line testing'",
  "Broaden the audience — 'project management' instead of 'agile sprint planning'",
  "Try adjacent markets — if 'CPaaS API' has no results, try 'business SMS'",
];

export default function EmptyState({ niche }) {
  const navigate = useNavigate();

  return (
    <div className="flex flex-col items-center text-center py-16 px-4 max-w-lg mx-auto">
      {/* Illustrated icon */}
      <div className="relative mb-6">
        <div className="w-20 h-20 bg-slate-100 rounded-full flex items-center justify-center">
          <SearchX className="w-10 h-10 text-slate-400" />
        </div>
        {/* Subtle glow */}
        <div className="absolute inset-0 rounded-full blur-xl bg-brand-200 opacity-30 -z-10" />
      </div>

      <h2 className="text-xl font-bold font-display text-slate-800 mb-2">
        No pain points found for "{niche}"
      </h2>
      <p className="text-sm text-slate-500 font-sans leading-relaxed mb-8 max-w-sm">
        Reddit doesn't have enough discussion about this specific topic for us to
        extract meaningful pain points. This usually means the niche is too narrow
        or uses industry jargon that users don't post with.
      </p>

      {/* Suggestions list */}
      <div className="w-full bg-amber-50 border border-amber-100 rounded-2xl p-5 mb-6 text-left">
        <div className="flex items-center gap-2 mb-3">
          <Lightbulb className="w-4 h-4 text-amber-600 flex-shrink-0" />
          <span className="text-xs font-semibold text-amber-700 uppercase tracking-wide">
            Try a broader niche
          </span>
        </div>
        <ul className="space-y-2">
          {BROADER_SUGGESTIONS.map((s, i) => (
            <li key={i} className="flex items-start gap-2 text-sm text-amber-800 font-sans">
              <span className="text-amber-400 mt-0.5 flex-shrink-0">→</span>
              {s}
            </li>
          ))}
        </ul>
      </div>

      <button
        onClick={() => navigate("/")}
        className="inline-flex items-center gap-2 px-6 py-3 rounded-xl
                   bg-gradient-to-r from-brand-600 to-purple-600 text-white
                   font-semibold text-sm hover:from-brand-700 hover:to-purple-700
                   transition-all duration-200 shadow-md shadow-brand-200"
      >
        Try a different niche
      </button>
    </div>
  );
}
