"""[3] 예측 정확도 채점

[프로젝트 평가 기준]
  핵심 질문: "뉴스가 뜬 종목이 실제로 유의미하게 변동했는가?"
  주지표: Magnitude Hit  — |actual_change| >= MAGNITUDE_THRESHOLD
  부지표: Direction      — up/down 방향 일치 (참고용)

is_correct = magnitude_hit (주지표)
abnormal_return = 1.0(hit) / 0.0(miss) — 집계용
"""
import logging
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger("stoogle.evaluation")

MAGNITUDE_THRESHOLD = 0.02  # ±2% 이상 변동 = 유의미한 변동


# ─────────────────────────────────────────────────────────────────────────────
# Step A — 예측 저장
# ─────────────────────────────────────────────────────────────────────────────

def save_prediction(
    db,
    ticker: str,
    direction: str,
    confidence: float,
    evidence: str,
    model_version: str,
    news_id: Optional[int] = None,
    base_price: Optional[float] = None,
) -> None:
    try:
        from models.db_models import PredictionLog

        if base_price is None:
            try:
                from services.stock_service import get_current_price
                price_info = get_current_price(ticker)
                base_price = price_info.get("price") if price_info else None
            except Exception:
                pass

        today_str = date.today().strftime("%Y-%m-%d")
        target_str = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")

        db.add(
            PredictionLog(
                ticker=ticker,
                source_ticker=ticker,
                direction=direction,
                confidence=confidence,
                reason=evidence,
                model_version=model_version,
                prediction_date=today_str,
                target_date=target_str,
                predicted_at=datetime.utcnow(),
                base_close=base_price,
                status="pending",
            )
        )
        db.commit()
    except Exception as e:
        logger.error("save_prediction 실패 [%s]: %s", ticker, e)
        try:
            db.rollback()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Step B — D+3 채점
# ─────────────────────────────────────────────────────────────────────────────

def is_d3_passed(predicted_at: datetime, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    try:
        from pykrx import stock
        biz = stock.get_previous_business_days(
            fromdate=predicted_at.strftime("%Y%m%d"),
            todate=now.strftime("%Y%m%d"),
        )
        if biz:
            return len(biz) >= 4
    except Exception:
        pass
    return (now - predicted_at).days >= 4


def score_pending_predictions(
    db,
    model_version: Optional[str] = None,
    force_score_all: bool = False,
) -> dict:
    """
    D+3가 경과한 pending 예측을 실제 주가와 대조해 채점.

    주지표: is_correct = magnitude_hit (|actual_change| >= MAGNITUDE_THRESHOLD)
    부지표: abnormal_return = 1.0(hit) / 0.0(miss)  — 동일값, 집계용
    참고:   actual_direction 컬럼에 실제 방향(up/down) 저장
    """
    try:
        from models.db_models import PredictionLog

        q = db.query(PredictionLog).filter(PredictionLog.status == "pending")
        if model_version is not None:
            q = q.filter(PredictionLog.model_version == model_version)
        pending = q.all()

        scored = skipped = 0
        for p in pending:
            if not force_score_all and not is_d3_passed(p.predicted_at):
                continue

            if not p.base_close or not p.prediction_date:
                p.status = "skipped"
                skipped += 1
                continue

            d3_price = _get_price_after_trading_days(p.ticker, p.prediction_date, days=3)
            if d3_price is None:
                p.status = "skipped"
                skipped += 1
                continue

            actual_change = (d3_price - p.base_close) / p.base_close
            p.actual_change = round(actual_change, 6)
            p.actual_close = d3_price
            p.actual_direction = "up" if actual_change > 0 else "down"

            # 주지표: 변동 폭 (프로젝트 목적과 일치)
            magnitude_hit = abs(actual_change) >= MAGNITUDE_THRESHOLD
            p.is_correct = magnitude_hit
            p.abnormal_return = 1.0 if magnitude_hit else 0.0

            p.status = "scored"
            p.evaluated_at = datetime.utcnow()
            scored += 1

        db.commit()
        return {"scored": scored, "skipped": skipped, "total": len(pending)}
    except Exception as e:
        logger.error("score_pending_predictions 실패: %s", e)
        return {"error": str(e)}


def score_all_pending(db, model_version: Optional[str] = None) -> dict:
    """백테스트 전용: D+3 체크 없이 전체 pending 즉시 채점."""
    return score_pending_predictions(db, model_version=model_version, force_score_all=True)


# ─────────────────────────────────────────────────────────────────────────────
# Step C — 지표 집계
# ─────────────────────────────────────────────────────────────────────────────

def prediction_metrics(db, model_version: Optional[str] = None) -> dict:
    """
    주지표: magnitude_hit_rate  — |Δ| >= MAGNITUDE_THRESHOLD
    calibration: high-confidence 예측의 hit_rate가 전체보다 높은지
    """
    try:
        from models.db_models import PredictionLog

        q = db.query(
            PredictionLog.is_correct,
            PredictionLog.confidence,
            PredictionLog.abnormal_return,
        ).filter(PredictionLog.status == "scored")
        if model_version is not None:
            q = q.filter(PredictionLog.model_version == model_version)
        rows = q.all()
        n = len(rows)
        if n == 0:
            return {"n_scored": 0}

        # 주지표
        mag_hit = sum(1 for r in rows if r.is_correct) / n

        # calibration
        high = [r for r in rows if r.confidence is not None and r.confidence >= 0.7]
        high_hit = sum(1 for r in high if r.is_correct) / max(len(high), 1)

        return {
            "n_scored": n,
            "magnitude_hit_rate": round(mag_hit, 3),
            "threshold": MAGNITUDE_THRESHOLD,
            "high_confidence_hit_rate": round(high_hit, 3),
            "n_high_conf": len(high),
        }
    except Exception as e:
        logger.error("prediction_metrics 실패: %s", e)
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ─────────────────────────────────────────────────────────────────────────────

def _get_price_after_trading_days(
    ticker: str, from_date_str: str, days: int = 3
) -> Optional[float]:
    try:
        from pykrx import stock as pykrx_stock

        base = datetime.strptime(from_date_str, "%Y-%m-%d")
        to_dt = (base + timedelta(days=days + 7)).strftime("%Y%m%d")
        from_dt = (base + timedelta(days=1)).strftime("%Y%m%d")

        df = pykrx_stock.get_market_ohlcv_by_date(
            fromdate=from_dt, todate=to_dt, ticker=ticker
        )
        if df is None or df.empty or len(df) < days:
            return None
        return float(df.iloc[days - 1]["종가"])
    except Exception as e:
        logger.warning("D+%d 종가 조회 실패 [%s %s]: %s", days, ticker, from_date_str, e)
        return None