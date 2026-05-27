"""
KOSPI200 종목 목록 관리

tasks.py(Celery)와 relation_service.py 모두 KOSPI200 목록이 필요하지만,
relation_service가 tasks를 임포트하면 Celery 앱 초기화 + pykrx 호출이
API 요청 경로에서 발생한다. 이 모듈을 중간 계층으로 분리하여 의존성을 끊는다.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

KOSPI200_FALLBACK: list[str] = [
    "005930", "000660", "035420", "051910", "207940",
    "035720", "066570", "005380", "000270", "068270",
    "028260", "105560", "055550", "032830", "003550",
    "259960", "012330", "015760", "030200", "096770",
    "017670", "034730", "009150", "010950", "000810",
    "011200", "034020", "033780", "003490", "316140",
]


def load_kospi200() -> list[str]:
    """
    pykrx에서 KOSPI 200 실시간 구성 종목을 조회한다.
    조회 실패 시 fallback 목록을 반환한다.
    """
    try:
        from pykrx import stock as pykrx_stock
        from services.stock_service import _normalize_ticker_list

        today = datetime.today().strftime("%Y%m%d")
        tickers = _normalize_ticker_list(
            pykrx_stock.get_index_portfolio_deposit_file("1028", date=today)
        )
        if len(tickers) > 10:
            return tickers
    except Exception as e:
        logger.warning("KOSPI200 구성 종목 조회 실패 — fallback 사용: %s", e)
    return KOSPI200_FALLBACK


KOSPI200_TICKERS: list[str] = load_kospi200()
