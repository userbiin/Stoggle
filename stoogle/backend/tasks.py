"""
Celery 자동화 스케줄러

실행:
  celery -A tasks worker --loglevel=info
  celery -A tasks beat --loglevel=info
"""
import os
import time
import logging
import asyncio
from datetime import datetime, timedelta
from celery import Celery
from celery.schedules import crontab
from celery.signals import task_prerun, task_postrun, task_failure
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# task_id → 시작 시각 (signal 간 공유)
_task_start_times: dict[str, float] = {}


@task_prerun.connect
def _on_task_start(task_id: str, task, **kw):
    _task_start_times[task_id] = time.time()


@task_postrun.connect
def _on_task_done(task_id: str, task, retval, state: str, **kw):
    duration = time.time() - _task_start_times.pop(task_id, time.time())
    logger.info({
        "event": "celery_task",
        "task": task.name,
        "state": state,
        "duration_s": round(duration, 2),
    })


@task_failure.connect
def _on_task_failure(task_id: str, exception: Exception, **kw):
    logger.error({
        "event": "celery_task_failure",
        "task_id": task_id,
        "error_type": type(exception).__name__,
        "error": str(exception),
    })

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery("Stoogle", broker=REDIS_URL, backend=REDIS_URL)

app.conf.timezone = "Asia/Seoul"
app.conf.beat_schedule = {
    # ── 주가 관련 ──────────────────────────────────────────────────────────
    # 코스피 200 현재가 Redis 캐싱 (1분)
    "fetch-top200-prices": {
        "task": "tasks.fetch_top200_prices",
        "schedule": 60.0,
    },
    # 당일 주가 히스토리 업데이트 (장 마감 후 오후 4시)
    "update-prices-daily": {
        "task": "tasks.update_price_history",
        "schedule": crontab(hour=16, minute=0),
    },
    # ── 뉴스 관련 ──────────────────────────────────────────────────────────
    # 전종목 뉴스 수집 (1시간)
    "crawl-all-news": {
        "task": "tasks.crawl_all_news",
        "schedule": crontab(minute=0),
    },
    # 주요 종목 뉴스 사전 수집 (매일 오전 8시 30분)
    "prefetch-news-daily": {
        "task": "tasks.prefetch_news_for_major_stocks",
        "schedule": crontab(hour=8, minute=30),
    },
    # ── 공시 관련 ──────────────────────────────────────────────────────────
    # DART 공시 수집 (매일 오전 8시)
    "fetch-dart-filings": {
        "task": "tasks.fetch_dart_filings",
        "schedule": crontab(hour=8, minute=0),
    },
    # ── 분석 관련 ──────────────────────────────────────────────────────────
    # 전종목 상관계수 재계산 (매일 자정)
    "recompute-correlations": {
        "task": "tasks.recompute_correlations",
        "schedule": crontab(hour=0, minute=0),
    },
    # 종목 관계도 갱신 (매주 월요일 오전 9시)
    "update-relations-weekly": {
        "task": "tasks.update_relation_graphs",
        "schedule": crontab(hour=9, minute=0, day_of_week="monday"),
    },
    # ── 레지스트리 ─────────────────────────────────────────────────────────
    # KRX 전종목 레지스트리 갱신 (매주 월요일 오전 7시 — 장 시작 전)
    "refresh-ticker-registry": {
        "task": "tasks.refresh_ticker_registry",
        "schedule": crontab(hour=7, minute=0, day_of_week="monday"),
    },
    # ── 보정 ───────────────────────────────────────────────────────────────
    # 예측 정확도 평가 + confidence 보정 (매일 오전 2시)
    "calibrate-predictions-daily": {
        "task": "tasks.calibrate_predictions",
        "schedule": crontab(hour=2, minute=0),
    },
    # DART 공시·재무제표 색인 (매일 오후 6시)
    "index-dart-daily": {
        "task": "tasks.index_dart_disclosures",
        "schedule": crontab(hour=18, minute=0),
    },
    # 장 마감 후 KOSPI200 현재가 장기 캐싱 (평일 15:35 — 주말까지 유지)
    "cache-eod-prices": {
        "task": "tasks.cache_eod_prices",
        "schedule": crontab(hour=15, minute=35, day_of_week="mon-fri"),
    },
    # 인기 종목 인사이트 캐시 선제 갱신 (매일 오전 8:30 — 장 시작 전)
    "warmup-popular-tickers": {
        "task": "tasks.warmup_popular_tickers",
        "schedule": crontab(hour=8, minute=30),
    },
}

# KOSPI 200 구성 종목 — services/kospi200.py에서 관리 (Celery와 분리)
from services.kospi200 import KOSPI200_TICKERS, KOSPI200_FALLBACK as _KOSPI200_FALLBACK


# ─────────────────────────────────────────────────────────────────────────────
# 뉴스 사전 수집
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=2, default_retry_delay=120)
def prefetch_news_for_major_stocks(self):
    """
    주요 종목(상위 30개) 뉴스를 장 시작 전 미리 수집해 Redis에 캐싱한다.

    크롤링 → 중복 제거 → LLM 랭킹 → Redis 저장 순으로 처리하여
    crawl_all_news와 동일한 캐시 품질을 보장한다.
    """
    from services.news_service import fetch_news, rank_news
    from services.cache_service import set_news_cache

    results = {}
    for ticker in KOSPI200_TICKERS[:30]:
        try:
            items = asyncio.run(fetch_news(ticker, page=1, force=True))
            if not items:
                results[ticker] = {"status": "ok", "count": 0}
                continue
            ranked = asyncio.run(rank_news(items))
            set_news_cache(ticker, [i.model_dump() for i in ranked])
            results[ticker] = {"status": "ok", "count": len(ranked)}
        except Exception as e:
            logger.warning("뉴스 사전 수집 실패 (%s): %s", ticker, e)
            results[ticker] = {"status": "error", "reason": str(e)}

    return {"status": "ok", "prefetched": results}


# ─────────────────────────────────────────────────────────────────────────────
# 종목 레지스트리 갱신
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=2, default_retry_delay=300)
def refresh_ticker_registry(self):
    """
    KRX 전종목 레지스트리를 pykrx로 재구축하여 Redis에 캐싱한다.
    매주 월요일 장 시작 전(오전 7시) 실행.
    """
    try:
        from services.stock_service import build_ticker_registry
        from services.cache_service import set_ticker_registry

        registry = build_ticker_registry()
        ok = set_ticker_registry(registry)
        return {"status": "ok", "count": len(registry), "cached": ok}
    except Exception as e:
        logger.error(f"레지스트리 갱신 실패: {e}")
        raise self.retry(exc=e)


# ─────────────────────────────────────────────────────────────────────────────
# 주가 수집
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=3, default_retry_delay=30)
def fetch_top200_prices(self):
    """
    코스피 전종목 당일 OHLCV를 한 번에 가져와 코스피 200 현재가를 Redis에 캐싱한다.

    API 호출 횟수:
      - get_market_ohlcv_by_ticker(date, market="KOSPI") → 1 call/분
      - 장 중(09:00~15:30, 월~금) = 390분 → 하루 390 calls
      (기존 종목별 개별 호출 방식 대비 30배 절감)
    """
    from datetime import datetime as dt

    now = dt.now()
    is_trading_hours = (
        now.weekday() < 5  # 월~금
        and (9, 0) <= (now.hour, now.minute) <= (15, 30)
    )

    if not is_trading_hours:
        return {"status": "skip", "reason": "장 운영 시간 외"}

    try:
        from pykrx import stock as pykrx_stock
        from services.cache_service import set_price_cache, TTL_PRICE

        today = now.strftime("%Y%m%d")
        # KOSPI 전종목을 날짜 기준으로 한 번에 조회 — 1 API call
        df = pykrx_stock.get_market_ohlcv_by_ticker(today, market="KOSPI")
        if df is None or df.empty:
            return {"status": "skip", "reason": "데이터 없음"}

        # 전일 종가 (등락률 계산용)
        prev_day = (now - timedelta(days=1)).strftime("%Y%m%d")
        df_prev = pykrx_stock.get_market_ohlcv_by_ticker(prev_day, market="KOSPI")

        updated = 0
        for ticker in KOSPI200_TICKERS:
            try:
                if ticker not in df.index:
                    continue
                row = df.loc[ticker]
                price = float(row["종가"])

                prev_price = price
                if df_prev is not None and not df_prev.empty and ticker in df_prev.index:
                    prev_price = float(df_prev.loc[ticker]["종가"])

                change_amount = price - prev_price
                change_pct = (change_amount / prev_price * 100) if prev_price else 0

                set_price_cache(ticker, {
                    "price": price,
                    "change": round(change_pct, 2),
                    "change_amount": round(change_amount, 0),
                }, ttl=TTL_PRICE)
                updated += 1
            except Exception as e:
                logger.warning(f"가격 캐싱 실패 ({ticker}): {e}")

        return {"status": "ok", "updated": updated, "total": len(KOSPI200_TICKERS), "api_calls": 2}
    except ImportError:
        return {"status": "skip", "reason": "pykrx 미설치"}
    except Exception as e:
        logger.error(f"fetch_top200_prices 실패: {e}")
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=3, default_retry_delay=120)
def update_price_history(self):
    """당일 주가 히스토리를 Redis에 캐싱한다 (장 마감 후)."""
    from services.stock_service import get_price_history

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            history = get_price_history(ticker, days=90)
            results[ticker] = len(history)
        except Exception as e:
            # 개별 종목 실패는 경고만 — 전체 태스크 재시도 금지
            logger.warning(f"히스토리 갱신 실패 ({ticker}): {e}")

    return {"status": "ok", "updated": results}


# ─────────────────────────────────────────────────────────────────────────────
# 뉴스 수집
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=2, default_retry_delay=120)
def crawl_all_news(self):
    """
    KOSPI200 종목 뉴스 강제 크롤링 (1시간 주기).

    크롤링 → 3단계 중복 제거 → LLM 랭킹 → Redis 캐시 저장 순으로 처리.
    캐시에는 항상 랭킹이 완료된 최종 목록이 저장된다.
    완료 후 dedup_and_index_news 태스크를 체이닝하여 pgvector 색인을 수행한다.
    """
    from services.news_service import fetch_news, rank_news
    from services.cache_service import set_news_cache, set_insight_cache
    from services.nlp_service import summarize_with_llm
    from services.stock_service import get_or_build_registry
    from models.db_models import InsightCache, SessionLocal

    registry = get_or_build_registry()
    results = {}
    news_for_dedup: dict[str, list[tuple[str, str, str]]] = {}

    for ticker in KOSPI200_TICKERS:
        try:
            # fetch_news(force=True): 크롤링 + _dedup_items 적용, 캐시 미저장
            items = asyncio.run(fetch_news(ticker, force=True))
            if not items:
                results[ticker] = 0
                continue

            # LLM 랭킹 (실패 시 키워드 폴백)
            ranked = asyncio.run(rank_news(items))

            # 랭킹 완료된 목록을 Redis에 저장
            set_news_cache(ticker, [i.model_dump() for i in ranked])
            results[ticker] = len(ranked)

            news_for_dedup[ticker] = [
                (i.url, i.title, i.summary or "") for i in ranked
            ]

            # 뉴스 갱신 후 LLM 요약 재생성 → InsightCache.summary 저장
            try:
                company_name = registry.get(ticker, {}).get("name", ticker)
                titles = [i.title for i in ranked]
                new_summary = asyncio.run(summarize_with_llm(ticker, company_name, titles))
                if new_summary:
                    db = SessionLocal()
                    try:
                        row = db.query(InsightCache).filter(InsightCache.ticker == ticker).first()
                        if row:
                            row.summary = new_summary
                        else:
                            db.add(InsightCache(ticker=ticker, summary=new_summary))
                        db.commit()
                        # Redis 캐시에도 반영
                        from services.cache_service import get_insight_cache
                        cached = get_insight_cache(ticker) or {}
                        cached["summary"] = new_summary
                        set_insight_cache(ticker, cached)
                    finally:
                        db.close()
            except Exception as e:
                logger.warning(f"요약 갱신 실패 ({ticker}): {e}")

        except Exception as e:
            logger.warning(f"뉴스 크롤링 실패 ({ticker}): {e}")

    # pgvector 색인 태스크 체이닝
    if news_for_dedup:
        dedup_and_index_news.delay(news_for_dedup)

    # 신규 뉴스가 쌓인 종목에 대해 관계 증분 갱신 트리거
    # (새 공급사·고객사 뉴스가 들어오면 다음 크롤 주기에 관계에 반영됨)
    updated_tickers = [t for t, cnt in results.items() if cnt > 0]
    if updated_tickers:
        _trigger_incremental_relation_update.delay(updated_tickers)

    return {"status": "ok", "crawled": results}


@app.task
def _trigger_incremental_relation_update(tickers: list[str]):
    """
    뉴스 크롤 후 신규 기사가 있는 종목의 관계를 증분 갱신한다.
    소급 배치(`retroactive_relation_seed`)와 달리 최신 뉴스 15건만 빠르게 처리.
    """
    from agents.relation_discovery_agent import discover_relations
    from services.cache_service import invalidate_edges_cache

    for ticker in tickers:
        try:
            count = asyncio.run(discover_relations(ticker))
            if count > 0:
                invalidate_edges_cache(ticker)
        except Exception as e:
            logger.debug("[%s] 증분 관계 갱신 실패: %s", ticker, e)


@app.task(bind=True, max_retries=1, default_retry_delay=60)
def dedup_and_index_news(self, news_by_ticker: dict):
    """
    뉴스 중복 제거 + pgvector 색인 (crawl_all_news 후속 태스크).

    news_by_ticker: {ticker: [(url, title, summary), ...]}
    VOYAGE_API_KEY 미설정 시 임베딩 없이 원본 목록을 그대로 반환한다.
    """
    from agents.dedup_indexer import Article, run as dedup_run
    from services.cache_service import get_news_cache, set_news_cache

    results = {}
    for ticker, raw_items in news_by_ticker.items():
        try:
            articles = [
                Article(url=url, title=title, summary=summary)
                for url, title, summary in raw_items
            ]
            unique = asyncio.run(dedup_run(articles))

            # 배치 내 중복 제거 기준 URL 집합으로 Redis 캐시 갱신
            # (dedup_run 이 원본을 그대로 반환한 경우는 갱신 불필요)
            if len(unique) < len(articles):
                unique_urls = {a.url for a in unique}
                cached = get_news_cache(ticker)
                if cached:
                    filtered = [item for item in cached if item.get("url") in unique_urls]
                    if filtered:
                        set_news_cache(ticker, filtered)

            results[ticker] = {"total": len(articles), "unique": len(unique)}
        except Exception as e:
            logger.warning("dedup 실패 (%s): %s", ticker, e)
            results[ticker] = {"error": str(e)}

    logger.info("dedup_and_index_news 완료: %d개 종목", len(results))
    return {"status": "ok", "indexed": results}


# ─────────────────────────────────────────────────────────────────────────────
# 공시 수집
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=2, default_retry_delay=300)
def fetch_dart_filings(self):
    """DART 공시 수집 (매일 오전 8시)"""
    try:
        # dart-fss 라이브러리 사용 — API 키 필요
        import dart_fss as dart
        dart_api_key = os.getenv("DART_API_KEY")
        if not dart_api_key:
            return {"status": "skip", "reason": "DART_API_KEY 미설정"}

        dart.set_api_key(dart_api_key)
        results = {}
        for ticker in KOSPI200_TICKERS:
            try:
                # 종목 코드로 최근 공시 조회
                bgn_de = (datetime.today() - timedelta(days=30)).strftime("%Y%m%d")
                filings = dart.filings.search(corp_code=ticker, bgn_de=bgn_de, pblntf_ty="A")
                results[ticker] = len(filings) if filings else 0
            except Exception as e:
                logger.warning(f"공시 수집 실패 ({ticker}): {e}")

        return {"status": "ok", "fetched": results}
    except ImportError:
        return {"status": "skip", "reason": "dart-fss 미설치"}
    except Exception as e:
        logger.error(f"fetch_dart_filings 실패: {e}")
        raise self.retry(exc=e)


# ─────────────────────────────────────────────────────────────────────────────
# 분석 · 관계도
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=2)
def recompute_correlations(self):
    """
    KOSPI200 전종목 Pearson 상관계수 재계산 + Redis 캐싱 (매일 자정).
    pykrx 60일 종가 기준으로 종목 간 상관계수를 갱신한다.
    """
    from services.relation_service import compute_correlations_only

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            corr_count = compute_correlations_only(ticker)
            results[ticker] = corr_count
        except Exception as e:
            logger.warning(f"상관계수 계산 실패 ({ticker}): {e}")

    return {"status": "ok", "computed": results}


@app.task(bind=True, max_retries=2)
def update_relation_graphs(self):
    """
    KOSPI200 종목 관계도 풀 갱신 (매주 월요일).

    흐름:
      1. dart_edge_extractor: DART API(최대주주·임원) + DartChunk regex → company_edges
      2. relation_discovery_agent: 뉴스+DART LLM → RelationCache(source=news|dart) + company_edges
      3. compute_correlations_only: Pearson → RelationCache(source=correlation, 발굴 관계 미덮어씀)
    """
    from services.relation_service import compute_correlations_only
    from agents.relation_discovery_agent import discover_relations
    from agents.dart_edge_extractor import extract_dart_edges

    from services.cache_service import invalidate_edges_cache

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            # 1. DART 구조화 엣지 추출 → company_edges (LLM 불필요, 빠름)
            dart_edges = asyncio.run(extract_dart_edges(ticker))
            # 2. 뉴스+DART LLM 관계 발굴 → RelationCache + company_edges
            discovered = asyncio.run(discover_relations(ticker))
            # 3. Pearson 상관계수 weight 보강 (관계 유형 분류에는 미사용)
            corr_count = compute_correlations_only(ticker)

            # company_edges 재구축 완료 → Redis 캐시 무효화 (다음 요청에 새 그래프 반영)
            invalidate_edges_cache(ticker)

            results[ticker] = {
                "dart_edges": dart_edges,
                "discovered": discovered,
                "corr_cached": corr_count,
            }
        except Exception as e:
            logger.error("[%s] 관계도 갱신 실패: %s", ticker, e)

    return {"status": "ok", "updated": results}


@app.task
def discover_relations_for_ticker(ticker: str):
    """단일 종목 관계 발굴 (온디맨드 트리거용)"""
    from agents.relation_discovery_agent import discover_relations

    count = asyncio.run(discover_relations(ticker))
    return {"ticker": ticker, "discovered": count}


@app.task(bind=True, max_retries=1)
def retroactive_relation_seed(self, tickers: list[str] | None = None):
    """
    과거 뉴스(NewsCache) + 멀티페이지 크롤링을 소급 탐색하여
    company_edges / RelationCache 초기 씨딩을 수행한다.

    - tickers=None 이면 KOSPI200 상위 50개 처리
    - 배치당 25건씩 LLM 호출 → 결과 합산 후 저장
    - 신규 서비스 시작 시 또는 company_edges 초기화 후 1회 실행 권장
    """
    from agents.relation_discovery_agent import discover_relations_retroactive
    from services.cache_service import invalidate_edges_cache

    targets = tickers or KOSPI200_TICKERS[:50]
    results = {}

    for ticker in targets:
        try:
            count = asyncio.run(discover_relations_retroactive(ticker))
            if count > 0:
                invalidate_edges_cache(ticker)
            results[ticker] = count
            logger.info("[%s] 소급 씨딩: %d건", ticker, count)
        except Exception as e:
            logger.error("[%s] 소급 씨딩 실패: %s", ticker, e)
            results[ticker] = 0

    total = sum(results.values())
    logger.info("retroactive_relation_seed 완료: %d개 종목, %d건 관계 저장", len(results), total)
    return {"status": "ok", "total": total, "by_ticker": results}


@app.task
def retroactive_seed_single(ticker: str):
    """단일 종목 소급 씨딩 (온디맨드)"""
    from agents.relation_discovery_agent import discover_relations_retroactive
    from services.cache_service import invalidate_edges_cache

    count = asyncio.run(discover_relations_retroactive(ticker))
    if count > 0:
        invalidate_edges_cache(ticker)
    return {"ticker": ticker, "seeded": count}


# ─────────────────────────────────────────────────────────────────────────────
# 보정 태스크
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=2, default_retry_delay=300)
def calibrate_predictions(self):
    """예측 정확도 평가 + confidence 보정 (매일 02:00)"""
    from agents.calibrator import run_daily

    try:
        result = asyncio.run(run_daily())
        return {
            "status": "ok",
            "sample_count": result.sample_count,
            "direction_accuracy": round(result.direction_accuracy, 4),
            "mean_calibrated_confidence": round(result.mean_calibrated_confidence, 4),
        }
    except Exception as e:
        logger.error("calibrate_predictions 실패: %s", e)
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=3, default_retry_delay=120)
def index_dart_disclosures(self):
    """
    주요 종목 DART 공시·재무제표 pgvector 색인 (매일 18:00).

    색인 완료 후 dart_edge_extractor를 체이닝하여
    특수관계자·계열사 정보를 company_edges에 적재한다.
    """
    from agents.dart_indexer import run as dart_run

    results = {}
    for ticker in KOSPI200_TICKERS[:30]:
        try:
            count = asyncio.run(dart_run(ticker))
            results[ticker] = count
        except Exception as e:
            logger.error("[%s] DART 색인 실패: %s", ticker, e)
            self.retry(exc=e)

    # DART 색인 완료 후 엣지 추출 태스크 체이닝
    if results:
        extract_dart_edges_batch.delay(list(results.keys()))

    return {"indexed": results}


@app.task(bind=True, max_retries=2, default_retry_delay=60)
def extract_dart_edges_batch(self, tickers: list[str]):
    """
    DART 공시에서 사업 관계 엣지를 추출하여 company_edges에 적재 (index_dart_disclosures 후속).

    tickers: 처리 대상 종목 코드 목록
    """
    from agents.dart_edge_extractor import extract_dart_edges

    from services.cache_service import invalidate_edges_cache

    results = {}
    for ticker in tickers:
        try:
            count = asyncio.run(extract_dart_edges(ticker))
            results[ticker] = count
            if count > 0:
                # 새 엣지가 생겼으면 Redis 캐시 무효화
                invalidate_edges_cache(ticker)
        except Exception as e:
            logger.warning("[%s] DART 엣지 추출 실패: %s", ticker, e)
            results[ticker] = 0

    total = sum(results.values())
    logger.info("extract_dart_edges_batch 완료: %d개 종목, %d건 엣지 저장", len(results), total)
    return {"status": "ok", "edges_saved": results, "total": total}


@app.task
def extract_dart_edges_for_ticker(ticker: str):
    """단일 종목 DART 엣지 추출 (온디맨드)"""
    from agents.dart_edge_extractor import extract_dart_edges

    count = asyncio.run(extract_dart_edges(ticker))
    return {"ticker": ticker, "edges_saved": count}


# ─────────────────────────────────────────────────────────────────────────────
# 장 마감 후 가격 장기 캐싱 (주말/공휴일 대비)
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=3, default_retry_delay=60)
def cache_eod_prices(self):
    """
    장 마감 직후(15:35) KOSPI200 종가를 48h TTL로 Redis에 저장한다.
    주말·공휴일에도 가장 최근 종가를 조회할 수 있도록 캐시를 유지한다.
    """
    try:
        from pykrx import stock as pykrx_stock
        from services.cache_service import set_price_cache

        TTL_EOD = 60 * 60 * 48  # 48시간 — 주말(토~월)을 포함한 시간

        today = datetime.today().strftime("%Y%m%d")
        yesterday = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")

        df = pykrx_stock.get_market_ohlcv_by_ticker(today, market="KOSPI")
        df_prev = pykrx_stock.get_market_ohlcv_by_ticker(yesterday, market="KOSPI")

        if df is None or df.empty:
            return {"status": "skip", "reason": "데이터 없음"}

        updated = 0
        for ticker in KOSPI200_TICKERS:
            try:
                if ticker not in df.index:
                    continue
                price = float(df.loc[ticker]["종가"])
                prev_price = (
                    float(df_prev.loc[ticker]["종가"])
                    if df_prev is not None and not df_prev.empty and ticker in df_prev.index
                    else price
                )
                change_amount = price - prev_price
                change_pct = (change_amount / prev_price * 100) if prev_price else 0

                set_price_cache(ticker, {
                    "price": price,
                    "change": round(change_pct, 2),
                    "change_amount": round(change_amount, 0),
                }, ttl=TTL_EOD)
                updated += 1
            except Exception as e:
                logger.warning(f"EOD 가격 캐싱 실패 ({ticker}): {e}")

        return {"status": "ok", "updated": updated}
    except ImportError:
        return {"status": "skip", "reason": "pykrx 미설치"}
    except Exception as e:
        logger.error(f"cache_eod_prices 실패: {e}")
        raise self.retry(exc=e)


# ─────────────────────────────────────────────────────────────────────────────
# 온디맨드 태스크
# ─────────────────────────────────────────────────────────────────────────────

@app.task
def analyze_single_ticker(ticker: str):
    """
    단일 종목 전체 파이프라인 갱신 (온디맨드 트리거).

    흐름:
      1. 가격 캐시 갱신
      2. 뉴스 수집 + LLM 요약
      3. analysis_agent 구조화 분석 → InsightCache
      4. 관계 발굴 → RelationCache + company_edges
    """
    from services.news_service import fetch_news
    from services.nlp_service import extract_keywords, summarize_with_llm
    from services.stock_service import get_current_price, get_price_history, get_or_build_registry

    get_current_price(ticker)
    get_price_history(ticker, days=90)

    items = asyncio.run(fetch_news(ticker))
    titles = [i.title for i in items]
    extract_keywords(titles)

    registry = get_or_build_registry()
    company_name = registry.get(ticker, {}).get("name", ticker)
    asyncio.run(summarize_with_llm(ticker, company_name, titles))

    # analysis_agent 구조화 분석 → InsightCache 저장
    if items:
        try:
            from agents.dedup_indexer import Article
            from agents.analysis_agent import run_and_save
            articles = [Article(url=n.url, title=n.title, summary=n.summary or "") for n in items]
            asyncio.run(run_and_save(articles, ticker))
        except Exception as e:
            logger.warning("[%s] analysis_agent 실패: %s", ticker, e)

    # 관계 발굴 → RelationCache + company_edges
    try:
        from agents.relation_discovery_agent import discover_relations
        discovered = asyncio.run(discover_relations(ticker))
        logger.info("[%s] analyze_single_ticker: 관계 발굴 %d건", ticker, discovered)
    except Exception as e:
        logger.warning("[%s] 관계 발굴 실패: %s", ticker, e)

    return {"ticker": ticker, "done": True}


@app.task(bind=True, max_retries=1)
def warmup_popular_tickers(self):
    """
    장 시작 전(08:30) KOSPI200 상위 50개 종목 인사이트 캐시를 선제 갱신한다.

    InsightCache가 없거나 6시간 이상 지난 종목만 대상으로 하여
    불필요한 LLM 재호출을 방지한다.
    """
    from services.cache_service import get_news_cache, set_news_cache
    from services.news_service import fetch_news, rank_news
    import json as _json

    results: dict[str, str] = {}
    warmup_targets = KOSPI200_TICKERS[:50]

    for ticker in warmup_targets:
        try:
            # InsightCache 만료 여부 확인 (6시간 초과 = 갱신 대상)
            from models.db_models import InsightCache, SessionLocal
            from datetime import timezone

            db = SessionLocal()
            stale = True
            try:
                row = db.query(InsightCache).filter(InsightCache.ticker == ticker).first()
                if row and row.updated_at:
                    age_h = (datetime.utcnow() - row.updated_at).total_seconds() / 3600
                    stale = age_h >= 6
            finally:
                db.close()

            if not stale:
                results[ticker] = "fresh"
                continue

            # 뉴스 강제 갱신
            items = asyncio.run(fetch_news(ticker, force=True))
            if items:
                ranked = asyncio.run(rank_news(items))
                set_news_cache(ticker, [i.model_dump() for i in ranked])

            # analysis_agent 백그라운드 실행 (Celery 체이닝)
            analyze_single_ticker.delay(ticker)
            results[ticker] = "queued"
        except Exception as e:
            logger.warning("[%s] warm-up 실패: %s", ticker, e)
            results[ticker] = f"error: {e}"

    warmed = sum(1 for v in results.values() if v == "queued")
    fresh = sum(1 for v in results.values() if v == "fresh")
    logger.info("warmup_popular_tickers 완료: queued=%d fresh=%d", warmed, fresh)
    return {"status": "ok", "queued": warmed, "fresh": fresh}
