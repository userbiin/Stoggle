# 뉴스 수집

import re
import html
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import httpx
import feedparser            # pip install feedparser
from bs4 import BeautifulSoup


KST = timezone(timedelta(hours=9))
FRESH_WINDOW = timedelta(hours=1)   # Celery 1시간 주기 → 최근 1시간 기사만
DEDUP_SIMILARITY = 0.85             # 제목 유사도 이 이상이면 교차 소스 중복으로 간주
UA = {"User-Agent": "Mozilla/5.0"}

# ─────────────────────────────────────────────────────────────
# 언론사 RSS 피드
# ⚠️ 아래 URL은 예시. 각 언론사 RSS 안내 페이지에서 실제 주소를 확인할 것.
#    (RSS 주소는 자주 바뀌므로 코드에 박기 전 반드시 한 번 fetch 테스트)
# ─────────────────────────────────────────────────────────────
RSS_FEEDS = {
    "연합뉴스_경제": "https://www.yna.co.kr/rss/economy.xml",
    "연합뉴스_산업": "https://www.yna.co.kr/rss/industry.xml",
    "한국경제":      "https://www.hankyung.com/feed/economy",
    "매일경제":      "https://www.mk.co.kr/rss/30100041/",
    # ... 주요 언론사 30~50개로 확장
}

# 네이버 뉴스 섹션 (sid)
NAVER_SECTIONS = {
    "정치": "100", "경제": "101", "사회": "102", "세계": "104",
}


# ─────────────────────────────────────────────────────────────
# 공통 유틸 (news_collector.py와 동일 — utils 모듈로 빼서 공유해도 됨)
# ─────────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")

def clean_text(s: str) -> str:
    if not s:
        return ""
    return html.unescape(_TAG_RE.sub("", s)).strip()


def _norm_title(t: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", t).lower()


def _trigrams(s: str) -> frozenset[str]:
    return frozenset(s[i:i+3] for i in range(len(s) - 2)) if len(s) >= 3 else frozenset({s})


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


# ─────────────────────────────────────────────────────────────
# [C-1] 정확 중복제거용 키 추출
# ─────────────────────────────────────────────────────────────
def canonical_key(url: str) -> str | None:
    if not url:
        return None
    # https://n.news.naver.com/article/023/0001234567 or .../mnews/article/...
    m = re.search(r"article/(?:mnews/)?(\d+)/(\d+)", url)
    if m:
        return f"naver:{m.group(1)}:{m.group(2)}"
    # 구형 쿼리스트링: ...&oid=023&aid=0001234567
    m_oid = re.search(r"[?&]oid=(\d+)", url)
    m_aid = re.search(r"[?&]aid=(\d+)", url)
    if m_oid and m_aid:
        return f"naver:{m_oid.group(1)}:{m_aid.group(1)}"
    # 언론사 원본 URL: 쿼리/프래그먼트 제거, host 정규화
    p = urlparse(url)
    host = p.netloc.lower().replace("www.", "")
    return f"{host}{p.path.rstrip('/')}"


# ─────────────────────────────────────────────────────────────
# [A] RSS 수집기
# ─────────────────────────────────────────────────────────────
async def fetch_rss(name: str, url: str) -> list[dict]:
    try:
        feed = await asyncio.to_thread(feedparser.parse, url)
    except Exception as e:
        print(f"[RSS:{name}] 파싱 실패: {e}")
        return []

    now = datetime.now(timezone.utc)
    out = []
    for e in feed.entries:
        pub = None
        if getattr(e, "published_parsed", None):
            # feedparser의 *_parsed는 UTC 기준 struct_time
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        # 최근 1시간 기사만 (pubDate 없으면 보수적으로 포함)
        if pub and (now - pub) > FRESH_WINDOW:
            continue
        out.append({
            "title": clean_text(e.get("title", "")),
            "link": e.get("link", ""),
            "pub_date": pub.astimezone(KST).isoformat() if pub else None,
            "source": name,
            "origin": "rss",
        })
    return out


async def collect_rss() -> list[dict]:
    results = await asyncio.gather(*[
        fetch_rss(name, url) for name, url in RSS_FEEDS.items()
    ])
    return [a for sub in results for a in sub]


# ─────────────────────────────────────────────────────────────
# [B] 섹션 크롤링기 (보조)
# ⚠️ 네이버 섹션 페이지는 JS 렌더링 + DOM이 자주 바뀐다.
#    아래 셀렉터는 현재 구조 기준이며, 깨지면 실제 페이지 보고 수정 필요.
#    RSS를 주 소스로 두고, 섹션은 best-effort 보강으로만 쓸 것.
# ─────────────────────────────────────────────────────────────
async def fetch_section(client: httpx.AsyncClient, category: str, sid: str) -> list[dict]:
    url = f"https://news.naver.com/section/{sid}"
    try:
        res = await client.get(url, headers=UA)
        res.raise_for_status()
    except Exception as e:
        print(f"[Section:{category}] 수집 실패: {e}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    out = []
    for a in soup.select("a[href*='/article/']"):
        href = a.get("href", "")
        title = clean_text(a.get_text())
        if not title or len(title) < 8:   # 썸네일/메뉴 링크 등 노이즈 제거
            continue
        out.append({
            "title": title,
            "link": href if href.startswith("http") else f"https://news.naver.com{href}",
            "pub_date": datetime.now(KST).isoformat(),  # 섹션 기사는 스크랩 시점 = 현재로 간주
            "source": "naver_section",
            "origin": "section",
            "category": category,
        })
    return out


async def collect_sections() -> list[dict]:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        results = await asyncio.gather(*[
            fetch_section(client, cat, sid) for cat, sid in NAVER_SECTIONS.items()
        ])
    return [a for sub in results for a in sub]


# ─────────────────────────────────────────────────────────────
# [C] 통합 중복제거 (2단계)
# ─────────────────────────────────────────────────────────────
def merge_and_dedup(articles: list[dict]) -> list[dict]:
    kept: list[dict] = []
    seen_keys: set[str] = set()
    kept_trigrams: list[frozenset] = []

    for a in articles:
        if not a.get("title") or not a.get("link"):
            continue

        # 1단계: 정확 키(oid+aid / 정규화 URL) 매칭
        key = canonical_key(a["link"])
        if key and key in seen_keys:
            continue

        # 2단계: trigram Jaccard 유사도 fallback (교차 소스 중복 잡기)
        # SequenceMatcher 대비 set 연산으로 처리 속도 ↑
        norm = _norm_title(a["title"])
        if not norm:
            continue
        tg = _trigrams(norm)
        if any(_jaccard(tg, k) >= DEDUP_SIMILARITY for k in kept_trigrams):
            continue

        if key:
            seen_keys.add(key)
        kept_trigrams.append(tg)
        kept.append(a)

    return kept


# ─────────────────────────────────────────────────────────────
# 진입점: 기존 collect_candidates()를 이걸로 교체
# ─────────────────────────────────────────────────────────────
async def collect_candidates() -> list[dict]:
    rss, section = await asyncio.gather(collect_rss(), collect_sections())
    print(f"RSS: {len(rss)}건 / 섹션: {len(section)}건")
    merged = merge_and_dedup(rss + section)
    print(f"통합 중복제거 후: {len(merged)}건")
    return merged


if __name__ == "__main__":
    cands = asyncio.run(collect_candidates())
    for c in cands[:10]:
        print(f"[{c['origin']}/{c['source']}] {c['title']}")