# Stoggle — News-based Stock Impact Analysis Module

> **모듈 목적**: 새로 올라오는 뉴스 중 특정 종목 주가에 실제로 영향을 줄 가능성이 높은 뉴스를 자동 선별하여 종목별로 ranking
>
> **접근 방식**: LLM 에이전트 ❌ → 머신러닝 + Event Study ⭕ (API 비용 회피)

---

## 📌 프로젝트 컨텍스트

이 모듈은 **stoogle (스토글)** — "주식 전용 구글" 컨셉의 한국 주식 인사이트 플랫폼 — 의 핵심 컴포넌트 중 하나다. 캡스톤 프로젝트로 진행 중이며, 5월 마무리를 목표로 한다.

### Stoogle 전체 시스템에서의 위치

```
┌─────────────────────────────────────────────────────────────┐
│                    수집 모듈 (Collection Layer)              │
│   주가(pykrx) │ 뉴스 │ 공시(OpenDART) │ 관계 도출           │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│           Celery Scheduler + Agent/Service Workers           │
│                  ★ 본 모듈은 여기에 위치 ★                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              저장소 (Redis / PostgreSQL / pgvector / ES)     │
└─────────────────────────────────────────────────────────────┘
```

### 관련 모듈과의 인터페이스

| 모듈 | 역할 | 본 모듈과의 관계 |
|------|------|------------------|
| 뉴스 수집 모듈 | 네이버 금융, BIGKinds 등에서 뉴스 수집 | **입력 제공** |
| 주가 수집 모듈 | pykrx로 시세 데이터 수집 | **라벨링용 데이터 제공** |
| 공시 수집 모듈 | OpenDART에서 공시 수집 | **Hard label 보강** |
| 관계 도출 모듈 | 뉴스↔종목 매핑 (pgvector + ES) | **종목 매칭 (Phase 2)** |

---

## 핵심 출력

```python
# 입력
news = {"title": "...", "content": "...", "published_at": "...", ...}

# 출력
[
    {"ticker": "005930", "name": "삼성전자", "impact_score": 0.87},
    {"ticker": "000660", "name": "SK하이닉스", "impact_score": 0.42},
    ...
]
```

---

## 🏗️ 시스템 파이프라인

```
[1] 뉴스 수집 (네이버 금융 종목별 페이지)
         ↓
[2] 전처리 (중복 제거, 정규화)
         ↓
[3] 종목 매칭 (Phase 1: 페이지 기반 / Phase 2: 임베딩 기반)
         ↓
[4] 이벤트 클러스터링 (Sentence-BERT + HDBSCAN)
         ↓
[5] Feature 생성 (텍스트 + 메타 + 시장 데이터)
         ↓
[6] Impact Score 모델 (LightGBM)
         ↓
[7] 종목별 뉴스 ranking
```

---

## 📂 디렉토리 구조 (제안)

```
news_impact/
├── README.md                          # 이 파일
├── pyproject.toml                     # 의존성 관리
├── .env.example                       # 환경 변수 템플릿
│
├── data/
│   ├── raw/                          # 원본 수집 데이터
│   ├── processed/                    # 전처리 완료
│   └── labels/                       # 라벨링 결과
│
├── src/
│   ├── collectors/
│   │   ├── naver_finance.py          # 네이버 금융 종목별 뉴스 크롤러
│   │   └── opendart.py               # OpenDART 공시 수집
│   │
│   ├── preprocessing/
│   │   ├── deduplicator.py           # 중복 뉴스 제거
│   │   ├── normalizer.py             # 텍스트 정규화
│   │   └── ticker_matcher.py         # 종목 매칭 (Phase 1: page-based)
│   │
│   ├── clustering/
│   │   ├── embedder.py               # Sentence-BERT 임베딩
│   │   └── event_clusterer.py        # HDBSCAN 클러스터링
│   │
│   ├── labeling/
│   │   ├── market_model.py           # ★ α, β 추정 (Critical 1)
│   │   ├── abnormal_return.py        # CAR 계산
│   │   ├── label_generator.py        # 자동 라벨 생성
│   │   └── opendart_hard_label.py    # 공시 기반 hard label
│   │
│   ├── features/
│   │   ├── text_features.py          # TF-IDF, 임베딩, 감성 점수
│   │   ├── meta_features.py          # 언론사, 시간, 클러스터 크기
│   │   └── market_features.py        # 수익률, 변동성, 거래량
│   │
│   ├── models/
│   │   ├── stage0_sanity.py          # OpenDART-only baseline
│   │   ├── stage1_tfidf.py           # TF-IDF + LightGBM
│   │   ├── stage2_embedding.py       # KoBERT + LightGBM
│   │   └── trainer.py                # 공통 학습 루틴
│   │
│   ├── evaluation/
│   │   ├── ir_metrics.py             # Precision@k, Recall@k, NDCG
│   │   ├── backtest.py               # ★ Financial backtest
│   │   └── ablation.py               # Ablation study
│   │
│   └── inference/
│       ├── predictor.py              # 실시간 점수 예측
│       └── ranker.py                 # 종목별 ranking
│
├── notebooks/
│   ├── 01_eda.ipynb                  # 탐색적 데이터 분석
│   ├── 02_market_model.ipynb         # Market Model 검증
│   └── 03_label_quality.ipynb        # 라벨 품질 분석
│
├── scripts/
│   ├── collect_data.py               # 데이터 수집 진입점
│   ├── train.py                      # 학습 진입점
│   └── evaluate.py                   # 평가 진입점
│
├── migrations/                       # PostgreSQL 스키마 (Supabase 연동)
│   └── 001_news_impact_tables.sql
│
└── tests/
    ├── test_market_model.py
    ├── test_labeling.py
    └── test_features.py
```

---

## 모델링 단계 (Stage별 진행)

각 Stage는 **합격 기준**을 통과해야 다음 단계로 진행한다.

### Stage 0 — Sanity Check (1주차)

**목적**: 데이터/라벨링이 학습 가능한지 검증

- OpenDART 공시 발표일 → positive label
- 랜덤 일자 → negative label
- 단순 binary classifier (Logistic Regression)
- **합격 기준**: AUC ≥ 0.7
- ❌ 안 나오면 뉴스 기반은 더 어려움 → 데이터/라벨 재검토

### Stage 1 — TF-IDF Baseline (2주차)

**목적**: 디버깅 가능한 baseline 확보

- TF-IDF (한국어 형태소 분석: Mecab/Okt)
- LightGBM binary classifier
- 텍스트 feature만 사용
- **합격 기준**: precision@10 ≥ 0.3

### Stage 2 — 임베딩 + 메타 feature 결합 (3주차)

- KoBERT/KoELECTRA 문장 임베딩 + 메타 feature 결합
- LightGBM
- **합격 기준**: Stage 1 대비 precision@10 +0.05 이상

### Stage 3 — 확장 (시간 남으면)

- Sector-aware multi-task learning
- Attention 기반 뉴스 중요도 학습 (MIL: Multiple Instance Learning)

---

## 🔧 필수 구현 사항

### 4.1 라벨링: Market Model 기반 CAR

**왜 단순 차감(`stock - market`)은 안 되는가?**

종목별 시장 민감도(베타)가 다르다. 베타가 큰 종목(예: 카카오, 셀트리온)은 항상 abnormal return이 커서 "중요 뉴스"로 잘못 라벨링된다.

**구현 핵심**:

```python
# 1. 추정 윈도우 (이벤트 -250 ~ -30일)에서 종목별 α, β 추정
import statsmodels.api as sm

def estimate_market_model(stock_returns, market_returns):
    X = sm.add_constant(market_returns)
    model = sm.OLS(stock_returns, X).fit()
    return model.params['const'], model.params['market']  # α, β

# 2. 이벤트 윈도우에서 abnormal return 계산
def calc_abnormal_return(actual_ret, market_ret, alpha, beta):
    expected_ret = alpha + beta * market_ret
    return actual_ret - expected_ret

# 3. CAR (Cumulative Abnormal Return)
def calc_CAR(abnormal_returns, t1=0, t2=3):
    return abnormal_returns[t1:t2+1].sum()

# 4. 라벨 생성
# threshold는 전체 분포의 상위 10~20% 지점으로 설정
label = 1 if abs(CAR) >= threshold else 0
```

**캐싱 전략**: KOSPI 200 종목 각각의 α, β를 매월 1회 재추정 → Supabase의 `market_model_params` 테이블에 저장

```sql
CREATE TABLE market_model_params (
    ticker VARCHAR(10),
    estimation_date DATE,
    alpha FLOAT,
    beta FLOAT,
    r_squared FLOAT,
    PRIMARY KEY (ticker, estimation_date)
);
```

### 4.2 동일 날짜 다중 뉴스 문제 우회

**문제**: 하루에 같은 종목 뉴스가 30개면, 그날 +3% 상승한 게 어느 뉴스 때문인지 알 수 없음

**해결책 (둘 중 택1 또는 둘 다)**:

**방법 A — Pre-market 뉴스만 사용** (권장)
- 8:00~8:50 사이 뉴스만 학습 데이터로 사용
- 그날 시초가→종가 반응 측정
- 장중 다른 뉴스에 의한 오염 최소화

**방법 B — 단독 이벤트만 사용**
- 한 종목에 대해 ±2시간 내 다른 클러스터 뉴스가 없는 케이스만 선별
- 데이터 양 ↓ but 라벨 품질 ↑

### 4.3 Hard Label 보강 (OpenDART)

뉴스 라벨 품질 한계를 보완하기 위해 공시 데이터를 hard label로 활용:

```python
HARD_LABEL_EVENTS = [
    '유상증자결정', '무상증자결정',
    '합병결정', '분할결정',
    '자기주식취득결정', '자기주식처분결정',
    '단일판매·공급계약체결',
    '주요사항보고서',
]

# 공시 발표 ±1시간 내 뉴스는 무조건 positive
if news.has_matching_disclosure(within_hours=1):
    label = 1  # hard positive
```

### 4.4 종목 매칭 (Phase별)

**Phase 1 (capstone 기간) — 매칭 회피**
- 네이버 금융 종목별 뉴스 페이지 직접 크롤링
- URL: `https://finance.naver.com/item/news.naver?code={ticker}`
- **장점**: 페이지 자체가 종목 매칭을 끝낸 상태, 알고리즘 부담 0

**Phase 2 (capstone 이후) — stoggle 관계 도출 모듈에 통합**
- pgvector로 회사 임베딩 저장
- 뉴스 임베딩 ↔ 회사 임베딩 cosine similarity
- Elasticsearch로 회사명/별칭 키워드 매칭 보강
- 두 점수 결합: `score = α * semantic + (1-α) * keyword`

### 4.5 이벤트 클러스터링

```python
from sentence_transformers import SentenceTransformer
import hdbscan

# 1. 임베딩
model = SentenceTransformer('jhgan/ko-sbert-sts')  # 한국어 모델
embeddings = model.encode(news_titles)

# 2. 클러스터링 (같은 날짜 + 같은 종목 그룹 내에서)
clusterer = hdbscan.HDBSCAN(min_cluster_size=2, metric='cosine')
clusters = clusterer.fit_predict(embeddings)

# 3. 클러스터 대표 뉴스 (centroid에 가장 가까운 뉴스)
```

### 4.6 Feature 설계

| 카테고리 | Feature |
|---------|---------|
| 텍스트 | TF-IDF top-k, KoBERT 임베딩, FinBERT 감성 점수 |
| 뉴스 메타 | 언론사 등급, 발행 시간대, 클러스터 크기 (확산도) |
| 시장 데이터 | 최근 5/20일 수익률, 변동성, 거래량 z-score |
| 종목 연결 | 회사명 등장 빈도, 섹터 키워드 매칭 점수 |
| 공시 결합 | 동일 시점 OpenDART 공시 존재 여부 |

---

## 🚨 강조 사항 (반드시 지킬 것)

### 🔴 Critical 1: Market Model 사용
- ChatGPT 안의 `stock_return - market_return`은 학계 baseline 중 가장 약함
- 종목별 α, β 추정 후 abnormal return 계산 **필수**
- 안 그러면 high-beta 종목(카카오, 셀트리온 등) 편향 발생

### 🔴 Critical 2: 라벨이 Ground Truth가 아님을 인지
- "주가 반응 = 중요 뉴스"는 **단방향만** 성립
- 역방향 함정:
  - 시장 전체 이슈/외국인 수급으로 움직였을 수 있음
  - 효율적 시장에서 이미 반영됨 (pre-leaked)
  - PEAD 현상으로 늦게 반응
- → **OpenDART hard label과 결합으로 보완**

### 🔴 Critical 3: 데이터 양보다 라벨 품질
- pre-market 뉴스 한정 + 단독 이벤트 한정으로 데이터가 줄어도 OK
- 노이즈 라벨로 학습된 모델은 **sensational 키워드만 학습**하게 됨

### 🔴 Critical 4: 단계적 검증
- Stage 0 sanity check 통과 못 하면 뒤로 못 감
- 각 stage마다 합격 기준 명시 후 진행

### 🔴 Critical 5: Look-ahead bias 절대 금지
- 학습 시점에 미래 정보가 들어가지 않도록 주의
- `train_test_split`은 **시계열 기반**으로 (예: 2024 학습, 2025 평가)
- α, β 추정 윈도우도 이벤트 시점 **이전** 데이터만 사용

---

## 📊 평가 방법

### 5.1 정보 검색 Metric

```python
def precision_at_k(predictions, labels, k=10):
    top_k_indices = np.argsort(predictions)[-k:]
    return labels[top_k_indices].sum() / k
```

| Metric | 설명 |
|--------|------|
| Precision@k | 모델이 "중요"라고 한 상위 k개 중 실제 중요 비율 |
| Recall@k | 실제 중요 뉴스 중 상위 k개에 포함된 비율 |
| MAP | 종목별 ranking 품질 평균 |
| NDCG@k | ranking 순서까지 고려 (CAR 크기로 가중) |

### 5.2 Financial Backtest (필수)

> 정보 검색 metric만으로는 "주식 전용 구글"의 가치 증명 불충분

```python
# 모델이 impact_score >= 0.7로 판단한 뉴스가 나오면 매수, T+3일 청산
for news in test_set:
    if model.predict(news) >= 0.7:
        entry_price = stock.open_price[news.date + 1]
        exit_price = stock.close_price[news.date + 3]
        ret = (exit_price - entry_price) / entry_price
        returns.append(ret)

sharpe = mean(returns) / std(returns) * sqrt(252)
hit_ratio = sum(r > 0 for r in returns) / len(returns)
```

| 지표 | 목표 |
|------|------|
| 평균 수익률 (시장 대비 초과) | > 0 |
| Sharpe ratio | > 1.0 |
| Hit ratio (승률) | > 55% |
| Max drawdown | < 20% |

### 5.3 정성적 평가

- 케이스 스터디: 모델이 high score 매긴 뉴스 상위 20개 직접 검토
- "이 뉴스가 정말 중요한가?" 사람이 읽고 판단
- 실패 케이스 분류 (false positive 패턴 분석)

### 5.4 Ablation Study

| 실험 | 목적 |
|------|------|
| Market Model 제거 → 단순 차감 | 베타 보정의 효과 측정 |
| Pre-market 한정 → 전체 시간대 | 라벨 품질 영향 측정 |
| OpenDART hard label 제거 | 공시 결합의 효과 측정 |
| 텍스트 feature만 vs 전체 | 메타/시장 feature 기여도 |

---

## 🛠️ 개발 환경

### 의존성

```toml
# pyproject.toml 핵심 패키지
[project]
dependencies = [
    "pykrx>=1.0.45",                    # 주가 데이터
    "OpenDartReader>=0.2.0",            # 공시 데이터
    "pandas>=2.0",
    "numpy>=1.24",
    "scikit-learn>=1.3",
    "lightgbm>=4.0",                    # 메인 모델
    "statsmodels>=0.14",                # Market Model OLS
    "sentence-transformers>=2.2",       # 임베딩
    "hdbscan>=0.8.33",                  # 클러스터링
    "konlpy>=0.6",                      # 한국어 형태소 분석
    "transformers>=4.30",               # KoBERT/KoELECTRA
    "psycopg2-binary>=2.9",             # PostgreSQL
    "sqlalchemy>=2.0",
    "celery>=5.3",                      # 작업 스케줄링
    "redis>=5.0",
    "python-dotenv>=1.0",
]
```

### 환경 변수 (`.env.example`)

```bash
# Database (Supabase)
DATABASE_URL=postgresql://user:pass@host:port/db
REDIS_URL=redis://localhost:6379

# OpenDART API
OPENDART_API_KEY=your_key_here

# Optional: BIGKinds
BIGKINDS_API_KEY=

# Model
MODEL_VERSION=stage1_tfidf_v0.1
```

### 데이터베이스 스키마 (핵심 테이블)

```sql
-- 뉴스 원본
CREATE TABLE news (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    url TEXT UNIQUE,
    published_at TIMESTAMPTZ NOT NULL,
    source VARCHAR(100),
    ticker VARCHAR(10),                 -- Phase 1: 페이지 기반 매칭
    cluster_id BIGINT,                  -- 이벤트 클러스터 ID
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Market Model 파라미터
CREATE TABLE market_model_params (
    ticker VARCHAR(10),
    estimation_date DATE,
    alpha FLOAT,
    beta FLOAT,
    r_squared FLOAT,
    PRIMARY KEY (ticker, estimation_date)
);

-- 라벨링 결과
CREATE TABLE news_labels (
    news_id BIGINT REFERENCES news(id),
    ticker VARCHAR(10),
    car_3d FLOAT,                       -- 3일 누적 abnormal return
    car_5d FLOAT,
    label_soft INT,                     -- CAR 기반 라벨
    label_hard INT,                     -- OpenDART 기반 hard label
    PRIMARY KEY (news_id, ticker)
);

-- 모델 예측 결과
CREATE TABLE impact_predictions (
    news_id BIGINT REFERENCES news(id),
    ticker VARCHAR(10),
    impact_score FLOAT,
    model_version VARCHAR(50),
    predicted_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (news_id, ticker, model_version)
);
```

---

## 📅 일정 (capstone 5월 마무리)

| 주차 | 작업 |
|------|------|
| 5월 1주 | Phase 1 데이터 수집 + Market Model 구현 + Stage 0 |
| 5월 2주 | 라벨링 파이프라인 + Stage 1 (TF-IDF) |
| 5월 3주 | Stage 2 (임베딩) + frontend 통합 |
| 5월 4주 | 평가 (검색 metric + backtest) + 문서 정리 |

---

## 🎓 참고 자료

### Event Study 방법론
- MacKinlay (1997). "Event Studies in Economics and Finance"
- Campbell, Lo, MacKinlay. *The Econometrics of Financial Markets*, Ch. 4

### 한국어 NLP
- KoBERT: https://github.com/SKTBrain/KoBERT
- ko-sbert-sts: https://huggingface.co/jhgan/ko-sbert-sts
- KoFinBERT (있다면): 금융 도메인 특화

### 데이터 소스
- pykrx: https://github.com/sharebook-kr/pykrx
- OpenDART: https://opendart.fss.or.kr/
- 네이버 금융 종목별 뉴스: `https://finance.naver.com/item/news.naver?code={ticker}`

---

## ⚠️ 주의사항 / 함정 모음

> Claude Code 세션이 새로 시작되어도 잊지 말 것

1. **시계열 데이터 분할**: 절대 random split 금지. 시간 순서 기반 분할.
2. **추정 윈도우**: Market Model의 α, β 추정은 이벤트 시점 이전 데이터만 사용 (look-ahead 방지).
3. **거래 정지/급락 종목 제외**: 추정 윈도우 내 거래 정지가 있던 종목은 베타 추정 불안정 → 제외.
4. **공휴일/주말 처리**: 뉴스는 24시간 발생, 거래일은 한정. 주말 뉴스는 다음 거래일 효과로 매핑.
5. **상장폐지 종목**: 과거 데이터에 포함된 상폐 종목 처리 방안 필요 (survivorship bias).
6. **클래스 불균형**: 중요 뉴스는 전체의 10~20%. `class_weight='balanced'` 또는 SMOTE 고려.
7. **TF-IDF 한국어**: 영어 default tokenizer 쓰면 안 됨. Mecab/Okt 필수.
8. **메모리**: KoBERT 임베딩은 메모리 많이 먹음. 배치 처리 + 디스크 캐싱 필수.
9. **저작권**: 뉴스 본문 저장 시 저작권 이슈. 학술 목적이면 OK지만 공개 시 주의.

---

## 🔗 Stoggle 프로젝트 연결

- **Notion 캡스톤 문서**: page ID `318368ca-4a88-80a0-b1fb-fcd49a3cfbfa`
- **GitHub**: stoggle 메인 레포
- **Supabase**: 공유 PostgreSQL (팀원 간 DB 공유)
- **Codespaces**: 협업 개발 환경

### 다른 모듈과의 인터페이스

```python
# 입력: 뉴스 수집 모듈에서 큐에 넣은 뉴스
# Celery task
@app.task
def process_news_impact(news_id: int):
    news = News.get(news_id)
    candidates = match_tickers(news)            # 종목 후보 (Phase 1: news.ticker만)
    for ticker in candidates:
        score = predict_impact(news, ticker)
        ImpactPrediction.create(
            news_id=news_id,
            ticker=ticker,
            impact_score=score,
            model_version=MODEL_VERSION,
        )
```

---

## 💡 Claude Code 사용 시 컨텍스트 팁

새 세션 시작 시 다음 명령어로 컨텍스트를 빠르게 복원:

```
@README.md 를 읽고 현재 어느 Stage 작업 중인지 파악해줘.
이전 세션에서 [Stage X 작업 중 / 데이터 수집 중 / 평가 중] 이었어.
```

**현재 진행 상태** (이 부분은 매번 업데이트):
- [ ] Stage 0: Sanity check
- [ ] Stage 1: TF-IDF baseline
- [ ] Stage 2: 임베딩 모델
- [ ] Stage 3: 확장
- [ ] 평가 및 문서화

**최근 작업 내역**:
- (작업할 때마다 여기 기록)
