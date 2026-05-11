# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stoogle ("주식 전용 구글") is a Korean stock insight search platform. It provides unified company insights including news, price charts, keyword analysis, and company relationships for KOSPI/KOSDAQ markets.

## Tech Stack

- **Frontend**: React 18 + React Router v6, Recharts, D3.js (port 3000)
- **Backend**: FastAPI + SQLAlchemy 2.0 + pykrx (port 8000)
- **Database**: PostgreSQL 16 with pgvector (Docker); falls back to SQLite when `DATABASE_URL` is unset
- **Task Queue**: Celery + Redis for scheduled data fetching
- **LLM**: CLAUDE claude-ai via LangChain for summarization

## Common Commands

### Frontend (from `stoggle/frontend/`)
```bash
npm install          # Install dependencies
npm start            # Dev server on http://localhost:3000
npm run build        # Production build
```

### Backend (from `stoggle/backend/`)
```bash
# Use .venv (Python 3.12, full stack). There is also a venv/ at the repo root
# (Python 3.14, fastapi-only stub) — do NOT use that one for backend work.
source /workspaces/Stoggle/.venv/bin/activate
pip install -r requirements.txt
python models/db_models.py                    # Create/init DB tables
uvicorn main:app --reload --port 8000         # Dev server (Swagger at /docs)
```

### Database
```bash
docker-compose up -d    # Start PostgreSQL with pgvector
# Default credentials: stoogle:stoogle1234@localhost:5432/stoogle
# DATABASE_URL=postgresql://stoogle:stoogle1234@localhost:5432/stoogle
```

### Celery (from `stoggle/backend/`)
```bash
docker run -d -p 6379:6379 redis:7            # Start Redis (required)
celery -A tasks worker --loglevel=info        # Worker
celery -A tasks beat --loglevel=info          # Scheduler (separate terminal)
```

## Testing & Linting

There are currently **no tests** and **no linting configs**. The project has no `pytest`, Jest test files, ESLint, Prettier, Black, or Flake8 configuration.

## Architecture

### Backend Structure (`stoggle/backend/`)

**Routers** → **Services** → **Models** pattern:

- `routers/` — FastAPI route handlers
  - `search.py`: `GET /api/v1/search?q=` — company search
  - `insight.py`: `GET /api/v1/insight/{ticker}` — price, PER/PBR, keywords, LLM summary
  - `news.py`: `GET /api/v1/news/{ticker}?page=` — ranked news articles
  - `relations.py`: `GET /api/v1/relations/{ticker}` — correlation-based company relationships + impact analysis
- `services/` — business logic
  - `stock_service.py`: pykrx-based price & market data
  - `news_service.py`: Naver Finance scraping + keyword-based sentiment ranking
  - `nlp_service.py`: Korean keyword extraction (KoNLPy Okt, regex fallback) + CLAUDE summarization
  - `relation_service.py`: Pearson correlation between stock price series
  - `cache_service.py`: Redis key/value caching with TTLs (ticker registry, prices, news)
- `models/db_models.py`: SQLAlchemy ORM (Company, PriceHistory, NewsCache, InsightCache, RelationCache, DartAnalysis, PredictionLog); pgvector-only tables (NewsVector, PredictionVector, DartChunk) are defined conditionally when `DATABASE_URL` points to PostgreSQL
- `models/schemas.py`: Pydantic v2 response models. Key shape: `InsightResponse` is a flat model (`ticker`, `name`, `market`, `sector`, `price`, `change`, `change_amount`, `market_cap`, `per`, `pbr`, `eps`, `summary`, `keywords: list[Keyword]`, `price_history: list[PricePoint]`). `RelationsResponse` carries `nodes`, `links`, `related_companies`, and `impact` separately.
- `agents/` — LLM-based agents
  - `news_agent.py`: LangChain news analysis (gpt-4o-mini)
  - `summary_agent.py`: LangChain news summarization
  - `relevance_agent.py`: two-stage relevance filter — rule-based prefilter then Ollama scoring via OpenAI-compatible endpoint (`OLLAMA_BASE_URL`/`OLLAMA_MODEL`); `EXAONE_API_KEY` in `.env.example` is not wired into the current code. Also defines its own `Article` dataclass (identical fields to `dedup_indexer.Article`) — `analysis_agent.py` imports the authoritative one from `dedup_indexer`.
  - `dedup_indexer.py`: pgvector-based news deduplication; defines the shared `Article` dataclass (not yet wired to any Celery task — populates `NewsVector` on-demand only)
  - `naver_news_crawler.py`: Naver News API crawler (uses `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`)
  - `analysis_agent.py`: unified ticker analysis — single GPT-4o structured-output call producing `AnalysisResult {events, relations, summary, sentiment, impacts, evidence}`; boosts context with pgvector cosine search over past articles; saves to `InsightCache`
  - `dart_analyzer.py`: extracts key financials (revenue, op_profit, capex, inventory) from raw DART disclosure text via GPT-4o structured output; saves to `DartAnalysis` table
  - `dart_indexer.py`: downloads DART corp-code XML + filings, splits into ≤400-token chunks, indexes into `DartChunk` pgvector table
  - `calibrator.py`: evaluates D+3 prediction accuracy and re-calibrates confidence scores via Isotonic Regression; embeds `reason` text into `PredictionVector`
- `tasks.py`: Celery Beat schedule (10 tasks, `Asia/Seoul` timezone):

| Task | Schedule | Description |
|---|---|---|
| `fetch_top200_prices` | every 60s | KOSPI200 prices → Redis (skips outside 09:00–15:30 KST) |
| `update_price_history` | daily 16:00 | 90-day OHLCV history → Redis (post-close) |
| `crawl_all_news` | hourly | KOSPI200 news scrape + cache refresh |
| `prefetch_news_for_major_stocks` | daily 08:30 | Pre-warm top-30 tickers before market open |
| `fetch_dart_filings` | daily 08:00 | DART filings via `dart-fss` (skips if `DART_API_KEY` unset) |
| `recompute_correlations` | daily 00:00 | Pearson correlation for all KOSPI200 pairs |
| `update_relation_graphs` | Mon 09:00 | Full relation type reclassification (correlation + DART) |
| `refresh_ticker_registry` | Mon 07:00 | KRX full ticker list → Redis (before market open) |
| `calibrate_predictions` | daily 02:00 | Evaluate D+3 prediction accuracy + re-calibrate confidence |
| `index_dart_disclosures` | daily 18:00 | DART filings + financials → pgvector index |

  Also defines `analyze_single_ticker(ticker)` as an on-demand Celery task. It is **not yet wired into any route** — integration into `insight.py` is pending.

**Cache pattern:** DB cache tables (NewsCache, InsightCache, RelationCache) are populated exclusively by Celery tasks. `cache_service.py` handles Redis hot-path caching (ticker registry TTL 7d, prices TTL 60s). API routes fetch live from pykrx/Naver/OpenAI.

### Frontend Structure (`stoggle/frontend/src/`)

- Routes: `/` (MainPage), `/search?q=` (SearchResultsPage), `/company/:ticker` (CompanyDetailPage)
- `utils/mockData.js` provides mock data; `REACT_APP_USE_MOCK` defaults to `true`, so the frontend runs in mock mode unless you explicitly set it to `false`
- API proxy configured to `http://localhost:8000` in package.json
- Styles: single `styles/global.css` with plain class names + CSS variables (e.g. `--color-brand: #534AB7`); no inline style objects, no CSS-in-JS

## Key Design Decisions

- All external data fetching has graceful fallbacks (empty results if pykrx unavailable, regex extraction if KoNLPy fails, heuristic summary if CLAUDE fails)
- Sentiment analysis uses simple Korean keyword matching (상승/급등 = positive, 하락/급락 = negative), not ML models
- Company relationship strength is determined by Pearson correlation thresholds (0.8+ = 경쟁, 0.6+ = 협력, 0.4+ = 공급망, <0.4 = 관심); three sources: pykrx price correlation, DART filings, LangChain news extraction
- Impact rules in `relation_service.py` and the `ImpactList` component are hardcoded per ticker (currently only Samsung/SK Hynix), not dynamically computed
- Tier strategy for stock data freshness: Tier 1 (KOSPI 200) = 1-min updates, Tier 2 (KOSPI full) = 10-min, Tier 3 (KOSDAQ) = daily; user search temporarily promotes a ticker to Tier 1
- KOSPI200 ticker list is loaded dynamically from pykrx at startup (`_load_kospi200()`); a 30-ticker fallback list in `tasks.py` is used only if the pykrx call fails
- Backend uses async routes with `httpx.AsyncClient` for concurrent I/O

## Absolute Rules (do not violate)

- **No hardcoded company names** in code — all service functions take only `ticker: str`; `refresh_ticker_registry` handles registration
- **No hardcoded colors** — always use CSS variables; new tokens go in `styles/global.css`
- **No inline styles** — all styles go in `styles/global.css` using CSS variables; no style objects, no CSS-in-JS
- **mockData first** — when adding a new feature, add sample data to `utils/mockData.js` before connecting to the real API

## Environment Variables

See `stoogle/backend/.env.example`. Key variables:

| Variable | Purpose | Required |
|---|---|---|
| `OPENAI_API_KEY` | LLM summaries | No (falls back to heuristic) |
| `DATABASE_URL` | PostgreSQL connection | No (falls back to SQLite) |
| `REDIS_URL` | Celery broker | No (defaults to `redis://localhost:6379/0`) |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | Naver News API | No (news fetch fails gracefully) |
| `ALLOWED_ORIGINS` | CORS allowed origins | No (defaults to `http://localhost:3000`) |
| `DART_API_KEY` | DART filings API (opendart.fss.or.kr) | No (task skips gracefully) |
| `EXAONE_API_KEY` | Listed in `.env.example` but not used in current code | — |
| `OLLAMA_BASE_URL` | Ollama server for relevance scoring (default: `http://localhost:11434`) | No (relevance_agent skips gracefully) |
| `OLLAMA_MODEL` | Ollama model name (default: `exaone3.5:7.8b`) — run `ollama pull exaone3.5:7.8b` | No |
| `LLM_MODEL` | OpenAI model for analysis_agent (default: `gpt-4o-mini`) | No |

## Other Context Files

`stoogle/CLAUDE_CONTEXT.md` is a Korean-language architecture document written during early planning. Its API response schemas and Celery schedules are partially outdated — treat this file as historical reference, not ground truth. The schemas in `models/schemas.py` and the task table above are authoritative.

## Git Workflow

- Main branch: `develop`
- Feature branches: `feat/*`
