# Stoogle — 주식 종목 인사이트 플랫폼

> 어떤 기업을 검색해도 동일한 품질의 주가·뉴스·관계도 인사이트를 제공하는 "주식 전용 구글"

검토 기준일: 2026-04-30

---

## 프로젝트 개요

Stoogle은 한국 주식 종목을 검색하면 기업 상세 정보, 주가 차트, 최근 뉴스, 키워드, 연관 기업 관계도, 영향 종목을 한 화면에서 보여주는 웹 애플리케이션입니다.

현재 코드는 **KRX 주가 수집, Redis 캐싱, FastAPI API 서빙, Pearson 상관계수 기반 기업 관계 도출, D3 관계 시각화가 구현된 MVP 단계**입니다. 프론트엔드는 기본적으로 mock 데이터를 사용하며, `REACT_APP_USE_MOCK=false`로 설정하면 FastAPI 백엔드 API를 호출합니다.

---

## 현재 구현 상태

### 프론트엔드

- React 18 + React Router v6 기반 SPA
- 라우트 구현
  - `/` — 검색 홈
  - `/search?q={query}` — 검색 결과
  - `/company/:ticker` — 기업 상세 인사이트
- 구현된 화면/컴포넌트
  - `MainPage` — 구글 스타일 검색 홈, 추천 검색 칩
  - `SearchResultsPage` — 종목 검색 결과, 백엔드 API 또는 mock 데이터 사용
  - `CompanyDetailPage` — 기업 요약, 지표, 주가 차트, 키워드, 뉴스, 관계도, 영향 종목
  - `TopBar`, `PriceChart`, `WordCloudSection`, `NewsSection`, `RelationGraph`, `RelationList`, `ImpactList`
- 시각화 라이브러리
  - Recharts — 주가 AreaChart
  - D3 + d3-cloud — 관계 그래프, 워드 클라우드
- 현재 스타일 방식
  - CSS Modules가 아니라 `global.css`의 CSS 변수 + 컴포넌트 내부 인라인 스타일 중심
- API 연결 방식
  - `Stoogle/frontend/package.json`의 `"proxy": "http://localhost:8000"` 설정
  - `axios.get('/api/v1/...')` 형태로 FastAPI 호출
- mock 기본값
  - `REACT_APP_USE_MOCK !== 'false'`이면 mock 데이터 사용
  - 실데이터를 보려면 프론트 실행 시 `REACT_APP_USE_MOCK=false` 필요

### 백엔드

- FastAPI 앱과 라우터 구현
  - `GET /api/v1/search?q={query}`
  - `GET /api/v1/insight/{ticker}`
  - `GET /api/v1/news/{ticker}`
  - `GET /api/v1/relations/{ticker}`
  - `GET /health`
- 주가/종목 서비스
  - `pykrx`로 KOSPI/KOSDAQ/KONEX 종목 레지스트리 구축
  - 종목 검색, 현재가, 주가 히스토리, 시총/PER/PBR/EPS 조회
  - Redis 캐시 우선 사용, 캐시 미스 시 `pykrx` 직접 호출
- 뉴스 서비스
  - 네이버 금융 종목 뉴스 페이지 크롤링
  - 간단한 규칙 기반 뉴스 카테고리 분류와 감성 라벨링
  - page=1 뉴스 Redis 캐시
- NLP/LLM
  - `konlpy`가 있으면 Okt 명사 추출, 실패 시 정규식 fallback
  - OpenAI API 키가 있으면 뉴스 요약 생성
  - API 키가 없거나 호출 실패 시 간단 fallback 요약 반환
- 관계 분석
  - Redis 캐시가 적용된 주가 히스토리 기반으로 기준 종목과 KOSPI200 후보 종목 간 종가 Pearson 상관계수 계산
  - D3 관계 그래프용 `nodes`, `links`, `related_companies` 반환
  - OpenAI API 키가 있으면 최신 뉴스와 관계사 목록을 바탕으로 영향 종목 추론
- Celery 자동화
  - Redis broker/backend 사용
  - 주가 캐시, 뉴스 사전 수집, 뉴스 크롤링, DART 공시 수집, 상관계수 재계산, 관계도 갱신 태스크 정의
- DB 모델
  - SQLAlchemy ORM 모델 정의 완료
  - `companies`, `price_history`, `news_cache`, `insight_cache`, `relation_cache`, `news_vectors`
  - pgvector 기반 뉴스 embedding 색인 모듈은 구현되어 있으나, 현재 핵심 주가/관계 API 흐름에는 직접 필요하지 않음

---

## 프로젝트 구조

```text
Stoogle/
├── frontend/
│   ├── package.json
│   ├── public/
│   │   └── index.html
│   └── src/
│       ├── App.js
│       ├── index.js
│       ├── pages/
│       │   ├── MainPage.js
│       │   ├── SearchResultsPage.js
│       │   └── CompanyDetailPage.js
│       ├── components/
│       │   ├── TopBar.js
│       │   ├── PriceChart.js
│       │   ├── WordCloudSection.js
│       │   ├── NewsSection.js
│       │   ├── RelationGraph.js
│       │   ├── RelationList.js
│       │   └── ImpactList.js
│       ├── utils/
│       │   └── mockData.js
│       └── styles/
│           └── global.css
│
├── backend/
│   ├── main.py
│   ├── tasks.py
│   ├── requirements.txt
│   ├── .env.example
│   ├── routers/
│   │   ├── search.py
│   │   ├── insight.py
│   │   ├── news.py
│   │   └── relations.py
│   ├── services/
│   │   ├── stock_service.py
│   │   ├── news_service.py
│   │   ├── nlp_service.py
│   │   ├── relation_service.py
│   │   └── cache_service.py
│   ├── models/
│   │   ├── schemas.py
│   │   └── db_models.py
│   └── agents/
│       ├── news_agent.py
│       ├── summary_agent.py
│       ├── relevance_agent.py
│       └── dedup_indexer.py
└── README.md
```

---

## 빠른 시작

### 1. 프론트엔드 실행

```bash
cd Stoogle/frontend
npm install
npm start
```

기본값은 mock 데이터 모드입니다.

```bash
# 백엔드 API를 호출하려면
REACT_APP_USE_MOCK=false npm start
```

실행 주소: `http://localhost:3000`

### 2. 백엔드 실행

```bash
cd Stoogle/backend

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env

python models/db_models.py
uvicorn main:app --reload --port 8000
```

Swagger UI: `http://localhost:8000/docs`

### 3. Redis / Celery 실행

Redis는 캐시와 Celery broker/backend로 사용됩니다. Redis가 없어도 일부 API는 외부 API를 직접 호출하며 동작하지만, 검색/주가/뉴스 성능과 자동화 태스크에는 Redis가 필요합니다.

```bash
docker run -d -p 6379:6379 redis:7

cd Stoogle/backend
celery -A tasks worker --loglevel=info
celery -A tasks beat --loglevel=info
```

### 4. 로컬 PostgreSQL 실행

루트의 `docker-compose.yml`은 PostgreSQL 16 + pgvector 이미지를 실행합니다.

```bash
docker-compose up -d
```

기본 접속 정보:

```env
DATABASE_URL=postgresql://stoogle:stoogle1234@localhost:5432/stoogle
```

---

## API 엔드포인트

| Method | URL | 설명 | 현재 데이터 소스 |
|--------|-----|------|------------------|
| GET | `/api/v1/search?q={query}` | 기업명·종목코드 검색 | Redis registry → pykrx |
| GET | `/api/v1/insight/{ticker}` | 기업 종합 인사이트 | pykrx + 뉴스 + NLP/LLM |
| GET | `/api/v1/news/{ticker}` | 기업 뉴스 목록 | Redis news cache → 네이버 금융 크롤링 |
| GET | `/api/v1/relations/{ticker}` | 연관 기업 관계도/영향 종목 | pykrx 상관계수 + OpenAI optional |
| GET | `/health` | 서버 상태 확인 | FastAPI |

---

## Notion API 명세 블록 기준 진행 상태

캡처된 API 명세는 뉴스·DART·LLM 분석까지 포함한 전체 자동화 파이프라인입니다. 현재 우선 구현 범위는 KRX 주가 수집, Redis 캐싱, FastAPI API 서빙, Pearson 기업 관계 도출, D3 관계 시각화입니다.

| 단계 | 명칭 | 명세 요약 | 현재 상태 |
|------|------|-----------|-----------|
| 1 | 뉴스 수집 | Naver API/Celery로 1시간마다 기사 URL·제목·발행시각 수집 | 부분 구현: 네이버 금융 종목 뉴스 크롤링 + Redis 캐시. Naver Search API 기반 쿼리 템플릿 수집은 미연결 |
| 2 | 원문 추출·요약 에이전트 | 기사 URL → 본문 추출 → 품질 평가 → GPT-4o-mini 3문장 요약 → DB 저장 | 부분 구현: `summary_agent.py` 구현. 기존 뉴스 API 파이프라인에는 미연결 |
| 3 | Embedding 선필터 | 요약 기사 전체 → 중복 제거 후 pgvector 색인, 모델 BGE-M3 | 부분 구현: `dedup_indexer.py` 구현. 현재 모델은 OpenAI `text-embedding-3-small`이며 BGE-M3 아님 |
| 4 | 관련성 판별 에이전트 | 종목 프로필 + 후보 기사 → EXAONE 점수 0~5, 4점 이상 통과 | 구현 파일 있음: `relevance_agent.py`. 기존 뉴스 수집 파이프라인에는 미연결 |
| 5 | 조건부 게이트 | 통과 기사 0건이면 종료, 1건 이상이면 다음 단계 | 미구현: 통합 파이프라인 연결 시 구현 필요 |
| 6 | 이벤트·관계·요약 통합 에이전트 | 통과 기사 + DART/관계 RAG → structured output | 부분 구현: 영향 종목 추론은 `news_agent.py`에 있음. DART/관계 RAG 통합은 미구현 |
| 7 | 결과 저장 | 통합 에이전트 출력 → PostgreSQL 저장 + Redis 캐시 갱신 | 미구현: 핵심 API는 현재 Redis + pykrx 중심 |
| 8 | DART 수집 | corp_code, stock_code → 재무제표·사업보고서·공시 원문 | 부분 구현: Celery 태스크 골격 있음. corp_code 매핑/저장 검증 필요 |
| 9 | DART 청킹·색인 | DART 원문 → 섹션 청크 → pgvector 저장, 모델 BGE-M3 | 미구현 |
| 10 | DART 재무분석 에이전트 | DART 청크 → 핵심 수치 추출 → PostgreSQL 저장 | 미구현 |
| 11 | Calibrator | 예측 레코드 + D+3 실제 주가 → confidence 보정값 pgvector 저장 | 미구현 |

핵심 구현 범위 상태:

- [x] KRX 주가 수집 모듈
- [x] Redis 캐싱
- [x] FastAPI 백엔드 API 서빙
- [x] Pearson 상관계수 기반 기업 관계 도출
- [x] D3.js 기업 관계 시각화
- [~] API 키/LLM 입력 시 동작: OpenAI/EXAONE 키가 없으면 fallback 또는 빈 결과. 키 입력 후에는 요약·영향 추론·관련도 판별 모듈이 동작 가능하지만, 전체 뉴스 파이프라인 연결은 추가 구현 필요

---

## 환경변수

`Stoogle/backend/.env.example`을 복사해 `.env`를 생성합니다.

```env
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
DART_API_KEY=
DATABASE_URL=postgresql://postgres.[PROJECT_ID]:[YOUR-PASSWORD]@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres
REDIS_URL=redis://localhost:6379/0
ALLOWED_ORIGINS=http://localhost:3000
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
EXAONE_API_KEY=
EXAONE_BASE_URL=https://api.exaone.lgai.ai/v1
EXAONE_MODEL=EXAONE-3.5-7.8B-Instruct
```

주의:

- OpenAI API 키가 없으면 LLM 요약과 영향 종목 추론은 fallback 또는 빈 결과로 처리됩니다.
- EXAONE API 키가 없으면 관련도 판별 에이전트는 0점 처리되며, 현재 핵심 주가/관계 API에는 영향이 없습니다.
- DART API 키가 없으면 공시 수집 Celery 태스크는 skip됩니다.
- `DATABASE_URL`은 SQLAlchemy 테이블 생성과 pgvector 뉴스 색인 모듈에서 사용됩니다.
- PostgreSQL 드라이버(`psycopg2-binary`)와 `pgvector` Python 패키지는 `requirements.txt`에 포함되어 있습니다.
- 예제 파일에는 실제 서비스 키를 넣지 말고 placeholder만 유지하는 것이 안전합니다.

---

## Supabase / DB 사용 현황

현재 Supabase는 SDK, Auth, Storage로 사용되지 않습니다. `.env.example`의 `DATABASE_URL`이 Supabase Postgres Pooler 주소로 되어 있어 **관리형 PostgreSQL 후보**로 잡혀 있는 상태입니다.

로컬 개발용으로는 루트 `docker-compose.yml`에 PostgreSQL 16 + pgvector 구성이 있습니다. 따라서 DB 선택지는 크게 두 가지입니다.

- 로컬 개발: `docker-compose up -d` 후 `postgresql://stoogle:stoogle1234@localhost:5432/stoogle`
- 관리형 운영 후보: Supabase Pooler URL

현재 구현:

- SQLAlchemy ORM 모델은 정의되어 있음
- `news_vectors` 테이블과 `Vector(1536)` embedding 컬럼 정의
- `python models/db_models.py` 실행 시 PostgreSQL에서는 `CREATE EXTENSION IF NOT EXISTS vector` 수행
- `agents/dedup_indexer.py`에서 OpenAI embedding + pgvector cosine distance 기반 중복 제거/색인 구현
- `python models/db_models.py`로 테이블 생성 가능
- `DATABASE_URL`이 없으면 SQLite(`sqlite:///./Stoogle.db`)로 fallback

아직 제한:

- 핵심 주가/관계 API는 Redis + pykrx 중심이며 DB 영구 저장을 필수로 사용하지 않음
- 뉴스 요약/관련도/중복 제거 에이전트는 구현되어 있지만 기존 뉴스 API 파이프라인에 완전히 연결되지는 않음
- Supabase Pooler 환경에서 pgvector extension 생성 권한과 vector index 생성 전략은 별도 검증 필요

---

## Celery 스케줄

| 태스크 | 주기 | 현재 상태 |
|--------|------|-----------|
| `fetch_top200_prices` | 60초 | 장중 KOSPI200 현재가 Redis 캐싱 |
| `update_price_history` | 매일 16:00 | 90일 히스토리 Redis 캐싱 |
| `crawl_all_news` | 매시 정각 | KOSPI200 뉴스 강제 크롤링 후 Redis 캐싱 |
| `prefetch_news_for_major_stocks` | 매일 08:30 | 주요 30개 종목 뉴스 page=1 Redis 사전 캐싱 |
| `fetch_dart_filings` | 매일 08:00 | DART API 키 필요, 결과는 현재 반환값 중심 |
| `recompute_correlations` | 매일 00:00 | 상관계수 계산만 수행, 영구 저장 미구현 |
| `update_relation_graphs` | 매주 월요일 09:00 | 관계도 계산 수행, 영구 저장 미구현 |
| `refresh_ticker_registry` | 매주 월요일 07:00 | KRX 종목 레지스트리 Redis 갱신 |

현재 주의점:

- 일부 주석은 Redis/DB 저장을 암시하지만 실제 구현은 계산 후 반환 또는 Redis TTL 캐시 중심입니다.

---

## 검토 결과와 남은 작업

### 완료

- [x] React SPA 라우팅 구현
- [x] 검색 홈/검색 결과/기업 상세 화면 구현
- [x] mock 데이터 기반 프론트엔드 전체 플로우 구현
- [x] FastAPI 앱, CORS, 라우터 4종 구현
- [x] pykrx 기반 종목 검색/주가/시총 데이터 수집 서비스 구현
- [x] Redis 기반 종목 레지스트리/현재가/주가 히스토리/뉴스 캐싱 구현
- [x] 네이버 금융 뉴스 크롤링 및 간단 랭킹/분류 구현
- [x] 키워드 추출 및 OpenAI 요약 fallback 구현
- [x] Pearson 상관계수 기반 기업 관계 도출 구현
- [x] D3 관계 그래프용 데이터 생성 및 프론트 시각화 구현
- [x] Celery 자동화 태스크 골격 및 뉴스 사전 수집 태스크 구현
- [x] SQLAlchemy ORM 모델 및 pgvector 뉴스 벡터 모델 정의

### 진행 중 / 보완 필요

- [ ] `REACT_APP_USE_MOCK=false` 상태에서 프론트-백엔드 실데이터 E2E 검증
- [ ] Redis 실행 환경에서 검색/뉴스/가격 캐시 동작 검증
- [ ] Notion API 명세 block과 실제 FastAPI 응답 스키마 최종 대조
- [ ] Supabase/PostgreSQL을 핵심 데이터 영구 저장소로 쓸지 결정하고 저장/조회 로직 연결
- [ ] DART 공시 수집에서 ticker와 corp_code 매핑 검증
- [ ] 관계도/상관계수 계산 결과 저장 구조 구현
- [ ] 뉴스 요약/관련도/중복 제거 에이전트를 뉴스 수집 파이프라인에 연결
- [ ] API 에러 응답과 프론트 fallback UX 보강
- [ ] 단위 테스트/통합 테스트 추가
- [ ] `.env.example`의 민감 키 placeholder 정리

---

## 개발 우선순위 제안

1. **실행 검증** — 프론트 mock 모드, 백엔드 `/docs`, Redis 없는 상태의 fallback 확인
2. **실데이터 연결** — `REACT_APP_USE_MOCK=false`로 검색/상세 페이지 E2E 검증
3. **Redis 안정화** — registry, price, history, news 캐시 TTL과 장애 fallback 점검
4. **API 명세 대조** — Notion API 명세 block과 Swagger 응답 모델 비교
5. **DB 방향 결정** — SQLite 유지, Supabase Postgres 도입, 또는 Redis 중심 유지 중 선택
6. **영구 저장 구현** — 뉴스/요약/관계 분석 결과를 SQLAlchemy 모델과 연결
7. **테스트 추가** — 서비스 함수 fallback, API 응답 스키마, 프론트 API 모드 검증
