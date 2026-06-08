"""
네이버 뉴스 섹션 크롤러

섹션(정치/경제/사회/세계)별 최신 기사를 수집한다.

전략:
  1차: 네이버 뉴스 내부 JSON API 호출 (Network 탭에서 확인한 엔드포인트)
  2차: 정적 HTML 파싱 (BeautifulSoup fallback)

요청 간 delay_seconds 딜레이로 서버 부하를 줄인다.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from agents.rss_collector import RawArticle

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://news.naver.com",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "application/json, text/plain, */*",
}

# sid → (category_name, section_url)
_SECTIONS: dict[str, tuple[str, str]] = {
    "100": ("정치", "https://news.naver.com/section/100"),
    "101": ("경제", "https://news.naver.com/section/101"),
    "102": ("사회", "https://news.naver.com/section/102"),
    "104": ("세계", "https://news.naver.com/section/104"),
}

# 네이버 뉴스 내부 섹션 기사 목록 JSON API
# 브라우저 Network 탭에서 확인: XHR/Fetch 탭 → /api/exists/section 계열
_SECTION_JSON_URLS: list[str] = [
    "https://news.naver.com/api/exists/section/articles?sid={sid}&clusteringType=1&clusterArticleCount=1&pageSize=20",
    "https://news.naver.com/api/exists/section/headline?sid={sid}&size=20",
]

_OID_AID_RE = re.compile(r"oid=(\d+)[&;].*?aid=(\d+)|/article/(\d+)/(\d+)")


def _canonical_naver_url(url: str) -> Optional[str]:
    """네이버 기사 URL에서 oid+aid를 추출해 정규 URL을 반환한다."""
    m = _OID_AID_RE.search(url)
    if not m:
        return None
    oid = m.group(1) or m.group(3)
    aid = m.group(2) or m.group(4)
    return f"https://n.news.naver.com/article/{oid}/{aid}"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# 1차: 내부 JSON API
# ─────────────────────────────────────────────────────────────────────────────

def _try_json_api(sid: str, category: str) -> list[RawArticle]:
    """네이버 뉴스 내부 JSON API 시도. 실패하면 빈 리스트 반환."""
    articles: list[RawArticle] = []
    for tpl in _SECTION_JSON_URLS:
        url = tpl.format(sid=sid)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=8)
            if resp.status_code != 200:
                continue
            data = resp.json()

            # 응답 구조는 엔드포인트마다 다를 수 있으므로 공통 필드만 추출
            items = (
                data.get("articleList")
                or data.get("clusterList")
                or data.get("items")
                or []
            )
            if not items:
                continue

            for item in items:
                # 중첩 구조 평탄화 (clusterList의 경우 representArticle 하위)
                article = item.get("representArticle") or item
                title = article.get("title") or article.get("articleTitle") or ""
                link = article.get("articleUrl") or article.get("link") or ""
                if not title or not link:
                    continue

                canonical = _canonical_naver_url(link) or link
                press = article.get("officeName") or article.get("press") or "네이버"
                pub_dt = article.get("articleSendDate") or article.get("publishedAt") or _now_iso()

                articles.append(RawArticle(
                    url=canonical,
                    title=title.strip(),
                    summary=article.get("summary") or "",
                    published_at=pub_dt,
                    source=press,
                    category=category,
                    source_type="section_crawl",
                ))

            if articles:
                logger.debug("섹션 JSON API 성공 [%s/%s]: %d건", sid, category, len(articles))
                return articles
        except Exception as e:
            logger.debug("섹션 JSON API 실패 [%s %s]: %s", sid, url, e)

    return []


# ─────────────────────────────────────────────────────────────────────────────
# 2차: 정적 HTML 파싱 (fallback)
# ─────────────────────────────────────────────────────────────────────────────

# 네이버 뉴스 섹션 HTML에서 기사 링크를 찾는 셀렉터 후보 (우선순위 순)
_ARTICLE_SELECTORS = [
    "a.sa_text_title",          # 표준 섹션 리스트
    "a.cluster_text_headline",  # 클러스터 헤드라인
    ".cluster_head_news a",     # 클러스터 헤드
    ".news_tit a",              # 일부 섹션 변형
    "a[data-clk]",              # data-clk 속성 (광범위 매칭)
]


def _extract_press(tag) -> str:
    """기사 링크 태그 주변에서 언론사명을 추출한다."""
    for selector in [
        lambda t: t.find_parent("li") and t.find_parent("li").select_one(".press"),
        lambda t: t.find_parent("div") and t.find_parent("div").select_one(".press"),
        lambda t: t.find_next_sibling("span"),
    ]:
        try:
            el = selector(tag)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return text
        except Exception:
            pass
    return "네이버"


def _crawl_section_html(sid: str, category: str) -> list[RawArticle]:
    """BeautifulSoup으로 섹션 페이지 정적 HTML에서 기사를 추출한다."""
    section_url = _SECTIONS[sid][1]
    articles: list[RawArticle] = []
    seen: set[str] = set()
    try:
        resp = requests.get(section_url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for selector in _ARTICLE_SELECTORS:
            for a in soup.select(selector):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if not title or not href:
                    continue

                full_url = urljoin("https://news.naver.com", href)
                canonical = _canonical_naver_url(full_url) or full_url

                if canonical in seen:
                    continue
                seen.add(canonical)

                articles.append(RawArticle(
                    url=canonical,
                    title=title,
                    summary="",
                    published_at=_now_iso(),
                    source=_extract_press(a),
                    category=category,
                    source_type="section_crawl",
                ))

        logger.debug("섹션 HTML [%s/%s]: %d건", sid, category, len(articles))
    except Exception as e:
        logger.warning("섹션 HTML 크롤링 실패 [%s]: %s", section_url, e)

    return articles


# ─────────────────────────────────────────────────────────────────────────────
# 퍼블릭 API
# ─────────────────────────────────────────────────────────────────────────────

def collect(delay_seconds: float = 1.5) -> list[RawArticle]:
    """
    네이버 뉴스 섹션별 최신 기사를 수집한다.

    각 섹션마다 JSON API → HTML 순으로 시도하며,
    섹션 간 delay_seconds 딜레이를 준다.
    """
    articles: list[RawArticle] = []
    seen_urls: set[str] = set()

    for i, (sid, (category, _)) in enumerate(_SECTIONS.items()):
        if i > 0:
            time.sleep(delay_seconds)

        # 1차: JSON API
        section_articles = _try_json_api(sid, category)

        # 2차: HTML fallback
        if not section_articles:
            section_articles = _crawl_section_html(sid, category)

        for art in section_articles:
            if art.url not in seen_urls:
                seen_urls.add(art.url)
                articles.append(art)

    logger.info("네이버 섹션 크롤링 완료: %d건", len(articles))
    return articles
