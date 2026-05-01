"""
Celery 자동화 스케줄러

실행:
  celery -A tasks worker --loglevel=info
  celery -A tasks beat --loglevel=info
"""
import os
import logging
import asyncio

from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery("stoggle", broker=REDIS_URL, backend=REDIS_URL)

app.conf.timezone = "Asia/Seoul"
app.conf.beat_schedule = {
    # 매일 오전 8:30 — 주요 종목 뉴스 사전 수집
    "prefetch-news-daily": {
        "task": "tasks.prefetch_news_for_major_stocks",
        "schedule": crontab(hour=8, minute=30),
    },
    # 매일 오후 4시 — 당일 주가 데이터 업데이트
    "update-prices-daily": {
        "task": "tasks.update_price_history",
        "schedule": crontab(hour=16, minute=0),
    },
    # 매주 월요일 오전 9시 — 종목 관계도 갱신
    "update-relations-weekly": {
        "task": "tasks.update_relation_graphs",
        "schedule": crontab(hour=9, minute=0, day_of_week="monday"),
    },
    # 매일 오후 6시 — DART 공시·재무제표 색인
    "index-dart-daily": {
        "task": "tasks.index_dart_disclosures",
        "schedule": crontab(hour=18, minute=0),
    },
}

MAJOR_TICKERS = [
    "005930", "000660", "035420", "051910",
    "207940", "035720", "066570", "005380", "000270",
]


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def prefetch_news_for_major_stocks(self):
    """주요 종목 뉴스 사전 수집"""
    from services.news_service import fetch_news, rank_news

    results = {}
    for ticker in MAJOR_TICKERS:
        try:
            items = asyncio.run(fetch_news(ticker))
            ranked = rank_news(items)
            results[ticker] = len(ranked)
        except Exception as e:
            logger.error("[%s] 뉴스 수집 실패: %s", ticker, e)
            self.retry(exc=e)

    return {"fetched": results}


@app.task(bind=True, max_retries=3, default_retry_delay=120)
def update_price_history(self):
    """당일 주가 데이터 업데이트"""
    from services.stock_service import get_price_history

    results = {}
    for ticker in MAJOR_TICKERS:
        try:
            history = get_price_history(ticker, days=5)
            results[ticker] = len(history)
        except Exception as e:
            logger.error("[%s] 주가 업데이트 실패: %s", ticker, e)
            self.retry(exc=e)

    return {"updated": results}


@app.task(bind=True, max_retries=2)
def update_relation_graphs(self):
    """종목 관계도 갱신"""
    from services.relation_service import compute_relations

    results = {}
    for ticker in MAJOR_TICKERS:
        try:
            data = compute_relations(ticker)
            results[ticker] = len(data.get("nodes", []))
        except Exception as e:
            logger.error("[%s] 관계도 갱신 실패: %s", ticker, e)
            self.retry(exc=e)

    return {"updated": results}


@app.task(bind=True, max_retries=3, default_retry_delay=120)
def index_dart_disclosures(self):
    """주요 종목 DART 공시·재무제표 pgvector 색인 (매일 18:00)"""
    from agents.dart_indexer import run as dart_run

    results = {}
    for ticker in MAJOR_TICKERS:
        try:
            count = asyncio.run(dart_run(ticker))
            results[ticker] = count
        except Exception as e:
            logger.error("[%s] DART 색인 실패: %s", ticker, e)
            self.retry(exc=e)

    return {"indexed": results}


@app.task
def analyze_single_ticker(ticker: str):
    """단일 종목 인사이트 갱신 (온디맨드)"""
    from services.news_service import fetch_news
    from services.nlp_service import extract_keywords, summarize_with_llm

    items = asyncio.run(fetch_news(ticker))
    titles = [i.title for i in items]
    keywords = extract_keywords(titles)
    summary = asyncio.run(summarize_with_llm(ticker, ticker, titles))

    return {"ticker": ticker, "keywords": len(keywords), "summary": bool(summary)}
