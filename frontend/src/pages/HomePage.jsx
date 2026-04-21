import { useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { Search, Zap, ArrowRight, AlertCircle } from "lucide-react";

/**
 * HomePage.jsx — Landing page / niche input
 *
 * Layout decisions:
 * - Full-viewport height with a centered column — classic SaaS landing pattern
 *   that focuses all attention on a single action (the submit).
 * - Very light background (slate-50) with a subtle radial gradient blob behind
 *   the card gives depth without distracting from the copy.
 * - The input + button are on the same visual level as a search bar to trigger
 *   the familiar "just type and go" mental model.
 *
 * Data flow:
 *   user types niche → clicks "Mine Reddit"
 *     → POST /api/analyze → { job_id }
 *     → navigate to /results/{job_id}
 *
 * The loading state disables the button and changes its label to prevent
 * double-submission (common on slow connections).
 */

const API_BASE = "http://localhost:8000";

// Example niches that auto-fill the input when clicked.
// Chosen to cover B2B SaaS, productivity, and marketing — three hot categories.
const EXAMPLE_NICHES = [
  "CRM Software",
  "Task Managers",
  "Email Marketing Tools",
];

export default function HomePage() {
  const [niche, setNiche] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  // Handle form submit — call the backend and redirect immediately.
  // The ResultsPage will handle the WebSocket progress stream.
  async function handleSubmit(e) {
    e.preventDefault();
    const trimmed = niche.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);

    try {
      const { data } = await axios.post(`${API_BASE}/api/analyze`, {
        niche: trimmed,
        max_threads: 50,
        use_cache: true,
      });

      // If the response is a cache hit, the report is immediately available.
      // We still navigate to ResultsPage — it handles the from_cache case.
      navigate(`/results/${data.job_id}`, {
        state: {
          fromCache: data.from_cache,
          report: data.report || null,
        },
      });
    } catch (err) {
      const message =
        err.response?.data?.detail ||
        err.message ||
        "Could not connect to the backend. Is the server running?";
      setError(message);
      setLoading(false);
    }
  }

  return (
    // Outer wrapper: full viewport height, centered, light background with
    // a soft radial gradient blob for visual interest
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center px-4 relative overflow-hidden">

      {/* Decorative gradient blob — positioned behind everything with -z-10 */}
      <div
        className="absolute top-0 left-1/2 -translate-x-1/2 w-[900px] h-[600px] rounded-full opacity-20 blur-3xl -z-10"
        style={{
          background:
            "radial-gradient(ellipse at center, #818cf8 0%, #c084fc 50%, transparent 100%)",
        }}
      />

      {/* ── Logo / wordmark ──────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 mb-12">
        <div className="w-8 h-8 bg-brand-600 rounded-lg flex items-center justify-center">
          <Zap className="w-4 h-4 text-white" fill="white" />
        </div>
        <span className="font-semibold text-slate-800 text-sm tracking-wide uppercase">
          PainMiner
        </span>
      </div>

      {/* ── Hero copy ────────────────────────────────────────────────────── */}
      <div className="text-center max-w-2xl mb-10">
        <h1 className="font-display text-5xl md:text-6xl font-extrabold text-slate-900 leading-tight tracking-tight mb-4">
          Find what your market{" "}
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-brand-500 to-purple-500">
            is screaming about.
          </span>
        </h1>
        <p className="text-lg text-slate-500 font-sans leading-relaxed">
          Paste a product niche. We mine Reddit for real user pain points — no
          surveys, no guesswork.
        </p>
      </div>

      {/* ── Input card ───────────────────────────────────────────────────── */}
      {/*
        The card has a white background with a soft shadow to lift it off the
        page — this focuses attention on the single action.
      */}
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-xl bg-white rounded-2xl shadow-xl shadow-slate-200/80 p-6 border border-slate-100"
      >
        {/* Search input with icon */}
        <div className="relative mb-4">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
          <input
            id="niche-input"
            type="text"
            value={niche}
            onChange={(e) => setNiche(e.target.value)}
            placeholder="e.g. project management software, CRM tools, email marketing..."
            className="w-full pl-12 pr-4 py-4 rounded-xl border border-slate-200 bg-slate-50
                       text-slate-800 placeholder-slate-400 text-sm font-sans
                       focus:outline-none focus:ring-2 focus:ring-brand-400 focus:border-transparent
                       transition-all duration-200"
            disabled={loading}
            autoFocus
          />
        </div>

        {/* Submit button */}
        <button
          type="submit"
          disabled={loading || !niche.trim()}
          className="w-full flex items-center justify-center gap-2 py-4 px-6 rounded-xl
                     bg-gradient-to-r from-brand-600 to-purple-600 text-white font-semibold text-sm
                     hover:from-brand-700 hover:to-purple-700 active:scale-[0.99]
                     disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100
                     transition-all duration-200 shadow-md shadow-brand-200"
        >
          {loading ? (
            <>
              {/* Spinner while request is in-flight */}
              <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
              </svg>
              Starting analysis…
            </>
          ) : (
            <>
              Mine Reddit
              <ArrowRight className="w-4 h-4" />
            </>
          )}
        </button>

        {/* Error message */}
        {error && (
          <div className="mt-4 flex items-start gap-2 text-red-600 bg-red-50 border border-red-100 rounded-lg px-4 py-3">
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span className="text-sm font-sans">{error}</span>
          </div>
        )}
      </form>

      {/* ── Example niche chips ───────────────────────────────────────────── */}
      {/*
        Chips serve double duty: they lower the barrier for first-time users
        (no blank-slate anxiety) and implicitly communicate what a valid niche
        looks like without needing a tooltip.
      */}
      <div className="flex items-center gap-2 mt-5 flex-wrap justify-center">
        <span className="text-xs text-slate-400 font-sans">Try:</span>
        {EXAMPLE_NICHES.map((n) => (
          <button
            key={n}
            onClick={() => setNiche(n)}
            className="text-xs px-3 py-1.5 rounded-full border border-slate-200 bg-white
                       text-slate-600 hover:border-brand-300 hover:text-brand-600 hover:bg-brand-50
                       transition-all duration-150 font-sans cursor-pointer"
          >
            {n}
          </button>
        ))}
      </div>

      {/* ── Disclaimer ───────────────────────────────────────────────────── */}
      <p className="mt-8 text-xs text-slate-400 font-sans text-center">
        Uses Reddit's public API · Results take 30–60 seconds · Costs ~$0.05–0.15 per run
      </p>
    </div>
  );
}
