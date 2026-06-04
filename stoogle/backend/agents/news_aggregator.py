"""
뉴스 통합 수집기 + 중복 제거 + 프리필터

3개 소스(RSS / 네이버 섹션 크롤링 / 네이버 검색 API)를 결합하고
중복 제거 → 프리필터를 적용하여 하위 파이프라인에 전달할 기사 목록을 반환한다.

중복 제거 2단계:
  1. URL 정규화 — 네이버 oid+aid 추출, 쿼리파라미터 제거
  2. 제목 유사도 — 같은 source에서 90% 이상 유사 제목은 중복 처리

프리필터:
  - EXCLUDE_CATEGORIES / EXCLUDE_KEYWORDS 기반 규칙 필터
  - filter_config.py로 외부화 가능 (현재는 모듈 상수로 관리)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlparse, urlencode, parse_qs

import httpx

from agents.rss_collector import RawArticle

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 프리필터 설정 (수정이 필요하면 이 블록만 변경)
# ─────────────────────────────────────────────────────────────────────────────

EXCLUDE_CATEGORIES: set[str] = {
    "연예", "스포츠", "날씨", "운세", "라이프", "푸드", "여행", "패션", "게임",
}

EXCLUDE_KEYWORDS: list[str] = [
    # 연예
    "아이돌", "드라마", "영화", "배우", "가수", "오디션", "K-POP", "팬덤",
    # 스포츠
    "축구", "야구", "농구", "배구", "골프", "테니스", "올림픽", "월드컵", "프로야구", "KBO",
    # 날씨·기상
    "날씨", "기상청", "기온", "강수", "태풍", "폭염", "한파",
    # 운세
    "운세", "오늘의 운세", "별자리",
    # 라이프스타일
    "레시피", "맛집", "여행지", "패션", "뷰티", "인테리어",
    # 부고
    "별세", "부고", "타계", "사망", "장례",
    # 방송·예능
    "시청률", "예능", "방청", "출연진", "캐스팅",
]

# 제목 유사도 임계값: 같은 source에서 이 값 이상이면 중복
TITLE_SIMILARITY_THRESHOLD = 0.90

# 네이버 기사 oid+aid 추출 패턴
_OID_AID_RE = re.compile(r"oid=(\d+).*?aid=(\d+)|/article/(\d+)/(\d+)")

# 네이버 API 파라미터
_NAVER_API_URL = "https://openapi.naver.com/v1/search/news.json"
_NAVER_CATEGORIES = {
    "경제": ["경제", "금리", "환율", "무역", "수출", "반도체", "코스피"],
    "정치": ["정치", "국회", "대통령", "선거"],
    "사회": ["사회", "복지", "교육", "노동"],
}


# ─────────────────────────────────────────────────────────────────────────────
# URL 정규화
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    """
    중복 판단을 위한 정규 URL을 반환한다.
    - 네이버 기사: oid+aid 조합으로 고정
    - 그 외: 쿼리파라미터 제거 + 소문자 scheme/host
    """
    m = _OID_AID_RE.search(url)
    if m:
        oid = m.group(1) or m.group(3)
        aid = m.group(2) or m.group(4)
        return f"naver:{oid}/{aid}"

    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


# ─────────────────────────────────────────────────────────────────────────────
# 프리필터
# ─────────────────────────────────────────────────────────────────────────────

def _is_filtered(article: RawArticle) -> bool:
    """True이면 파이프라인에서 제외한다."""
    if article.category in EXCLUDE_CATEGORIES:
        return True
    title_l = article.title.lower()
    return any(kw.lower() in title_l for kw in EXCLUDE_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────────
# 중복 제거
# ─────────────────────────────────────────────────────────────────────────────

def _dedup(articles: list[RawArticle]) -> list[RawArticle]:
    """
    2단계 중복 제거:
      1. URL 정규화 기반 exact 중복
      2. 소스 무관 제목 유사도 >= TITLE_SIMILARITY_THRESHOLD (크로스소스 중복 포함)
    """
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    unique: list[RawArticle] = []

    for art in articles:
        norm = _normalize_url(art.url)
        if norm in seen_urls:
            continue

        if any(
            SequenceMatcher(None, art.title, t).ratio() >= TITLE_SIMILARITY_THRESHOLD
            for t in seen_titles
        ):
            continue

        seen_urls.add(norm)
        seen_titles.append(art.title)
        unique.append(art)

    return unique


# ─────────────────────────────────────────────────────────────────────────────
# 소스 3: 네이버 검색 API (보조)
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_naver_api(category: str, query: str, display: int = 20) -> list[RawArticle]:
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _NAVER_API_URL,
                headers={
                    "X-Naver-Client-Id": client_id,
                    "X-Naver-Client-Secret": client_secret,
                },
                params={"query": query, "display": display, "sort": "date"},
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])

        def _strip(text: str) -> str:
            import html as _html
            return re.sub(r"<[^>]+>", "", _html.unescape(text or "")).strip()

        articles = []
        for item in items:
            url = item.get("link", "").strip()
            title = _strip(item.get("title", ""))
            if not url or not title:
                continue
            articles.append(RawArticle(
                url=url,
                title=title,
                summary=_strip(item.get("description", "")),
                published_at=item.get("pubDate", ""),
                source="네이버검색",
                category=category,
                source_type="naver_api",
            ))
        return articles
    except Exception as e:
        logger.warning("네이버 API 실패 [%s/%s]: %s", category, query, e)
        return []


async def _collect_naver_api() -> list[RawArticle]:
    sem = asyncio.Semaphore(3)  # 네이버 API rate limit(초당 10건) 대응

    async def _limited(cat: str, kw: str) -> list[RawArticle]:
        async with sem:
            result = await _fetch_naver_api(cat, kw)
            await asyncio.sleep(0.15)
            return result

    tasks = [
        _limited(cat, kw)
        for cat, kws in _NAVER_CATEGORIES.items()
        for kw in kws
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    articles: list[RawArticle] = []
    for r in results:
        if isinstance(r, list):
            articles.extend(r)
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# 퍼블릭 API
# ─────────────────────────────────────────────────────────────────────────────

def aggregate(window_minutes: int = 70) -> list[RawArticle]:
    """
    3개 소스를 결합하고 중복 제거 + 프리필터를 적용한 기사 목록을 반환한다.

    우선순위: RSS → 섹션 크롤링 → 네이버 API(보조)
    RSS·섹션이 먼저 들어와 있으면 네이버 API 중복이 dedup 단계에서 제거된다.

    Parameters
    ----------
    window_minutes : RSS 수집 시간 윈도우 (분)

    Returns
    -------
    list[RawArticle] — 중복 제거 + 필터링된 기사
    """
    from agents.rss_collector import collect as rss_collect
    from agents.naver_section_crawler import collect as section_collect

    all_articles: list[RawArticle] = []

    # 소스 1: RSS
    try:
        rss_articles = rss_collect(window_minutes=window_minutes)
        all_articles.extend(rss_articles)
        logger.info("[소스1/RSS] %d건", len(rss_articles))
    except Exception as e:
        logger.error("[소스1/RSS] 수집 실패: %s", e)

    # 소스 2: 네이버 섹션 크롤링
    try:
        section_articles = section_collect()
        all_articles.extend(section_articles)
        logger.info("[소스2/섹션] %d건", len(section_articles))
    except Exception as e:
        logger.error("[소스2/섹션] 수집 실패: %s", e)

    # 소스 3: 네이버 API (보조, 키 없으면 자동 skip)
    try:
        naver_articles = asyncio.run(_collect_naver_api())
        all_articles.extend(naver_articles)
        logger.info("[소스3/네이버API] %d건", len(naver_articles))
    except Exception as e:
        logger.error("[소스3/네이버API] 수집 실패: %s", e)

    before_dedup = len(all_articles)

    # 프리필터
    filtered = [a for a in all_articles if not _is_filtered(a)]
    after_filter = len(filtered)

    # 중복 제거
    unique = _dedup(filtered)

    logger.info(
        "뉴스 통합 완료 — 수집 %d건 → 필터 후 %d건 → 중복제거 후 %d건",
        before_dedup, after_filter, len(unique),
    )
    return unique
