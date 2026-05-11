"""
예측 정확도 보정 에이전트 (Calibrator)

입력:
  - records: 예측 레코드 목록 (List[PredictionRecord])
  - actual_prices: D+3 실제 종가 {ticker: close_price}

출력: CalibrationStats (보정된 confidence 점수 float 포함)

동작:
  1. 예측 방향(up/down) vs 실제 방향 비교 → Direction Accuracy 계산
  2. Isotonic Regression으로 confidence 보정
  3. reason 텍스트 임베딩 → PredictionVector(pgvector) 저장
  4. PredictionLog 테이블 업데이트

Celery: 매일 02:00 자동 실행 (tasks.py의 calibrate_predictions 태스크)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_NEUTRAL_BAND = 0.005  # ±0.5% 이내는 neutral로 판정
_MIN_SAMPLES_FOR_CALIBRATION = 5  # 이 미만이면 보정 없이 raw confidence 반환


# ─────────────────────────────────────────────────────────────────────────────
# 공개 입출력 타입
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PredictionRecord:
    ticker: str             # 예측 대상 종목코드
    source_ticker: str      # 분석 출처 종목코드
    direction: str          # "up" / "down" / "neutral"
    confidence: float       # 원시 confidence 0.0~1.0
    reason: str             # 판단 근거 텍스트 (임베딩용)
    prediction_date: str    # D+0 YYYY-MM-DD
    target_date: str        # D+3 YYYY-MM-DD
    predicted_at: datetime = field(default_factory=datetime.utcnow)
    db_id: Optional[int] = None   # 이미 PredictionLog에 저장된 경우 id


@dataclass
class CalibrationStats:
    direction_accuracy: float           # 방향 정확도 (0.0~1.0)
    sample_count: int                   # 평가된 예측 수
    mean_raw_confidence: float          # 보정 전 평균 confidence
    mean_calibrated_confidence: float   # 보정 후 평균 confidence
    calibrated_scores: list[float]      # 레코드별 보정 confidence (입력 순서 동일)


# ─────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ─────────────────────────────────────────────────────────────────────────────

def _direction(base: float, actual: float) -> str:
    if base <= 0:
        return "neutral"
    change = (actual - base) / base
    if change > _NEUTRAL_BAND:
        return "up"
    if change < -_NEUTRAL_BAND:
        return "down"
    return "neutral"


def _fetch_close_on_date(ticker: str, date_str: str) -> Optional[float]:
    """
    특정 날짜의 종가를 pykrx에서 조회한다.
    date_str: YYYY-MM-DD
    """
    try:
        from pykrx import stock as pykrx_stock

        yyyymmdd = date_str.replace("-", "")
        # 혹시 휴장일이면 전후 5일 범위로 조회 후 가장 가까운 날짜 선택
        from_dt = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y%m%d")
        df = pykrx_stock.get_market_ohlcv_by_date(
            fromdate=from_dt, todate=yyyymmdd, ticker=ticker
        )
        if df is None or df.empty:
            return None
        return float(df.iloc[-1]["종가"])
    except Exception as e:
        logger.warning("종가 조회 실패 [%s %s]: %s", ticker, date_str, e)
        return None


def _isotonic_calibrate(
    confidences: list[float],
    labels: list[int],
) -> list[float]:
    """
    Isotonic Regression으로 raw confidence → calibrated probability.

    labels: 1 = correct, 0 = incorrect
    샘플 수 부족 시 raw confidence 반환.
    """
    if len(confidences) < _MIN_SAMPLES_FOR_CALIBRATION:
        return confidences

    try:
        from sklearn.isotonic import IsotonicRegression

        X = np.array(confidences, dtype=np.float64)
        y = np.array(labels, dtype=np.float64)

        ir = IsotonicRegression(out_of_bounds="clip", increasing=True)
        ir.fit(X, y)
        return [float(v) for v in ir.predict(X)]
    except Exception as e:
        logger.warning("Isotonic Regression 실패 — raw 반환: %s", e)
        return confidences


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """OpenAI text-embedding-3-small 배치 임베딩. 실패 시 빈 리스트 반환."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not texts:
        return []
    try:
        from openai import AsyncOpenAI
        from agents.dedup_indexer import EMBED_MODEL, EMBED_BATCH_SIZE

        client = AsyncOpenAI(api_key=api_key)
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            resp = await client.embeddings.create(
                model=EMBED_MODEL, input=texts[i : i + EMBED_BATCH_SIZE]
            )
            embeddings.extend(item.embedding for item in resp.data)
        return embeddings
    except Exception as e:
        logger.warning("임베딩 실패 (pgvector 저장 건너뜀): %s", e)
        return []


def _save_prediction_logs(
    records: list[PredictionRecord],
    outcomes: list[dict],       # {actual_direction, base_close, actual_close, is_correct}
    calibrated: list[float],
) -> list[int]:
    """PredictionLog upsert 후 저장된 id 목록 반환."""
    try:
        from models.db_models import PredictionLog, SessionLocal

        db = SessionLocal()
        ids: list[int] = []
        try:
            now = datetime.utcnow()
            for rec, out, cal in zip(records, outcomes, calibrated):
                if rec.db_id:
                    row = db.query(PredictionLog).filter(PredictionLog.id == rec.db_id).first()
                    if row:
                        row.actual_direction = out["actual_direction"]
                        row.base_close = out["base_close"]
                        row.actual_close = out["actual_close"]
                        row.is_correct = out["is_correct"]
                        row.calibrated_confidence = cal
                        row.evaluated_at = now
                        ids.append(row.id)
                        continue

                row = PredictionLog(
                    ticker=rec.ticker,
                    source_ticker=rec.source_ticker,
                    direction=rec.direction,
                    confidence=rec.confidence,
                    calibrated_confidence=cal,
                    reason=rec.reason,
                    prediction_date=rec.prediction_date,
                    target_date=rec.target_date,
                    predicted_at=rec.predicted_at,
                    base_close=out["base_close"],
                    actual_close=out["actual_close"],
                    actual_direction=out["actual_direction"],
                    is_correct=out["is_correct"],
                    evaluated_at=now,
                )
                db.add(row)
                db.flush()
                ids.append(row.id)

            db.commit()
        finally:
            db.close()
        return ids
    except Exception as e:
        logger.error("PredictionLog 저장 실패: %s", e)
        return []


async def _save_prediction_vectors(
    log_ids: list[int],
    reasons: list[str],
    labels: list[int],
    calibrated: list[float],
) -> None:
    """reason 텍스트를 임베딩하여 PredictionVector에 저장."""
    try:
        from models.db_models import PredictionVector, SessionLocal, PGVECTOR_AVAILABLE

        if not PGVECTOR_AVAILABLE or PredictionVector is None:
            return

        embeddings = await _embed_texts(reasons)
        if not embeddings or len(embeddings) != len(log_ids):
            return

        db = SessionLocal()
        try:
            for log_id, emb, label, cal in zip(log_ids, embeddings, labels, calibrated):
                existing = (
                    db.query(PredictionVector)
                    .filter(PredictionVector.prediction_log_id == log_id)
                    .first()
                )
                if existing:
                    existing.embedding = emb
                    existing.is_correct = bool(label)
                    existing.calibrated_confidence = cal
                else:
                    db.add(PredictionVector(
                        prediction_log_id=log_id,
                        embedding=emb,
                        is_correct=bool(label),
                        calibrated_confidence=cal,
                    ))
            db.commit()
            logger.info("PredictionVector %d건 저장 완료", len(log_ids))
        finally:
            db.close()
    except Exception as e:
        logger.error("PredictionVector 저장 실패: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# 퍼블릭 API
# ─────────────────────────────────────────────────────────────────────────────

async def run(
    records: list[PredictionRecord],
    actual_prices: dict[str, float],
) -> CalibrationStats:
    """
    예측 레코드를 평가하고 confidence를 보정한다.

    Parameters
    ----------
    records       : 평가할 예측 레코드 목록
    actual_prices : D+3 실제 종가 {ticker: close_price}

    Returns
    -------
    CalibrationStats — mean_calibrated_confidence 가 핵심 float 출력값
    """
    if not records:
        return CalibrationStats(
            direction_accuracy=0.0,
            sample_count=0,
            mean_raw_confidence=0.0,
            mean_calibrated_confidence=0.0,
            calibrated_scores=[],
        )

    outcomes: list[dict] = []
    evaluated: list[PredictionRecord] = []
    labels: list[int] = []

    for rec in records:
        actual_close = actual_prices.get(rec.ticker)
        if actual_close is None:
            logger.debug("실제 종가 없음 — 건너뜀 [%s]", rec.ticker)
            continue

        base_close = _fetch_close_on_date(rec.ticker, rec.prediction_date)
        if base_close is None:
            logger.debug("기준 종가 없음 — 건너뜀 [%s %s]", rec.ticker, rec.prediction_date)
            continue

        actual_dir = _direction(base_close, actual_close)
        # neutral 예측은 정확도 계산에서 제외
        is_correct = (
            rec.direction == actual_dir
            if rec.direction != "neutral"
            else None
        )

        outcomes.append({
            "base_close": base_close,
            "actual_close": actual_close,
            "actual_direction": actual_dir,
            "is_correct": is_correct,
        })
        evaluated.append(rec)
        if is_correct is not None:
            labels.append(1 if is_correct else 0)

    if not evaluated:
        return CalibrationStats(
            direction_accuracy=0.0,
            sample_count=0,
            mean_raw_confidence=0.0,
            mean_calibrated_confidence=0.0,
            calibrated_scores=[],
        )

    # Direction Accuracy (neutral 제외)
    scored = [o for o in outcomes if o["is_correct"] is not None]
    direction_accuracy = sum(1 for o in scored if o["is_correct"]) / len(scored) if scored else 0.0

    # Confidence 보정
    raw_confidences = [r.confidence for r in evaluated]
    non_neutral_indices = [i for i, r in enumerate(evaluated) if r.direction != "neutral"]
    non_neutral_confidences = [raw_confidences[i] for i in non_neutral_indices]
    non_neutral_labels = labels  # already filtered above

    calibrated_all = list(raw_confidences)  # start with raw, overwrite non-neutral
    if non_neutral_confidences:
        cal_scores = _isotonic_calibrate(non_neutral_confidences, non_neutral_labels)
        for idx, cal in zip(non_neutral_indices, cal_scores):
            calibrated_all[idx] = cal

    # DB 저장
    log_ids = _save_prediction_logs(evaluated, outcomes, calibrated_all)
    if log_ids:
        await _save_prediction_vectors(
            log_ids,
            [r.reason for r in evaluated],
            labels,
            calibrated_all,
        )

    mean_cal = float(np.mean(calibrated_all))
    logger.info(
        "보정 완료 — 샘플 %d건, 방향 정확도 %.1f%%, 평균 보정 confidence %.3f",
        len(evaluated), direction_accuracy * 100, mean_cal,
    )

    return CalibrationStats(
        direction_accuracy=direction_accuracy,
        sample_count=len(evaluated),
        mean_raw_confidence=float(np.mean(raw_confidences)),
        mean_calibrated_confidence=mean_cal,
        calibrated_scores=calibrated_all,
    )


async def run_daily() -> CalibrationStats:
    """
    매일 02:00 Celery 태스크에서 호출.

    target_date가 오늘 이전이고 아직 평가되지 않은 PredictionLog를 조회,
    실제 종가를 pykrx로 가져와 run()을 실행한다.
    """
    try:
        from models.db_models import PredictionLog, SessionLocal

        cutoff = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

        db = SessionLocal()
        try:
            pending = (
                db.query(PredictionLog)
                .filter(
                    PredictionLog.target_date <= cutoff,
                    PredictionLog.actual_direction.is_(None),
                )
                .all()
            )
        finally:
            db.close()

        if not pending:
            logger.info("평가 대기 예측 없음")
            return CalibrationStats(0.0, 0, 0.0, 0.0, [])

        logger.info("평가 대기 예측 %d건 처리 시작", len(pending))

        # target_date별로 유니크 ticker 수집 후 실제 종가 일괄 조회
        target_tickers: dict[str, set[str]] = {}
        for row in pending:
            target_tickers.setdefault(row.target_date, set()).add(row.ticker)

        actual_prices: dict[str, float] = {}
        for target_date, tickers in target_tickers.items():
            for ticker in tickers:
                price = _fetch_close_on_date(ticker, target_date)
                if price is not None:
                    actual_prices[ticker] = price

        records = [
            PredictionRecord(
                ticker=row.ticker,
                source_ticker=row.source_ticker or "",
                direction=row.direction,
                confidence=row.confidence,
                reason=row.reason or "",
                prediction_date=row.prediction_date,
                target_date=row.target_date,
                predicted_at=row.predicted_at,
                db_id=row.id,
            )
            for row in pending
        ]

        return await run(records, actual_prices)

    except Exception as e:
        logger.error("run_daily 실패: %s", e)
        return CalibrationStats(0.0, 0, 0.0, 0.0, [])


# ─────────────────────────────────────────────────────────────────────────────
# 직접 실행 (테스트)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from datetime import date, timedelta

    today = date.today()
    d0 = (today - timedelta(days=4)).strftime("%Y-%m-%d")
    d3 = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    sample_records = [
        PredictionRecord(
            ticker="000660", source_ticker="005930",
            direction="up", confidence=0.82,
            reason="공급 계약 확대 수혜 예상",
            prediction_date=d0, target_date=d3,
        ),
        PredictionRecord(
            ticker="009150", source_ticker="005930",
            direction="up", confidence=0.65,
            reason="부품 발주 증가로 매출 상승",
            prediction_date=d0, target_date=d3,
        ),
        PredictionRecord(
            ticker="035420", source_ticker="005930",
            direction="down", confidence=0.45,
            reason="업황 악화시 광고 시장 위축 우려",
            prediction_date=d0, target_date=d3,
        ),
    ]

    # 실제 종가 수동 제공 (pykrx 없는 환경용)
    mock_actuals = {"000660": 185000.0, "009150": 157000.0, "035420": 210000.0}

    result = asyncio.run(run(sample_records, mock_actuals))
    print(f"\n방향 정확도:     {result.direction_accuracy:.1%}")
    print(f"평가 샘플 수:    {result.sample_count}건")
    print(f"평균 raw conf:   {result.mean_raw_confidence:.3f}")
    print(f"평균 보정 conf:  {result.mean_calibrated_confidence:.3f}")
    print(f"보정 점수:       {[f'{s:.3f}' for s in result.calibrated_scores]}")
