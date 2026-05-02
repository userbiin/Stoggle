"""
뉴스 크롤링 + 랭킹 서비스 (네이버 금융 뉴스 기반)
"""
import logging
import re
from datetime import datetime
from typing import Optional
import httpx
from bs4 import BeautifulSoup

from models.schemas import NewsItem

logger = logging.getLogger(__name__)

NAVER_NEWS_URL = "https://finance.naver.com/item/news_news.naver"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com",
}


async def fetch_news(ticker: str, page: int = 1, force: bool = False) -> list[NewsItem]:
    """
    종목 뉴스 반환. Redis 캐시(1시간) → 네이버 금융 크롤링 순으로 시도.

    crawl_all_news Celery 태스크가 1시간마다 page=1 캐시를 워밍업하므로
    대부분의 요청은 캐시에서 즉시 반환된다.
    page > 1 이거나 캐시 미스인 경우에만 실제 크롤링 수행.

    force=True 이면 캐시를 무시하고 항상 크롤링 (crawl_all_news 전용).
    """
    from services.cache_service import get_news_cache, set_news_cache

    # page=1 이고 강제 갱신이 아닌 경우 캐시 우선 조회
    if page == 1 and not force:
        cached = get_news_cache(ticker)
        if cached:
            return [NewsItem(**item) for item in cached]

    params = {"code": ticker, "page": page}
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
            res = await client.get(NAVER_NEWS_URL, params=params)
            res.raise_for_status()
    except Exception as e:
        logger.warning(f"뉴스 크롤링 실패 ({ticker}, page={page}): {e}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    rows = soup.select("table.type5 tr")

    items = []
    for i, row in enumerate(rows):
        a_tag = row.select_one("td.title a")
        info_td = row.select_one("td.info")
        date_td = row.select_one("td.date")

        if not a_tag:
            continue

        title = a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        url = f"https://finance.naver.com{href}" if href.startswith("/") else href
        source = info_td.get_text(strip=True) if info_td else ""
        date_str = date_td.get_text(strip=True) if date_td else ""

        items.append(NewsItem(
            id=i + 1,
            title=title,
            source=source,
            published_at=_parse_date(date_str),
            url=url,
            sentiment="neutral",
            summary=None,
            category=_categorize(title),
        ))

    items = items[:20]

    # page=1 크롤링 결과를 캐시에 저장 (다음 요청부터 캐시 hit)
    if page == 1 and items:
        set_news_cache(ticker, [i.model_dump() for i in items])

    return items


def _parse_date(raw: str) -> str:
    """
    네이버 날짜 문자열 → ISO 형식
    """
    raw = raw.strip()
    for fmt in ("%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return datetime.now().isoformat()


def _categorize(title: str) -> str:
    mapping = {
        "실적": ["실적", "영업이익", "매출", "분기", "흑자", "적자", "어닝"],
        "기술": ["기술", "개발", "특허", "AI", "반도체", "공정", "수율"],
        "분석": ["목표주가", "리포트", "전망", "분석", "투자의견"],
        "이슈": ["규제", "제재", "소송", "사고", "리콜", "논란"],
    }
    for category, keywords in mapping.items():
        if any(kw in title for kw in keywords):
            return category
    return "일반"


def rank_news(items: list[NewsItem]) -> list[NewsItem]:
    """
    감성 분석 후 중요도 순 정렬 (간단 규칙 기반)
    """
    positive_words = ["상승", "급등", "호실적", "흑자", "확정", "수혜", "개선", "달성", "돌파"]
    negative_words = ["하락", "급락", "부진", "적자", "우려", "리스크", "제재", "소송", "하향"]

    def score(item: NewsItem) -> int:
        s = 0
        for w in positive_words:
            if w in item.title:
                s += 1
        for w in negative_words:
            if w in item.title:
                s -= 1
        return s

    for item in items:
        s = score(item)
        if s > 0:
            item.sentiment = "positive"
        elif s < 0:
            item.sentiment = "negative"
        else:
            item.sentiment = "neutral"

    return sorted(items, key=lambda x: abs(score(x)), reverse=True)
