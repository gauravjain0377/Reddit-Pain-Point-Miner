import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { Zap, History, Search } from "lucide-react";
import HomePage from "./pages/HomePage";
import ResultsPage from "./pages/ResultsPage";
import HistoryPage from "./pages/HistoryPage";

/**
 * App.jsx — Root component
 *
 * Routes:
 *  /                  → HomePage   (niche input + submit)
 *  /results/:jobId    → ResultsPage (live progress → full dashboard)
 *  /history           → HistoryPage (localStorage search history)
 *
 * The persistent NavBar is rendered inside BrowserRouter so it has access
 * to NavLink's active-state detection. It's excluded from ResultsPage's
 * full-screen progress view via the page-level layout, not here.
 */

function NavBar() {
  return (
    <nav className="fixed top-1 left-1/2 -translate-x-1/2 z-50
                    flex items-center gap-1 bg-white/80 backdrop-blur-md
                    border border-slate-200/80 rounded-full px-3 py-1.5
                    shadow-sm shadow-slate-200/50">
      {/* Logo mark */}
      <div className="flex items-center gap-1.5 pr-3 border-r border-slate-200 mr-1">
        <div className="w-5 h-5 bg-brand-600 rounded-md flex items-center justify-center">
          <Zap className="w-3 h-3 text-white" fill="white" />
        </div>
        <span className="text-xs font-bold text-slate-700 tracking-wide">PainMiner</span>
      </div>

      <NavLink
        to="/"
        end
        className={({ isActive }) =>
          `flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150 ${
            isActive
              ? "bg-brand-600 text-white shadow-sm"
              : "text-slate-500 hover:text-slate-700 hover:bg-slate-100"
          }`
        }
      >
        <Search className="w-3 h-3" />
        Search
      </NavLink>

      <NavLink
        to="/history"
        className={({ isActive }) =>
          `flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150 ${
            isActive
              ? "bg-brand-600 text-white shadow-sm"
              : "text-slate-500 hover:text-slate-700 hover:bg-slate-100"
          }`
        }
      >
        <History className="w-3 h-3" />
        History
      </NavLink>
    </nav>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <NavBar />
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/results/:jobId" element={<ResultsPage />} />
        <Route path="/history" element={<HistoryPage />} />
      </Routes>
    </BrowserRouter>
  );
}
