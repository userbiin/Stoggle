import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter
from models.schemas import InsightResponse
from services.stock_service import get_price_history, get_market_cap_info, get_or_build_registry
from services.news_service import fetch_news
from services.nlp_service import extract_keywords, summarize_with_llm

router = APIRouter(tags=["insight"])
logger = logging.getLogger(__name__)

# asyncio.create_task 결과 레퍼런스 유지 (GC 방지)
_bg_tasks: set = set()

# Stale-While-Revalidate 임계값
_INSIGHT_FRESH_SECS = 1800    # 30분 이내: FRESH — 즉시 반환
_INSIGHT_STALE_SECS = 7200    # 2시간 이내: STALE — 즉시 반환 + 백그라운드 갱신


def _load_analysis_with_age(ticker: str) -> tuple[dict | None, float]:
    """
    InsightCache를 조회하고 (data, age_seconds) 를 반환한다.

    우선순위:
      1. Redis insight 캐시 (_cached_at 포함)
      2. DB InsightCache (updated_at 기준 나이 계산 + Redis에도 저장)

    나이를 알 수 없으면 float('inf') 반환 → 항상 갱신 대상으로 처리.
    """
    # 1. Redis 우선 조회
    try:
        from services.cache_service import get_insight_cache
        cached = get_insight_cache(ticker)
        if cached:
            age = time.time() - cached.get("_cached_at", 0)
            data = {k: v for k, v in cached.items() if k != "_cached_at"}
            return data, age
    except Exception:
        pass

    # 2. DB InsightCache
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
                # Redis에 올려 다음 요청부터 빠르게
                set_insight_cache(ticker, data)
                return data, age
        finally:
            db.close()
    except Exception as e:
        logger.debug("InsightCache 조회 실패 [%s]: %s", ticker, e)

    return None, float("inf")


def _trigger_celery_refresh(ticker: str) -> None:
    """Celery analyze_single_ticker 태스크로 전체 파이프라인 갱신을 예약한다."""
    try:
        from tasks import analyze_single_ticker
        analyze_single_ticker.delay(ticker)
        logger.info("[%s] Celery 인사이트 갱신 트리거", ticker)
    except Exception as e:
        logger.warning("[%s] Celery 트리거 실패 (Redis/Celery 미연결?): %s", ticker, e)


async def _run_analysis_background(ticker: str, articles: list) -> None:
    """
    analysis_agent.run_and_save()를 asyncio 백그라운드에서 실행.
    완료 후 Redis insight 캐시를 갱신한다.
    """
    try:
        from agents.analysis_agent import run_and_save
        await run_and_save(articles, ticker)
        # 새 분석 결과를 Redis에 반영
        updated_data, _ = _load_analysis_with_age(ticker)
        logger.info("백그라운드 분석 완료 [%s]", ticker)
    except Exception as e:
        logger.warning("백그라운드 분석 실패 [%s]: %s", ticker, e)


@router.get("/insight/{ticker}", response_model=InsightResponse)
async def get_insight(ticker: str):
    ticker = ticker.upper()

    registry = get_or_build_registry()
    meta = registry.get(ticker, {})
    company_name = meta.get("name", ticker)
    market = meta.get("market", "KOSPI")
    sector = meta.get("sector", "")

    # 독립적인 I/O를 asyncio.gather로 병렬 실행 — cold path ~50% 단축
    price_history, cap_info, news_items = await asyncio.gather(
        asyncio.to_thread(get_price_history, ticker, 90),
        asyncio.to_thread(get_market_cap_info, ticker),
        fetch_news(ticker, page=1),
    )

    titles = [n.title for n in news_items]

    latest_price = None
    change = None
    change_amount = None
    if price_history:
        latest_price = price_history[-1].close
        if len(price_history) >= 2:
            prev_close = price_history[-2].close
            change_amount = round(latest_price - prev_close, 0)
            change = round((change_amount / prev_close * 100), 2) if prev_close else 0

    # ── Stale-While-Revalidate ─────────────────────────────────────────────
    # FRESH  (< 30분): 즉시 반환
    # STALE  (< 2시간): 즉시 반환 + Celery 백그라운드 갱신
    # EXPIRED/MISS   : asyncio 백그라운드 갱신 + 현재 응답은 폴백 데이터
    cached_analysis, analysis_age = _load_analysis_with_age(ticker)

    if cached_analysis:
        if analysis_age >= _INSIGHT_FRESH_SECS:
            # STALE: 사용자는 기다리지 않고, Celery가 다음 요청을 위해 갱신
            _trigger_celery_refresh(ticker)
            logger.info("[%s] 인사이트 STALE (%.0f초) — 반환 후 갱신", ticker, analysis_age)
    elif news_items:
        # MISS: asyncio 백그라운드로 분석 시작 (Celery가 없어도 작동)
        from agents.dedup_indexer import Article
        articles = [
            Article(url=n.url, title=n.title, summary=n.summary or "")
            for n in news_items
        ]
        task = asyncio.create_task(_run_analysis_background(ticker, articles))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

    # 요약: 캐시 우선, 완전 cold-start(캐시 없음)일 때만 LLM 직접 호출
    if cached_analysis and cached_analysis.get("summary"):
        summary = cached_analysis["summary"]
    elif not cached_analysis and titles:
        summary = await summarize_with_llm(ticker, company_name, titles)
    else:
        summary = None

    # 키워드: LLM 이벤트 우선, 없으면 뉴스 제목 빈도 폴백
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
