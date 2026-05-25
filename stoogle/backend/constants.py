"""
프로젝트 공통 상수.

tasks.py / relation_service.py / stock_service.py 모두 이 파일에서 import한다.
외부 서비스(pykrx 제외)에 의존하지 않아 순환 import 위험이 없다.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_KOSPI200_FALLBACK: list[str] = [
    "005930", "000660", "035420", "051910", "207940",
    "035720", "066570", "005380", "000270", "068270",
    "028260", "105560", "055550", "032830", "003550",
    "259960", "012330", "015760", "030200", "096770",
    "017670", "034730", "009150", "010950", "000810",
    "011200", "034020", "033780", "003490", "316140",
]


def _load_kospi200() -> list[str]:
    """
    pykrx에서 KOSPI 200 실시간 구성 종목을 조회한다.
    조회 실패 시 fallback 목록을 반환한다.
    stock_service를 import하지 않아 순환 의존성이 없다.
    """
    try:
        from pykrx import stock as pykrx_stock

        today = datetime.today().strftime("%Y%m%d")
        raw = pykrx_stock.get_index_portfolio_deposit_file("1028", date=today)
        tickers = list(raw) if raw is not None else []
        tickers = [str(t).zfill(6) for t in tickers if str(t).strip()]
        if len(tickers) > 10:
            return tickers
    except Exception as e:
        logger.warning("KOSPI200 구성 종목 조회 실패 — fallback 사용: %s", e)
    return list(_KOSPI200_FALLBACK)


KOSPI200_TICKERS: list[str] = _load_kospi200()
