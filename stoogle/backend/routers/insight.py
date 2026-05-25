import asyncio
import json
import logging
import os

from fastapi import APIRouter
from models.schemas import InsightResponse
from services.stock_service import get_price_history, get_market_cap_info, get_or_build_registry
from services.news_service import fetch_news
from services.nlp_service import extract_keywords, summarize_with_llm

router = APIRouter(tags=["insight"])
logger = logging.getLogger(__name__)

_bg_tasks: set = set()
_pending_tickers: set[str] = set()  # asyncio fallback 중복 방지

_ANALYSIS_LOCK_TTL = 600  # Redis lock TTL (초)


def _load_cached_analysis(ticker: str) -> dict | None:
    """
    InsightCache 테이블에서 analysis_agent 결과를 조회한다.
    keywords_json 컬럼에 events / relations / impacts / evidence / sentiment 포함.
    """
    try:
        from models.db_models import InsightCache, SessionLocal

        db = SessionLocal()
        try:
            row = db.query(InsightCache).filter(InsightCache.ticker == ticker).first()
            if row and row.keywords_json:
                return json.loads(row.keywords_json)
        finally:
            db.close()
    except Exception as e:
        logger.debug("InsightCache 조회 실패 [%s]: %s", ticker, e)
    return None


async def _run_analysis_background(ticker: str, articles: list) -> None:
    """analysis_agent.run_and_save() asyncio fallback — Redis 미사용 시."""
    try:
        from agents.analysis_agent import run_and_save
        await run_and_save(articles, ticker)
        logger.info("백그라운드 분석 완료 [%s]", ticker)
    except Exception as e:
        logger.warning("백그라운드 분석 실패 [%s]: %s", ticker, e)
    finally:
        _pending_tickers.discard(ticker)


def _trigger_analysis(ticker: str, articles: list) -> None:
    """
    Celery 가용 시 analyze_single_ticker.delay() 트리거,
    Redis 미응답 시 asyncio 백그라운드로 fallback.
    두 경로 모두 중복 실행 방지 처리를 포함한다.
    """
    # Celery (Redis) 경로
    try:
        import redis as _redis
        r = _redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            socket_timeout=0.5,
        )
        lock_key = f"analysis:running:{ticker}"
        if r.set(lock_key, "1", nx=True, ex=_ANALYSIS_LOCK_TTL):
            from tasks import analyze_single_ticker
            analyze_single_ticker.delay(ticker)
            logger.info("Celery 분석 트리거 [%s]", ticker)
        else:
            logger.debug("분석 이미 실행 중, 건너뜀 [%s]", ticker)
        return
    except Exception:
        pass

    # asyncio fallback (in-process 중복 방지)
    if ticker in _pending_tickers:
        logger.debug("asyncio 분석 이미 진행 중, 건너뜀 [%s]", ticker)
        return
    _pending_tickers.add(ticker)
    task = asyncio.create_task(_run_analysis_background(ticker, articles))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@router.get("/insight/{ticker}", response_model=InsightResponse)
async def get_insight(ticker: str):
    ticker = ticker.upper()

    # 종목명·시장·섹터 정보를 레지스트리에서 조회
    registry = get_or_build_registry()
    meta = registry.get(ticker, {})
    company_name = meta.get("name", ticker)
    market = meta.get("market", "KOSPI")
    sector = meta.get("sector", "")

    price_history = get_price_history(ticker, days=90)
    cap_info = get_market_cap_info(ticker)
    news_items = await fetch_news(ticker, page=1)

    titles = [n.title for n in news_items]
    urls = [n.url for n in news_items]
    keywords = extract_keywords(titles) if titles else []
    summary = await summarize_with_llm(ticker, company_name, titles, news_urls=urls) if titles else None

    latest_price = None
    change = None
    change_amount = None
    if price_history:
        latest_price = price_history[-1].close
        if len(price_history) >= 2:
            prev_close = price_history[-2].close
            change_amount = round(latest_price - prev_close, 0)
            change = round((change_amount / prev_close * 100), 2) if prev_close else 0

    # ── analysis_agent 결과 (캐시 우선, 없으면 백그라운드 트리거) ──────────
    cached_analysis = _load_cached_analysis(ticker)

    if not cached_analysis and news_items:
        from agents.dedup_indexer import Article
        articles = [
            Article(url=n.url, title=n.title, summary=n.summary or "")
            for n in news_items
        ]
        _trigger_analysis(ticker, articles)

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
