import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { History, Search, Trash2, ExternalLink, Clock, BarChart2 } from "lucide-react";

/**
 * HistoryPage.jsx — previous search history from localStorage.
 *
 * Storage format (key: "painminer_history"):
 *   Array of { jobId, niche, date, painPointCount, report }
 *   Limited to 20 most recent entries (oldest pruned automatically).
 *
 * Design: minimal card list matching the ResultsDashboard aesthetic —
 * niche name prominent, date and count as metadata, "View" links back
 * to the cached results page.
 */

const STORAGE_KEY = "painminer_history";
const MAX_ENTRIES = 20;

export function saveToHistory(jobId, niche, report) {
  try {
    const existing = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    // Prevent duplicates — update existing entry if same jobId
    const filtered = existing.filter((e) => e.jobId !== jobId);
    const entry = {
      jobId,
      niche,
      date: new Date().toISOString(),
      painPointCount: report?.top_pain_points?.length ?? 0,
    };
    const updated = [entry, ...filtered].slice(0, MAX_ENTRIES);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
  } catch {
    // localStorage quota exceeded or unavailable — fail silently
  }
}

export function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function clearHistory() {
  localStorage.removeItem(STORAGE_KEY);
}

function formatDate(iso) {
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function timeAgo(iso) {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  } catch {
    return "";
  }
}

export default function HistoryPage() {
  const [entries, setEntries] = useState([]);
  const navigate = useNavigate();

  useEffect(() => {
    setEntries(loadHistory());
  }, []);

  function handleClear() {
    clearHistory();
    setEntries([]);
  }

  function handleDelete(jobId) {
    try {
      const updated = entries.filter((e) => e.jobId !== jobId);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      setEntries(updated);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 pt-20 pb-16 px-4">
      <div className="max-w-2xl mx-auto">

        {/* Page header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold font-display text-slate-900">
              Search History
            </h1>
            <p className="text-sm text-slate-500 font-sans mt-1">
              {entries.length} previous{" "}
              {entries.length === 1 ? "analysis" : "analyses"} — results are
              cached and instantly available.
            </p>
          </div>
          {entries.length > 0 && (
            <button
              onClick={handleClear}
              className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-red-500
                         transition-colors font-medium"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Clear all
            </button>
          )}
        </div>

        {/* ── Empty state ──────────────────────────────────────────────────── */}
        {entries.length === 0 && (
          <div className="text-center py-20 bg-white rounded-2xl border border-dashed border-slate-200">
            <div className="w-14 h-14 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <History className="w-7 h-7 text-slate-400" />
            </div>
            <h2 className="text-base font-semibold text-slate-700 mb-2">
              No history yet
            </h2>
            <p className="text-sm text-slate-400 font-sans max-w-xs mx-auto mb-6">
              Run your first analysis and your results will appear here for
              quick access.
            </p>
            <button
              onClick={() => navigate("/")}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl
                         bg-gradient-to-r from-brand-600 to-purple-600 text-white
                         text-sm font-semibold hover:from-brand-700 hover:to-purple-700
                         transition-all duration-200"
            >
              <Search className="w-4 h-4" />
              Start a Search
            </button>
          </div>
        )}

        {/* ── History cards ────────────────────────────────────────────────── */}
        <div className="space-y-3">
          {entries.map((entry, i) => (
            <div
              key={entry.jobId}
              className="flex items-center gap-4 bg-white rounded-2xl border border-slate-100
                         shadow-sm hover:shadow-md hover:border-slate-200 p-4
                         transition-all duration-200 group animate-fade-in"
              style={{ animationDelay: `${i * 40}ms`, animationFillMode: "both", opacity: 0 }}
            >
              {/* Rank / index badge */}
              <div className="w-9 h-9 rounded-xl bg-brand-50 flex items-center justify-center flex-shrink-0">
                <BarChart2 className="w-4 h-4 text-brand-500" />
              </div>

              {/* Main content */}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-slate-800 truncate">
                  {entry.niche}
                </p>
                <div className="flex items-center gap-3 mt-0.5">
                  <span className="flex items-center gap-1 text-xs text-slate-400">
                    <Clock className="w-3 h-3" />
                    {timeAgo(entry.date)}
                  </span>
                  <span className="text-xs text-slate-400">·</span>
                  <span className="text-xs text-slate-500 font-medium">
                    {entry.painPointCount} pain point{entry.painPointCount !== 1 ? "s" : ""}
                  </span>
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={() => navigate(`/results/${entry.jobId}`)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                             bg-brand-50 text-brand-600 text-xs font-semibold
                             hover:bg-brand-100 transition-colors"
                >
                  <ExternalLink className="w-3 h-3" />
                  View
                </button>
                <button
                  onClick={() => handleDelete(entry.jobId)}
                  className="p-1.5 rounded-lg text-slate-300 hover:text-red-400
                             hover:bg-red-50 transition-colors opacity-0 group-hover:opacity-100"
                  title="Remove from history"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
