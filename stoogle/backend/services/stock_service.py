# 주가 서비스
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


# 오늘 날짜
def _today() -> str:
    return datetime.today().strftime("%Y%m%d")


# N일 전 날짜
def _n_days_ago(n: int) -> str:
    return (datetime.today() - timedelta(days=n)).strftime("%Y%m%d")


# 장중 여부
def _is_trading_hours() -> bool:
    now = datetime.now()
    return (
        now.weekday() < 5
        and (9, 0) <= (now.hour, now.minute) <= (15, 30)
    )


# 가격 캐시 TTL
def _cache_ttl_for_price() -> int:
    return 60 if _is_trading_hours() else 60 * 60 * 48


# 히스토리 캐시 TTL
def _cache_ttl_for_history() -> int:
    return 600 if _is_trading_hours() else 60 * 60 * 24


# 종목코드 정규화
def _normalize_ticker(value) -> Optional[str]:
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


# 종목코드 리스트 정규화
def _normalize_ticker_list(raw) -> list[str]:
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


# 종목명 보정
def _coerce_company_name(value, ticker: str) -> str:
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


# 종목명 조회
def _get_ticker_name(ticker: str) -> str:
    if not PYKRX_AVAILABLE:
        return ticker

    try:
        return _coerce_company_name(pykrx_stock.get_market_ticker_name(ticker), ticker)
    except Exception:
        return ticker


# 업종 맵 조회
def _get_sector_map(market: str, date_str: str) -> dict[str, str]:
    try:
        df = pykrx_stock.get_market_sector_classifications(date_str, market=market)
        if df is None or df.empty:
            return {}

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


# 레지스트리 구축
def build_ticker_registry() -> dict:
    if not PYKRX_AVAILABLE:
        return {}

    registry: dict = {}
    today = _today()

    for market in ("KOSPI",):
        try:
            tickers = _normalize_ticker_list(
                pykrx_stock.get_market_ticker_list(today, market=market)
            )
        except Exception as e:
            logger.warning(f"{market} 종목 리스트 조회 실패: {e}")
            tickers = []

        sector_map = _get_sector_map(market, today)

        for ticker in tickers:
            name = _get_ticker_name(ticker)
            registry[ticker] = {
                "ticker": ticker,
                "name": name,
                "market": market,
                "sector": sector_map.get(ticker, ""),
            }

    if not registry:
        import json, os
        seed_path = os.getenv("TICKER_MAP_PATH", "ticker_map.json")
        try:
            with open(seed_path, encoding="utf-8") as f:
                seed = json.load(f)
            for k, v in seed.items():
                if isinstance(v, dict):
                    tk = v.get("ticker") or k
                    registry[tk] = {"ticker": tk, "name": v.get("name", tk),
                                    "market": v.get("market", "KOSPI"),
                                    "sector": v.get("sector", "")}
                else:
                    tk = str(v).zfill(6)
                    registry[tk] = {"ticker": tk, "name": k,
                                    "market": "KOSPI", "sector": ""}
            logger.warning("ticker_map.json seed로 레지스트리 구축: %d종목", len(registry))
        except Exception as e:
            logger.warning("ticker_map.json 로드 실패 — KOSPI200 fallback: %s", e)
            from services.kospi200 import KOSPI200_FALLBACK as _f
            for tk in _f:
                registry[tk] = {"ticker": tk, "name": _get_ticker_name(tk),
                                "market": "KOSPI", "sector": ""}

    return registry


# 레지스트리 조회 또는 구축
def get_or_build_registry() -> dict:
    cached = get_ticker_registry()
    if cached:
        return cached

    logger.info("레지스트리 캐시 없음 — pykrx 풀스캔 시작")
    registry = build_ticker_registry()
    if registry:
        set_ticker_registry(registry)
    return registry


# 주가 히스토리 조회
def get_price_history(ticker: str, days: int = 90) -> list[PricePoint]:
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


# 기간별 히스토리 조회
def get_price_history_range(
    ticker: str,
    fromdate: str,
    todate: Optional[str] = None,
) -> list[PricePoint]:
    if not PYKRX_AVAILABLE:
        return []

    end = (todate or _today()).replace("-", "")
    start = fromdate.replace("-", "")

    try:
        df = pykrx_stock.get_market_ohlcv_by_date(
            fromdate=start,
            todate=end,
            ticker=ticker,
        )
        if df is None or df.empty:
            return []

        return [
            PricePoint(
                date=str(date_idx)[:10],
                close=float(row["종가"]),
                volume=int(row["거래량"]),
            )
            for date_idx, row in df.iterrows()
        ]

    except Exception as e:
        logger.warning(f"주가 히스토리(기간) 조회 실패 ({ticker}, {start}~{end}): {e}")
        return []


# 특정일 종가 조회
def get_close_price_on(ticker: str, as_of) -> Optional[float]:
    if not PYKRX_AVAILABLE:
        return None
    try:
        from datetime import timedelta as _td
        if isinstance(as_of, str):
            from datetime import datetime as _dt
            as_of = _dt.strptime(as_of[:10], "%Y-%m-%d")
        end_str = as_of.strftime("%Y%m%d")
        start_str = (as_of - _td(days=7)).strftime("%Y%m%d")
        df = pykrx_stock.get_market_ohlcv_by_date(
            fromdate=start_str, todate=end_str, ticker=ticker
        )
        if df is None or df.empty:
            return None
        return float(df["종가"].iloc[-1])
    except Exception as e:
        logger.warning("get_close_price_on 실패 (%s, %s): %s", ticker, as_of, e)
        return None


# 현재가 조회
def get_current_price(ticker: str) -> Optional[dict]:
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


# 시총 및 펀더멘털 조회
def get_market_cap_info(ticker: str) -> Optional[dict]:
    try:
        import httpx

        url = f"https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{ticker}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = httpx.get(url, headers=headers, timeout=8.0)
        r.raise_for_status()

        import json as _json
        raw = r.content.decode("euc-kr").strip()
        if not raw:
            logger.warning("빈 응답 수신 — 시세 정보 없음 (ticker=%s)", ticker)
            return None
        areas = _json.loads(raw).get("result", {}).get("areas", [])
        datas = areas[0].get("datas", []) if areas else []
        if not datas:
            return None

        item = datas[0]

        def _f(key) -> Optional[float]:
            v = item.get(key)
            try:
                f = float(v)
                return f if f != 0.0 else None
            except (TypeError, ValueError):
                return None

        price = _f("nv")
        eps = _f("eps")
        bps = _f("bps")
        shares = _f("countOfListedStock")

        def _div(a, b) -> Optional[float]:
            if a is None or b is None or b == 0:
                return None
            return round(a / b, 2)

        return {
            "market_cap": (price * shares) if price and shares else None,
            "per": _div(price, eps),
            "pbr": _div(price, bps),
            "eps": eps,
        }
    except Exception as e:
        logger.warning(f"시총 정보 조회 실패 ({ticker}): {e}")
        return None


# 종목 검색
def search_companies(query: str) -> list[CompanyBrief]:
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

    results.sort(key=lambda r: (
        0 if r.ticker == query or r.name == query else 1
    ))
    return results


# 단일 종목 조회
def get_company_brief(ticker: str) -> Optional[CompanyBrief]:
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
