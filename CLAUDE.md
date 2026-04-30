# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stoogle ("주식 전용 구글") is a Korean stock insight search platform. It provides unified company insights including news, price charts, keyword analysis, and company relationships for KOSPI/KOSDAQ markets.

## Tech Stack

- **Frontend**: React 18 + React Router v6, Recharts, D3.js (port 3000)
- **Backend**: FastAPI + SQLAlchemy 2.0 + pykrx (port 8000)
- **Database**: PostgreSQL 16 with pgvector (Docker), SQLite for local dev
- **Task Queue**: Celery + Redis for scheduled data fetching
- **LLM**: OpenAI gpt-4o-mini via LangChain for summarization

## Common Commands

### Frontend (from `Stoogle/frontend/`)
```bash
npm install          # Install dependencies
npm start            # Dev server on http://localhost:3000
```

### Backend (from `Stoogle/backend/`)
```bash
pip install -r requirements.txt
python models/db_models.py                    # Create/init DB tables
uvicorn main:app --reload --port 8000         # Dev server (Swagger at /docs)
```

### Database
```bash
docker-compose up -d    # Start PostgreSQL with pgvector
```

### Celery (from `Stoogle/backend/`)
```bash
celery -A tasks worker --loglevel=info    # Worker
celery -A tasks beat --loglevel=info      # Scheduler (separate terminal)
```

## Architecture

### Backend Structure (`Stoogle/backend/`)

**Routers** → **Services** → **Models** pattern:

- `routers/` — FastAPI route handlers
  - `search.py`: `GET /api/v1/search?q=` — company search
  - `insight.py`: `GET /api/v1/insight/{ticker}` — price, PER/PBR, keywords, LLM summary
  - `news.py`: `GET /api/v1/news/{ticker}` — ranked news articles
  - `relations.py`: `GET /api/v1/relations/{ticker}` — correlation-based company relationships
- `services/` — business logic
  - `stock_service.py`: pykrx-based price & market data
  - `news_service.py`: Naver Finance scraping + keyword-based sentiment ranking
  - `nlp_service.py`: Korean keyword extraction (KoNLPy Okt, regex fallback) + OpenAI summarization
  - `relation_service.py`: Pearson correlation between stock price series
- `models/db_models.py`: SQLAlchemy ORM (Company, PriceHistory, NewsCache, InsightCache, RelationCache)
- `models/schemas.py`: Pydantic response models
- `agents/naver_news_crawler.py`: Naver News API integration
- `tasks.py`: Celery Beat schedule (news prefetch 8:30, prices 16:00, relations Monday 9:00 KST)

### Frontend Structure (`Stoogle/frontend/src/`)

- Routes: `/` (MainPage), `/search?q=` (SearchResultsPage), `/company/:ticker` (CompanyDetailPage)
- `utils/mockData.js` provides mock data; toggle via `REACT_APP_USE_MOCK`
- API proxy configured to `http://localhost:8000` in package.json

## Key Design Decisions

- All external data fetching has graceful fallbacks (empty results if pykrx unavailable, regex extraction if KoNLPy fails, heuristic summary if OpenAI fails)
- Sentiment analysis uses simple Korean keyword matching (상승/급등 = positive, 하락/급락 = negative), not ML models
- Company relationship strength is determined by Pearson correlation thresholds (0.8+ = 경쟁, 0.6+ = 협력, 0.4+ = 공급망, <0.4 = 관심)
- Backend uses async routes with `httpx.AsyncClient` for concurrent I/O

## Environment Variables

See `Stoogle/backend/.env.example`. Key variables: `OPENAI_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `DART_API_KEY`.

## Git Workflow

- Main branch: `develop`
- Feature branches: `feat/*`
