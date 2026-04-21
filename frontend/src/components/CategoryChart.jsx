import { CATEGORY_COLORS } from "./ResultsDashboard";

/**
 * CategoryChart.jsx — pure CSS horizontal bar chart with click filtering.
 *
 * No chart library needed — we just set each bar's width as a percentage
 * of the maximum count using an inline style. CSS transitions handle
 * the width animation.
 *
 * Clicking a category row calls onCategoryClick(category), which is handled
 * in ResultsDashboard to filter the pain point list.
 * Clicking the active category again clears the filter (handled in parent).
 */

export default function CategoryChart({ breakdown, activeCategory, onCategoryClick }) {
  const entries = Object.entries(breakdown ?? {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return null;

  const maxCount = Math.max(...entries.map(([, c]) => c));

  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-widest mb-4">
        Categories
      </h3>

      <div className="space-y-2.5">
        {entries.map(([category, count]) => {
          const color = CATEGORY_COLORS[category] ?? CATEGORY_COLORS["Other"];
          const widthPct = maxCount > 0 ? (count / maxCount) * 100 : 0;
          const isActive = activeCategory === category;
          const isDimmed = activeCategory && !isActive;

          return (
            <button
              key={category}
              onClick={() => onCategoryClick(category)}
              className={[
                "w-full text-left group transition-all duration-150 rounded-lg p-1 -mx-1",
                isActive ? "ring-2 ring-brand-400 ring-offset-1 bg-brand-50" : "hover:bg-slate-50",
                isDimmed ? "opacity-40" : "opacity-100",
              ].join(" ")}
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className={`text-xs font-semibold ${color.text} px-2 py-0.5 rounded-full ${color.bg}`}>
                  {category}
                </span>
                <span className="text-xs font-bold text-slate-600">{count}</span>
              </div>

              {/* Bar track */}
              <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-500 ease-out"
                  style={{
                    width: `${widthPct}%`,
                    backgroundColor: color.bar,
                  }}
                />
              </div>
            </button>
          );
        })}
      </div>

      {activeCategory && (
        <p className="mt-3 text-xs text-center text-brand-500 font-medium">
          Click again to clear filter
        </p>
      )}
    </div>
  );
}
