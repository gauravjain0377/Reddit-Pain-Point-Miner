# Reddit Pain Point Miner 🔍

> **Mine Reddit for real user pain points in 60 seconds.** Paste a product niche, get a ranked, evidence-backed list of what your market is screaming about — no surveys, no guesswork.

---

## What it does

1. **Discovers** relevant subreddits using Reddit search + a GPT-4o suggestion pass
2. **Fetches** up to 500 posts + their comment trees across those communities
3. **Extracts** structured pain points from each thread using a carefully-engineered GPT-4o prompt
4. **Deduplicates** near-identical pain points using Jaccard similarity per category
5. **Ranks** every pain point by a composite score (severity × 0.4 + mentions × 0.3 + confidence × 0.3)
6. **Generates** an executive summary and streams live progress to the React frontend via WebSocket

The output is a shareable, filterable dashboard with category breakdowns, verbatim Reddit quotes, and export to Markdown or JSON.

---

## Architecture

```
Frontend (React + Vite)          Backend (FastAPI + LangGraph)
+----------------------+         +----------------------------------------+
|  HomePage            |         |  POST /api/analyze                     |
|  v POST /api/analyze |-------->|    -> check SQLite cache                |
|                      |         |    -> launch pipeline in ThreadPoolExec |
|  ResultsPage         |         |                                        |
|  v WS /ws/{job_id}   |<--------|  WebSocket /ws/{job_id}                |
|    live step updates |         |    <- asyncio.Queue <- progress_callback|
|                      |         |                                        |
|  ResultsDashboard    |         |  LangGraph Agent (6 nodes):            |
|    CategoryChart     |         |    subreddit_discovery                 |
|    PainPointCards    |         |    thread_fetcher                      |
|    ExportBar         |         |    pain_extractor                      |
|  HistoryPage         |         |    deduplicator                        |
+----------------------+         |    ranker                              |
                                 |    report_generator                    |
                                 +----------------------------------------+
```

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Reddit application (free) — [create one here](https://www.reddit.com/prefs/apps)
- An OpenAI API key with GPT-4o access

---

## Local Setup (5 steps)

### 1 — Clone the repo

```bash
git clone https://github.com/your-username/reddit-pain-point-miner.git
cd reddit-pain-point-miner
```

### 2 — Configure environment variables

```bash
cp backend/.env.example backend/.env
# Open backend/.env and fill in all values (see table below)
```

### 3 — Install backend dependencies

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 4 — Install frontend dependencies

```bash
cd ../frontend
npm install
```

### 5 — Run both servers

**Terminal A — backend:**
```bash
cd backend
uvicorn api.main:app --reload --port 8000
```

**Terminal B — frontend:**
```bash
cd frontend
npm run dev
```

Open **http://localhost:5173** in your browser.

---

## Environment Variables

| Variable | Required | Example | Description |
|---|---|---|---|
| `REDDIT_CLIENT_ID` | Yes | `AbCd1234XyZ` | From your Reddit app's "personal use script" |
| `REDDIT_CLIENT_SECRET` | Yes | `secret_abc123` | From your Reddit app settings |
| `REDDIT_USER_AGENT` | Yes | `PainMiner/1.0 by u/youruser` | Identifies your bot to Reddit |
| `OPENAI_API_KEY` | Yes | `sk-proj-...` | OpenAI API key with GPT-4o access |
| `MAX_COST_USD` | No | `1.00` | Per-job cost ceiling (default: $1.00) |
| `MAX_THREADS_PER_SUBREDDIT` | No | `100` | Reddit posts to fetch per community |
| `CACHE_TTL_HOURS` | No | `24` | How long to cache results (default: 24h) |
| `LOG_LEVEL` | No | `INFO` | Uvicorn/Python log level |

---

## Running Tests

```bash
cd backend
pytest tests/ -v
```

The test suite mocks all external calls (Reddit + OpenAI), so no API keys are needed.

---

## Deployment

### Backend on Railway (free tier)

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) and click **New Project** then **Deploy from GitHub repo**
3. Set the **Root Directory** to `backend/` in Railway settings
4. Add all environment variables in the **Variables** tab
5. Railway auto-detects the `Dockerfile` and builds automatically
6. Copy the Railway public URL (e.g. `https://painminer-backend.up.railway.app`)

### Frontend on Vercel (free tier)

1. Go to [vercel.com](https://vercel.com) and click **New Project**, then import your GitHub repo
2. Set **Root Directory** to `frontend/`
3. Add this environment variable in Vercel settings:
   ```
   VITE_API_URL = https://painminer-backend.up.railway.app
   ```
4. Click **Deploy** — Vercel detects Vite automatically
5. The `frontend/vercel.json` already handles SPA routing rewrites

---

## Customising the Extraction

All prompt engineering lives in two files:

- `backend/extractor.py` — `EXTRACTION_SYSTEM_PROMPT` and `EXTRACTION_USER_PROMPT`
- See **PROMPTS.md** for full prompt documentation and a customisation guide

Common customisations:
- Change the category taxonomy in the system prompt
- Adjust severity scoring weights in `agent_graph.py`
- Add domain-specific few-shot examples to the system prompt
- Change the output language (add "Respond in Spanish" to the system prompt)

---

## Contributing

1. Fork the repo and create a feature branch: `git checkout -b feature/my-change`
2. Run the test suite: `pytest backend/tests/ -v`
3. Format Python code: `black backend/`
4. Open a PR with a clear description of what changed and why

Bug reports and feature requests via GitHub Issues are welcome.

---

## License

MIT
