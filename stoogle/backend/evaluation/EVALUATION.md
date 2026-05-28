# Stoogle — Evaluation & Quality Metrics 명세

> **목적**: 멀티 에이전트 파이프라인의 (1) 호출량/비용, (2) 할루시네이션, (3) 예측 정확도를 에이전트별·모듈별로 측정·저장·조회한다.
> **대상**: `backend/agents/` (summary, relevance, news, dedup) + 통합 분석 에이전트
> **연관 문서**: `OBSERVABILITY.md`(정량 지표 수집), `News_README.md`(Market Model CAR 평가), 수행계획서 Calibrator 모듈

---

## 0. 핵심 설계 원칙 (반드시 읽을 것)

세 지표는 **측정 방법이 근본적으로 다르다.** 혼동하면 평가 신뢰도가 무너진다.

| 지표 | 종류 | 정답 존재? | 측정 방법 |
|------|------|:---:|----------|
| [1] 호출량/지연/비용 | 정량 | 불필요 | 코드 데코레이터 자동 집계 |
| [2] 할루시네이션 | 품질 | 부분적 | 코드 검증(가짜 종목/근거) + LLM-as-Judge(요약 충실도) |
| [3] 예측 정확도 | 품질 | **나중에 나옴** | **실제 주가(pykrx)로 채점 — judge 아님** |

> 🔴 **[3]에 LLM-as-Judge를 쓰지 말 것.** 예측 정확도는 D+3 실제 주가라는 객관적 정답이 시간이 지나면 나온다. LLM에게 "이 예측 잘했어?"를 물으면 순환논리(LLM이 LLM 채점)에 빠져 평가가 무의미해진다. judge는 [2]의 요약 충실도처럼 *정답이 없는 경우에만* 사용한다.

---

## 1. 디렉토리 구조 (추가 제안)

```
backend/
├── evaluation/
│   ├── __init__.py
│   ├── observability.py        # [1] 에이전트 호출 추적 데코레이터
│   ├── hallucination_check.py  # [2-A] 코드 기반 grounding 검증
│   ├── faithfulness.py         # [2-B] LLM-as-Judge 요약 충실도
│   ├── prediction_scorer.py    # [3] D+3 주가 대조 채점
│   ├── market_model.py         # [3+] CAR 기반 abnormal return (News_README 연계)
│   └── metrics_api.py          # 집계 결과 조회 엔드포인트
└── tasks.py                    # Calibrator Celery 태스크 (채점 스케줄)
```

---

## 2. DB 스키마 (impact_predictions 확장)

기존 `impact_predictions` 테이블에 채점용 컬럼을 추가한다.

```sql
-- 예측 + 채점 통합 테이블
CREATE TABLE IF NOT EXISTS impact_predictions (
    id              BIGSERIAL PRIMARY KEY,
    news_id         BIGINT,
    ticker          VARCHAR(10),
    direction       VARCHAR(4),        -- 'up' / 'down'
    confidence      FLOAT,             -- 에이전트 신뢰도 0~1
    evidence        TEXT,              -- 근거 문장
    model_version   VARCHAR(50),
    -- 예측 시점 박제 (look-ahead 방지)
    predicted_at    TIMESTAMPTZ DEFAULT NOW(),
    base_price      FLOAT,             -- 예측 시점 종가 (등락 기준값)
    -- 채점 결과 (D+3 후 Calibrator가 채움)
    status          VARCHAR(10) DEFAULT 'pending',  -- pending / scored / skipped
    actual_change   FLOAT,             -- D+3 실제 등락률
    abnormal_return FLOAT,             -- CAR (market model 보정 후)
    correct         BOOLEAN,           -- 방향 정답 여부
    scored_at       TIMESTAMPTZ
);

-- [2] 할루시네이션 검증 로그
CREATE TABLE IF NOT EXISTS hallucination_logs (
    id              BIGSERIAL PRIMARY KEY,
    agent           VARCHAR(50),
    module          VARCHAR(50),
    checked         INT,               -- 검증 대상 수
    invalid_ticker  INT,               -- 존재하지 않는 종목 수
    missing_evidence INT,              -- 근거 없는 추론 수
    faithfulness    FLOAT,             -- 요약 충실도 0~1 (summary 전용)
    logged_at       TIMESTAMPTZ DEFAULT NOW()
);

-- [3+] Market Model 파라미터 캐시 (News_README와 동일)
CREATE TABLE IF NOT EXISTS market_model_params (
    ticker          VARCHAR(10),
    estimation_date DATE,
    alpha           FLOAT,
    beta            FLOAT,
    r_squared       FLOAT,
    PRIMARY KEY (ticker, estimation_date)
);
```

---

## 3. [1] 호출량 / 지연 / 비용

에이전트별 LLM 호출을 데코레이터로 감싸 `agent`/`module` 태그로 분리 집계한다.

```python
# backend/evaluation/observability.py
import time, json, logging
from functools import wraps

logger = logging.getLogger("stoogle.agent")
_stats = {}

def track_agent(agent_name: str, module: str):
    """에이전트 LLM 호출의 호출량·토큰·지연·실패를 모듈/에이전트 단위로 집계."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            key = f"{module}.{agent_name}"
            _stats.setdefault(key, {"calls": 0, "in_tok": 0, "out_tok": 0,
                                    "errors": 0, "total_ms": 0})
            try:
                result = func(*args, **kwargs)
                usage = getattr(result, "usage", None)
                in_tok = getattr(usage, "input_tokens", 0) or 0
                out_tok = getattr(usage, "output_tokens", 0) or 0
                _stats[key]["calls"] += 1
                _stats[key]["in_tok"] += in_tok
                _stats[key]["out_tok"] += out_tok
                _stats[key]["total_ms"] += (time.time() - start) * 1000
                logger.info(json.dumps({
                    "event": "llm_call", "module": module, "agent": agent_name,
                    "status": "success", "input_tokens": in_tok, "output_tokens": out_tok,
                    "latency_ms": round((time.time() - start) * 1000, 2),
                }, ensure_ascii=False))
                return result
            except Exception as e:
                _stats[key]["errors"] += 1
                logger.error(json.dumps({
                    "event": "llm_call", "module": module, "agent": agent_name,
                    "status": "error", "error_type": type(e).__name__,
                }, ensure_ascii=False))
                raise
        return wrapper
    return decorator

def get_agent_stats():
    return _stats
```

적용:

```python
# backend/agents/summary_agent.py
from evaluation.observability import track_agent

@track_agent(agent_name="summary_agent", module="news_pipeline")
def summarize(text: str):
    return claude_client.messages.create(...)
```

> ⚠️ **비용 단가는 30일마다 갱신**: Claude/EXAONE/GPT 단가는 분기마다 바뀐다. 오래된 단가표는 비용을 20~40% 틀리게 계산한다. `metrics_api.py`의 단가 상수를 주기적으로 최신값으로 교체할 것.

---

## 4. [2] 할루시네이션

종류에 따라 측정법이 다르다. **코드로 잡히는 것 우선, judge는 보조.**

### 4-A. 코드 기반 grounding 검증 (judge 불필요)

영향 종목 추론에서 가짜 종목/가짜 근거를 잡는다. KRX 상장 종목이라는 정답 집합이 있어 100% 자동 검증 가능하다.

```python
# backend/evaluation/hallucination_check.py
def check_grounding(result: dict, valid_tickers: set, source_articles: list) -> dict:
    """통합 에이전트 출력의 할루시네이션을 코드로 검증."""
    impacts = result.get("impacts", [])
    total = len(impacts)
    if total == 0:
        return {"checked": 0, "invalid_ticker": 0, "missing_evidence": 0, "hallucination_rate": 0.0}

    invalid_ticker = 0
    missing_evidence = 0
    source_text = " ".join(a.get("content", "") for a in source_articles)

    for imp in impacts:
        # (1) 존재하지 않는 종목을 지어냈나?
        if imp.get("ticker") not in valid_tickers:
            invalid_ticker += 1
        # (2) 근거 문장이 실제 입력 기사에 있나?
        ev = imp.get("evidence", "")
        if ev and ev not in source_text:
            missing_evidence += 1

    hallucinated = invalid_ticker + missing_evidence
    return {
        "checked": total,
        "invalid_ticker": invalid_ticker,
        "missing_evidence": missing_evidence,
        "hallucination_rate": round(hallucinated / total, 3),
    }
```

> **완전 일치는 너무 빡빡함**: `ev not in source_text`는 LLM이 근거 문장을 살짝 다듬으면 false positive가 난다. 개선안 — evidence를 임베딩해서 원문 청크와 cosine similarity > 0.8이면 "근거 있음"으로 판정 (pgvector 활용). 완전 일치 → 부분 매칭 → 임베딩 유사도 순으로 단계적 완화.

### 4-B. LLM-as-Judge 요약 충실도 (정답 없을 때)

`summary_agent`는 코드로 못 잡는다. "요약이 원문에 충실한가"는 의미 판단이라 judge가 필요하다.

```python
# backend/evaluation/faithfulness.py
import json

def judge_faithfulness(source: str, summary: str, judge_client) -> dict:
    prompt = f"""원문과 요약을 비교한다. 요약의 각 문장이 원문으로 뒷받침되는지 판단하라.
원문에 없는 정보를 지어낸 문장 수를 세어라. JSON으로만 답하라.

원문: {source[:3000]}
요약: {summary}

형식: {{"total": 정수, "unsupported": 정수, "unsupported_examples": [문자열]}}"""

    resp = judge_client.messages.create(
        model="claude-sonnet-4-20250514",  # 평가 대상보다 같거나 강한 모델
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    data = json.loads(resp.content[0].text)
    total = max(data["total"], 1)
    return {
        "faithfulness": round(1 - data["unsupported"] / total, 3),  # 1.0 = 완전 충실
        "unsupported_count": data["unsupported"],
        "examples": data.get("unsupported_examples", []),
    }
```

> **judge 주의사항 2가지**:
> 1. judge 모델은 평가 대상과 같거나 강한 모델 사용. EXAONE이 만든 걸 EXAONE이 채점하면 자기편향 발생.
> 2. judge도 틀린다. judge가 "할루시네이션"이라 표시한 20~30개를 사람이 검수해 judge 자체의 정확도(judge accuracy)를 한 번 측정해두면 발표에서 방어 가능.

---

## 5. [3] 예측 정확도 — 실제 주가로 채점

**예측 시점 박제 → D+3 후 채점** 2단계. 이것이 프로젝트의 핵심 평가 지표이며 수행계획서 Calibrator 모듈에 해당한다.

### 5-A. 예측 시점: 레코드 저장 (look-ahead 방지)

```python
# backend/evaluation/prediction_scorer.py
def save_prediction(db, news_id, ticker, direction, confidence, evidence, model_version):
    db.execute("""
        INSERT INTO impact_predictions
        (news_id, ticker, direction, confidence, evidence, model_version, base_price, status)
        VALUES (:nid, :tk, :dir, :conf, :ev, :mv, :bp, 'pending')
    """, {
        "nid": news_id, "tk": ticker, "dir": direction,
        "conf": confidence, "ev": evidence, "mv": model_version,
        "bp": get_current_price(ticker),  # 예측 시점 종가 = 등락 기준
    })
```

> `status='pending'`과 `base_price`가 핵심. 예측 시점에 미래 주가를 절대 참조하지 않는다.

### 5-B. D+3 후: 채점 (Calibrator, 매일 02:00)

```python
# backend/tasks.py
from pykrx import stock

@app.task
def calibrate_predictions():
    """3거래일 지난 pending 예측을 실제 주가와 대조해 채점."""
    pending = db.query("""
        SELECT * FROM impact_predictions
        WHERE status='pending' AND predicted_at <= NOW() - INTERVAL '3 days'
    """)
    for p in pending:
        d3_price = get_price_after_trading_days(p.ticker, p.predicted_at, days=3)
        if d3_price is None:                 # 거래정지/상폐 → 제외
            db.update_status(p.id, "skipped")
            continue

        actual_change = (d3_price - p.base_price) / p.base_price
        predicted_up = (p.direction == "up")
        actual_up = (actual_change > 0)
        correct = (predicted_up == actual_up)

        db.execute("""
            UPDATE impact_predictions
            SET status='scored', actual_change=:ac, correct=:ok, scored_at=NOW()
            WHERE id=:id
        """, {"ac": actual_change, "ok": correct, "id": p.id})
```

### 5-C. 지표 집계 — Direction Accuracy + Calibration

```python
def prediction_metrics(db):
    rows = db.query("SELECT correct, confidence FROM impact_predictions WHERE status='scored'")
    n = len(rows)
    if n == 0:
        return {}
    accuracy = sum(r.correct for r in rows) / n
    high = [r for r in rows if r.confidence >= 0.7]
    high_acc = sum(r.correct for r in high) / max(len(high), 1)
    return {
        "n_scored": n,
        "direction_accuracy": round(accuracy, 3),        # 목표 0.6+
        "high_confidence_accuracy": round(high_acc, 3),   # 높은 confidence가 더 정확해야 정상
    }
```

> **Calibration이 핵심 인사이트**: 단순 정확도만 보지 말 것. "confidence 0.9 예측이 0.5 예측보다 실제로 더 잘 맞나"를 확인한다. 맞아떨어지면 confidence가 신뢰할 만하다는 증거. 어긋나면 Calibrator가 보정할 대상. 발표용으로 신뢰도 구간별 정확도 막대그래프 1개 추천.

---

## 6. [3+] Market Model 기반 CAR 보정 (선택, 평가 고도화)

단순 등락(`actual_change > 0`)은 학계 baseline 중 가장 약하다. 종목별 베타가 달라 고베타 종목(카카오, 셀트리온)이 항상 abnormal로 잡힌다. News_README의 Market Model을 [3]에 얹으면 평가가 단단해진다.

```python
# backend/evaluation/market_model.py
import statsmodels.api as sm

def estimate_market_model(stock_returns, market_returns):
    """추정 윈도우(이벤트 -250~-30일)에서 종목별 α, β 추정."""
    X = sm.add_constant(market_returns)
    model = sm.OLS(stock_returns, X).fit()
    return model.params[0], model.params[1], model.rsquared  # α, β, R²

def calc_abnormal_return(actual_ret, market_ret, alpha, beta):
    """실제 수익률 - 시장모델 기대 수익률."""
    expected = alpha + beta * market_ret
    return actual_ret - expected

def calc_CAR(abnormal_returns):
    """이벤트 윈도우(D~D+3) 누적 abnormal return."""
    return sum(abnormal_returns)
```

채점 시 `actual_change` 대신 `abnormal_return`으로 방향 판정하면 시장 전체 움직임에 오염되지 않은 순수 영향만 측정된다.

> 🔴 **look-ahead 절대 금지**: α, β 추정 윈도우는 이벤트 시점 *이전* 데이터만 사용. 추정 윈도우 내 거래정지 종목은 베타 불안정 → 제외. KOSPI200 종목 α/β는 매월 1회 재추정해 `market_model_params`에 캐시.

---

## 7. 집계 조회 엔드포인트

```python
# backend/evaluation/metrics_api.py
from fastapi import APIRouter
from evaluation.observability import get_agent_stats

router = APIRouter(prefix="/api/v1/_internal")

IN_PRICE = 3.0   # USD per 1M input tokens (최신 단가로 교체)
OUT_PRICE = 15.0

@router.get("/agent-stats")
async def agent_stats():
    stats = get_agent_stats()
    for s in stats.values():
        s["avg_latency_ms"] = round(s["total_ms"] / max(s["calls"], 1), 1)
        s["est_cost_usd"] = round(s["in_tok"]/1e6*IN_PRICE + s["out_tok"]/1e6*OUT_PRICE, 4)
    return stats

@router.get("/prediction-metrics")
async def pred_metrics():
    return prediction_metrics(db)

@router.get("/hallucination-summary")
async def hallu_summary():
    return db.query("""
        SELECT agent, AVG(faithfulness) AS avg_faith,
               SUM(invalid_ticker + missing_evidence)::float / NULLIF(SUM(checked),0) AS hallu_rate
        FROM hallucination_logs GROUP BY agent
    """)
```

---

## 8. 목표 지표 (캡스톤 발표용)

| 지표 | 목표 | 출처 |
|------|------|------|
| Direction Accuracy | ≥ 60% | 수행계획서 |
| High-confidence Accuracy | > 전체 정확도 | Calibration 검증 |
| 관련성 에이전트 Recall/F1 | (정의 후) | AI 파이프라인 |
| 할루시네이션율 (가짜 종목/근거) | < 5% | 코드 검증 |
| 요약 Faithfulness | ≥ 0.9 | LLM-as-Judge |
| 파이프라인 Latency | < 30분 | AI 파이프라인 |

---

## 9. 구현 체크리스트

- [ ] `evaluation/` 디렉토리 + 모듈 6종 생성
- [ ] `impact_predictions` 테이블에 채점 컬럼 추가 (base_price, status, actual_change, correct 등)
- [ ] `hallucination_logs`, `market_model_params` 테이블 생성
- [ ] [1] `track_agent` 데코레이터를 4개 에이전트에 적용
- [ ] [2-A] `check_grounding` 통합 에이전트 출력에 연결
- [ ] [2-B] `judge_faithfulness` summary 파이프라인에 연결 (judge 모델 분리)
- [ ] [3-A] `save_prediction` 통합 에이전트 예측 시점에 연결
- [ ] [3-B] `calibrate_predictions` Celery 태스크 등록 (매일 02:00)
- [ ] [3-C] `prediction_metrics` 집계 함수 구현
- [ ] [3+] Market Model CAR 보정 (선택, 시간 여유 시)
- [ ] `metrics_api.py` 엔드포인트 3종 노출
- [ ] judge accuracy 사람 검수 (20~30 샘플)
- [ ] 비용 단가 상수 최신화 점검
