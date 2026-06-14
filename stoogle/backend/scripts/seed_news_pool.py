# 뉴스 풀 적재
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv

# 백엔드 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"


def _naver_headers() -> dict:
    cid = os.getenv("NAVER_CLIENT_ID")
    csec = os.getenv("NAVER_CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다.")
    return {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}


def search_news(query: str, display: int = 100, start: int = 1, sort: str = "date") -> list[dict]:
    try:
        resp = httpx.get(
            NAVER_SEARCH_URL,
            headers=_naver_headers(),
            params={"query": query, "display": display, "start": start, "sort": sort},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("items", [])
    except Exception as e:
        logger.warning("Naver 검색 실패 (query=%s): %s", query, e)
        return []


def seed_for_ticker(ticker: str, company_name: str, days: int, max_pages: int) -> int:
    from email.utils import parsedate_to_datetime
    from models.db_models import NewsCache, SessionLocal

    cutoff = datetime.utcnow() - timedelta(days=days)
    db = SessionLocal()
    saved = 0

    try:
        for page in range(1, max_pages + 1):
            start_idx = (page - 1) * 100 + 1
            items = search_news(query=company_name, display=100, start=start_idx)
            if not items:
                break

            stopped_early = False
            for it in items:
                raw_pub = it.get("pubDate", "")
                try:
                    pub_dt = parsedate_to_datetime(raw_pub)
                    # tz-aware → naive UTC
                    pub_naive = pub_dt.replace(tzinfo=None) if pub_dt.tzinfo else pub_dt
                except Exception:
                    continue

                # cutoff 이전 기사만 저장 (오래된 것부터 적재)
                if pub_naive < cutoff:
                    stopped_early = True
                    continue  # 이 기사는 범위 밖 — 계속 진행 (정렬 보장 안 됨)

                url = it.get("link") or it.get("originallink", "")
                if not url:
                    continue

                # 중복 확인
                existing = db.query(NewsCache).filter(NewsCache.url == url).first()
                if existing:
                    continue

                # HTML 태그 제거
                import re
                title = re.sub(r"<[^>]+>", "", it.get("title", ""))
                summary = re.sub(r"<[^>]+>", "", it.get("description", ""))

                db.add(NewsCache(
                    ticker=ticker,
                    title=title[:500],
                    source=it.get("source", "naver"),
                    published_at=pub_naive.strftime("%Y-%m-%dT%H:%M:%S"),
                    url=url[:1000],
                    sentiment="neutral",
                    summary=summary[:2000] if summary else None,
                    fetched_at=pub_naive,   # fetched_at = pubDate (백테스트 시점 기준)
                ))
                saved += 1

            db.commit()
            time.sleep(0.35)  # Naver 속도 제한 보호

            if stopped_early and page >= 3:
                break  # 오래된 기사가 다수 보이면 조기 종료

        logger.info("[%s] %s: %d건 저장", ticker, company_name, saved)
    except Exception as e:
        logger.error("[%s] 저장 실패: %s", ticker, e)
        db.rollback()
    finally:
        db.close()

    return saved


def get_kospi50_info() -> list[tuple[str, str]]:
    try:
        from pykrx import stock
        tickers = list(stock.get_index_portfolio_deposit_file("1028"))[:50]
        result = []
        for t in tickers:
            try:
                name = stock.get_market_ticker_name(t)
                result.append((t, name))
            except Exception:
                result.append((t, t))
        return result
    except Exception:
        from evaluation.dataset_builder import _FALLBACK_TICKERS
        return [(t, t) for t in _FALLBACK_TICKERS[:30]]


def main():
    parser = argparse.ArgumentParser(description="백테스트 뉴스 풀 적재")
    parser.add_argument("--days", type=int, default=14, help="최근 N일 뉴스 수집 (기본 14)")
    parser.add_argument("--max_pages", type=int, default=5, help="종목당 최대 페이지 (기본 5 = 500건)")
    parser.add_argument("--tickers", nargs="*", help="종목코드 직접 지정 (미지정 시 KOSPI50)")
    args = parser.parse_args()

    if args.tickers:
        from pykrx import stock as pykrx_stock
        targets = []
        for t in args.tickers:
            try:
                name = pykrx_stock.get_market_ticker_name(t)
            except Exception:
                name = t
            targets.append((t, name))
    else:
        targets = get_kospi50_info()

    logger.info("적재 대상: %d개 종목, 최근 %d일, 페이지당 100건×%d", len(targets), args.days, args.max_pages)
    total = 0
    for i, (ticker, name) in enumerate(targets, 1):
        logger.info("[%d/%d] %s (%s)", i, len(targets), name, ticker)
        total += seed_for_ticker(ticker, name, args.days, args.max_pages)

    logger.info("전체 완료: %d건 저장", total)


if __name__ == "__main__":
    main()
