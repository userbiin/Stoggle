# Celery 스케줄러
import os
import sys
import time
import logging
import asyncio
from datetime import datetime, timedelta

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import nest_asyncio
nest_asyncio.apply()

from celery import Celery
from celery.signals import worker_process_init

# 워커 초기화
@worker_process_init.connect
def _init_worker_process(**kwargs):
    if _BACKEND_DIR not in sys.path:
        sys.path.insert(0, _BACKEND_DIR)
from celery.schedules import crontab
from celery.signals import task_prerun, task_postrun, task_failure
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

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
    "fetch-top200-prices": {
        "task": "tasks.fetch_top200_prices",
        "schedule": 60.0,
    },
    "update-prices-daily": {
        "task": "tasks.update_price_history",
        "schedule": crontab(hour=16, minute=0),
    },
    "crawl-all-news": {
        "task": "tasks.crawl_all_news",
        "schedule": crontab(minute=0),
    },
    "run-exaone-news-pipeline": {
        "task": "tasks.run_exaone_news_pipeline",
        "schedule": crontab(minute=30),
    },
    "prefetch-news-daily": {
        "task": "tasks.prefetch_news_for_major_stocks",
        "schedule": crontab(hour=8, minute=30),
    },
    "fetch-dart-filings": {
        "task": "tasks.fetch_dart_filings",
        "schedule": crontab(hour=8, minute=0),
    },
    "recompute-correlations": {
        "task": "tasks.recompute_correlations",
        "schedule": crontab(hour=0, minute=0),
    },
    "update-relations-weekly": {
        "task": "tasks.update_relation_graphs",
        "schedule": crontab(hour=9, minute=0, day_of_week="monday"),
    },
    "refresh-ticker-registry": {
        "task": "tasks.refresh_ticker_registry",
        "schedule": crontab(hour=7, minute=0, day_of_week="monday"),
    },
    "calibrate-predictions-daily": {
        "task": "tasks.calibrate_predictions",
        "schedule": crontab(hour=2, minute=0),
    },
    "index-dart-daily": {
        "task": "tasks.index_dart_disclosures",
        "schedule": crontab(hour=18, minute=0),
    },
    "cache-eod-prices": {
        "task": "tasks.cache_eod_prices",
        "schedule": crontab(hour=15, minute=35, day_of_week="mon-fri"),
    },
    "warmup-popular-tickers": {
        "task": "tasks.warmup_popular_tickers",
        "schedule": crontab(hour=8, minute=30),
    },
}

from services.kospi200 import KOSPI200_TICKERS, KOSPI200_FALLBACK as _KOSPI200_FALLBACK


# 가격이력 upsert
def _upsert_price_history_to_db(ticker: str, history: list) -> None:
    if not history:
        return
    try:
        from models.db_models import PriceHistory, SessionLocal

        rows = [
            {"ticker": ticker, "date": p.date, "close": p.close, "volume": p.volume}
            for p in history
        ]
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        db = SessionLocal()
        try:
            stmt = pg_insert(PriceHistory).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "date"],
                set_={"close": stmt.excluded.close, "volume": stmt.excluded.volume},
            )
            db.execute(stmt)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("price_history DB 저장 실패 (%s): %s", ticker, e)


# 회사 upsert
def _upsert_companies_to_db(registry: dict) -> None:
    if not registry:
        return
    try:
        from models.db_models import Company, SessionLocal

        now = datetime.utcnow()
        rows = [
            {
                "ticker": info["ticker"],
                "name": info.get("name", info["ticker"]),
                "market": info.get("market", ""),
                "updated_at": now,
            }
            for info in registry.values()
            if info.get("ticker")
        ]
        if not rows:
            return

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        db = SessionLocal()
        try:
            stmt = pg_insert(Company).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker"],
                set_={
                    "name": stmt.excluded.name,
                    "market": stmt.excluded.market,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            db.execute(stmt)
            db.commit()
        finally:
            db.close()
        logger.info("companies DB 갱신 완료: %d건", len(rows))
    except Exception as e:
        logger.warning("companies DB 저장 실패: %s", e)


# 뉴스캐시 upsert
def _upsert_news_cache_to_db(ticker: str, items: list) -> None:
    if not items:
        return
    try:
        from models.db_models import NewsCache, SessionLocal

        now = datetime.utcnow()
        db = SessionLocal()
        try:
            db.query(NewsCache).filter(NewsCache.ticker == ticker).delete()
            db.bulk_insert_mappings(NewsCache, [
                {
                    "ticker": ticker,
                    "title": item.title,
                    "source": item.source,
                    "published_at": item.published_at,
                    "url": item.url,
                    "sentiment": item.sentiment,
                    "summary": item.summary,
                    "category": item.category,
                    "fetched_at": now,
                }
                for item in items
            ])
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("news_cache DB 저장 실패 (%s): %s", ticker, e)


# 시장모델 파라미터 갱신
def _update_market_model_params() -> None:
    if datetime.today().day != 1:
        return
    try:
        from collections import defaultdict
        from models.db_models import PriceHistory, SessionLocal
        from evaluation.market_model import estimate_market_model, save_market_params

        db = SessionLocal()
        try:
            estimation_date = datetime.today().strftime("%Y-%m-%d")

            rows = (
                db.query(PriceHistory.ticker, PriceHistory.date, PriceHistory.close)
                .filter(PriceHistory.ticker.in_(KOSPI200_TICKERS))
                .order_by(PriceHistory.date)
                .all()
            )
            if not rows:
                logger.warning("price_history 없음 — market_model_params 건너뜀")
                return

            prices_by_ticker: dict[str, dict[str, float]] = defaultdict(dict)
            all_dates: set[str] = set()
            for tkr, date_str, close in rows:
                prices_by_ticker[tkr][date_str] = close
                all_dates.add(date_str)

            sorted_dates = sorted(all_dates)
            mkt_closes: list[float] = []
            for d in sorted_dates:
                closes = [
                    prices_by_ticker[t][d]
                    for t in KOSPI200_TICKERS
                    if d in prices_by_ticker[t]
                ]
                if closes:
                    mkt_closes.append(sum(closes) / len(closes))

            market_returns = [
                (mkt_closes[i] - mkt_closes[i - 1]) / mkt_closes[i - 1]
                for i in range(1, len(mkt_closes))
                if mkt_closes[i - 1] > 0
            ]

            saved = 0
            for tkr in KOSPI200_TICKERS:
                try:
                    td = prices_by_ticker.get(tkr, {})
                    if len(td) < 30:
                        continue
                    s_prices = [td[d] for d in sorted(td.keys())]
                    stock_returns = [
                        (s_prices[i] - s_prices[i - 1]) / s_prices[i - 1]
                        for i in range(1, len(s_prices))
                        if s_prices[i - 1] > 0
                    ]
                    n = min(len(stock_returns), len(market_returns))
                    if n < 20:
                        continue
                    alpha, beta, r_sq = estimate_market_model(
                        stock_returns[-n:], market_returns[-n:]
                    )
                    save_market_params(tkr, estimation_date, alpha, beta, r_sq, db)
                    saved += 1
                except Exception as e:
                    logger.warning("market_model_params 계산 실패 (%s): %s", tkr, e)

            logger.info("market_model_params 갱신 완료: %d건", saved)
        finally:
            db.close()
    except Exception as e:
        logger.warning("_update_market_model_params 실패: %s", e)


# 뉴스 사전수집
@app.task(bind=True, max_retries=2, default_retry_delay=120)
def prefetch_news_for_major_stocks(self):
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


# 레지스트리 갱신
@app.task(bind=True, max_retries=2, default_retry_delay=300)
def refresh_ticker_registry(self):
    try:
        from services.stock_service import build_ticker_registry
        from services.cache_service import set_ticker_registry

        registry = build_ticker_registry()
        ok = set_ticker_registry(registry)
        _upsert_companies_to_db(registry)
        return {"status": "ok", "count": len(registry), "cached": ok}
    except Exception as e:
        logger.error(f"레지스트리 갱신 실패: {e}")
        raise self.retry(exc=e)


# 현재가 수집
@app.task(bind=True, max_retries=3, default_retry_delay=30)
def fetch_top200_prices(self):
    from datetime import datetime as dt

    now = dt.now()
    is_trading_hours = (
        now.weekday() < 5
        and (9, 0) <= (now.hour, now.minute) <= (15, 30)
    )

    if not is_trading_hours:
        return {"status": "skip", "reason": "장 운영 시간 외"}

    try:
        from pykrx import stock as pykrx_stock
        from services.cache_service import set_price_cache, TTL_PRICE

        today = now.strftime("%Y%m%d")
        df = pykrx_stock.get_market_ohlcv_by_ticker(today, market="KOSPI")
        if df is None or df.empty:
            return {"status": "skip", "reason": "데이터 없음"}

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


# 가격이력 갱신
@app.task(bind=True, max_retries=3, default_retry_delay=120)
def update_price_history(self):
    from services.stock_service import get_price_history

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            history = get_price_history(ticker, days=90)
            results[ticker] = len(history)
            _upsert_price_history_to_db(ticker, history)
        except Exception as e:
            logger.warning(f"히스토리 갱신 실패 ({ticker}): {e}")

    return {"status": "ok", "updated": results}


# 뉴스 크롤링
@app.task(bind=True, max_retries=2, default_retry_delay=120)
def crawl_all_news(self):
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
            items = asyncio.run(fetch_news(ticker, force=True))
            if not items:
                results[ticker] = 0
                continue

            company_name = registry.get(ticker, {}).get("name", ticker)
            ranked = asyncio.run(rank_news(items, company_name=company_name))

            try:
                from agents.relevance_agent import Article as RelevArticle, run as relevance_run
                relev_articles = [
                    RelevArticle(url=i.url, title=i.title, summary=i.summary or "")
                    for i in ranked
                ]
                scored = asyncio.run(relevance_run(ticker, relev_articles))
                if scored:
                    relevant_urls = {s.article.url for s in scored}
                    ranked = [i for i in ranked if i.url in relevant_urls]
                    logger.debug("relevance_agent 필터 [%s]: %d→%d건", ticker, len(relev_articles), len(ranked))
            except Exception as e:
                logger.debug("relevance_agent 필터 건너뜀 (%s): %s", ticker, e)

            set_news_cache(ticker, [i.model_dump() for i in ranked])
            _upsert_news_cache_to_db(ticker, ranked)
            results[ticker] = len(ranked)

            news_for_dedup[ticker] = [
                (i.url, i.title, i.summary or "") for i in ranked
            ]
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

    if news_for_dedup:
        dedup_and_index_news.delay(news_for_dedup)

    updated_tickers = [t for t, cnt in results.items() if cnt > 0]
    for ticker in updated_tickers:
        analyze_single_ticker.delay(ticker)

    return {"status": "ok", "crawled": results}


# Exaone 파이프라인
@app.task(bind=True, max_retries=1, default_retry_delay=120)
def run_exaone_news_pipeline(self):
    import httpx as _httpx

    _ollama_root = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    if _ollama_root.endswith("/v1"):
        _ollama_root = _ollama_root[:-3]
    try:
        _httpx.get(f"{_ollama_root}/api/tags", timeout=5).raise_for_status()
    except Exception as e:
        logger.info("Ollama 미연결 — run_exaone_news_pipeline skip: %s", e)
        return {"status": "skipped", "reason": "ollama_unreachable"}

    from agents.news_sources import collect_candidates
    from agents.naver_news_crawler import fetch_all_bodies
    from agents.relevance_agent import (
        Article as RelevArticle,
        batch_filter,
        map_articles_to_tickers,
        run as relevance_run,
    )
    from services.cache_service import get_news_cache, set_news_cache

    async def _pipeline() -> list[tuple[str, object]]:
        raw = await collect_candidates()
        await fetch_all_bodies(raw)
        logger.info("RSS/섹션 수집: %d건", len(raw))

        articles = [
            RelevArticle(
                url=a["link"],
                title=a["title"],
                summary=(a.get("body") or a.get("description", ""))[:500],
            )
            for a in raw
            if a.get("title") and a.get("link")
        ]

        filtered = await batch_filter(articles)
        if not filtered:
            return []

        ticker_map = await map_articles_to_tickers(filtered)
        if not ticker_map:
            return []

        results: list[tuple[str, object]] = []
        for ticker, arts in ticker_map.items():
            scored = await relevance_run(ticker, arts)
            results.extend((ticker, s) for s in scored)
        return results

    try:
        pipeline_results = asyncio.run(_pipeline())
    except Exception as exc:
        logger.warning("Ollama 파이프라인 실패: %s", exc)
        raise self.retry(exc=exc)

    by_ticker: dict[str, list] = {}
    for ticker, scored in pipeline_results:
        by_ticker.setdefault(ticker, []).append(scored)

    direction_to_sentiment = {"상승": "positive", "하락": "negative", "중립": "neutral"}
    updated = {}
    for ticker, scored_list in by_ticker.items():
        existing = get_news_cache(ticker) or []
        existing_urls = {e.get("url") for e in existing}

        new_items = []
        for idx, s in enumerate(scored_list):
            if s.article.url in existing_urls:
                continue
            new_items.append({
                "id": -(idx + 1),
                "title": s.article.title,
                "source": "ollama",
                "published_at": "",
                "url": s.article.url,
                "sentiment": direction_to_sentiment.get(s.direction, "neutral"),
                "summary": s.reason,
                "category": None,
            })

        if new_items:
            set_news_cache(ticker, new_items + existing)
            updated[ticker] = len(new_items)

    logger.info("Ollama 파이프라인 완료: %d건 → %d 종목 캐시 갱신", len(pipeline_results), len(updated))
    return {"status": "ok", "inserted": updated}


# 뉴스 중복제거
@app.task(bind=True, max_retries=1, default_retry_delay=60)
def dedup_and_index_news(self, news_by_ticker: dict):
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


# 공시 수집
@app.task(bind=True, max_retries=2, default_retry_delay=300)
def fetch_dart_filings(self):
    try:
        import dart_fss as dart
        dart_api_key = os.getenv("DART_API_KEY")
        if not dart_api_key:
            return {"status": "skip", "reason": "DART_API_KEY 미설정"}

        dart.set_api_key(dart_api_key)
        results = {}
        for ticker in KOSPI200_TICKERS:
            try:
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


# 상관계수 재계산
@app.task(bind=True, max_retries=2)
def recompute_correlations(self):
    from services.relation_service import compute_correlations_only

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            corr_count = compute_correlations_only(ticker)
            results[ticker] = corr_count
        except Exception as e:
            logger.warning(f"상관계수 계산 실패 ({ticker}): {e}")

    return {"status": "ok", "computed": results}


# 관계도 갱신
@app.task(bind=True, max_retries=2)
def update_relation_graphs(self):
    from services.relation_service import compute_correlations_only
    from agents.relation_discovery_agent import discover_relations
    from agents.dart_edge_extractor import extract_dart_edges

    from services.cache_service import invalidate_edges_cache

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            dart_edges = asyncio.run(extract_dart_edges(ticker))
            discovered = asyncio.run(discover_relations(ticker))
            corr_count = compute_correlations_only(ticker)

            invalidate_edges_cache(ticker)

            results[ticker] = {
                "dart_edges": dart_edges,
                "discovered": discovered,
                "corr_cached": corr_count,
            }
        except Exception as e:
            logger.error("[%s] 관계도 갱신 실패: %s", ticker, e)

    return {"status": "ok", "updated": results}


# 단일관계 발굴
@app.task
def discover_relations_for_ticker(ticker: str):
    from agents.relation_discovery_agent import discover_relations

    count = asyncio.run(discover_relations(ticker))
    return {"ticker": ticker, "discovered": count}


# 관계 소급씨딩
@app.task(bind=True, max_retries=1)
def retroactive_relation_seed(self, tickers: list[str] | None = None):
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


# 단일 소급씨딩
@app.task
def retroactive_seed_single(ticker: str):
    from agents.relation_discovery_agent import discover_relations_retroactive
    from services.cache_service import invalidate_edges_cache

    count = asyncio.run(discover_relations_retroactive(ticker))
    if count > 0:
        invalidate_edges_cache(ticker)
    return {"ticker": ticker, "seeded": count}


# 예측 보정
@app.task(bind=True, max_retries=2, default_retry_delay=300)
def calibrate_predictions(self):
    from agents.calibrator import run_daily

    try:
        result = asyncio.run(run_daily())
        _update_market_model_params()
        return {
            "status": "ok",
            "sample_count": result.sample_count,
            "direction_accuracy": round(result.direction_accuracy, 4),
            "mean_calibrated_confidence": round(result.mean_calibrated_confidence, 4),
        }
    except Exception as e:
        logger.error("calibrate_predictions 실패: %s", e)
        raise self.retry(exc=e)


# DART 색인
@app.task(bind=True, max_retries=3, default_retry_delay=120)
def index_dart_disclosures(self):
    from agents.dart_indexer import run as dart_run

    results = {}
    for ticker in KOSPI200_TICKERS[:30]:
        try:
            count = asyncio.run(dart_run(ticker))
            results[ticker] = count
        except Exception as e:
            logger.error("[%s] DART 색인 실패: %s", ticker, e)
            self.retry(exc=e)

    if results:
        extract_dart_edges_batch.delay(list(results.keys()))

    return {"indexed": results}


# DART 엣지 배치
@app.task(bind=True, max_retries=2, default_retry_delay=60)
def extract_dart_edges_batch(self, tickers: list[str]):
    from agents.dart_edge_extractor import extract_dart_edges

    from services.cache_service import invalidate_edges_cache

    results = {}
    for ticker in tickers:
        try:
            count = asyncio.run(extract_dart_edges(ticker))
            results[ticker] = count
            if count > 0:
                invalidate_edges_cache(ticker)
        except Exception as e:
            logger.warning("[%s] DART 엣지 추출 실패: %s", ticker, e)
            results[ticker] = 0

    total = sum(results.values())
    logger.info("extract_dart_edges_batch 완료: %d개 종목, %d건 엣지 저장", len(results), total)
    return {"status": "ok", "edges_saved": results, "total": total}


# DART 엣지 단일
@app.task
def extract_dart_edges_for_ticker(ticker: str):
    from agents.dart_edge_extractor import extract_dart_edges

    count = asyncio.run(extract_dart_edges(ticker))
    return {"ticker": ticker, "edges_saved": count}


# 장마감 가격 캐싱
@app.task(bind=True, max_retries=3, default_retry_delay=60)
def cache_eod_prices(self):
    try:
        from pykrx import stock as pykrx_stock
        from services.cache_service import set_price_cache

        TTL_EOD = 60 * 60 * 48

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


# 단일종목 분석
@app.task
def analyze_single_ticker(ticker: str):
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

    if items:
        try:
            from agents.dedup_indexer import Article
            from agents.analysis_agent import run_and_save
            articles = [Article(url=n.url, title=n.title, summary=n.summary or "") for n in items]
            asyncio.run(run_and_save(articles, ticker))
        except Exception as e:
            logger.warning("[%s] analysis_agent 실패: %s", ticker, e)

    try:
        from agents.relation_discovery_agent import discover_relations
        discovered = asyncio.run(discover_relations(ticker))
        logger.info("[%s] analyze_single_ticker: 관계 발굴 %d건", ticker, discovered)
    except Exception as e:
        logger.warning("[%s] 관계 발굴 실패: %s", ticker, e)

    return {"ticker": ticker, "done": True}


# 인기종목 웜업
@app.task(bind=True, max_retries=1)
def warmup_popular_tickers(self):
    from services.cache_service import get_news_cache, set_news_cache
    from services.news_service import fetch_news, rank_news
    import json as _json

    results: dict[str, str] = {}
    warmup_targets = KOSPI200_TICKERS[:50]

    for ticker in warmup_targets:
        try:
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

            items = asyncio.run(fetch_news(ticker, force=True))
            if items:
                ranked = asyncio.run(rank_news(items))
                set_news_cache(ticker, [i.model_dump() for i in ranked])

            analyze_single_ticker.delay(ticker)
            results[ticker] = "queued"
        except Exception as e:
            logger.warning("[%s] warm-up 실패: %s", ticker, e)
            results[ticker] = f"error: {e}"

    warmed = sum(1 for v in results.values() if v == "queued")
    fresh = sum(1 for v in results.values() if v == "fresh")
    logger.info("warmup_popular_tickers 완료: queued=%d fresh=%d", warmed, fresh)
    return {"status": "ok", "queued": warmed, "fresh": fresh}
