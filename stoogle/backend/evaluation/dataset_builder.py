# 데이터셋 빌더
from __future__ import annotations

import logging
import random
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

_KOSPI50_INDEX = "1028"

_FALLBACK_TICKERS = [
    "005930", "000660", "035420", "005380", "051910",
    "006400", "035720", "207940", "068270", "105560",
    "055550", "086790", "003550", "066570", "009150",
    "028260", "000270", "012330", "034730", "096770",
    "323410", "018260", "032830", "003490", "029780",
    "011200", "010130", "005490", "000810", "042660",
]


# KOSPI50 종목
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


# 거래일 목록
def get_trading_days(fromdate: str, todate: str) -> list[str]:
    try:
        from pykrx import stock
        days = stock.get_previous_business_days(fromdate=fromdate, todate=todate)
        result = [str(d)[:10] for d in days]
        if result:
            return result
        logger.warning("거래일 조회 결과가 비어있음 (KRX 응답 없음), 주말 제외 날짜 사용")
    except Exception as e:
        logger.warning("거래일 조회 실패, 주말 제외 날짜 사용: %s", e)

    start = datetime.strptime(fromdate, "%Y%m%d")
    end = datetime.strptime(todate, "%Y%m%d")
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


# 데이터셋 빌드
def build_dataset(
    start: str = "20260301",
    end: str = "20260430",
    n_samples: int = 300,
    seed: int = 42,
    safety_days: int = 5,
) -> list[dict]:
    random.seed(seed)
    tickers = get_kospi50_tickers()
    trading_days = get_trading_days(start, end)

    if not tickers:
        raise RuntimeError("종목 목록을 가져올 수 없습니다.")
    if not trading_days:
        raise RuntimeError(f"거래일 목록이 없습니다 ({start}~{end}).")

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
        as_of = datetime.strptime(day_str, "%Y-%m-%d").replace(hour=9, minute=0, second=0)
        samples.append({"ticker": ticker, "as_of": as_of, "date_str": day_str})

    logger.info("데이터셋 빌드 완료: %d 샘플 (시도 %d회)", len(samples), attempts)
    return samples


# DB기반 데이터셋
def build_dataset_from_db(
    start: str,
    end: str,
    n_samples: int = 300,
    seed: int = 42,
    safety_days: int = 3,
) -> list[dict]:
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

    eligible = [
        (t, d) for t, d in rows
        if datetime.strptime(d, "%Y-%m-%d").date() <= cutoff
    ]
    if not eligible:
        raise ValueError(
            f"채점 가능 (ticker, date) 페어 0개 (cutoff={cutoff}). "
            f"safety_days를 줄이거나 뉴스 풀 적재 필요."
        )

    take = min(n_samples, len(eligible))
    sampled = random.sample(eligible, take)

    return [{
        "ticker": t,
        "as_of": datetime.strptime(d, "%Y-%m-%d").replace(hour=9),
        "date_str": d,
    } for t, d in sampled]
