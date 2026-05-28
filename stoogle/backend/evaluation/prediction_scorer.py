"""[3] 예측 정확도 채점 — 예측 저장(Step A) + D+3 대조 채점(Step B)

두 단계로 분리해 look-ahead를 원천 차단한다:
  A. 예측 시점: save_prediction() → status='pending', base_price 박제
  B. D+3 이후:  score_pending_predictions() → 실제 주가와 대조
"""
import logging
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger("stoogle.evaluation")


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
    """
    예측 시점 레코드를 status='pending'으로 저장한다.
    base_price는 예측 시점 종가로 박제 — 채점 시 look-ahead 방지.
    """
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

def score_pending_predictions(db) -> dict:
    """
    3거래일 지난 pending 예측을 실제 주가와 대조해 채점.
    calibrate_predictions Celery 태스크(매일 02:00)에서 호출한다.
    """
    try:
        from models.db_models import PredictionLog

        cutoff = datetime.utcnow() - timedelta(days=3)
        pending = (
            db.query(PredictionLog)
            .filter(
                PredictionLog.status == "pending",
                PredictionLog.predicted_at <= cutoff,
            )
            .all()
        )

        scored = skipped = 0
        for p in pending:
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
            p.is_correct = (p.direction == p.actual_direction)
            p.status = "scored"
            p.evaluated_at = datetime.utcnow()
            scored += 1

        db.commit()
        return {"scored": scored, "skipped": skipped, "total": len(pending)}
    except Exception as e:
        logger.error("score_pending_predictions 실패: %s", e)
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Step C — 지표 집계
# ─────────────────────────────────────────────────────────────────────────────

def prediction_metrics(db) -> dict:
    """
    Direction Accuracy + Calibration 집계.
    목표: direction_accuracy >= 0.6, high_confidence_accuracy > 전체 정확도.
    """
    try:
        from models.db_models import PredictionLog

        rows = (
            db.query(PredictionLog.is_correct, PredictionLog.confidence)
            .filter(PredictionLog.status == "scored")
            .all()
        )
        n = len(rows)
        if n == 0:
            return {"n_scored": 0}

        accuracy = sum(1 for r in rows if r.is_correct) / n
        high = [r for r in rows if r.confidence is not None and r.confidence >= 0.7]
        high_acc = sum(1 for r in high if r.is_correct) / max(len(high), 1)

        return {
            "n_scored": n,
            "direction_accuracy": round(accuracy, 3),
            "high_confidence_accuracy": round(high_acc, 3),
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
    """from_date_str 이후 N 거래일의 종가를 pykrx로 조회."""
    try:
        from pykrx import stock as pykrx_stock

        base = datetime.strptime(from_date_str, "%Y-%m-%d")
        # 여유 있게 +7일 범위 조회 후 N번째 거래일 선택
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
