"""
뉴스 크롤링 + 랭킹 서비스 (네이버 금융 뉴스 기반)
"""
import json
import logging
import os
import re
from datetime import datetime
from difflib import SequenceMatcher
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

# 제목 유사도 임계값: 이 값 이상이면 동일 기사로 판단
TITLE_SIMILARITY_THRESHOLD = 0.85


def _dedup_items(items: list[NewsItem]) -> list[NewsItem]:
    """
    3단계 중복 제거:
      1. URL 동일
      2. (제목, 출처) 완전 일치
      3. 제목 유사도 >= TITLE_SIMILARITY_THRESHOLD (SequenceMatcher)

    네이버 Finance는 동일 기사를 URL 파라미터만 달리해 여러 번 노출하거나,
    동일 내용을 미세하게 다른 제목으로 반복 제공하는 경우가 있음.
    """
    seen_urls: set[str] = set()
    seen_title_source: set[tuple[str, str]] = set()
    unique_titles: list[str] = []
    deduped: list[NewsItem] = []

    for item in items:
        # 1단계: URL 중복
        if item.url in seen_urls:
            logger.debug("URL 중복 제거: %s", item.url)
            continue

        # 2단계: 제목+출처 완전 일치
        title_key = (item.title, item.source)
        if title_key in seen_title_source:
            logger.debug("제목+출처 중복 제거: %.60s", item.title)
            continue

        # 3단계: 제목 유사도
        if any(
            SequenceMatcher(None, item.title, t).ratio() >= TITLE_SIMILARITY_THRESHOLD
            for t in unique_titles
        ):
            logger.debug("제목 유사도 중복 제거: %.60s", item.title)
            continue

        seen_urls.add(item.url)
        seen_title_source.add(title_key)
        unique_titles.append(item.title)
        deduped.append(item)

    for idx, item in enumerate(deduped, 1):
        item.id = idx

    return deduped


async def fetch_news(ticker: str, page: int = 1, force: bool = False) -> list[NewsItem]:
    """
    종목 뉴스 반환. Redis 캐시(1시간) → 네이버 금융 크롤링 순으로 시도.

    crawl_all_news Celery 태스크가 1시간마다 page=1 캐시를 랭킹 결과로 갱신하므로
    대부분의 요청은 캐시에서 즉시 반환된다.
    page > 1 이거나 캐시 미스인 경우에만 실제 크롤링 수행.

    force=True 이면 캐시를 무시하고 항상 크롤링 (Celery 태스크 전용).
    force=True 시 캐싱은 하지 않음 — 태스크가 랭킹 후 직접 캐시에 저장한다.
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

    raw: list[NewsItem] = []
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

        raw.append(NewsItem(
            id=i + 1,
            title=title,
            source=source,
            published_at=_parse_date(date_str),
            url=url,
            sentiment="neutral",
            summary=None,
            category=_categorize(title),
        ))

    items = _dedup_items(raw)[:20]

    # force=False(API 캐시 미스)일 때만 원본을 캐시 — Celery 태스크는 랭킹 후 직접 저장
    if page == 1 and items and not force:
        set_news_cache(ticker, [i.model_dump() for i in items])

    return items


def _parse_date(raw: str) -> str:
    raw = raw.strip()
    for fmt in ("%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
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


_IRRELEVANT_TITLE_KEYWORDS = [
    "마스터즈", "챔피언십", "티샷", "그린", "버디", "보기",
    "골프", "축구", "야구", "농구", "리그", "우승", "시상식", "출전",
    "콘서트", "팬미팅", "드라마", "영화", "예능",
]


def _is_irrelevant_title(title: str) -> bool:
    return any(kw in title for kw in _IRRELEVANT_TITLE_KEYWORDS)


async def rank_news(items: list[NewsItem], company_name: Optional[str] = None) -> list[NewsItem]:
    """
    Claude로 (1) 주가 관련성, (2) 시장 중요도, (3) 감성을 평가.
    relevance <= 1 인 기사(스폰서 스포츠대회·행사·인물 동정 등)는 제거한다.
    ANTHROPIC_API_KEY 미설정·호출 실패 시 키워드 폴백.
    """
    if not items:
        return items

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rank_by_heuristic(items)

    try:
        import anthropic

        subject = f"'{company_name}' 종목" if company_name else "해당 종목"
        titles = "\n".join(f"{i + 1}. {item.title}" for i, item in enumerate(items))
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            messages=[{"role": "user", "content": (
                f"당신은 한국 주식 애널리스트입니다. 아래 뉴스 제목이 {subject}의 '주가'에 "
                "미칠 영향을 평가하세요.\n"
                "- relevance(0~5): 주가 영향 가능성. 기업명이 들어가도 스폰서 스포츠 대회·행사·"
                "시상식·인물 동정 등 주가와 무관하면 0~1점.\n"
                "- importance(0~10): 시장 중요도(실적·계약·규제·수급 등일수록 높음).\n"
                "- sentiment: positive | negative | neutral.\n\n"
                f"{titles}\n\n"
                "반드시 JSON 배열로만 응답(다른 텍스트 금지):\n"
                '[{"index": 1, "sentiment": "positive", "relevance": 4, "importance": 8}, ...]'
            )}],
        )
        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        analysis: list[dict] = json.loads(raw)
        meta: dict[int, tuple[str, int, int]] = {
            r["index"]: (
                r.get("sentiment", "neutral"),
                int(r.get("relevance", 3)),
                int(r.get("importance", 5)),
            )
            for r in analysis
        }

        ranked: list[tuple[int, int, NewsItem]] = []
        for i, item in enumerate(items, 1):
            sentiment, relevance, importance = meta.get(i, ("neutral", 3, 5))
            item.sentiment = sentiment
            if relevance <= 1:
                continue
            ranked.append((relevance, importance, item))

        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item for _, _, item in ranked]

    except Exception as e:
        logger.warning("LLM 감성 분석 실패, 키워드 폴백: %s", e)
        return _rank_by_heuristic(items)


def _rank_by_heuristic(items: list[NewsItem]) -> list[NewsItem]:
    """키워드 기반 감성 분석 + 중요도 정렬 (rank_news 폴백). 비금융 기사 1차 제거."""
    items = [i for i in items if not _is_irrelevant_title(i.title)]
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
