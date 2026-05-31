"""
news_collector.py
─────────────────────────────────────────────────────────────
Stoogle 뉴스 수집 + 2단계 필터 파이프라인

설계 원칙: 싼 필터 → 비싼 필터 (cost & noise 둘 다 줄이기)

    [1] 수집 (메타데이터만)            ── 싸다
    [2] HTML 정제 + 중복 제거          ── 싸다
    [3] 규칙 프리필터                  ── 싸다 (종목명 매칭 + 사건 키워드)
         └ 통과 못 하면 여기서 폐기 (조건부 게이트)
    [4] 본문 fetch                     ── 중간 (통과한 것만)
    [5] EXAONE 채점 (0~5)              ── 비싸다 (통과한 것만)
         └ 4점 이상만 최종 통과
"""

import os
import re
import html
import json
import asyncio
from difflib import SequenceMatcher

import httpx

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
NAVER_API_URL = "https://openapi.naver.com/v1/search/news.json"

EXAONE_API_KEY = os.getenv("EXAONE_API_KEY")
EXAONE_BASE_URL = os.getenv("EXAONE_BASE_URL", "https://api.exaone.lgai.ai/v1")
EXAONE_MODEL = os.getenv("EXAONE_MODEL", "EXAONE-3.5-7.8B-Instruct")

# ⚠️ "경제", "사회" 같은 통짜 단어는 거의 모든 뉴스에 매칭됨 → 제거.
#    분야명이 아니라 "사건성(event-driven)" 키워드를 써야 신호 대 잡음비가 올라간다.
CATEGORIES = {
    "정치": ["수출 규제", "관세", "제재", "정부 지원금", "보조금", "국산화"],
    "사회": ["최저임금", "파업", "리콜", "중대재해", "집단소송"],
    "경제": ["금리 인상", "금리 인하", "유상증자", "무상증자", "실적 발표",
             "공급 계약", "수주", "인수합병", "감산", "증설"],
}

# 프리필터용 사건 키워드 (주가를 실제로 움직이는 표현들)
EVENT_KEYWORDS = [
    "실적", "영업이익", "적자", "흑자", "수주", "계약", "공급", "리콜",
    "규제", "제재", "관세", "증자", "감자", "인수", "합병", "분할",
    "자사주", "배당", "상장폐지", "감산", "증설", "투자", "소송", "특허",
    "승인", "허가", "파업", "급등", "급락", "목표주가",
]

PASS_THRESHOLD = 4          # EXAONE 점수 4점 이상만 통과
DEDUP_SIMILARITY = 0.82     # 제목 유사도 이 이상이면 중복으로 간주


# ─────────────────────────────────────────────────────────────
# [2] 정제 유틸
# ─────────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")

def clean_text(s: str) -> str:
    """네이버 API가 넣어주는 <b> 태그, HTML 엔티티(&quot; 등) 제거."""
    if not s:
        return ""
    s = _TAG_RE.sub("", s)        # <b>, </b> 등 제거
    s = html.unescape(s)          # &quot; &amp; &lt; → " & <
    return s.strip()


def _norm_title(t: str) -> str:
    """중복 비교용 정규화: 한글/영문/숫자만 남기고 소문자화."""
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", t).lower()


# ─────────────────────────────────────────────────────────────
# [3] 종목 매칭 사전 (KRX 종목명 → 종목코드)
# ─────────────────────────────────────────────────────────────
def build_name_to_ticker() -> dict[str, str]:
    """
    pykrx로 KRX 전 종목 {종목명: 종목코드} 사전 구축.
    실제 서비스에서는 매일 1회 캐싱해두고 재사용할 것.
    """
    from pykrx import stock
    mapping = {}
    for market in ("KOSPI", "KOSDAQ"):
        for ticker in stock.get_market_ticker_list(market=market):
            name = stock.get_market_ticker_name(ticker)
            # 너무 짧은 종목명(1글자)은 오탐이 많아 제외
            if len(name) >= 2:
                mapping[name] = ticker
    return mapping


def match_tickers(text: str, name_to_ticker: dict[str, str]) -> list[dict]:
    """
    본문/제목에서 종목명이 '그대로' 등장하는 경우만 후보로 잡는다.
    긴 이름부터 매칭해서 부분 문자열 오탐을 줄인다.
    (대규모로 가면 Aho-Corasick / pgvector 임베딩 매칭으로 교체 권장)
    """
    found = {}
    for name in sorted(name_to_ticker, key=len, reverse=True):
        if name in text:
            found[name_to_ticker[name]] = name
    return [{"ticker": t, "name": n} for t, n in found.items()]


# ─────────────────────────────────────────────────────────────
# [1] 수집
# ─────────────────────────────────────────────────────────────
async def fetch_category_news(category: str, query: str, display: int = 100) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": query, "display": min(display, 100), "sort": "date"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(NAVER_API_URL, headers=headers, params=params)
            res.raise_for_status()
            items = res.json().get("items", [])
            for item in items:
                item["category"] = category
                item["title"] = clean_text(item.get("title", ""))
                item["description"] = clean_text(item.get("description", ""))
            return items
    except Exception as e:
        print(f"[{category}] 뉴스 수집 실패: {e}")
        return []


async def collect_candidates() -> list[dict]:
    """수집 + 중복 제거. 제목 완전일치가 아니라 유사도 기반으로 거른다."""
    tasks = [
        fetch_category_news(cat, kw)
        for cat, kws in CATEGORIES.items()
        for kw in kws
    ]
    results = await asyncio.gather(*tasks)

    kept: list[dict] = []
    kept_norms: list[str] = []
    for items in results:
        for item in items:
            norm = _norm_title(item["title"])
            if not norm:
                continue
            # 이미 담긴 제목들과 유사도 비교 (언론사마다 미세하게 다른 제목 제거)
            is_dup = any(
                SequenceMatcher(None, norm, k).ratio() >= DEDUP_SIMILARITY
                for k in kept_norms
            )
            if not is_dup:
                kept.append(item)
                kept_norms.append(norm)
    return kept


# ─────────────────────────────────────────────────────────────
# [3] 규칙 프리필터 (EXAONE 앞에서 공짜로 거르기 = 조건부 게이트)
# ─────────────────────────────────────────────────────────────
def passes_prefilter(article: dict, name_to_ticker: dict[str, str]) -> list[dict]:
    """
    종목명이 매칭되고 AND 사건 키워드가 하나라도 있어야 통과.
    통과 시 후보 종목 리스트를 반환, 아니면 빈 리스트(= EXAONE 호출 안 함).
    """
    text = article["title"] + " " + article.get("body", article.get("description", ""))
    matched = match_tickers(text, name_to_ticker)
    has_event = any(kw in text for kw in EVENT_KEYWORDS)
    return matched if (matched and has_event) else []


# ─────────────────────────────────────────────────────────────
# [4] 본문 fetch (프리필터 통과한 것만)
# ─────────────────────────────────────────────────────────────
async def fetch_article_body(url: str) -> str:
    """
    원문 본문 추출. 네이버 검색 API는 본문(content)을 안 주므로 직접 가져와야 함.
    여기서는 간단 버전. 견고하게 하려면 trafilatura 사용 권장:
        import trafilatura
        return trafilatura.extract(trafilatura.fetch_url(url)) or ""
    또는 프로젝트에 이미 있는 원문 추출 에이전트(summary_agent)를 재사용할 것.
    """
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            res = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(res.text, "html.parser")
            # 네이버 뉴스 본문 영역 후보
            node = soup.select_one("#dic_area") or soup.select_one("#newsct_article")
            if node:
                return clean_text(node.get_text(separator=" "))
            # fallback: 가장 긴 <p> 묶음
            ps = sorted((p.get_text() for p in soup.find_all("p")), key=len, reverse=True)
            return clean_text(" ".join(ps[:5]))
    except Exception as e:
        print(f"본문 추출 실패 {url}: {e}")
        return ""


# ─────────────────────────────────────────────────────────────
# [5] EXAONE 채점 (0~5) — 루브릭 + few-shot이 품질의 핵심
# ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """너는 한국 주식시장 애널리스트다.
주어진 뉴스가 특정 종목의 '주가'에 실질적 영향을 줄 가능성을 0~5점으로 평가한다.

[채점 기준]
0 = 해당 종목과 무관
1 = 언급되나 주가와 무관 (인사/동정/일반 홍보)
2 = 간접적이거나 장기적 영향 가능성만 있음
3 = 영향 가능하나 불확실하거나 이미 시장에 알려진 사실
4 = 주가에 영향 줄 구체적 사건 (실적, 대형 계약, 규제, 수급 변화)
5 = 즉각적이고 강한 호재/악재 (서프라이즈성 사건)

반드시 아래 JSON 형식만 출력한다. 설명·서론·코드블록 금지.
{"score": <0~5 정수>, "direction": "상승|하락|중립", "reason": "기사 근거 한 문장"}"""

# few-shot: 작은 모델일수록 예시 한두 개로 채점 일관성이 크게 올라간다.
FEWSHOT = [
    {"role": "user", "content":
        '종목: 한미반도체\n뉴스: 미국이 대중 반도체 장비 수출 규제를 강화한다고 발표했다.'},
    {"role": "assistant", "content":
        '{"score": 4, "direction": "하락", "reason": "장비 수출 규제 강화는 반도체 장비사 매출에 직접 영향을 준다."}'},
    {"role": "user", "content":
        '종목: 삼성전자\n뉴스: 삼성전자 사장이 사내 체육대회에 참석해 직원들을 격려했다.'},
    {"role": "assistant", "content":
        '{"score": 1, "direction": "중립", "reason": "사내 행사 참석은 주가와 무관한 동정 기사다."}'},
]


def _parse_json(text: str) -> dict:
    """모델이 코드블록이나 잡설을 붙여도 JSON만 안전하게 추출."""
    text = text.strip().replace("```json", "").replace("```", "")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"score": 0, "direction": "중립", "reason": "parse_error"}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {"score": 0, "direction": "중립", "reason": "parse_error"}


async def score_with_exaone(company_name: str, article: dict) -> dict:
    """EXAONE으로 (기사, 종목) 쌍의 영향 점수 0~5를 매긴다."""
    body = article.get("body", article.get("description", ""))[:2000]  # 토큰 절약
    user_msg = f"종목: {company_name}\n뉴스 제목: {article['title']}\n뉴스 본문: {body}"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *FEWSHOT,
                {"role": "user", "content": user_msg}]
    payload = {
        "model": EXAONE_MODEL,
        "messages": messages,
        "temperature": 0.1,   # 채점은 일관성이 중요 → 낮게
        "max_tokens": 200,
    }
    headers = {"Authorization": f"Bearer {EXAONE_API_KEY}",
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(f"{EXAONE_BASE_URL}/chat/completions",
                                    headers=headers, json=payload)
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
            return _parse_json(content)
    except Exception as e:
        print(f"EXAONE 채점 실패: {e}")
        return {"score": 0, "direction": "중립", "reason": "api_error"}


# ─────────────────────────────────────────────────────────────
# 오케스트레이션
# ─────────────────────────────────────────────────────────────
async def run_pipeline() -> list[dict]:
    name_to_ticker = build_name_to_ticker()

    # [1][2] 수집 + 중복 제거
    candidates = await collect_candidates()
    print(f"수집/중복제거 후: {len(candidates)}건")

    # [3] 규칙 프리필터 — EXAONE 갈 가치가 있는 기사만 추림
    gated = []
    for art in candidates:
        tickers = passes_prefilter(art, name_to_ticker)
        if tickers:
            gated.append((art, tickers))
    print(f"프리필터 통과: {len(gated)}건 (EXAONE 호출 대상)")

    # [4] 통과한 기사만 본문 fetch
    await asyncio.gather(*[
        _attach_body(art) for art, _ in gated
    ])

    # [5] EXAONE 채점 → 4점 이상만 최종 통과
    passed = []
    for art, tickers in gated:
        for tk in tickers:
            result = await score_with_exaone(tk["name"], art)
            if result["score"] >= PASS_THRESHOLD:
                passed.append({
                    "ticker": tk["ticker"],
                    "name": tk["name"],
                    "title": art["title"],
                    "url": art.get("link"),
                    "impact_score": result["score"] / 5.0,  # 0~1 정규화
                    "direction": result["direction"],
                    "reason": result["reason"],
                })

    # 종목별로 점수 높은 순 정렬
    passed.sort(key=lambda x: x["impact_score"], reverse=True)
    return passed


async def _attach_body(art: dict):
    art["body"] = await fetch_article_body(art["link"])


if __name__ == "__main__":
    final = asyncio.run(run_pipeline())
    print(f"\n최종 통과 뉴스: {len(final)}건")
    for r in final[:10]:
        print(f"[{r['name']}/{r['ticker']}] {r['impact_score']:.2f} "
              f"({r['direction']}) {r['title']}")
        print(f"    근거: {r['reason']}")