"""
백테스트 평가 데이터셋 빌더

KOSPI50 종목 × 과거 기간 거래일에서 (ticker, as_of) 쌍을 무작위 추출한다.
시장 편향 방지를 위해 랜덤 시드를 고정하여 재현성을 보장한다.
시점 시각은 09:00 KST — 장 시작 직전 기준으로 pre-market 뉴스만 포함.
"""
from __future__ import annotations

import logging
import random
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# KOSPI50 pykrx 인덱스 코드 (2024년 기준)
_KOSPI50_INDEX = "1028"

# pykrx 실패 시 fallback — KOSPI 대형주 30종
_FALLBACK_TICKERS = [
    "005930", "000660", "035420", "005380", "051910",
    "006400", "035720", "207940", "068270", "105560",
    "055550", "086790", "003550", "066570", "009150",
    "028260", "000270", "012330", "034730", "096770",
    "323410", "018260", "032830", "003490", "029780",
    "011200", "010130", "005490", "000810", "042660",
]


def get_kospi50_tickers() -> list[str]:
    try:
        from pykrx import stock
        tickers = stock.get_index_portfolio_deposit_file(_KOSPI50_INDEX)
        result = [str(t) for t in tickers][:50]
        if result:
            return result
    except Exception as e:
        logger.warning("KOSPI50 종목 조회 실패, fallback 사용: %s", e)
    return _FALLBACK_TICKERS


def get_trading_days(fromdate: str, todate: str) -> list[str]:
    """
    두 날짜 사이의 거래일 목록 반환 (YYYY-MM-DD).
    pykrx 실패 시 주말 제외 캘린더 날짜로 대체 (공휴일 불포함 — 허용 오차).
    """
    try:
        from pykrx import stock
        days = stock.get_previous_business_days(fromdate=fromdate, todate=todate)
        result = [str(d)[:10] for d in days]
        if result:
            return result
        logger.warning("거래일 조회 결과가 비어있음 (KRX 응답 없음), 주말 제외 날짜 사용")
    except Exception as e:
        logger.warning("거래일 조회 실패, 주말 제외 날짜 사용: %s", e)

    # fallback: 주말 제외
    start = datetime.strptime(fromdate, "%Y%m%d")
    end = datetime.strptime(todate, "%Y%m%d")
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # 월~금
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def build_dataset(
    start: str = "20260301",
    end: str = "20260430",
    n_samples: int = 300,
    seed: int = 42,
    safety_days: int = 5,
) -> list[dict]:
    """
    과거 기간에서 (ticker, as_of) 트리플을 n_samples개 무작위 추출.

    Parameters
    ----------
    start, end   : YYYYMMDD 형식
    n_samples    : 추출 수
    seed         : 재현성용 랜덤 시드
    safety_days  : as_of <= today - safety_days 강제 (D+3 즉시 채점 보장).
                   0이면 제약 없음 (라이브/대기 모드).

    Returns
    -------
    list of {"ticker": str, "as_of": datetime, "date_str": str}
    """
    random.seed(seed)
    tickers = get_kospi50_tickers()
    trading_days = get_trading_days(start, end)

    if not tickers:
        raise RuntimeError("종목 목록을 가져올 수 없습니다.")
    if not trading_days:
        raise RuntimeError(f"거래일 목록이 없습니다 ({start}~{end}).")

    # 즉시 채점 가능 날짜만 필터 (D+3가 이미 경과한 것만)
    if safety_days > 0:
        cutoff = date.today() - timedelta(days=safety_days)
        trading_days = [
            d for d in trading_days
            if datetime.strptime(d, "%Y-%m-%d").date() <= cutoff
        ]
        if len(trading_days) < 3:
            raise ValueError(
                f"채점 가능 거래일 부족: {len(trading_days)}일 "
                f"(cutoff={cutoff}, safety_days={safety_days}). "
                f"--start/--end를 더 옛날로 옮기거나, "
                f"--safety_days=0으로 제약 해제하거나, "
                f"뉴스 풀을 더 깊이 적재한 후 재시도."
            )

    logger.info(
        "데이터셋 빌드 시작: 종목 %d개 × %d 거래일 → %d 샘플 추출 (safety_days=%d)",
        len(tickers), len(trading_days), n_samples, safety_days,
    )

    seen: set[tuple] = set()
    samples: list[dict] = []
    max_pool = len(tickers) * len(trading_days)

    if n_samples > max_pool:
        logger.warning("요청 샘플(%d) > 가용 조합(%d), 전체 추출", n_samples, max_pool)
        n_samples = max_pool

    attempts = 0
    max_attempts = n_samples * 30

    while len(samples) < n_samples and attempts < max_attempts:
        attempts += 1
        day_str = random.choice(trading_days)
        ticker = random.choice(tickers)
        key = (day_str, ticker)
        if key in seen:
            continue
        seen.add(key)
        # 장 시작 직전(09:00) 기준 — safety_days가 D+3 경과를 보장
        as_of = datetime.strptime(day_str, "%Y-%m-%d").replace(hour=9, minute=0, second=0)
        samples.append({"ticker": ticker, "as_of": as_of, "date_str": day_str})

    logger.info("데이터셋 빌드 완료: %d 샘플 (시도 %d회)", len(samples), attempts)
    return samples

# evaluation/dataset_builder.py — 추가 함수
def build_dataset_from_db(
    start: str,           # YYYY-MM-DD
    end: str,             # YYYY-MM-DD
    n_samples: int = 300,
    seed: int = 42,
    safety_days: int = 3,
) -> list[dict]:
    """
    DB의 NewsCache에 실제 뉴스가 있는 (ticker, date) 페어에서만 추출.
    fallback 매칭 실패로 인한 no_news skip을 원천 제거.
    """
    import random
    from datetime import date, datetime, timedelta
    from sqlalchemy import func
    from models.db_models import NewsCache, SessionLocal

    random.seed(seed)
    start_iso = f"{start}T00:00:00"
    end_iso = f"{end}T23:59:59"
    cutoff = date.today() - timedelta(days=safety_days)

    db = SessionLocal()
    try:
        # 실제로 데이터가 있는 (ticker, 날짜) 페어만
        rows = (
            db.query(
                NewsCache.ticker,
                func.substr(NewsCache.published_at, 1, 10).label("d"),
            )
            .filter(NewsCache.published_at >= start_iso)
            .filter(NewsCache.published_at <= end_iso)
            .group_by(NewsCache.ticker, "d")
            .all()
        )
    finally:
        db.close()

    # safety_days 적용
    eligible = [
        (t, d) for t, d in rows
        if datetime.strptime(d, "%Y-%m-%d").date() <= cutoff
    ]
    if not eligible:
        raise ValueError(
            f"채점 가능 (ticker, date) 페어 0개 (cutoff={cutoff}). "
            f"safety_days를 줄이거나 뉴스 풀 적재 필요."
        )

    # 부족하면 가용분 전체, 충분하면 무작위 추출
    take = min(n_samples, len(eligible))
    sampled = random.sample(eligible, take)

    return [{
        "ticker": t,
        "as_of": datetime.strptime(d, "%Y-%m-%d").replace(hour=9),
        "date_str": d,
    } for t, d in sampled]
