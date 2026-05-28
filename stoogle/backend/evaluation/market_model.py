"""[3+] Market Model 기반 CAR (Cumulative Abnormal Return) 보정

단순 등락(actual_change > 0)은 고베타 종목이 항상 abnormal로 잡히는 약점이 있다.
Market Model로 종목별 α, β를 추정하면 시장 전체 움직임을 제거한 순수 영향만 측정된다.

⚠️ look-ahead 절대 금지: α, β 추정 윈도우는 이벤트 시점 이전 데이터만 사용.
   추정 윈도우 내 거래정지 종목은 베타 불안정 → 제외.
   KOSPI200 α/β는 매월 1회 재추정해 market_model_params 테이블에 캐시.
"""
import logging
from typing import Optional

logger = logging.getLogger("stoogle.evaluation")


def estimate_market_model(
    stock_returns: list[float], market_returns: list[float]
) -> tuple[float, float, float]:
    """
    추정 윈도우(이벤트 -250~-30일)에서 종목별 α, β 추정.

    Returns
    -------
    (alpha, beta, r_squared)
    statsmodels 미설치 시 (0.0, 1.0, 0.0) 반환.
    """
    try:
        import statsmodels.api as sm
        import numpy as np

        X = sm.add_constant(market_returns)
        model = sm.OLS(stock_returns, X).fit()
        alpha = float(model.params[0])
        beta = float(model.params[1])
        return alpha, beta, float(model.rsquared)
    except Exception as e:
        logger.warning("market_model 추정 실패: %s", e)
        return 0.0, 1.0, 0.0


def calc_abnormal_return(
    actual_ret: float, market_ret: float, alpha: float, beta: float
) -> float:
    """실제 수익률 - 시장모델 기대 수익률."""
    expected = alpha + beta * market_ret
    return actual_ret - expected


def calc_CAR(abnormal_returns: list[float]) -> float:
    """이벤트 윈도우(D~D+3) 누적 abnormal return."""
    return sum(abnormal_returns)


def get_market_params(
    ticker: str, estimation_date: str, db
) -> Optional[tuple[float, float, float]]:
    """market_model_params 테이블에서 α, β 조회."""
    try:
        from models.db_models import MarketModelParam

        row = (
            db.query(MarketModelParam)
            .filter(
                MarketModelParam.ticker == ticker,
                MarketModelParam.estimation_date == estimation_date,
            )
            .first()
        )
        if row:
            return row.alpha, row.beta, row.r_squared
        return None
    except Exception as e:
        logger.warning("market_model_params 조회 실패: %s", e)
        return None


def save_market_params(
    ticker: str,
    estimation_date: str,
    alpha: float,
    beta: float,
    r_squared: float,
    db,
) -> None:
    """α, β 파라미터를 market_model_params 테이블에 캐시."""
    try:
        from models.db_models import MarketModelParam

        existing = (
            db.query(MarketModelParam)
            .filter(
                MarketModelParam.ticker == ticker,
                MarketModelParam.estimation_date == estimation_date,
            )
            .first()
        )
        if existing:
            existing.alpha = alpha
            existing.beta = beta
            existing.r_squared = r_squared
        else:
            db.add(
                MarketModelParam(
                    ticker=ticker,
                    estimation_date=estimation_date,
                    alpha=alpha,
                    beta=beta,
                    r_squared=r_squared,
                )
            )
        db.commit()
    except Exception as e:
        logger.warning("market_model_params 저장 실패: %s", e)
