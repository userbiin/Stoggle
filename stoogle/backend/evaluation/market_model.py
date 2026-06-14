# 시장 모델
import logging
from typing import Optional

logger = logging.getLogger("stoogle.evaluation")


def estimate_market_model(
    stock_returns: list[float], market_returns: list[float]
) -> tuple[float, float, float]:
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
    expected = alpha + beta * market_ret
    return actual_ret - expected


def calc_CAR(abnormal_returns: list[float]) -> float:
    return sum(abnormal_returns)


def get_market_params(
    ticker: str, estimation_date: str, db
) -> Optional[tuple[float, float, float]]:
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
