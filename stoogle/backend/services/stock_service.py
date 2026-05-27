"""
pykrx를 이용한 주가 데이터 수집 서비스

검색 흐름:
  1. Redis 레지스트리 조회 → O(1) 이름 매칭
  2. 레지스트리 없으면 pykrx 풀스캔 후 Redis에 저장 (초기 1회만)
  3. 가격 데이터도 Redis 캐시 우선, 없으면 pykrx 호출
"""
from datetime import datetime, timedelta
from typing import Optional
import logging

import pandas as pd

logger = logging.getLogger(__name__)

try:
    from pykrx import stock as pykrx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False
    logger.warning("pykrx 미설치 — 주가 기능 비활성화")

from models.schemas import PricePoint, CompanyBrief
from services.cache_service import (
    get_ticker_registry, set_ticker_registry,
    get_price_cache, set_price_cache,
    get_history_cache, set_history_cache,
)



def _today() -> str:
    return datetime.today().strftime("%Y%m%d")


def _n_days_ago(n: int) -> str:
    return (datetime.today() - timedelta(days=n)).strftime("%Y%m%d")


def _is_trading_hours() -> bool: # 장 중 여부 판별 (평일 09:00 ~ 15:00)
    now = datetime.now()
    return (
        now.weekday() < 5
        and (9, 0) <= (now.hour, now.minute) <= (15, 30)
    )


def _cache_ttl_for_price() -> int:
    """장 중이면 60초, 장외(주말·야간)면 48시간"""
    return 60 if _is_trading_hours() else 60 * 60 * 48


def _cache_ttl_for_history() -> int:
    """장 중이면 10분, 장외면 24시간"""
    return 600 if _is_trading_hours() else 60 * 60 * 24


def _normalize_ticker(value) -> Optional[str]:
    """pykrx/pandas 반환값에서 6자리 종목코드 문자열을 안전하게 추출."""
    if value is None:
        return None

    if isinstance(value, (pd.Series, pd.Index)):
        for item in value.tolist():
            ticker = _normalize_ticker(item)
            if ticker:
                return ticker
        return None

    if isinstance(value, pd.DataFrame):
        tickers = _normalize_ticker_list(value)
        return tickers[0] if tickers else None

    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        return text.zfill(6)
    return text if text else None


def _normalize_ticker_list(raw) -> list[str]:
    """list/Index/DataFrame 등 pykrx 반환 형태를 종목코드 리스트로 정규화."""
    if raw is None:
        return []

    if isinstance(raw, pd.DataFrame):
        candidate_columns = [
            "티커", "ticker", "Ticker", "종목코드", "단축코드", "stock_code", "code",
        ]
        for col in candidate_columns:
            if col in raw.columns:
                return [
                    ticker for ticker in (_normalize_ticker(v) for v in raw[col].tolist())
                    if ticker
                ]

        if raw.index is not None and len(raw.index) > 0:
            index_tickers = [
                ticker for ticker in (_normalize_ticker(v) for v in raw.index.tolist())
                if ticker
            ]
            if index_tickers:
                return index_tickers

        flattened = raw.to_numpy().ravel().tolist()
        return [
            ticker for ticker in (_normalize_ticker(v) for v in flattened)
            if ticker
        ]

    if isinstance(raw, (pd.Series, pd.Index)):
        raw = raw.tolist()

    try:
        iterator = list(raw)
    except TypeError:
        iterator = [raw]

    return [
        ticker for ticker in (_normalize_ticker(v) for v in iterator)
        if ticker
    ]


def _coerce_company_name(value, ticker: str) -> str:
    """종목명 반환값이 DataFrame/Series여도 검색 가능한 문자열로 보정."""
    if value is None:
        return ticker

    if isinstance(value, str):
        return value.strip() or ticker

    if isinstance(value, pd.Series):
        for item in value.tolist():
            name = _coerce_company_name(item, ticker)
            if name != ticker:
                return name
        return ticker

    if isinstance(value, pd.DataFrame):
        candidate_columns = ["종목명", "한글명", "name", "Name", "회사명"]
        for col in candidate_columns:
            if col in value.columns and not value[col].empty:
                return _coerce_company_name(value[col].iloc[0], ticker)
        if not value.empty:
            return _coerce_company_name(value.iloc[0, 0], ticker)
        return ticker

    text = str(value).strip()
    return text if text and text != "nan" else ticker


def _get_ticker_name(ticker: str) -> str:
    if not PYKRX_AVAILABLE:
        return ticker

    try:
        return _coerce_company_name(pykrx_stock.get_market_ticker_name(ticker), ticker)
    except Exception:
        return ticker


# ---------------------------------------------------------------------------
# 종목 레지스트리 (ticker → {ticker, name, market})
# ---------------------------------------------------------------------------

def _get_sector_map(market: str, date_str: str) -> dict[str, str]:
    """
    pykrx에서 시장별 업종 분류 맵을 조회한다.
    반환: {ticker: sector_name}
    실패 시 빈 딕셔너리 반환 (레지스트리 구축 중단 방지).
    """
    try:
        df = pykrx_stock.get_market_sector_classifications(date_str, market=market)
        if df is None or df.empty:
            return {}

        # pykrx 반환 컬럼에서 업종명 컬럼을 동적으로 탐색
        sector_col = next(
            (c for c in df.columns if "업종" in c or "sector" in c.lower()),
            None,
        )
        if not sector_col:
            return {}

        result = {}
        for idx, row in df.iterrows():
            ticker = _normalize_ticker(idx)
            if ticker:
                result[ticker] = str(row[sector_col]).strip()
        return result
    except Exception as e:
        logger.warning("%s 업종 정보 조회 실패: %s", market, e)
        return {}


def build_ticker_registry() -> dict:
    if not PYKRX_AVAILABLE:
        return {}

    registry: dict = {}
    today = _today()

    for market in ("KOSPI", "KOSDAQ", "KONEX"):
        try:
            tickers = _normalize_ticker_list(
                pykrx_stock.get_market_ticker_list(today, market=market)
            )
        except Exception as e:
            logger.warning(f"{market} 종목 리스트 조회 실패: {e}")
            tickers = []

        # 업종 정보 일괄 조회 (실패해도 레지스트리 구축 계속)
        sector_map = _get_sector_map(market, today)

        for ticker in tickers:
            name = _get_ticker_name(ticker)
            registry[ticker] = {
                "ticker": ticker,
                "name": name,
                "market": market,
                "sector": sector_map.get(ticker, ""),
            }

    # get_market_ticker_list 가 빈 결과를 반환하는 환경(KRX API 제한 등)에 대한 fallback
    if not registry:
        logger.warning("전종목 리스트 조회 불가 — KOSPI200 fallback으로 레지스트리 구축")
        from services.kospi200 import KOSPI200_FALLBACK as _KOSPI200_FALLBACK
        for ticker in _KOSPI200_FALLBACK:
            name = _get_ticker_name(ticker)
            registry[ticker] = {"ticker": ticker, "name": name, "market": "KOSPI", "sector": ""}

    logger.info(f"종목 레지스트리 구축 완료: {len(registry)}종목")
    return registry


def get_or_build_registry() -> dict:
    """Redis에서 레지스트리 조회, 없으면 pykrx로 구축 후 캐싱."""
    cached = get_ticker_registry()
    if cached:
        return cached

    logger.info("레지스트리 캐시 없음 — pykrx 풀스캔 시작")
    registry = build_ticker_registry()
    if registry:
        set_ticker_registry(registry)
    return registry


# ---------------------------------------------------------------------------
# 주가 히스토리
# ---------------------------------------------------------------------------

def get_price_history(ticker: str, days: int = 90) -> list[PricePoint]:
    """
    주가 히스토리 조회. Redis 캐시 → pykrx 순으로 시도.
    """
    # 캐시 확인 (days=90 고정 키 사용, 더 짧은 요청엔 슬라이스)
    cached = get_history_cache(ticker)
    if cached is not None:
        points = [PricePoint(**p) for p in cached]
        return points[-days:] if len(points) > days else points

    if not PYKRX_AVAILABLE:
        return []

    try:
        df = pykrx_stock.get_market_ohlcv_by_date(
            fromdate=_n_days_ago(days),
            todate=_today(),
            ticker=ticker,
        )
        if df is None or df.empty:
            return []

        result = []
        for date_idx, row in df.iterrows():
            result.append(PricePoint(
                date=str(date_idx)[:10],
                close=float(row["종가"]),
                volume=int(row["거래량"]),
            ))

        set_history_cache(ticker, [p.model_dump() for p in result], ttl=_cache_ttl_for_history())
        return result

    except Exception as e:
        logger.warning(f"주가 히스토리 조회 실패 ({ticker}): {e}")
        return []


# ---------------------------------------------------------------------------
# 현재가
# ---------------------------------------------------------------------------

def get_current_price(ticker: str) -> Optional[dict]:
    """
    현재가 + 등락률 조회 Redis 캐시(60초) → pykrx
    """
    cached = get_price_cache(ticker)
    if cached is not None:
        return cached

    if not PYKRX_AVAILABLE:
        return None

    try:
        df = pykrx_stock.get_market_ohlcv_by_date(
            fromdate=_n_days_ago(5),
            todate=_today(),
            ticker=ticker,
        )
        if df is None or df.empty:
            return None

        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        price = float(latest["종가"])
        prev_price = float(prev["종가"])
        change_amount = price - prev_price
        change_pct = (change_amount / prev_price * 100) if prev_price else 0

        result = {
            "price": price,
            "change": round(change_pct, 2),
            "change_amount": round(change_amount, 0),
        }
        set_price_cache(ticker, result, ttl=_cache_ttl_for_price())
        return result

    except Exception as e:
        logger.warning(f"현재가 조회 실패 ({ticker}): {e}")
        return None


# ---------------------------------------------------------------------------
# 시총 · 펀더멘털
# ---------------------------------------------------------------------------

def get_market_cap_info(ticker: str) -> Optional[dict]:
    """시총, PER, PBR, EPS 조회"""
    if not PYKRX_AVAILABLE:
        return None

    try:
        df = pykrx_stock.get_market_fundamental_by_date(
            fromdate=_n_days_ago(5),
            todate=_today(),
            ticker=ticker,
        )
        if df is None or df.empty:
            return None

        row = df.iloc[-1]
        cap_df = pykrx_stock.get_market_cap_by_date(
            fromdate=_n_days_ago(5),
            todate=_today(),
            ticker=ticker,
        )
        market_cap = (
            float(cap_df.iloc[-1]["시가총액"])
            if cap_df is not None and not cap_df.empty
            else None
        )

        def _to_optional_float(val) -> Optional[float]:
            try:
                f = float(val)
                return f if f != 0.0 else None
            except (TypeError, ValueError):
                return None

        return {
            "market_cap": market_cap,
            "per": _to_optional_float(row.get("PER")),
            "pbr": _to_optional_float(row.get("PBR")),
            "eps": _to_optional_float(row.get("EPS")),
        }
    except Exception as e:
        logger.warning(f"시총 정보 조회 실패 ({ticker}): {e}")
        return None


# ---------------------------------------------------------------------------
# 종목 검색 (레지스트리 기반 O(1))
# ---------------------------------------------------------------------------

def search_companies(query: str) -> list[CompanyBrief]:
    """
    종목명 또는 종목코드로 기업 검색.

    Redis 레지스트리를 활용하여 O(N_matches) 로 검색한 뒤
    매칭 종목에 대해서만 현재가를 조회한다.
    (구 방식: 전종목 루프 × API 호출 → 수 분 소요)
    """
    query = query.strip()
    registry = get_or_build_registry()

    results: list[CompanyBrief] = []
    query_lower = query.lower()

    for ticker, meta in registry.items():
        ticker = _normalize_ticker(ticker) or str(ticker)
        name = _coerce_company_name(meta.get("name", ""), ticker)
        if query_lower in name.lower() or query == ticker:
            price_info = get_current_price(ticker)
            results.append(CompanyBrief(
                ticker=ticker,
                name=name,
                market=meta.get("market", ""),
                sector=meta.get("sector", ""),
                price=price_info.get("price") if price_info else None,
                change=price_info.get("change") if price_info else None,
            ))
            if len(results) >= 20:
                break

    # 정확 일치(종목코드 or 이름)를 최상위로 정렬
    results.sort(key=lambda r: (
        0 if r.ticker == query or r.name == query else 1
    ))
    return results


# ---------------------------------------------------------------------------
# 단일 종목 메타 조회 (ticker → CompanyBrief)
# ---------------------------------------------------------------------------

def get_company_brief(ticker: str) -> Optional[CompanyBrief]:
    """레지스트리에서 단일 종목 정보를 반환한다."""
    registry = get_or_build_registry()
    meta = registry.get(ticker.upper())
    if not meta:
        return None

    price_info = get_current_price(ticker)
    name = _coerce_company_name(meta.get("name", ticker), ticker)
    return CompanyBrief(
        ticker=ticker,
        name=name,
        market=meta.get("market", ""),
        sector="",
        price=price_info.get("price") if price_info else None,
        change=price_info.get("change") if price_info else None,
    )
