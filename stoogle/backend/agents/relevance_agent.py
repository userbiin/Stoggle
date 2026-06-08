"""
종목 관련도 판별 에이전트

3단 파이프라인:
  1단계 — Ollama 배치 필터 (공통 1회): 경제/기업 무관 기사 제거
  2단계 — 기사별 종목 매핑 (기사당 1회): 직·간접 관련 종목 추출
  3단계 — Ollama 개별 점수 (종목×기사): 4점 이상만 저장

흐름:
  batch_filter(articles)              →  filtered: list[Article]       (공통, 1회)
  map_articles_to_tickers(filtered)   →  { ticker: [Article, ...] }    (기사당 1회)
  run(ticker, mapped_articles)        →  list[ScoredArticle]            (종목별)

4점 미만은 폐기, 4점 이상만 반환.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from evaluation.observability import track_agent

load_dotenv()

logger = logging.getLogger(__name__)

# Ollama API 설정
_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
_OLLAMA_BASE_URL = _base if _base.endswith("/v1") else f"{_base}/v1"
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "exaone3.5:7.8b")
_OLLAMA_CONCURRENCY = 5   # 동시 API 호출 수 제한
_SCORE_THRESHOLD = 4       # 이 점수 미만은 폐기
_MAX_BATCH_SIZE = 800      # 배치 1회 최대 기사 수 (컨텍스트 한도 대응)

# 영문 등록명 → 한글 별칭 매핑 (레지스트리에 영문명으로 등록된 경우)
_KO_ALIAS: dict[str, str] = {
    "NAVER": "네이버",
    "POSCO": "포스코",
    "KT": "케이티",
    "LG": "엘지",
    "SK": "에스케이",
    "KB": "케이비",
    "NH": "엔에이치",
}

# 런타임 캐시
_TICKER_INFO: dict[str, tuple[str, str]] = {}   # ticker → (name, sector)
_NAME_TO_TICKER: dict[str, str] = {}            # 회사명 → ticker (앱 시작 1회)


# ---------------------------------------------------------------------------
# 데이터 타입
# ---------------------------------------------------------------------------

@dataclass
class Article:
    url: str
    title: str
    summary: str = ""
    news_cache_id: Optional[int] = None


@dataclass
class ScoredArticle:
    article: Article
    score: int


# ---------------------------------------------------------------------------
# 회사 정보 조회
# ---------------------------------------------------------------------------

def _get_company_info(ticker: str) -> tuple[str, str]:
    """종목코드 → (회사명, 섹터). DB → pykrx → 캐시 순으로 조회."""
    if ticker in _TICKER_INFO:
        return _TICKER_INFO[ticker]

    try:
        from models.db_models import SessionLocal, Company
        db = SessionLocal()
        try:
            row = db.query(Company).filter(Company.ticker == ticker).first()
            if row:
                result = (row.name, row.sector or "")
                _TICKER_INFO[ticker] = result
                return result
        finally:
            db.close()
    except Exception as e:
        logger.debug("DB 조회 실패: %s", e)

    try:
        from pykrx import stock as pykrx_stock
        name = pykrx_stock.get_market_ticker_name(ticker)
        if name:
            result = (name, "")
            _TICKER_INFO[ticker] = result
            return result
    except Exception as e:
        logger.debug("pykrx 조회 실패: %s", e)

    return (ticker, "")


def _build_name_to_ticker_map() -> dict[str, str]:
    """
    companies 테이블에서 { 회사명: ticker } 매핑을 구축 (앱 시작 후 1회 캐시).
    _KO_ALIAS 역방향도 포함하여 한글 별칭으로도 조회 가능하게 함.
    """
    global _NAME_TO_TICKER
    if _NAME_TO_TICKER:
        return _NAME_TO_TICKER

    result: dict[str, str] = {}
    try:
        from models.db_models import SessionLocal, Company
        db = SessionLocal()
        try:
            for row in db.query(Company).all():
                if row.name and row.ticker:
                    result[row.name] = row.ticker
        finally:
            db.close()
    except Exception as e:
        logger.warning("name→ticker 맵 구축 실패: %s", e)

    # _KO_ALIAS 역방향 보강 (예: "NAVER"→"035420" 가 있으면 "네이버"→"035420" 추가)
    for en_name, ko_name in _KO_ALIAS.items():
        if en_name in result:
            result[ko_name] = result[en_name]
        elif ko_name in result:
            result[en_name] = result[ko_name]

    _NAME_TO_TICKER = result
    logger.info("name→ticker 맵 구축 완료: %d개 종목", len(result))
    return result


# ---------------------------------------------------------------------------
# 1단계: Ollama 배치 필터 (공통 1회)
# ---------------------------------------------------------------------------

async def _batch_filter_once(articles: list[Article]) -> list[int]:
    """
    articles 목록에서 경제/기업/주식시장 관련 기사 인덱스(1-based) 반환.
    파싱 실패 시 빈 리스트 반환.
    """
    titles = "\n".join(f"{i+1}. {a.title}" for i, a in enumerate(articles))
    prompt = (
        "당신은 주식 뉴스 필터입니다.\n"
        "아래 기사 목록에서 기업·산업·주식시장에 관련된 기사의 번호만 반환하세요.\n"
        "스포츠, 연예, 사건사고, 날씨, 생활정보 등은 제외합니다.\n\n"
        f"{titles}\n\n"
        "반환 형식: [1, 3, 7, ...]\n"
        "다른 텍스트 없이 JSON 배열만 답하세요."
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key="ollama", base_url=_OLLAMA_BASE_URL)
        response = await client.chat.completions.create(
            model=_OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r"\[[\d,\s]*\]", raw)
        if not match:
            logger.warning("배치 필터 파싱 실패: %r", raw[:200])
            return []
        indices = [int(x) for x in re.findall(r"\d+", match.group())]
        return [i for i in indices if 1 <= i <= len(articles)]
    except Exception as e:
        logger.error("배치 필터 Ollama 호출 실패: %s", e)
        return []


async def batch_filter(articles: list[Article]) -> list[Article]:
    """
    전체 기사에서 경제/기업 관련 기사만 추출 (공통 1회 실행).
    800건 초과 시 자동 분할. Ollama 실패 시 원본 전체 반환 (폴백).
    """
    if not articles:
        return []

    if len(articles) <= _MAX_BATCH_SIZE:
        indices = await _batch_filter_once(articles)
    else:
        mid = len(articles) // 2
        ids_1 = await _batch_filter_once(articles[:mid])
        ids_2 = await _batch_filter_once(articles[mid:])
        indices = ids_1 + [i + mid for i in ids_2]

    if not indices:
        logger.warning("배치 필터 결과 없음 — 원본 %d건 폴백", len(articles))
        return articles

    filtered = [articles[i - 1] for i in indices]
    logger.info("1단계 배치 필터: %d → %d건", len(articles), len(filtered))
    return filtered


# ---------------------------------------------------------------------------
# 2단계: 기사별 종목 매핑
# ---------------------------------------------------------------------------

async def _map_article_once(article: Article, name_map: dict[str, str]) -> list[str]:
    """
    기사 제목/요약 → 직·간접 관련 KOSPI200 ticker 리스트.
    파싱 실패 또는 Ollama 오류 시 빈 리스트 반환.
    """
    prompt = (
        "당신은 주식 뉴스 분석가입니다.\n\n"
        "아래 기사가 직접 또는 간접적으로 영향을 미치는\n"
        "KOSPI200 종목명을 모두 반환하세요.\n\n"
        f"기사 제목: {article.title}\n"
        f"기사 요약: {article.summary or '없음'}\n\n"
        "판단 기준:\n"
        "- 직접 언급된 기업\n"
        "- 동일 산업 내 경쟁사/협력사 (예: 반도체 업황 → 삼성전자, SK하이닉스)\n"
        "- 공급망 상하류 기업 (예: 배터리 소재 → LG에너지솔루션)\n"
        "- 규제/정책 영향을 받는 기업 (예: 금리 인상 → 은행주)\n\n"
        "반환 형식: [\"삼성전자\", \"SK하이닉스\", ...]\n"
        "해당 종목이 없으면: []\n"
        "다른 텍스트 없이 JSON 배열만 답하세요."
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key="ollama", base_url=_OLLAMA_BASE_URL)
        response = await client.chat.completions.create(
            model=_OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=500,
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if not match:
            logger.warning("종목 매핑 파싱 실패: %r", raw[:200])
            return []
        names: list[str] = json.loads(match.group())
        tickers = [name_map[n] for n in names if n in name_map]
        return list(dict.fromkeys(tickers))  # 중복 제거, 순서 유지
    except Exception as e:
        logger.error("종목 매핑 Ollama 호출 실패 [%s]: %s", article.title[:40], e)
        return []


async def _map_with_semaphore(
    sem: asyncio.Semaphore,
    article: Article,
    name_map: dict[str, str],
) -> tuple[Article, list[str]]:
    async with sem:
        tickers = await _map_article_once(article, name_map)
        return (article, tickers)


async def map_articles_to_tickers(articles: list[Article]) -> dict[str, list[Article]]:
    """
    기사 목록 → { ticker: [Article, ...] } 매핑.

    각 기사에 대해 Ollama로 직·간접 관련 종목을 추출한다.
    병렬 5 (asyncio.Semaphore). name→ticker 변환은 DB companies 테이블 조회.
    """
    if not articles:
        return {}

    name_map = _build_name_to_ticker_map()
    if not name_map:
        logger.warning("name→ticker 맵이 비어 있음 — 종목 매핑 불가")
        return {}

    sem = asyncio.Semaphore(_OLLAMA_CONCURRENCY)
    tasks = [_map_with_semaphore(sem, a, name_map) for a in articles]
    pairs = await asyncio.gather(*tasks)

    ticker_map: dict[str, list[Article]] = {}
    for article, tickers in pairs:
        for t in tickers:
            ticker_map.setdefault(t, []).append(article)

    mapped_total = sum(len(v) for v in ticker_map.values())
    logger.info(
        "2단계 종목 매핑: %d건 기사 → %d개 종목, 총 %d건 배정",
        len(articles), len(ticker_map), mapped_total,
    )
    return ticker_map


# ---------------------------------------------------------------------------
# 3단계: Ollama 개별 점수
# ---------------------------------------------------------------------------

@track_agent("relevance_agent", "news_pipeline")
async def _score_with_ollama(
    ticker: str,
    company_name: str,
    article: Article,
) -> int:
    """Ollama EXAONE로 기사 관련도 점수 반환 (0~5). API 실패 시 0 반환."""
    prompt = (
        f"다음 기사가 {company_name}({ticker}) 종목과 얼마나 관련 있는지 평가하세요.\n\n"
        f"제목: {article.title}\n"
        f"요약: {article.summary or '없음'}\n\n"
        "평가 기준:\n"
        "5점: 해당 기업 직접 언급, 핵심 사업 관련\n"
        "4점: 해당 기업 간접 관련, 동일 산업 주요 이슈\n"
        "3점: 업종 전반 영향, 관련성 보통\n"
        "2점: 거시경제 이슈, 간접 영향\n"
        "1점: 관련성 낮음\n"
        "0점: 전혀 무관\n\n"
        "숫자 하나만 답하세요 (0~5)."
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key="ollama", base_url=_OLLAMA_BASE_URL)
        response = await client.chat.completions.create(
            model=_OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=5,
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r"[0-5]", raw)
        if not match:
            logger.warning("Ollama 응답 파싱 실패: %r", raw)
            return 0
        return int(match.group())
    except Exception as e:
        logger.error("Ollama API 호출 실패 [%s]: %s", article.url, e)
        return 0


async def _score_with_semaphore(
    sem: asyncio.Semaphore,
    ticker: str,
    company_name: str,
    article: Article,
) -> tuple[Article, int]:
    async with sem:
        score = await _score_with_ollama(ticker, company_name, article)
        return (article, score)


# ---------------------------------------------------------------------------
# 퍼블릭 API
# ---------------------------------------------------------------------------

async def run(
    ticker: str,
    articles: list[Article],
) -> list[ScoredArticle]:
    """
    기사 목록을 Ollama로 0~5점 평가하여 4점 이상만 점수 내림차순으로 반환.

    crawl_all_news: 종목별 Naver Finance 기사를 직접 전달
    crawl_category_news: map_articles_to_tickers() 결과를 전달
    """
    if not articles:
        return []

    company_name, _ = _get_company_info(ticker)

    sem = asyncio.Semaphore(_OLLAMA_CONCURRENCY)
    tasks = [_score_with_semaphore(sem, ticker, company_name, a) for a in articles]
    scored_pairs = await asyncio.gather(*tasks)

    results = [
        ScoredArticle(article=article, score=score)
        for article, score in scored_pairs
        if score >= _SCORE_THRESHOLD
    ]
    results.sort(key=lambda x: x.score, reverse=True)

    logger.info("[%s] %s — Ollama 점수: %d → %d건 (4점 이상)",
                ticker, company_name, len(articles), len(results))
    return results


# ---------------------------------------------------------------------------
# 직접 실행 (테스트)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample = [
        Article(url="https://example.com/1", title="반도체 업황 악화로 HBM 수요 둔화 우려", summary="메모리 재고 증가"),
        Article(url="https://example.com/2", title="국제 유가 하락세 지속", summary="중동 불안 완화로 원유 공급 증가"),
        Article(url="https://example.com/3", title="AI 반도체 시장 2030년까지 5배 성장 전망", summary="HBM 경쟁 심화"),
        Article(url="https://example.com/4", title="프로야구 삼성 라이온즈 홈런더비 우승", summary="스포츠 소식"),
    ]

    async def _test():
        # 1단계
        filtered = await batch_filter(sample)
        print(f"1단계 배치 필터: {len(sample)} → {len(filtered)}건")
        for a in filtered:
            print(f"  - {a.title}")

        # 2단계
        ticker_map = await map_articles_to_tickers(filtered)
        print(f"\n2단계 종목 매핑: {len(ticker_map)}개 종목")
        for t, arts in ticker_map.items():
            print(f"  [{t}] {[a.title[:30] for a in arts]}")

        # 3단계
        for t, arts in ticker_map.items():
            results = await run(t, arts)
            print(f"\n3단계 [{t}]: {len(results)}건 (4점 이상)")
            for r in results:
                print(f"  [{r.score}점] {r.article.title}")

    asyncio.run(_test())
