# Stoogle — Backtest Evaluation 명세

> **목적**: 과거 시점 뉴스로 영향 종목을 예측하고 이미 알려진 D+3 주가로 즉시 채점하여 D+3 실시간 대기 없이 모델 정확도를 평가한다.
> **대상**: KOSPI50 종목 × 과거 N일 → 수백 건의 예측 레코드를 한 번에 생성·채점
> **선행 문서**: `EVALUATION.md`(`impact_predictions` 스키마, 채점 로직), `News_README.md`(평가 방법론), `OBSERVABILITY.md`(에이전트 추적)

---

## 0. 핵심 제약과 설계 결론 (반드시 먼저 읽을 것)

백테스트는 단순히 "과거 날짜를 인자로 받는다"가 아니다. **모든 데이터 소스에서 미래 정보 누출(look-ahead bias)을 차단**해야 한다.

### 0-1. Naver Search API 제약

🔴 **공식 Naver Search API는 날짜 범위 파라미터를 지원하지 않는다.** 받는 파라미터는 `query`, `display`, `start`, `sort`뿐이다. SerpAPI 같은 서드파티는 지원하지만 본 프로젝트는 공식 API 사용.

→ **결론: 클라이언트 측 필터링 필수.** API 응답의 `pubDate` 필드(RFC 822 형식)를 파싱해 `as_of` 이전 뉴스만 남긴다.

🔴 **Naver API는 깊은 과거(수개월 전) 뉴스를 안정적으로 반환하지 않는다.** 검색 결과가 최신 위주라 백테스트 가용 범위가 제한된다.

→ **결론: 백테스트 시작 전 뉴스 풀을 DB에 미리 적재.** 적재된 `news` 테이블을 백테스트 소스로 사용하면 API 호출 없이 안정적이고 빠르다.

### 0-2. 시점 격리 대상 (5개 데이터 소스 전부)

| 소스 | 누출 경로 | 차단 방법 |
|------|----------|----------|
| 뉴스 | `as_of` 이후 기사 | `pubDate < as_of` 필터 |
| pgvector RAG | `as_of` 이후 색인 청크 | `created_at < as_of` 필터 |
| DART 공시 | `as_of` 이후 공시 | `rcept_dt < as_of` 필터 |
| Market Model α/β | `as_of` 포함/이후 데이터 | 추정 윈도우 `[as_of-250, as_of-30]` |
| base_price | 미래/오답 시점 종가 | `as_of` 거래일 종가 고정 |

→ **결론: 5개 전부에 `as_of` 필터를 적용하지 않으면 백테스트 정확도가 비현실적으로 부풀려진다.** look-ahead 검증 스크립트로 자체 점검 필수.

---

## 1. 디렉토리 구조

```
backend/
├── evaluation/
│   ├── backtest.py              # 시점 격리 예측 함수
│   ├── dataset_builder.py       # 평가 트리플 (date, ticker) 생성
│   ├── news_filter.py           # Naver pubDate 파싱 + 클라이언트 필터
│   ├── verify_lookahead.py      # 누출 검증 스크립트
│   └── prediction_scorer.py     # 기존 — model_version 필터 추가
├── scripts/
│   ├── seed_news_pool.py        # 백테스트 전 뉴스 적재
│   ├── run_backtest.py          # 메인 실행 스크립트
│   └── inspect_backtest.py      # 결과 조회/분석
```

---

## 2. 데이터 적재 전략 — 백테스트 *시작 전* 1회 실행

백테스트는 DB의 `news` 테이블을 소스로 한다. 미리 충분히 쌓아둬야 한다.

### 2-A. 옵션 1: 라이브 수집을 며칠 돌리기 (권장)

Celery `crawl_all_news`를 1~2주간 가동해 자연 적재. 가장 단순하고 안전.

```bash
# 1~2주간 매시간 자동 수집되는 결과를 그대로 사용
celery -A tasks worker --loglevel=info
celery -A tasks beat --loglevel=info
```

### 2-B. 옵션 2: Naver API로 즉시 적재 (시간 부족 시)

지금 당장 가능한 깊이까지 긁어와 DB에 박는다. 깊은 과거는 어렵지만 최근 1~2주 정도는 가능.

```python
# backend/scripts/seed_news_pool.py
import time
from email.utils import parsedate_to_datetime
from services.news_service import naver_search_news
from models.db_models import News, SessionLocal

KOSPI50 = ["005930", "000660", "035420", ...]  # pykrx로 동적 추출 권장

def seed(query_terms: list[str], max_pages: int = 10):
    db = SessionLocal()
    for q in query_terms:
        for page in range(1, max_pages + 1):
            start = (page - 1) * 100 + 1
            items = naver_search_news(query=q, display=100, start=start, sort="date")
            if not items:
                break
            for it in items:
                pub_dt = parsedate_to_datetime(it["pubDate"])  # RFC 822
                if db.query(News).filter_by(url=it["link"]).first():
                    continue
                db.add(News(
                    title=it["title"], content=it.get("description", ""),
                    url=it["link"], published_at=pub_dt, source="naver",
                ))
            db.commit()
            time.sleep(0.3)  # rate limit 보호
    db.close()

if __name__ == "__main__":
    # 회사명·키워드 다양화로 풀을 넓힌다
    terms = ["삼성전자", "SK하이닉스", "반도체", "현대차", "전기차", ...]
    seed(terms, max_pages=10)
```

> ⚠️ Naver API 일일 호출 한도 확인. 무료 플랜은 보통 25,000회/일이지만 변경 가능. 1회 호출 = 100건 회수 가능하므로 5만 건 적재에 500회 호출 필요.

---

## 3. Naver pubDate 파싱 + 시점 필터

API 응답의 `pubDate`는 RFC 822 형식(예: `"Tue, 03 Jun 2026 14:30:00 +0900"`). 표준 라이브러리로 파싱한다.

```python
# backend/evaluation/news_filter.py
from email.utils import parsedate_to_datetime
from datetime import datetime
from typing import Iterable

def parse_pubdate(rfc822_str: str) -> datetime:
    """Naver API의 pubDate(RFC 822) → tz-aware datetime."""
    return parsedate_to_datetime(rfc822_str)

def filter_before(items: Iterable[dict], as_of: datetime) -> list[dict]:
    """as_of 이전 발행 기사만 반환. pubDate 누락 항목은 안전하게 제외."""
    result = []
    for it in items:
        raw = it.get("pubDate")
        if not raw:
            continue
        try:
            pub = parse_pubdate(raw)
        except Exception:
            continue
        if pub < as_of:
            result.append({**it, "_parsed_pubdate": pub})
    return result
```

**라이브 호출 경로에서도 같은 필터를 적용해야 한다.** 백테스트 함수가 DB가 아니라 Naver API를 직접 호출하는 경로를 탈 경우, 응답 직후 `filter_before`로 한 번 더 거르는 안전망이 필요하다.

---

## 4. 시점 격리 예측 함수

기존 `run_full_analysis(ticker)`는 "지금"을 가정한다. 백테스트용 버전은 `as_of`를 받아 5개 소스 전부에 필터를 적용한다.

```python
# backend/evaluation/backtest.py
from datetime import datetime
from sqlalchemy import and_
from models.db_models import News, NewsVector, DartFiling, PredictionLog, SessionLocal
from agents.integration_agent import integration_agent
from services.stock_service import get_close_price_on
from evaluation.news_filter import filter_before

async def run_analysis_at(ticker: str, as_of: datetime, db, model_version="backtest_v1"):
    """
    as_of 시점만의 데이터로 ticker 영향 분석 → PredictionLog 저장.
    5개 데이터 소스 전부에 시점 필터 적용.
    """
    # 1) 뉴스: DB에서 as_of 이전만 (적재된 풀 사용)
    news = (db.query(News)
              .filter(News.published_at < as_of)
              .filter(News.ticker == ticker)              # Phase 1: 페이지 기반 매칭 결과
              .order_by(News.published_at.desc())
              .limit(50).all())
    if not news:
        return {"status": "no_news", "ticker": ticker, "as_of": as_of}

    # 2) RAG: pgvector 검색 시 색인 시점 필터
    rag_chunks = (db.query(NewsVector)
                    .filter(NewsVector.created_at < as_of)
                    # ... 임베딩 유사도 검색 (시점 필터 후 top-k)
                    .limit(20).all())

    # 3) DART: 공시 수신일(rcept_dt) 필터
    dart_chunks = (db.query(DartFiling)
                     .filter(DartFiling.ticker == ticker)
                     .filter(DartFiling.rcept_dt < as_of)
                     .limit(10).all())

    # 4) base_price: as_of 거래일 종가 (휴장일이면 직전 거래일)
    base_price = get_close_price_on(ticker, as_of)
    if base_price is None:
        return {"status": "no_price", "ticker": ticker, "as_of": as_of}

    # 5) 통합 에이전트 호출 (기존 로직 그대로)
    result = await integration_agent(
        news=news, rag_context=rag_chunks, dart=dart_chunks,
    )

    # 6) 예측 레코드 저장 — predicted_at을 as_of로 박제
    for impact in result.get("impacts", []):
        # 영향 대상 종목의 base_price도 as_of 기준으로 조회
        impact_base = get_close_price_on(impact["ticker"], as_of)
        if impact_base is None:
            continue
        db.add(PredictionLog(
            news_id=news[0].id,
            ticker=impact["ticker"],
            direction=impact["direction"],
            confidence=impact.get("confidence", 0.5),
            evidence=impact.get("evidence", ""),
            model_version=model_version,
            base_price=impact_base,
            predicted_at=as_of,                # 🔑 과거 시점으로 박제
            status="pending",
        ))
    db.commit()
    return {"status": "ok", "ticker": ticker, "as_of": as_of,
            "n_impacts": len(result.get("impacts", []))}
```

### 4-1. `get_close_price_on` — 휴장일 처리

`as_of`가 토요일/공휴일이면 가장 가까운 직전 거래일의 종가를 반환해야 한다.

```python
# backend/services/stock_service.py
from pykrx import stock

def get_close_price_on(ticker: str, as_of) -> float | None:
    """as_of 거래일의 종가. 휴장일이면 직전 거래일."""
    yyyymmdd = as_of.strftime("%Y%m%d")
    # pykrx는 휴장일을 빈 DF로 반환 → 5일 전부터 조회해 마지막 행 사용
    from_date = (as_of - timedelta(days=7)).strftime("%Y%m%d")
    df = stock.get_market_ohlcv(from_date, yyyymmdd, ticker)
    if df.empty:
        return None
    return float(df["종가"].iloc[-1])
```

---

## 5. 평가 데이터셋 빌드

KOSPI50 × 과거 N일에서 랜덤 추출. 시장 편향 방지를 위해 무작위가 핵심.

```python
# backend/evaluation/dataset_builder.py
import random
from datetime import datetime, timedelta
from pykrx import stock

def build_dataset(start="20260301", end="20260430", n_samples=300, seed=42):
    """과거 기간에서 (ticker, as_of) 트리플 n_samples개 무작위 추출."""
    random.seed(seed)  # 재현성
    kospi50 = stock.get_index_portfolio_deposit_file("1028")[:50]  # KOSPI50
    biz_days = stock.get_previous_business_days(fromdate=start, todate=end)

    samples = []
    while len(samples) < n_samples:
        date = random.choice(biz_days)
        ticker = random.choice(kospi50)
        # 같은 (date, ticker) 중복 방지
        if (date, ticker) in {(s["as_of"], s["ticker"]) for s in samples}:
            continue
        samples.append({
            "ticker": ticker,
            "as_of": datetime.combine(date, datetime.min.time().replace(hour=9)),
            # 09:00 = 장 시작 직전. 그 시점의 뉴스/공시까지만 사용.
        })
    return samples
```

> 시점 시각을 09:00으로 잡는 이유: 그날 장이 시작하기 전 상태에서 예측하고 그날 종가 이후 흐름으로 평가하는 구조. News_README의 "Pre-market 뉴스만 사용" 원칙과 맞물린다.

---

## 6. 백테스트 실행 + 즉시 채점

기존 `score_pending_predictions`(EVALUATION.md 5-B)가 `predicted_at` 기준으로 D+3 주가를 조회한다. 백테스트 레코드는 만들자마자 D+3가 *이미 지난 상태*이므로 한 번만 돌리면 전부 채점된다.

```python
# backend/scripts/run_backtest.py
import asyncio
from models.db_models import SessionLocal
from evaluation.dataset_builder import build_dataset
from evaluation.backtest import run_analysis_at
from evaluation.prediction_scorer import score_pending_predictions

MODEL_VERSION = "backtest_v1"

async def main():
    db = SessionLocal()
    samples = build_dataset(n_samples=300)

    # 예측 생성 (과거 시점들로)
    for i, s in enumerate(samples):
        try:
            res = await run_analysis_at(s["ticker"], s["as_of"], db, MODEL_VERSION)
            print(f"[{i+1}/{len(samples)}] {res}")
        except Exception as e:
            print(f"[{i+1}] ERROR {s}: {type(e).__name__}: {e}")

    # 즉시 채점 (D+3가 이미 지난 데이터)
    scored = score_pending_predictions(db, model_version=MODEL_VERSION)
    print(f"\n채점 완료: {scored}")
    db.close()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 7. 결과 조회 — 기존 엔드포인트에 필터 추가

`/api/v1/_internal/prediction-metrics`에 `model_version` 쿼리 파라미터 추가하여 백테스트 결과만 분리 조회.

```python
# backend/evaluation/metrics_api.py — prediction_metrics 수정
from fastapi import Query

@router.get("/prediction-metrics")
async def pred_metrics(model_version: str | None = Query(None)):
    q = "SELECT correct, confidence FROM impact_predictions WHERE status='scored'"
    params = {}
    if model_version:
        q += " AND model_version = :mv"
        params["mv"] = model_version
    rows = db.execute(q, params).fetchall()
    n = len(rows)
    if n == 0:
        return {"n_scored": 0}
    acc = sum(r.correct for r in rows) / n
    high = [r for r in rows if r.confidence >= 0.7]
    high_acc = sum(r.correct for r in high) / max(len(high), 1)
    return {"n_scored": n,
            "direction_accuracy": round(acc, 3),
            "high_confidence_accuracy": round(high_acc, 3),
            "model_version": model_version or "all"}
```

호출:

```bash
curl "http://localhost:8000/api/v1/_internal/prediction-metrics?model_version=backtest_v1"
```

---

## 8. Look-ahead 누출 검증 (반드시 실행)

백테스트 신뢰도의 사활. 정확도가 70~80%+ 비현실적으로 높게 나오면 누출 의심.

```python
# backend/evaluation/verify_lookahead.py
from models.db_models import SessionLocal, PredictionLog, News, NewsVector, DartFiling

def verify(model_version="backtest_v1"):
    db = SessionLocal()
    preds = db.query(PredictionLog).filter_by(model_version=model_version).all()
    leaks = []
    for p in preds:
        as_of = p.predicted_at
        # base_price 시점 검증 — get_close_price_on이 as_of(또는 직전 거래일) 종가를 썼는지
        # (저장 시 base_price_date 컬럼을 같이 박아두면 정확히 검증 가능 — 스키마 보강 권장)

        # 연결된 뉴스가 as_of 이전인가?
        if p.news_id:
            n = db.query(News).get(p.news_id)
            if n and n.published_at >= as_of:
                leaks.append({"pred_id": p.id, "type": "news",
                              "pred_at": as_of, "news_at": n.published_at})
    db.close()
    print(f"검사 {len(preds)}건, 누출 {len(leaks)}건")
    for L in leaks[:20]:
        print(L)
    return leaks

if __name__ == "__main__":
    verify()
```

> 추가 권장: `PredictionLog`에 `base_price_date`, `n_source_news`, `latest_source_pubdate` 컬럼을 더하면 누출 검증이 훨씬 견고해진다.

---

## 9. 워크플로우 (3~4일 일정)

```
Day 1 — 준비
├─ 뉴스 풀 적재 (옵션 1 라이브 수집 시작 / 옵션 2 seed_news_pool.py)
├─ 데이터셋 빌더 + 시점 격리 함수 구현
└─ 소규모 테스트 (n_samples=20)로 파이프라인 검증

Day 2 — 실행
├─ 본 백테스트 (n_samples=300)
├─ 즉시 채점
└─ verify_lookahead.py로 누출 0건 확인

Day 3 — 분석
├─ /prediction-metrics?model_version=backtest_v1
├─ 종목별 / confidence 구간별 정확도 분해
└─ ablation: look-ahead 차단 ON vs OFF 비교 (필터 끄고 다시 돌려보기 — 정확도 부풀림 정도가 곧 강건성 지표)

Day 4 — 발표 자료
└─ 정확도 그래프, calibration 차트, 종목별 히트맵
```

---

## 10. 흔한 함정 (구현 시 체크)

1. **`predicted_at`이 NOW()로 박힘** — `PredictionLog` 컬럼 기본값이 `DEFAULT NOW()`라면 백테스트 경로는 반드시 명시적으로 `predicted_at=as_of` 지정. 누락 시 채점이 안 되거나(아직 D+3 미경과로 인식) 잘못된 D+3가 잡힘.
2. **timezone 혼용** — Naver `pubDate`는 tz-aware, DB는 종종 tz-naive. 비교 전 통일 (KST 기준 tz-aware 권장).
3. **휴장일 base_price** — `as_of`가 주말/공휴일이면 `get_close_price_on`이 직전 거래일을 반환하는지 확인. 직전 거래일을 못 찾으면 해당 샘플 skip.
4. **D+3 = 거래일 3일** — 캘린더 3일 아님. `pykrx.get_previous_business_days` 기반으로 계산.
5. **거래정지/상폐** — D+3 시점에 종목이 거래정지 상태면 채점 skip. `status='skipped'` 명시.
6. **표본 편향** — 특정 시기·섹터에 몰리지 않게 랜덤 시드 고정 + 분포 확인.
7. **재실행** — 동일 `(ticker, as_of, model_version)` 중복 방지 유니크 제약 또는 사전 조회.

---

## 11. Claude Code 구현 체크리스트

- [ ] `evaluation/news_filter.py`: pubDate 파싱 + `filter_before` 구현
- [ ] `evaluation/dataset_builder.py`: KOSPI50 × 기간 무작위 추출
- [ ] `evaluation/backtest.py`: `run_analysis_at(ticker, as_of, db, model_version)` 5개 소스 시점 필터
- [ ] `services/stock_service.get_close_price_on`: 휴장일 직전 거래일 fallback
- [ ] `scripts/seed_news_pool.py`: 백테스트 풀 적재 (Naver API + pubDate 파싱)
- [ ] `scripts/run_backtest.py`: 데이터셋 빌드 → 예측 생성 → 즉시 채점
- [ ] `evaluation/prediction_scorer.score_pending_predictions`: `model_version` 인자 추가
- [ ] `metrics_api.pred_metrics`: `model_version` 쿼리 파라미터 필터 추가
- [ ] `evaluation/verify_lookahead.py`: 5개 소스 누출 검증
- [ ] (권장) `PredictionLog`에 `base_price_date`, `latest_source_pubdate` 컬럼 추가
- [ ] 소규모(n=20) 테스트 → 누출 검증 → 본 실행(n=300)
- [ ] ablation: 시점 필터 OFF 버전과 정확도 비교 (강건성 증거)