"""
주요 한국 언론사 RSS 피드 수집기

feedparser로 RSS/Atom 피드를 파싱하고 최근 window_minutes 이내 기사를 반환한다.
개별 피드 실패는 경고 로그만 남기고 건너뜀 — 전체 수집에 영향 없음.
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import requests as _requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 공통 데이터 타입 (naver_section_crawler, news_aggregator에서 재사용)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawArticle:
    url: str
    title: str
    summary: str
    published_at: str    # ISO 8601 UTC
    source: str          # 언론사명
    category: str        # 경제/정치/사회/세계/기타
    source_type: str     # "rss" | "section_crawl" | "naver_api"


# ─────────────────────────────────────────────────────────────────────────────
# RSS 피드 목록
# ─────────────────────────────────────────────────────────────────────────────

RSS_FEEDS: list[tuple[str, str]] = [
    # 연합뉴스 (소문자 /rss/ 경로 — /RSS/ 는 404)
    ("연합뉴스", "https://www.yna.co.kr/rss/politics.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/society.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/international.xml"),
    # 조선일보
    ("조선일보", "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml"),
    # 동아일보
    ("동아일보", "https://rss.donga.com/total.xml"),
    # 한겨레
    ("한겨레", "https://www.hani.co.kr/rss/"),
    # 경향신문
    ("경향신문", "https://www.khan.co.kr/rss/rssdata/total_news.xml"),
    # 매일경제
    ("매일경제", "https://www.mk.co.kr/rss/30000001/"),
    ("매일경제", "https://www.mk.co.kr/rss/40300001/"),
    # 한국경제 (feedparser 직접 파싱 시 User-Agent 차단 → requests 경유 필요)
    ("한국경제", "https://www.hankyung.com/feed/all-news"),
    # 머니투데이
    ("머니투데이", "https://rss.mt.co.kr/mt_news.xml"),
    # JTBC
    ("JTBC", "https://fs.jtbc.co.kr/RSS/newsflash.xml"),
    # Google News Korea — 전체 + 섹션 (NATION/BUSINESS/TECHNOLOGY topic 슬러그)
    ("Google뉴스", "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"),
    ("Google뉴스_정치", "https://news.google.com/rss/headlines/section/topic/NATION?hl=ko&gl=KR&ceid=KR:ko"),
    ("Google뉴스_경제", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko"),
    ("Google뉴스_기술", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=ko&gl=KR&ceid=KR:ko"),
]

_DEFAULT_WINDOW_MINUTES = 70  # 1시간 주기 태스크 + 여유 10분

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


# ─────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ─────────────────────────────────────────────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "경제": ["경제", "증시", "주가", "금리", "환율", "무역", "수출", "수입", "기업", "반도체",
             "배터리", "투자", "코스피", "코스닥", "gdp", "물가", "인플레"],
    "정치": ["정치", "국회", "대통령", "선거", "정부", "여당", "야당", "외교", "안보", "국방"],
    "사회": ["사회", "사건", "사고", "복지", "교육", "노동", "환경", "보건", "의료", "범죄"],
    "세계": ["세계", "국제", "미국", "중국", "일본", "유럽", "러시아", "외국", "글로벌"],
}


def _infer_category(source: str, title: str) -> str:
    title_l = title.lower()
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(k in title_l for k in kws):
            return cat
    # 피드 소스명 기반 후보
    if "경제" in source or "finance" in source.lower() or "mk" in source.lower():
        return "경제"
    if "정치" in source:
        return "정치"
    return "경제"  # 기본값: 주식 관련성이 높음


def _parse_pub_dt(entry) -> Optional[datetime]:
    if getattr(entry, "published_parsed", None):
        ts = _time.mktime(entry.published_parsed)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    raw = getattr(entry, "published", None)
    if raw:
        try:
            return parsedate_to_datetime(raw)
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 퍼블릭 API
# ─────────────────────────────────────────────────────────────────────────────

def collect(window_minutes: int = _DEFAULT_WINDOW_MINUTES) -> list[RawArticle]:
    """
    등록된 RSS 피드를 순회하여 최근 window_minutes 이내 기사를 반환한다.

    Parameters
    ----------
    window_minutes : 수집 대상 기사의 최대 경과 시간(분). 기본 70분.

    Returns
    -------
    list[RawArticle] — 중복 URL 제거된 기사 목록
    """
    try:
        import feedparser
    except ImportError:
        logger.error("feedparser 미설치 — pip install feedparser")
        return []

    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=window_minutes)
    articles: list[RawArticle] = []
    seen_urls: set[str] = set()
    feed_count = 0

    for source, feed_url in RSS_FEEDS:
        try:
            # requests 경유 파싱 — feedparser 직접 호출 시 User-Agent 차단되는 피드 대응
            try:
                resp = _requests.get(feed_url, headers=_FETCH_HEADERS, timeout=10)
                feed = feedparser.parse(resp.content)
            except Exception:
                feed = feedparser.parse(feed_url)
            count = 0
            for entry in feed.entries:
                url = (entry.get("link") or "").strip()
                title = (entry.get("title") or "").strip()
                if not url or not title or url in seen_urls:
                    continue

                pub_dt = _parse_pub_dt(entry)
                # 발행일 없으면 허용, 명시적으로 오래된 기사만 제외
                if pub_dt and pub_dt < cutoff:
                    continue

                pub_str = pub_dt.isoformat() if pub_dt else datetime.now(tz=timezone.utc).isoformat()
                summary = (entry.get("summary") or entry.get("description") or "").strip()

                seen_urls.add(url)
                articles.append(RawArticle(
                    url=url,
                    title=title,
                    summary=summary,
                    published_at=pub_str,
                    source=source,
                    category=_infer_category(source, title),
                    source_type="rss",
                ))
                count += 1

            if count:
                logger.debug("RSS [%s] %d건", source, count)
            feed_count += 1
        except Exception as e:
            logger.warning("RSS 피드 실패 [%s %s]: %s", source, feed_url, e)

    logger.info("RSS 수집 완료: %d건 (피드 %d/%d)", len(articles), feed_count, len(RSS_FEEDS))
    return articles
