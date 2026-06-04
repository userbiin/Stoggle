"""
naver_news_crawler.py
─────────────────────────────────────────────────────────────
수집(news_sources) → 본문 fetch(httpx) → 종목 매칭(직접+섹터) → EXAONE/Ollama 채점

수정:
  - 본문 fetch를 httpx 비동기로 직접 처리 (trafilatura.fetch_url의 urllib3 풀 충돌 제거)
    → 'Connection pool is full' 경고 사라지고 본문이 실제로 채워짐
  - 섹터 매핑(SECTOR_TICKERS) 포함 → 종목명 없는 거시 뉴스도 후보 종목에 연결
  - 본문 성공 건수 디버그 출력
"""

import os
import re
import json
import asyncio
from datetime import datetime, timedelta

import httpx
import trafilatura

try:
    from .news_sources import collect_candidates   # 패키지로 임포트 시
except ImportError:
    from news_sources import collect_candidates    # 직접 실행 시

EXAONE_API_KEY = os.getenv("EXAONE_API_KEY")
EXAONE_BASE_URL = os.getenv("EXAONE_BASE_URL", "https://api.exaone.lgai.ai/v1")
EXAONE_MODEL = os.getenv("EXAONE_MODEL", "EXAONE-3.5-7.8B-Instruct")

_OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_URL = f"{_OLLAMA_BASE}/v1/chat/completions"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "exaone3.5:7.8b")

PASS_THRESHOLD = 4
BODY_CONCURRENCY = 20
UA = {"User-Agent": "Mozilla/5.0"}

# ─────────────────────────────────────────────────────────────
# 섹터 키워드 → 대표 종목코드 (필터 아님, 후보를 '추가'하는 도메인 지식)
# 종목명은 런타임에 pykrx name_to_ticker 역방향으로 해석. 자유롭게 확장.
# ─────────────────────────────────────────────────────────────
SECTOR_TICKERS = {
    "반도체":   ["005930", "000660", "042700", "000990"],   # 삼성전자, SK하이닉스, 한미반도체, DB하이텍
    "2차전지":  ["373220", "006400", "247540", "003670", "066970"],  # LG에너지솔루션, 삼성SDI, 에코프로비엠, 포스코퓨처엠, 엘앤에프
    "전기차":   ["373220", "006400", "005380", "000270"],   # LG에너지솔루션, 삼성SDI, 현대차, 기아
    "자동차":   ["005380", "000270", "012330"],             # 현대차, 기아, 현대모비스
    "바이오":   ["207940", "068270", "000100"],             # 삼성바이오로직스, 셀트리온, 유한양행
    "제약":     ["207940", "068270", "000100"],             # 삼성바이오로직스, 셀트리온, 유한양행
    "인터넷":   ["035420", "035720"],                       # NAVER, 카카오
    "플랫폼":   ["035420", "035720"],                       # NAVER, 카카오
    "조선":     ["329180", "010140", "009540"],             # HD현대중공업, 삼성중공업, 한화오션
    "방산":     ["012450", "079550", "064350"],             # 한화에어로스페이스, LIG넥스원, 현대로템
    "철강":     ["005490", "004020"],                       # POSCO홀딩스, 현대제철
    "화학":     ["051910", "011170"],                       # LG화학, 롯데케미칼
    "은행":     ["105560", "055550", "086790"],             # KB금융, 신한지주, 하나금융지주
    "금융":     ["105560", "055550", "086790"],             # KB금융, 신한지주, 하나금융지주
    "게임":     ["259960", "036570", "251270"],             # 크래프톤, 엔씨소프트, 넷마블
    "유통":     ["139480", "023530"],                       # 이마트, 롯데쇼핑
    "항공":     ["003490"],                                 # 대한항공
    "최저임금": ["139480", "023530"],                       # 이마트, 롯데쇼핑
}


def build_name_to_ticker() -> dict[str, str]:
    from pykrx import stock

    # 전체 종목 리스트 조회 시도 (최근 영업일 10일 내)
    used_date = None
    for back in range(10):
        d = (datetime.now() - timedelta(days=back)).strftime("%Y%m%d")
        try:
            if stock.get_market_ticker_list(d, market="KOSPI"):
                used_date = d
                break
        except Exception:
            continue

    if used_date:
        mapping = {}
        for market in ("KOSPI", "KOSDAQ"):
            for ticker in stock.get_market_ticker_list(used_date, market=market):
                name = stock.get_market_ticker_name(ticker)
                if len(name) >= 2:
                    mapping[name] = ticker
        print(f"종목 사전: {len(mapping)}개 (기준일 {used_date})")
        return mapping

    # KRX 전체 리스트 실패 → SECTOR_TICKERS 내 티커만 개별 조회
    print("KRX 전체 목록 조회 실패 — fallback: 섹터 티커 개별 조회")
    all_tickers = {t for tickers in SECTOR_TICKERS.values() for t in tickers}
    mapping = {}
    for ticker in all_tickers:
        try:
            name = stock.get_market_ticker_name(ticker)
            if name and len(name) >= 2:
                mapping[name] = ticker
        except Exception:
            continue
    if mapping:
        print(f"종목 사전(fallback): {len(mapping)}개")
        return mapping

    # 개별 조회도 실패 → 섹터 매핑 없이 파이프라인 계속
    print("종목 사전 구축 실패 — 섹터 확장 없이 진행")
    return {}


def resolve_sector_tickers(name_to_ticker: dict) -> dict:
    ticker_to_name = {v: k for k, v in name_to_ticker.items()}
    out = {}
    for sector, tickers in SECTOR_TICKERS.items():
        out[sector] = [(t, ticker_to_name[t]) for t in tickers if t in ticker_to_name]
    return out


def match_candidates(text: str, name_to_ticker: dict, sector_resolved: dict) -> list[dict]:
    found = {}
    for name in sorted(name_to_ticker, key=len, reverse=True):
        if name in text:
            found[name_to_ticker[name]] = name
    for sector, tickers in sector_resolved.items():
        if sector in text:
            for t, nm in tickers:
                found.setdefault(t, nm)
    return [{"ticker": t, "name": n} for t, n in found.items()]


# ─────────────────────────────────────────────────────────────
# 본문 fetch — httpx로 직접 다운로드, trafilatura는 파싱만
# ─────────────────────────────────────────────────────────────
async def fetch_body(client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str) -> str:
    async with sem:
        try:
            r = await client.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            html = r.text
        except Exception:
            return ""
    # extract는 CPU 파싱 → 스레드로 (네트워크 풀 충돌 없음)
    return await asyncio.to_thread(lambda: trafilatura.extract(html) or "")


async def fetch_all_bodies(candidates: list[dict]):
    sem = asyncio.Semaphore(BODY_CONCURRENCY)
    limits = httpx.Limits(max_connections=BODY_CONCURRENCY + 5)
    async with httpx.AsyncClient(follow_redirects=True, limits=limits) as client:
        bodies = await asyncio.gather(
            *[fetch_body(client, sem, a["link"]) for a in candidates]
        )
    for a, b in zip(candidates, bodies):
        a["body"] = b


# ─────────────────────────────────────────────────────────────
# 채점 (EXAONE API 또는 Ollama 로컬)
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
    text = text.strip().replace("```json", "").replace("```", "")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"score": 0, "direction": "중립", "reason": "parse_error"}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {"score": 0, "direction": "중립", "reason": "parse_error"}


async def score_article(
    client: httpx.AsyncClient, sem: asyncio.Semaphore,
    company_name: str, article: dict,
) -> dict:
    body = article.get("body", "")[:2000]
    user_msg = f"종목: {company_name}\n뉴스 제목: {article['title']}\n뉴스 본문: {body}"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *FEWSHOT,
                {"role": "user", "content": user_msg}]

    if EXAONE_API_KEY:
        url = f"{EXAONE_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {EXAONE_API_KEY}", "Content-Type": "application/json"}
        model = EXAONE_MODEL
    else:
        url = OLLAMA_URL
        headers = {"Authorization": "Bearer ollama", "Content-Type": "application/json"}
        model = OLLAMA_MODEL

    payload = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 200}
    async with sem:
        try:
            res = await client.post(url, headers=headers, json=payload)
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"]
            return _parse_json(content)
        except Exception as e:
            print(f"채점 실패 ({model}): {e}")
            return {"score": 0, "direction": "중립", "reason": "api_error"}


# ─────────────────────────────────────────────────────────────
# 오케스트레이션
# ─────────────────────────────────────────────────────────────
SCORE_CONCURRENCY = 10

async def run_pipeline() -> list[dict]:
    import time
    t0 = time.time()

    backend = f"EXAONE API ({EXAONE_MODEL})" if EXAONE_API_KEY else f"Ollama ({OLLAMA_MODEL})"
    print(f"채점 백엔드: {backend}")

    name_to_ticker = await asyncio.to_thread(build_name_to_ticker)
    sector_resolved = resolve_sector_tickers(name_to_ticker)

    candidates = await collect_candidates()
    print(f"수집/중복제거 후: {len(candidates)}건")

    await fetch_all_bodies(candidates)
    nonempty = sum(1 for a in candidates if a.get("body"))
    print(f"본문 fetch: {nonempty}/{len(candidates)}건 성공 ({time.time()-t0:.1f}s)")

    gated = []
    for a in candidates:
        text = a["title"] + " " + a.get("body", "")
        tickers = match_candidates(text, name_to_ticker, sector_resolved)
        if tickers:
            gated.append((a, tickers))
    n_pairs = sum(len(t) for _, t in gated)
    print(f"종목 매칭됨: {len(gated)}건 (채점 쌍 {n_pairs}개)")

    sem = asyncio.Semaphore(SCORE_CONCURRENCY)
    limits = httpx.Limits(max_connections=SCORE_CONCURRENCY + 2)
    async with httpx.AsyncClient(timeout=60, limits=limits) as client:
        tasks = [
            (a, tk, asyncio.ensure_future(score_article(client, sem, tk["name"], a)))
            for a, tickers in gated
            for tk in tickers
        ]
        results = await asyncio.gather(*[t for _, _, t in tasks], return_exceptions=True)

    passed = []
    for (a, tk, _), r in zip(tasks, results):
        if isinstance(r, Exception):
            continue
        if r["score"] >= PASS_THRESHOLD:
            passed.append({
                "ticker": tk["ticker"], "name": tk["name"],
                "title": a["title"], "url": a.get("link"),
                "impact_score": r["score"] / 5.0,
                "direction": r["direction"], "reason": r["reason"],
            })
    passed.sort(key=lambda x: x["impact_score"], reverse=True)
    print(f"최종 통과: {len(passed)}건 (총 {time.time()-t0:.1f}s)")
    return passed


if __name__ == "__main__":
    final = asyncio.run(run_pipeline())
    print(f"\n=== 최종 통과 뉴스 {len(final)}건 ===")
    for r in final[:15]:
        print(f"[{r['name']}/{r['ticker']}] {r['impact_score']:.2f} "
              f"({r['direction']}) {r['title']}")
        print(f"    근거: {r['reason']}")
