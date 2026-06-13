# Stoogle — 주식 전용 인사이트 검색엔진

> "주식 전용 구글"  
> KOSPI/KOSDAQ 종목의 뉴스·주가·기업 관계를 한 화면에서 탐색하는 AI 기반 검색 플랫폼

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| **종목 검색** | 종목명 또는 종목코드로 KOSPI/KOSDAQ 전종목 검색 |
| **인사이트 대시보드** | 주가 차트·시가총액·PER/PBR·키워드·AI 요약을 하나의 화면에 표시 |
| **뉴스 피드** | 감성 분석 + 관련도 랭킹이 적용된 최신 뉴스 |
| **기업 관계 그래프** | 주가 상관관계·DART 공시·뉴스 분석 기반 기업 관계 네트워크 시각화 |
| **AI 영향 예측** | Claude가 분석한 연관 종목별 상승/하락 영향 및 신뢰도 |
| **백테스트 평가** | 예측 정확도 측정 및 Isotonic Regression 기반 신뢰도 재보정 |

---

## 기술 스택

### Frontend
- **React 18** + React Router v6
- **Recharts** — 주가 차트
- **D3.js** — 기업 관계 Force-directed 그래프

### Backend
- **FastAPI** + SQLAlchemy 2.0
- **pykrx** — KRX 주가·시장 데이터
- **BeautifulSoup4 / Trafilatura** — 뉴스 크롤링·본문 추출
- **KoNLPy (Okt)** — 한국어 키워드 추출

### AI / LLM
- **Anthropic Claude** (`claude-sonnet-4-6`) — 뉴스 종합 분석, 기업 관계 발굴, 영향 예측
- **OpenAI** (`text-embedding-3-small`) — pgvector 뉴스 임베딩·중복 제거
- **VoyageAI** — 예측 벡터 임베딩
- **LangChain / LangChain-Anthropic** — 뉴스 에이전트

### Database
- **PostgreSQL 16 + pgvector** (Supabase 프로덕션) / SQLite 폴백
- **Redis** — 실시간 주가 캐시(TTL 60s), 관계 발굴 분산 락

### Task Queue
- **Celery + Redis** — 12개 정기 태스크 (Asia/Seoul 타임존)

---

## 아키텍처

```
Frontend (React 18, :3000)
    ↓ REST API
Backend (FastAPI, :8000)
    ├── routers/          # API 라우트 (search, insight, news, relations)
    ├── services/         # 비즈니스 로직 (stock, news, nlp, relation, cache)
    ├── agents/           # AI 에이전트 (analysis, relation_discovery, calibrator, ...)
    ├── models/           # ORM + Pydantic 스키마
    ├── evaluation/       # 백테스트·관측성·예측 정확도
    └── tasks.py          # Celery Beat 스케줄
         ↓
    PostgreSQL (pgvector) + Redis
```

### API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/v1/search?q=` | 종목 검색 |
| `GET` | `/api/v1/insight/{ticker}` | 주가·PER/PBR·키워드·AI 요약 |
| `GET` | `/api/v1/news/{ticker}?page=` | 랭킹된 뉴스 목록 |
| `GET` | `/api/v1/relations/{ticker}` | 기업 관계 그래프 + 영향 분석 |

---

## Celery 스케줄 (12 tasks)

| 태스크 | 주기 | 설명 |
|---|---|---|
| `fetch_top200_prices` | 60초 | KOSPI200 실시간 주가 → Redis |
| `cache_eod_prices` | 평일 15:35 | 장 마감 후 주가 캐시 (주말 대비) |
| `update_price_history` | 매일 16:00 | 90일 OHLCV 이력 갱신 |
| `crawl_all_news` | 매시간 | KOSPI200 종목 뉴스 → 관련도 필터 → 중복 제거 → AI 분석 |
| `crawl_category_news` | 매시간 | Naver API 정치·사회·경제 뉴스 → KOSPI200 관련도 필터 |
| `prefetch_news_for_major_stocks` | 매일 08:30 | 상위 30 종목 뉴스 프리웜 |
| `fetch_dart_filings` | 매일 08:00 | DART 공시 수집 |
| `recompute_correlations` | 매일 00:00 | KOSPI200 전종목 Pearson 상관계수 재계산 |
| `update_relation_graphs` | 월 09:00 | 관계 유형 전체 재분류 → DB 저장 |
| `refresh_ticker_registry` | 월 07:00 | KRX 전종목 목록 갱신 → Redis |
| `calibrate_predictions` | 매일 02:00 | D+3 예측 정확도 평가 + 신뢰도 재보정 |
| `index_dart_disclosures` | 매일 18:00 | DART 공시 → pgvector 인덱싱 |

---

## 모듈 설명

### `agents/`
| 모듈 | 역할 |
|---|---|
| `analysis_agent.py` | Claude structured-output으로 뉴스 이벤트·감성·관계·영향 분석 → InsightCache 저장 |
| `relation_discovery_agent.py` | 기업 관계 발굴 (뉴스 + DART 기반) → RelationCache + company_edges 저장 |
| `calibrator.py` | D+3 예측 정확도 평가 + Isotonic Regression으로 신뢰도 점수 보정 |
| `dart_indexer.py` | DART 공시 XML → 400토큰 청크 → pgvector DartChunk 인덱싱 |
| `dart_edge_extractor.py` | DART 공시에서 기업 관계 엣지 추출 |
| `dedup_indexer.py` | OpenAI 임베딩 기반 뉴스 중복 제거 → NewsVector 저장 |
| `relevance_agent.py` | 뉴스-종목 관련도 필터 (규칙 기반 → Ollama 점수) |
| `naver_news_crawler.py` | Naver Open API 뉴스 크롤러 |
| `naver_section_crawler.py` | Naver 섹션 페이지 크롤러 |
| `rss_collector.py` | 주요 언론사 RSS 수집기 (feedparser) |
| `news_agent.py` | LangChain 기반 뉴스 에이전트 (선택적) |
| `summary_agent.py` | 뉴스 제목 기반 요약 에이전트 |

### `services/`
| 모듈 | 역할 |
|---|---|
| `stock_service.py` | pykrx 기반 주가·시장 데이터 조회 |
| `news_service.py` | Naver Finance 뉴스 스크래핑 + 감성 랭킹 |
| `nlp_service.py` | KoNLPy 키워드 추출 + Claude 요약 |
| `relation_service.py` | Pearson 상관관계 + DB 기업 관계 조회 |
| `cache_service.py` | Redis 캐시 (티커 레지스트리, 주가, 인사이트) |

### `evaluation/`
| 모듈 | 역할 |
|---|---|
| `backtest.py` | 뉴스 기반 예측 vs 실제 주가 백테스트 |
| `market_model.py` | 시장 지수 모델 (statsmodels 기반) |
| `observability.py` | 에이전트 호출 추적·Prometheus 메트릭 |
| `prediction_scorer.py` | 예측 점수 산정 |
| `hallucination_check.py` | LLM 할루시네이션 검증 |
| `dataset_builder.py` | 평가 데이터셋 빌더 |

### DB 테이블
| 테이블 | 설명 |
|---|---|
| `companies` | 종목 기본 정보 |
| `price_history` | 90일 OHLCV 이력 |
| `news_cache` | 뉴스 캐시 |
| `insight_cache` | AI 분석 결과 캐시 |
| `relation_cache` | 기업 관계 캐시 (source: news/dart/correlation) |
| `company_edges` | 기업 관계 그래프 엣지 |
| `dart_analysis` | DART 공시 재무 분석 결과 |
| `prediction_log` | 예측 로그 |
| `news_vectors` | 뉴스 임베딩 (pgvector, PostgreSQL only) |
| `prediction_vectors` | 예측 임베딩 (pgvector, PostgreSQL only) |
| `dart_chunks` | DART 공시 청크 (pgvector, PostgreSQL only) |

---

## 빠른 시작

### 1. 환경 변수 설정

```bash
# stoogle/backend/.env
DATABASE_URL=postgresql://stoogle:stoogle1234@localhost:5432/stoogle
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
REDIS_URL=redis://localhost:6379/0
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
```

### 2. 데이터베이스 (PostgreSQL + pgvector)

```bash
docker-compose up -d
```

### 3. 백엔드

```bash
source .venv/bin/activate   # Python 3.12
cd stoogle/backend
uvicorn main:app --reload --port 8000
```

### 4. Celery 워커

```bash
docker run -d -p 6379:6379 redis:7
celery -A tasks worker --loglevel=info
celery -A tasks beat --loglevel=info
```

### 5. 프론트엔드

```bash
cd stoogle/frontend
npm install
REACT_APP_USE_MOCK=false npm start
```

Swagger UI: `http://localhost:8000/docs`  
Frontend: `http://localhost:3000`

---

## 환경 변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 권장 | Claude 분석·관계 발굴 |
| `OPENAI_API_KEY` | 선택 | pgvector 임베딩 (미설정 시 인덱싱 스킵) |
| `DATABASE_URL` | 선택 | PostgreSQL (미설정 시 SQLite 폴백) |
| `REDIS_URL` | 선택 | Celery 브로커 (기본: `redis://localhost:6379/0`) |
| `NAVER_CLIENT_ID/SECRET` | 선택 | Naver News API (미설정 시 스킵) |
| `DART_API_KEY` | 선택 | DART 공시 API (미설정 시 스킵) |
| `OLLAMA_BASE_URL` | 선택 | Ollama 로컬 모델 (관련도 필터, 기본: `http://localhost:11434`) |
| `OLLAMA_MODEL` | 선택 | Ollama 모델명 (기본: `exaone3.5:7.8b`) |
| `LLM_MODEL` | 선택 | Claude 모델 (기본: `claude-sonnet-4-6`) |
| `ALLOWED_ORIGINS` | 선택 | CORS (기본: `http://localhost:3000`) |

---

## 참고 문서

[`readme/`](./readme/) 폴더에 세부 설계 문서가 있습니다.

- [CLAUDE_CONTEXT.md](./readme/CLAUDE_CONTEXT.md) — 초기 아키텍처 설계 (한국어)
- [MONITORING.md](./readme/MONITORING.md) — 관측성·Prometheus 설정
- [News_README.md](./readme/News_README.md) — 뉴스 영향 분석 모듈 설계
- [Relation_README.md](./readme/Relation_README.md) — 기업 관계 모듈 설계
- [EVALUATION.md](./readme/EVALUATION.md) — 백테스트 및 평가 시스템
- [backtest.md](./readme/backtest.md) — 백테스트 실행 가이드
- [backtestPatch.md](./readme/backtestPatch.md) — 백테스트 패치 노트
