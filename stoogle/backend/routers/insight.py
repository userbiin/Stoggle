# 인사이트 라우터
import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter
from models.schemas import InsightResponse, PricePoint
from services.cache_service import get_history_cache, cache_get, cache_set
from services.stock_service import get_price_history, get_market_cap_info, get_or_build_registry
from services.news_service import fetch_news
from services.nlp_service import extract_keywords, summarize_with_llm

router = APIRouter(tags=["insight"])
logger = logging.getLogger(__name__)

_bg_tasks: set = set()

_INSIGHT_FRESH_SECS = 1800
_INSIGHT_STALE_SECS = 7200


# 캐시 나이 조회
def _load_analysis_with_age(ticker: str) -> tuple[dict | None, float]:
    try:
        from services.cache_service import get_insight_cache
        cached = get_insight_cache(ticker)
        if cached:
            age = time.time() - cached.get("_cached_at", 0)
            data = {k: v for k, v in cached.items() if k != "_cached_at"}
            return data, age
    except Exception:
        pass

    try:
        from models.db_models import InsightCache, SessionLocal
        from services.cache_service import set_insight_cache

        db = SessionLocal()
        try:
            row = db.query(InsightCache).filter(InsightCache.ticker == ticker).first()
            if row and row.keywords_json:
                age = (
                    (datetime.utcnow() - row.updated_at).total_seconds()
                    if row.updated_at else float("inf")
                )
                data = json.loads(row.keywords_json)
                if row.summary:
                    data["summary"] = row.summary
                set_insight_cache(ticker, data)
                return data, age
        finally:
            db.close()
    except Exception as e:
        logger.debug("InsightCache 조회 실패 [%s]: %s", ticker, e)

    return None, float("inf")


# Celery 갱신 트리거
def _trigger_celery_refresh(ticker: str) -> None:
    try:
        from tasks import analyze_single_ticker
        analyze_single_ticker.delay(ticker)
        logger.info("[%s] Celery 인사이트 갱신 트리거", ticker)
    except Exception as e:
        logger.warning("[%s] Celery 트리거 실패 (Redis/Celery 미연결?): %s", ticker, e)


# 백그라운드 분석 실행
async def _run_analysis_background(ticker: str, articles: list) -> None:
    try:
        from agents.analysis_agent import run_and_save
        await run_and_save(articles, ticker)
        updated_data, _ = _load_analysis_with_age(ticker)
        logger.info("백그라운드 분석 완료 [%s]", ticker)
    except Exception as e:
        logger.warning("백그라운드 분석 실패 [%s]: %s", ticker, e)


# 인사이트 조회
@router.get("/insight/{ticker}", response_model=InsightResponse)
async def get_insight(ticker: str):
    ticker = ticker.upper()

    registry = get_or_build_registry()
    meta = registry.get(ticker, {})
    company_name = meta.get("name", ticker)
    market = meta.get("market", "KOSPI")
    sector = meta.get("sector", "")

    async def _price_history_task() -> list:
        cached = get_history_cache(ticker)
        if cached is not None:
            return [PricePoint(**p) for p in cached][-90:]
        return await asyncio.to_thread(get_price_history, ticker, 90)

    async def _cap_info_task() -> dict | None:
        cached = cache_get(f"Stoogle:cap:{ticker}")
        if cached is not None:
            return cached
        result = await asyncio.to_thread(get_market_cap_info, ticker)
        if result:
            cache_set(f"Stoogle:cap:{ticker}", result, ttl=900)
        return result

    price_history, cap_info, news_items = await asyncio.gather(
        _price_history_task(),
        _cap_info_task(),
        fetch_news(ticker, page=1),
    )

    titles = [n.title for n in news_items]

    _sum_key = f"Stoogle:summary:{ticker}"
    summary = cache_get(_sum_key)
    if summary is None and titles:
        summary = await summarize_with_llm(ticker, company_name, titles)
        if summary:
            cache_set(_sum_key, summary, ttl=1800)

    latest_price = None
    change = None
    change_amount = None
    if price_history:
        latest_price = price_history[-1].close
        if len(price_history) >= 2:
            prev_close = price_history[-2].close
            change_amount = round(latest_price - prev_close, 0)
            change = round((change_amount / prev_close * 100), 2) if prev_close else 0

    cached_analysis, analysis_age = _load_analysis_with_age(ticker)

    if cached_analysis:
        if analysis_age >= _INSIGHT_FRESH_SECS:
            _trigger_celery_refresh(ticker)
            logger.info("[%s] 인사이트 STALE (%.0f초) — 반환 후 갱신", ticker, analysis_age)
    elif news_items:
        from agents.dedup_indexer import Article
        articles = [
            Article(url=n.url, title=n.title, summary=n.summary or "")
            for n in news_items
        ]
        task = asyncio.create_task(_run_analysis_background(ticker, articles))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

    if cached_analysis and cached_analysis.get("summary"):
        summary = cached_analysis["summary"]

    if cached_analysis and cached_analysis.get("events"):
        keywords = extract_keywords(cached_analysis["events"])
    else:
        keywords = extract_keywords(titles) if titles else []

    return InsightResponse(
        ticker=ticker,
        name=company_name,
        market=market,
        sector=sector,
        price=latest_price,
        change=change,
        change_amount=change_amount,
        market_cap=cap_info.get("market_cap") if cap_info else None,
        per=cap_info.get("per") if cap_info else None,
        pbr=cap_info.get("pbr") if cap_info else None,
        eps=cap_info.get("eps") if cap_info else None,
        summary=summary,
        keywords=keywords,
        price_history=price_history,
        events=cached_analysis.get("events", []) if cached_analysis else [],
        sentiment=cached_analysis.get("sentiment") if cached_analysis else None,
        analysis_impacts=cached_analysis.get("impacts", []) if cached_analysis else [],
        evidence=cached_analysis.get("evidence", []) if cached_analysis else [],
    )
