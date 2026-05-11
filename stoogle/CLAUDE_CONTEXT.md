# Stoogle 프로젝트 컨텍스트

당신은 **Stoogle** 프로젝트의 개발 어시스턴트입니다.
아래 내용을 숙지하고, 이후 모든 코딩 작업은 이 맥락을 기반으로 수행하세요.

---

## 프로젝트 한 줄 정의

> "주식 전용 구글" — 어떤 기업을 검색해도 동일한 품질의 인사이트(주가·뉴스·관계도·영향 종목)를 제공하는 범용 주식 인사이트 웹 플랫폼.

---

## 핵심 목적 (왜 만드는가)

주식 투자자는 특정 기업의 이슈가 **어떤 다른 종목에 영향을 주는지** 파악하기 어렵다.
예를 들어 삼성전자에 긍정적 뉴스가 터졌을 때, 협력업체·경쟁사·계열사 주가가 어떻게 움직일지
한눈에 볼 수 있는 서비스가 없다. Stoogle은 이 문제를 해결한다.

**3가지 핵심 가치:**
1. 기업 간 관계도 시각화 (협력사 / 경쟁사 / 계열사 / 고객사)
2. 오늘 이슈 키워드 → 영향받을 종목 자동 추론
3. 에이전트가 실시간으로 데이터를 자동 업데이트 (개발자 개입 없음)

---

## 기술 스택

### 프론트엔드
- **React 18** + React Router v6
- **Recharts** — 주가 차트
- **D3.js v7** — 기업 관계 포스 그래프 (노드 클릭 시 해당 기업 페이지 이동)
- CSS Modules — 컴포넌트별 스타일 격리
- 디자인 토큰: CSS 변수 (`--color-brand: #534AB7` 등), 라이트/다크 모드 대응

### 백엔드
- **FastAPI** + Uvicorn
- **pykrx** — KRX 전종목 주가 수집 (한국 주식)
- **LangChain + claude-ai** — 뉴스 요약, 기업 관계 추출
- **Celery + Redis** — 자동 스케줄링 (주가 1분, 뉴스 30분, 공시 1일)
- **SQLAlchemy** — ORM (PostgreSQL / SQLite)
- **DART OpenAPI** — 공시 데이터

---

## 프로젝트 폴더 구조

```
Stoogle/
├── frontend/
│   ├── public/index.html
│   ├── package.json
│   └── src/
│       ├── App.js                      # 라우팅: / | /search | /company/:ticker
│       ├── index.js
│       ├── styles/global.css           # CSS 변수 + 전역 리셋
│       ├── utils/mockData.js           # 백엔드 없이 쓰는 목 데이터
│       ├── pages/
│       │   ├── MainPage.js             # 구글 스타일 검색 홈
│       │   ├── MainPage.module.css
│       │   ├── SearchResultsPage.js    # 동명기업 선택 리스트
│       │   ├── SearchResultsPage.module.css
│       │   ├── CompanyDetailPage.js    # 기업 상세 인사이트 페이지
│       │   └── CompanyDetailPage.module.css
│       └── components/
│           ├── TopBar.js / .module.css         # 상단 고정 검색바
│           ├── PriceChart.js / .module.css     # Recharts 주가 차트 (기간 탭)
│           ├── WordCloudSection.js / .module.css  # 키워드 빈도 시각화
│           ├── NewsSection.js / .module.css    # 뉴스 목록 + 카테고리 탭 필터
│           ├── RelationGraph.js / .module.css  # D3 포스 그래프
│           ├── RelationList.js / .module.css   # 연관 기업 목록 + 탭 필터
│           └── ImpactList.js / .module.css     # 영향 종목 리스트
│
└── backend/
    ├── main.py              # FastAPI 앱 + CORS 설정
    ├── tasks.py             # Celery 스케줄 작업 정의
    ├── requirements.txt
    ├── .env.example
    ├── routers/
    │   ├── search.py        # GET /api/v1/search?q=
    │   ├── insight.py       # GET /api/v1/insight/{ticker}
    │   ├── news.py          # GET /api/v1/news/{ticker}
    │   └── relations.py     # GET /api/v1/relations/{ticker}
    ├── services/
    │   ├── stock_service.py    # pykrx 전종목 주가·메타 수집
    │   ├── news_service.py     # 뉴스 RSS 크롤링 + 랭킹 알고리즘
    │   ├── nlp_service.py      # 키워드 추출 + LLM 요약
    │   └── relation_service.py # 상관계수 계산 + 관계 도출
    ├── models/
    │   ├── schemas.py       # Pydantic 요청/응답 스키마
    │   └── db_models.py     # SQLAlchemy ORM (Company, NewsArticle, CompanyRelation, PriceHistory)
    └── agents/
        └── news_agent.py    # LangChain 에이전트 (search_news / get_stock_price / extract_relations 툴)
```

---

## 핵심 API 엔드포인트

| Method | URL | 설명 |
|--------|-----|------|
| GET | `/api/v1/search?q={query}` | 기업명·종목코드 검색 → CompanyBrief[] |
| GET | `/api/v1/insight/{ticker}` | 기업 종합 인사이트 → InsightResponse |
| GET | `/api/v1/news/{ticker}` | 기업 뉴스 목록 |
| GET | `/api/v1/relations/{ticker}` | 연관 기업 관계도 |
| GET | `/health` | 서버 상태 |

프론트엔드 `package.json`에 `"proxy": "http://localhost:8000"` 설정되어 있어
`axios.get('/api/v1/...')` 형태로 바로 호출 가능.

---

## 페이지별 화면 구성

### 1. 메인 페이지 (`/`)
- 중앙 정렬 로고 + 검색창 (구글 스타일)
- 오늘 많이 검색된 종목 칩 (클릭 시 검색 이동)

### 2. 검색 결과 페이지 (`/search?q=삼성전자`)
- 동명 기업 카드 리스트
- 각 카드: 기업명 / 종목코드·시장·섹터 / 현재가·등락률
- 카드 클릭 → `/company/{ticker}`로 이동

### 3. 기업 상세 페이지 (`/company/005930`)
상단부터 순서대로:
1. **Hero** — 기업명·배지·현재가·등락률 + 메트릭 카드 4개 (시총·PER·거래량·52주고가)
2. **주가 차트** — Recharts AreaChart + 기간 탭 (1일/1주/1달/3달/1년)
3. **AI 요약** — LLM이 생성한 3~4문장 오늘의 이슈 요약
4. **키워드** — 최근 7일 뉴스 기반 빈도 태그 (hot 키워드 강조)
5. **참고 뉴스** — 탭 필터(전체·공시·분석·이슈) + 랭킹 정렬 + 더보기
6. **관계도** — D3 포스 그래프 (노드 클릭 → 해당 기업 상세 이동)
7. **연관 기업 목록** — 탭 필터(전체·협력사·경쟁사·계열사) + 상관계수 표시
8. **영향 종목** — 오늘 핫 키워드 기준 상승/하락 가능성 배지

---

## 범용 백엔드 설계 원칙

**절대 지켜야 할 규칙:**
- 코드 어디에도 기업명(삼성전자, SK하이닉스 등)을 하드코딩하지 않는다
- 모든 서비스 함수는 `ticker: str` 하나만 파라미터로 받는다
- 신규 종목은 `refresh_ticker_registry` 태스크 실행 시 자동 등록된다

**Tier 전략 (전종목 효율 처리):**
- Tier 1 (코스피 200): 1분봉·실시간에 가까운 뉴스
- Tier 2 (코스피 전체): 10분 주기
- Tier 3 (코스닥 전체): 일봉 수준
- 사용자 검색 시 해당 종목 즉시 Tier 1으로 임시 승격

**관계 도출 3가지 소스:**
1. pykrx 주가 시계열 → Pearson 상관계수 (60일 기준)
2. DART 공시 → 계열사·주요주주 파싱
3. LangChain 에이전트 → 뉴스 기사에서 "A사 B사 납품/협력" 관계 추출

---

## Pydantic 응답 스키마 요약

```python
# 핵심 응답 구조 (schemas.py)
class InsightResponse(BaseModel):
    company:       CompanyBrief
    price:         PriceInfo           # current, change, change_pct, history[]
    metrics:       list[MetricCard]    # 시총, PER, PBR, 배당수익률
    summary:       str                 # LLM 생성 요약
    keywords:      list[Keyword]       # word, count, hot
    news:          list[NewsArticle]   # title, source, url, category, hot, rank_score
    relations:     list[RelatedCompany] # ticker, name, relation_type, correlation
    hot_keyword:   str
    impact_stocks: list[ImpactStock]   # ticker, name, reason, impact(+1/-1)
```

---

## 뉴스 랭킹 알고리즘

```python
rank_score = (
    min(views / 1000, 30)    # 조회수   최대 30점
  + min(likes / 100, 20)     # 공감수   최대 20점
  + (30 if trusted_source)   # 신뢰 언론사 30점
  + llm_relevance * 20       # LLM 관련성 점수 최대 20점
)
```

---

## Celery 자동화 스케줄

| 태스크 | 주기 | 설명 |
|--------|------|------|
| `fetch_top200_prices` | 60초 | 코스피 200 주가 Redis 캐싱 |
| `crawl_all_news` | 30분 | 전종목 뉴스 수집 |
| `fetch_dart_filings` | 매일 08:00 | 전종목 공시 수집 |
| `recompute_correlations` | 매일 00:00 | 전종목 상관계수 재계산 |
| `refresh_ticker_registry` | 매주 월요일 | KRX 종목 리스트 갱신 |

---

## 개발 실행 명령

```bash
# 프론트엔드 (백엔드 없이도 mockData로 동작)
cd Stoogle/frontend
npm install && npm start

# 백엔드
cd Stoogle/backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # API 키 입력 필요
python models/db_models.py    # DB 테이블 생성
uvicorn main:app --reload

# Celery (Redis 필요)
docker run -d -p 6379:6379 redis:7
celery -A tasks worker --loglevel=info
celery -A tasks beat --loglevel=info
```

---

## 환경변수 (.env)

```
CLAUDE_API_KEY=...        # LLM 요약·관계 추출
DART_API_KEY=...          # https://opendart.fss.or.kr 발급
DATABASE_URL=postgresql://user:password@localhost:5432/Stoogle
REDIS_URL=redis://localhost:6379/0
ALLOWED_ORIGINS=http://localhost:3000
```

---

## 현재 개발 상태

- [x] 전체 폴더 구조 및 파일 생성 완료
- [x] 프론트엔드 3개 페이지 + 7개 컴포넌트 구현
- [x] mockData.js로 백엔드 없이 UI 전체 동작
- [x] FastAPI 백엔드 라우터 4개 구현
- [x] 서비스 레이어 4개 구현 (stock / news / nlp / relation)
- [x] Celery 자동화 스케줄 구현
- [x] DB 모델 (SQLAlchemy ORM) 구현
- [x] LangChain 에이전트 구현
- [ ] 실데이터 연결 테스트 (pykrx ↔ 프론트 차트)
- [ ] DART API 키 연동 테스트
- [ ] CLAUDE API 요약 기능 테스트
- [ ] D3 그래프 실데이터 렌더링 확인
- [ ] Celery 워커 실행 및 스케줄 검증

---

## 코딩 작업 시 주의사항

1. **범용성 유지** — 특정 기업명을 코드에 하드코딩하지 말 것
2. **CSS Modules** — 스타일은 반드시 `.module.css` 파일로 분리
3. **에러 처리** — 모든 외부 API 호출은 try/except로 감싸고 fallback 반환
4. **비동기** — 백엔드 서비스 함수는 `async/await` 사용
5. **다크 모드** — CSS 변수만 사용, 컬러 하드코딩 금지
6. **mockData 우선** — 새 기능 추가 시 mockData에 먼저 샘플 데이터 추가 후 개발
