# Stoogle — Backtest v1.1 패치 명세

> **목적**: 백테스트 v1 실행 결과 발견된 두 가지 문제를 수정한다.
> 1. 채점 가능 시점 보장 (즉시 채점되는 백테스트로 복원)
> 2. pgvector RAG 시점 누출 차단 (look-ahead 방지)
>
> **선행**: `BACKTEST.md`, `EVALUATION.md`

---

## 문제 요약

| # | 증상 | 근본 원인 |
|---|------|----------|
| 1 | n=20 실행 결과 11건이 `pending` → "오늘 6/5 뉴스 기반"이라 D+3 미경과 | `dataset_builder`에 채점 가능 시점 제약 없음. 뉴스 풀이 1~2주치라 옛 샘플은 `no_news`로 skip, 최근 샘플만 살아남음 |
| 2 | `verify_lookahead`가 PASS여도 RAG로 미래 정보 누출 가능 | `pgvector` RAG 검색에 `as_of` 필터 미적용 (v2로 미뤄짐) |

---

## Patch 1 — `dataset_builder` `safety_days` 제약

### 변경 의도

`as_of <= today - safety_days` 강제. 만들어진 예측이 전부 즉시 채점 가능 상태로 떨어진다.

- `safety_days=5` (기본): D+3 거래일 + 주말 안전 커버
- `safety_days=0`: 제약 없음 (기존 동작, 라이브/대기 모드 호환)

### 수정 — `evaluation/dataset_builder.py`

```python
import random
from datetime import date, datetime, timedelta
from pykrx import stock

# fallback 종목 (KRX API 실패 시)
FALLBACK_KOSPI = [
    "005930", "000660", "035420", "035720", "005380",
    "051910", "006400", "068270", "207940", "005490",
    "012330", "028260", "066570", "003670", "015760",
    "032830", "017670", "105560", "055550", "086790",
    "000270", "096770", "034730", "018260", "010130",
    "009150", "011200", "316140", "024110", "267260",
]

def build_dataset(
    start: str,            # "YYYYMMDD"
    end: str,              # "YYYYMMDD"
    n_samples: int = 300,
    seed: int = 42,
    safety_days: int = 5,  # 🆕 즉시 채점 보장용
):
    """
    KOSPI50 × 거래일에서 (ticker, as_of) 트리플 무작위 추출.

    safety_days > 0 일 때: as_of <= today - safety_days 강제.
    이렇게 추출된 샘플은 D+3가 이미 지난 상태라 즉시 채점 가능.
    """
    random.seed(seed)

    try:
        kospi50 = stock.get_index_portfolio_deposit_file("1028")[:50]
    except Exception:
        kospi50 = FALLBACK_KOSPI

    biz_days = stock.get_previous_business_days(fromdate=start, todate=end)

    if safety_days > 0:
        cutoff = date.today() - timedelta(days=safety_days)
        biz_days = [d for d in biz_days if d <= cutoff]
        if len(biz_days) < 3:
            raise ValueError(
                f"채점 가능 거래일 부족: {len(biz_days)}일 "
                f"(cutoff={cutoff}, safety_days={safety_days}). "
                f"--start/--end를 더 옛날로 옮기거나, "
                f"--safety_days를 줄이거나(즉시 채점 포기), "
                f"뉴스 풀을 더 깊이 적재한 후 재시도."
            )

    samples, seen = [], set()
    max_attempts = max(n_samples * 10, 1000)
    for _ in range(max_attempts):
        if len(samples) >= n_samples:
            break
        d = random.choice(biz_days)
        t = random.choice(kospi50)
        if (d, t) in seen:
            continue
        seen.add((d, t))
        samples.append({
            "ticker": t,
            "as_of": datetime.combine(d, datetime.min.time().replace(hour=9)),
        })

    if len(samples) < n_samples:
        print(f"⚠️  요청 {n_samples}개 중 {len(samples)}개만 생성 (중복 회피 한계).")

    return samples
```

### CLI 통합 — `scripts/run_backtest.py`

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--n_samples", type=int, default=30)
parser.add_argument("--start", type=str, required=True)
parser.add_argument("--end", type=str, required=True)
parser.add_argument("--safety_days", type=int, default=5,
                    help="as_of <= today - safety_days 강제. 0이면 제약 해제.")
parser.add_argument("--model_version", type=str, default="backtest_v1")
args = parser.parse_args()

samples = build_dataset(
    start=args.start, end=args.end,
    n_samples=args.n_samples, safety_days=args.safety_days,
)
```

### 검증

```bash
# 오늘 6/5 기준 — 뉴스 풀이 5/22~6/4면 유효 범위는 5/22~5/30
python scripts/run_backtest.py \
    --n_samples 30 --start 20260522 --end 20260530 --safety_days 5

# 즉시 채점이 일어나야 함 — pending 0 기대
python scripts/inspect_backtest.py --model_version backtest_v1
```

기대 결과: `pending=0`, `scored>0`. 만약 여전히 pending이 남으면 `score_pending_predictions`의 D+3 판정이 캘린더 기준일 가능성 → Patch 1-bis 진행.

### Patch 1-bis (조건부) — D+3 판정을 거래일 기준으로

`prediction_scorer.py`에서 `predicted_at <= NOW() - INTERVAL '3 days'` 같은 캘린더 조건이라면 거래일 기준으로 교체.

```python
# evaluation/prediction_scorer.py
from pykrx import stock
from datetime import datetime

def is_d3_passed(predicted_at: datetime, now: datetime | None = None) -> bool:
    """predicted_at 이후 거래일 3일 이상 경과 여부."""
    now = now or datetime.now()
    biz = stock.get_previous_business_days(
        fromdate=predicted_at.strftime("%Y%m%d"),
        todate=now.strftime("%Y%m%d"),
    )
    return len(biz) >= 4  # predicted_at 포함 4개 = D+3

def score_pending_predictions(db, model_version=None):
    q = db.query(PredictionLog).filter_by(status="pending")
    if model_version:
        q = q.filter_by(model_version=model_version)

    for p in q.all():
        if not is_d3_passed(p.predicted_at):
            continue  # 아직 D+3 미경과 → 건너뜀
        # ... 기존 채점 로직
```

---

## Patch 2 — pgvector RAG 시점 격리

### 문제

현재 RAG는 `as_of` 필터 없이 전체 벡터 인덱스에서 검색한다. 통합 에이전트가 미래 뉴스 청크를 컨텍스트로 받으면 정확도가 부풀려진다(look-ahead bias).

→ **두 가지 해결 옵션 중 선택. v1은 옵션 B 권장, v2는 옵션 A로 승격.**

### 옵션 A — `as_of` 필터 박은 RAG 검색 함수 (정공법, v2 권장)

`backend/evaluation/rag_filter.py` 신규:

```python
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from models.db_models import NewsVector

def search_rag_at(
    db: Session,
    query_embedding,
    as_of: datetime | None = None,
    top_k: int = 20,
):
    """
    pgvector RAG 검색. as_of가 주어지면 그 이전 색인 청크만 검색.

    as_of=None  → 라이브 모드 (필터 없음, 기존 동작)
    as_of=시각  → 백테스트 모드 (created_at < as_of)

    NewsVector에 created_at 컬럼이 없으면 News.published_at으로 join 필터.
    """
    stmt = select(NewsVector)
    if as_of is not None:
        # NewsVector.created_at이 있으면 사용
        if hasattr(NewsVector, "created_at"):
            stmt = stmt.where(NewsVector.created_at < as_of)
        else:
            # 없으면 원 뉴스의 published_at으로 join 필터
            from models.db_models import News
            stmt = stmt.join(News, News.id == NewsVector.news_id) \
                       .where(News.published_at < as_of)
    stmt = stmt.order_by(
        NewsVector.embedding.cosine_distance(query_embedding)
    ).limit(top_k)
    return db.execute(stmt).scalars().all()
```

`evaluation/backtest.py`의 RAG 호출 부분 교체:

```python
from evaluation.rag_filter import search_rag_at

rag_chunks = search_rag_at(db, query_embedding, as_of=as_of, top_k=20)
```

### 옵션 B — RAG OFF 토글 (v1 권장, 즉시 안전)

RAG 자체를 백테스트 v1에서 끈다. 시점 필터 구현 부담 없이 누출 위험 0.

`evaluation/backtest.py` 상단:

```python
# v1: pgvector RAG 시점 필터 미구현 → 누출 회피용 OFF
# v2: evaluation/rag_filter.search_rag_at 로 교체
USE_RAG = False
```

RAG 호출부:

```python
if USE_RAG:
    rag_chunks = search_rag_at(db, query_embedding, as_of=as_of)
else:
    rag_chunks = []
```

### 선택 기준

| 상황 | 추천 |
|------|------|
| 발표 임박 / 시간 부족 / 안전 우선 | **옵션 B** (v1 OFF) |
| RAG 효과를 평가에 포함하고 싶음 / 시간 여유 | 옵션 A |
| RAG 없이도 정확도가 충분히 나옴 | 옵션 B 그대로 v1 확정 |

> 캡스톤 보고서엔 솔직히 적어라: "v1 백테스트는 RAG OFF 상태에서 평가. v2에서 시점 필터 적용된 RAG 비교 예정." 이게 평가자 입장에선 약점이 아니라 단계적 검증 증거.

---

## Patch 3 — `verify_lookahead`에 RAG 검증 추가 (옵션 A 선택 시)

옵션 A로 가면 누출 검증도 RAG까지 확장한다. 옵션 B 선택했으면 이 패치 skip.

```python
# evaluation/verify_lookahead.py 확장
def verify_rag_lookahead(db, model_version):
    """
    RAG 청크 추적 컬럼이 있어야 검증 가능.
    PredictionLog에 rag_chunk_ids (ARRAY[BIGINT]) 컬럼이 없으면 WARN으로 skip.
    """
    from models.db_models import PredictionLog, NewsVector

    if not hasattr(PredictionLog, "rag_chunk_ids"):
        print("⚠️  PredictionLog.rag_chunk_ids 없음 — RAG 검증 skip. "
              "옵션 A 채택 시 스키마 보강 필요.")
        return []

    leaks = []
    preds = db.query(PredictionLog).filter_by(model_version=model_version).all()
    for p in preds:
        if not p.rag_chunk_ids:
            continue
        chunks = db.query(NewsVector).filter(NewsVector.id.in_(p.rag_chunk_ids)).all()
        for c in chunks:
            chunk_time = getattr(c, "created_at", None) or c.news.published_at
            if chunk_time >= p.predicted_at:
                leaks.append({
                    "pred_id": p.id, "chunk_id": c.id,
                    "pred_at": p.predicted_at, "chunk_at": chunk_time,
                })
    return leaks
```

`PredictionLog`에 컬럼 추가 (옵션 A 시):

```python
# models/db_models.py
class PredictionLog(Base):
    # ... 기존 컬럼
    rag_chunk_ids = Column(ARRAY(BigInteger), nullable=True)  # 🆕
```

`backtest.py`에서 저장 시 함께 박제:

```python
rag_chunks = search_rag_at(db, query_embedding, as_of=as_of)
# ... 통합 에이전트 호출
db.add(PredictionLog(
    # ...
    rag_chunk_ids=[c.id for c in rag_chunks],  # 🆕
))
```

---

## 통합 실행 워크플로우

```bash
# 0. (이전 시도 정리 — 선택)
python -c "
from models.db_models import SessionLocal, PredictionLog
db = SessionLocal()
db.query(PredictionLog).filter_by(model_version='backtest_v1').delete()
db.commit(); db.close()
"

# 1. Patch 1 + 2 적용 후 즉시 채점 가능한 소규모 백테스트
python scripts/run_backtest.py \
    --n_samples 30 \
    --start 20260522 --end 20260530 \
    --safety_days 5 \
    --model_version backtest_v1

# 2. 누출 검증
python -m evaluation.verify_lookahead --model_version backtest_v1
# 기대: 0건 PASS

# 3. 결과 확인
python scripts/inspect_backtest.py --model_version backtest_v1
# 기대: pending=0, scored=30 (또는 no_news로 일부 skip된 경우 scored < 30)

# 4. 부분 결과 분석 (pending 포함 분포 등)
python scripts/inspect_backtest.py --model_version backtest_v1 --breakdown confidence

# 5. REST 엔드포인트 확인
curl 'http://localhost:8000/api/v1/_internal/prediction-metrics?model_version=backtest_v1'
```

---

## Claude Code에 던질 프롬프트 (복붙용)

```
BACKTEST_PATCHES.md를 읽고 두 패치를 모두 적용해줘.

Patch 1: evaluation/dataset_builder.py
- safety_days 파라미터(기본 5) 추가
- as_of <= today - safety_days 강제
- scripts/run_backtest.py CLI에 --safety_days 옵션 통합
- (조건부) evaluation/prediction_scorer.py의 D+3 판정이 캘린더 기준이면 거래일 기준(is_d3_passed)으로 교체

Patch 2: pgvector RAG 시점 격리 — 옵션 B 적용 (v1 안전 우선)
- evaluation/backtest.py 상단에 USE_RAG = False 토글
- RAG 호출부를 USE_RAG 분기로 감싸서 OFF 시 빈 리스트 반환
- v2 전환을 위해 evaluation/rag_filter.py(옵션 A 코드)는 신규 파일로 생성하되 호출은 안 함 (주석으로 v2 활성화 방법 기록)

Patch 3: skip (옵션 B 채택했으므로 verify_lookahead 확장 불필요)

작업 후:
1. python scripts/run_backtest.py --n_samples 30 --start 20260522 --end 20260530 --safety_days 5 로 검증
2. pending=0 / scored>0 확인
3. python -m evaluation.verify_lookahead --model_version backtest_v1 PASS 확인
4. CLAUDE.md에 변경 이력 기록
```

---

## 변경 요약

| 파일 | 변경 |
|------|------|
| `evaluation/dataset_builder.py` | `safety_days` 파라미터, cutoff 필터 |
| `scripts/run_backtest.py` | `--safety_days` CLI 옵션 |
| `evaluation/prediction_scorer.py` | (조건부) D+3 거래일 기준 판정 |
| `evaluation/backtest.py` | `USE_RAG = False` 토글, RAG 분기 |
| `evaluation/rag_filter.py` | 🆕 신규 (v2용, v1에선 미호출) |

옵션 A 승격 시 추가:
| `models/db_models.py` | `PredictionLog.rag_chunk_ids` 컬럼 |
| `evaluation/verify_lookahead.py` | RAG 누출 검증 |
| `evaluation/backtest.py` | `USE_RAG = True`, `rag_chunk_ids` 박제 |