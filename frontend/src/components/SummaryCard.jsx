import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

/**
 * SummaryCard.jsx — collapsible executive summary block.
 *
 * Default state: collapsed to 3 lines via Tailwind's line-clamp-3.
 * Expanding reveals the full multi-paragraph summary.
 *
 * Why collapsible?
 * The summary can be 4+ long paragraphs. Showing it all by default
 * pushes the pain point cards below the fold. Collapse-by-default
 * means power users get to the data immediately; others can expand.
 */

export default function SummaryCard({ summary }) {
  const [expanded, setExpanded] = useState(false);

  if (!summary) return null;

  // Split on double-newlines to render each paragraph separately
  const paragraphs = summary.split(/\n\n+/).filter(Boolean);

  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm overflow-hidden">
      {/* Left accent bar */}
      <div className="flex">
        <div className="w-1 bg-gradient-to-b from-brand-400 to-purple-400 flex-shrink-0" />
        <div className="flex-1 p-5">
          <h2 className="text-xs font-semibold text-brand-500 uppercase tracking-widest mb-3">
            Executive Summary
          </h2>

          {/* Summary text — clamped when collapsed */}
          <div
            className={[
              "text-sm text-slate-600 font-sans leading-relaxed space-y-3 transition-all duration-300",
              expanded ? "" : "line-clamp-3",
            ].join(" ")}
          >
            {paragraphs.map((para, i) => (
              <p key={i}>{para}</p>
            ))}
          </div>

          {/* Read more / less toggle */}
          <button
            onClick={() => setExpanded((e) => !e)}
            className="mt-3 flex items-center gap-1 text-xs font-semibold text-brand-500
                       hover:text-brand-700 transition-colors"
          >
            {expanded ? (
              <>
                <ChevronUp className="w-3.5 h-3.5" />
                Show less
              </>
            ) : (
              <>
                <ChevronDown className="w-3.5 h-3.5" />
                Read more
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
