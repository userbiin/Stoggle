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
}

# KOSPI 200 구성 종목 — pykrx로 동적 조회, 실패 시 아래 fallback 사용
_KOSPI200_FALLBACK = [
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
    """
    try:
        from pykrx import stock as pykrx_stock
        from services.stock_service import _normalize_ticker_list

        today = datetime.today().strftime("%Y%m%d")
        # KOSPI 200 인덱스 코드: 1028
        tickers = _normalize_ticker_list(
            pykrx_stock.get_index_portfolio_deposit_file("1028", date=today)
        )
        if len(tickers) > 10:
            return tickers
    except Exception as e:
        logger.warning(f"KOSPI200 구성 종목 조회 실패 — fallback 사용: {e}")
    return _KOSPI200_FALLBACK


KOSPI200_TICKERS = _load_kospi200()


# ─────────────────────────────────────────────────────────────────────────────
# 뉴스 사전 수집
# ─────────────────────────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=2, default_retry_delay=120)
def prefetch_news_for_major_stocks(self):
    """
    주요 종목 뉴스 page=1을 미리 수집해 Redis에 캐싱한다.

    beat_schedule에 등록된 태스크가 실제로 존재하도록 유지하고,
    개별 종목 실패는 전체 워커를 멈추지 않도록 결과에 기록한다.
    """
    from services.news_service import fetch_news, rank_news

    results = {}
    for ticker in KOSPI200_TICKERS[:30]:
        try:
            items = asyncio.run(fetch_news(ticker, page=1, force=True))
            ranked = rank_news(items)
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

async def _summarize_articles(ticker: str, urls: list[str]) -> None:
    """
    기사 URL 목록을 asyncio.gather로 병렬 요약 후 NewsCache.summary에 저장.
    개별 실패는 return_exceptions=True로 격리 — 전체 태스크에 영향 없음.
    품질 점수 0.3 미만 기사는 summary_agent 내부에서 자동 건너뜀.
    """
    from agents.summary_agent import run_and_save as summary_save

    results = await asyncio.gather(
        *[summary_save(u) for u in urls],
        return_exceptions=True,
    )
    ok = sum(1 for r in results if r and not isinstance(r, Exception))
    logger.info("summary_agent [%s]: %d/%d건 요약 완료", ticker, ok, len(urls))


def _save_news_to_db(ticker: str, items) -> dict[str, int]:
    """
    뉴스 기사를 NewsCache DB에 upsert하고 {url: id} 매핑을 반환한다.

    같은 URL의 기사가 이미 존재하면 sentiment/category/fetched_at만 갱신한다.
    DB 오류 시 빈 딕셔너리 반환 (호출자가 id 없이도 계속 진행 가능).
    """
    try:
        from models.db_models import NewsCache, SessionLocal
        from datetime import datetime as dt

        db = SessionLocal()
        url_to_id: dict[str, int] = {}
        try:
            urls = [i.url for i in items]
            existing_rows = db.query(NewsCache).filter(NewsCache.url.in_(urls)).all()
            existing_map = {row.url: row for row in existing_rows}

            now = dt.utcnow()
            for item in items:
                if item.url in existing_map:
                    row = existing_map[item.url]
                    row.sentiment = item.sentiment
                    row.category = item.category
                    row.fetched_at = now
                    url_to_id[item.url] = row.id
                else:
                    row = NewsCache(
                        ticker=ticker,
                        title=item.title,
                        source=item.source,
                        published_at=item.published_at,
                        url=item.url,
                        sentiment=item.sentiment,
                        summary=item.summary,
                        category=item.category,
                        fetched_at=now,
                    )
                    db.add(row)
                    db.flush()  # id 즉시 확보
                    url_to_id[item.url] = row.id

            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("NewsCache DB 저장 실패 (%s): %s", ticker, e)
        finally:
            db.close()

        return url_to_id
    except Exception as e:
        logger.warning("NewsCache DB 연결 실패 (%s): %s", ticker, e)
        return {}


@app.task(bind=True, max_retries=2, default_retry_delay=120)
def crawl_all_news(self):
    """
    KOSPI200 종목 뉴스 강제 크롤링 + Redis 캐시 갱신 (1시간 주기).

    수집 완료 후:
      1. NewsCache DB에 upsert (news_cache_id 확보)
      2. relevance_agent로 관련 기사 필터링 (Ollama 미응답 시 1단계 프리필터만 적용)
      3. dedup_and_index_news 태스크 체이닝 → 중복 제거 → pgvector 색인
         (news_cache_id를 함께 전달하여 NewsVector JOIN 가능)
    """
    from services.news_service import fetch_news
    from agents.relevance_agent import Article as RelevArticle, run as relevance_run

    results = {}
    # (url, title, summary, news_cache_id) 4-tuple
    news_for_dedup: dict[str, list[tuple[str, str, str, int | None]]] = {}

    for ticker in KOSPI200_TICKERS:
        try:
            items = asyncio.run(fetch_news(ticker, force=True))
            results[ticker] = len(items)
            if not items:
                continue

            # NewsCache DB upsert — news_cache_id 확보 (pgvector JOIN 용)
            url_to_id = _save_news_to_db(ticker, items)

            # summary_agent 병렬 실행 — 본문 추출 + Claude 요약 → NewsCache.summary 갱신
            asyncio.run(_summarize_articles(ticker, [i.url for i in items]))

            # relevance_agent 필터링
            # Ollama 미응답 → _ollama_available() False → 1단계 프리필터 결과 반환 (자동 fallback)
            articles = [
                RelevArticle(url=i.url, title=i.title, summary=i.summary or "")
                for i in items
            ]
            try:
                scored = asyncio.run(relevance_run(ticker, articles))
                relevant = [
                    (s.article.url, s.article.title, s.article.summary, url_to_id.get(s.article.url))
                    for s in scored
                ]
                logger.info("relevance 필터 [%s]: %d → %d건", ticker, len(items), len(relevant))
            except Exception as e:
                logger.warning("relevance 필터 실패 (%s) — 전체 기사 사용: %s", ticker, e)
                relevant = [(i.url, i.title, i.summary or "", url_to_id.get(i.url)) for i in items]

            if relevant:
                news_for_dedup[ticker] = relevant

        except Exception as e:
            logger.warning(f"뉴스 크롤링 실패 ({ticker}): {e}")

    # 수집 완료 → 중복 제거 + pgvector 색인 태스크 체이닝
    if news_for_dedup:
        dedup_and_index_news.delay(news_for_dedup)

    return {"status": "ok", "crawled": results}


@app.task(bind=True, max_retries=1, default_retry_delay=60)
def dedup_and_index_news(self, news_by_ticker: dict):
    """
    뉴스 중복 제거 + pgvector 색인 (crawl_all_news 후속 태스크).

    news_by_ticker: {ticker: [(url, title, summary, news_cache_id), ...]}
    news_cache_id가 설정되면 NewsVector.news_cache_id를 채워
    analysis_agent의 _retrieve_similar_news_context() JOIN이 작동한다.
    OPENAI_API_KEY 미설정 시 임베딩 없이 배치 내 중복 제거만 수행한다.
    """
    from agents.dedup_indexer import Article, run as dedup_run

    results = {}
    for ticker, raw_items in news_by_ticker.items():
        try:
            articles = [
                Article(url=url, title=title, summary=summary, news_cache_id=news_cache_id)
                for url, title, summary, news_cache_id in raw_items
            ]
            unique = asyncio.run(dedup_run(articles))
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
    """
    DART 공시 수집 + dart_analyzer 연결 (매일 오전 8시).

    흐름:
      1. dart_fss로 종목별 최근 30일 공시 목록 조회
      2. 공시 메타데이터를 텍스트로 변환 후 dart_analyzer.run_and_save() 호출
      3. dart_analysis 테이블에 재무 수치 + 인사이트 저장
    DART_API_KEY 미설정 시 즉시 skip (재시도 없음).
    """
    dart_api_key = os.getenv("DART_API_KEY")
    if not dart_api_key:
        return {"status": "skip", "reason": "DART_API_KEY 미설정"}

    try:
        import dart_fss as dart
        dart.set_api_key(dart_api_key)
    except ImportError:
        return {"status": "skip", "reason": "dart-fss 미설치"}

    from agents.dart_analyzer import run_and_save as dart_analyze

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            bgn_de = (datetime.today() - timedelta(days=30)).strftime("%Y%m%d")
            filings = dart.filings.search(corp_code=ticker, bgn_de=bgn_de, pblntf_ty="A")
            if not filings:
                results[ticker] = 0
                continue

            analyzed = 0
            for filing in filings[:3]:  # 종목당 최근 3건
                try:
                    # dart_fss 공시 객체 → 텍스트 변환
                    filing_dict = filing.to_dict() if hasattr(filing, "to_dict") else {}
                    filing_text = "\n".join(
                        f"{k}: {v}" for k, v in filing_dict.items() if v
                    ) or str(filing)

                    # rcept_dt: YYYYMMDD → YYYY-MM-DD
                    rcept_dt = filing_dict.get("rcept_dt", "")
                    filed_at = (
                        f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}"
                        if len(rcept_dt) == 8
                        else None
                    )

                    asyncio.run(dart_analyze(filing_text, ticker, filed_at=filed_at))
                    analyzed += 1
                except Exception as e:
                    logger.warning("dart_analyzer 처리 실패 (%s): %s", ticker, e)

            results[ticker] = analyzed
        except Exception as e:
            logger.warning(f"공시 수집 실패 ({ticker}): {e}")

    return {"status": "ok", "analyzed": results}


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
    상관계수 + DART 공시 기반 관계 유형 재분류 후 RelationCache DB에 upsert.
    """
    from services.relation_service import compute_relations
    from models.db_models import RelationCache, SessionLocal
    from datetime import datetime as dt

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            data = compute_relations(ticker)
            links = data.get("links", [])

            db = SessionLocal()
            try:
                saved = 0
                for link in links:
                    related_ticker = link.target
                    correlation = round(float(link.value), 4)
                    relation_type = link.type
                    reason = f"{relation_type} 관계 (상관계수 {correlation:.2f})"

                    existing = (
                        db.query(RelationCache)
                        .filter(
                            RelationCache.ticker == ticker,
                            RelationCache.related_ticker == related_ticker,
                        )
                        .first()
                    )
                    if existing:
                        existing.correlation = correlation
                        existing.relation_type = relation_type
                        existing.reason = reason
                        existing.updated_at = dt.utcnow()
                    else:
                        db.add(RelationCache(
                            ticker=ticker,
                            related_ticker=related_ticker,
                            correlation=correlation,
                            relation_type=relation_type,
                            reason=reason,
                        ))
                    saved += 1

                db.commit()
                results[ticker] = saved
                logger.info("[%s] RelationCache upsert %d건", ticker, saved)
            except Exception as e:
                db.rollback()
                logger.error("[%s] RelationCache 저장 실패: %s", ticker, e)
                results[ticker] = 0
            finally:
                db.close()

        except Exception as e:
            logger.error("[%s] 관계도 갱신 실패: %s", ticker, e)

    return {"status": "ok", "updated": results}


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
    """주요 종목 DART 공시·재무제표 pgvector 색인 (매일 18:00)"""
    from agents.dart_indexer import run as dart_run

    results = {}
    for ticker in KOSPI200_TICKERS:
        try:
            count = asyncio.run(dart_run(ticker))
            results[ticker] = count
        except Exception as e:
            logger.error("[%s] DART 색인 실패: %s", ticker, e)
            self.retry(exc=e)

    return {"indexed": results}


# ─────────────────────────────────────────────────────────────────────────────

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
    """단일 종목 인사이트 갱신 (사용자 검색 시 온디맨드 트리거)"""
    import asyncio
    from services.news_service import fetch_news
    from services.nlp_service import extract_keywords, summarize_with_llm
    from services.stock_service import get_current_price, get_price_history

    # 가격 캐시 갱신
    get_current_price(ticker)
    get_price_history(ticker, days=90)

    # 뉴스 + 분석
    items = asyncio.run(fetch_news(ticker))
    titles = [i.title for i in items]
    keywords = extract_keywords(titles)

    # 종목명 조회 (레지스트리 기반, 없으면 ticker 코드 사용)
    from services.stock_service import get_or_build_registry
    registry = get_or_build_registry()
    company_name = registry.get(ticker, {}).get("name", ticker)

    summary = asyncio.run(summarize_with_llm(ticker, company_name, titles))

    return {
        "ticker": ticker,
        "keywords": len(keywords),
        "summary": bool(summary),
    }
