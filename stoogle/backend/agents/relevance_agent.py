"""
종목 관련도 판별 에이전트

2단계 구조:
  1단계 — 규칙 기반 프리필터 (기업명·산업 키워드 매칭)
  2단계 — EXAONE 3.5 API 문맥 판별 (0~5점)

4점 미만은 폐기, 4점 이상만 반환.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# EXAONE API 설정
_EXAONE_BASE_URL = os.getenv("EXAONE_BASE_URL", "https://api.exaone.lgai.ai/v1")
_EXAONE_MODEL = os.getenv("EXAONE_MODEL", "EXAONE-3.5-7.8B-Instruct")
_EXAONE_CONCURRENCY = 5   # 동시 API 호출 수 제한
_SCORE_THRESHOLD = 4       # 이 점수 미만은 폐기

# ---------------------------------------------------------------------------
# 섹터별 산업 키워드
# ---------------------------------------------------------------------------

_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "반도체": ["반도체", "D램", "낸드", "HBM", "파운드리", "웨이퍼", "칩", "메모리"],
    "전자": ["전자", "스마트폰", "디스플레이", "OLED", "LCD", "가전"],
    "자동차": ["자동차", "전기차", "EV", "하이브리드", "배터리", "자율주행", "수소차"],
    "바이오": ["바이오", "제약", "신약", "임상", "FDA", "허가", "의약품", "백신"],
    "IT": ["소프트웨어", "플랫폼", "클라우드", "AI", "인공지능", "데이터센터", "검색"],
    "화학": ["화학", "배터리소재", "리튬", "양극재", "전해질", "석유화학"],
    "에너지": ["에너지", "석유", "가스", "태양광", "풍력", "원전"],
    "금융": ["금융", "은행", "보험", "증권", "금리", "대출"],
    "통신": ["통신", "5G", "네트워크", "인터넷", "미디어"],
}

# 자주 조회되는 종목 캐시 (pykrx 호출 절감)
_TICKER_INFO: dict[str, tuple[str, str]] = {
    "005930": ("삼성전자", "반도체"),
    "000660": ("SK하이닉스", "반도체"),
    "035420": ("NAVER", "IT"),
    "051910": ("LG화학", "화학"),
    "207940": ("삼성바이오로직스", "바이오"),
    "035720": ("카카오", "IT"),
    "066570": ("LG전자", "전자"),
    "005380": ("현대차", "자동차"),
    "000270": ("기아", "자동차"),
    "096770": ("SK이노베이션", "에너지"),
    "068270": ("셀트리온", "바이오"),
    "003550": ("LG", "IT"),
    "012330": ("현대모비스", "자동차"),
    "028260": ("삼성물산", "건설"),
    "000810": ("삼성화재", "금융"),
}


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
# 1단계: 규칙 기반 프리필터
# ---------------------------------------------------------------------------

def _get_company_info(ticker: str) -> tuple[str, str]:
    """종목코드 → (회사명, 섹터). DB → pykrx → 캐시 순으로 조회."""
    # 캐시 확인
    if ticker in _TICKER_INFO:
        return _TICKER_INFO[ticker]

    # DB 조회
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

    # pykrx fallback
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


def _build_keywords(company_name: str, sector: str) -> list[str]:
    """회사명 + 섹터 키워드 목록 생성."""
    keywords = [company_name]

    # 회사명 축약 (예: "삼성전자" → "삼성", "SK하이닉스" → "하이닉스")
    if len(company_name) > 3:
        keywords.append(company_name[:2])

    # 섹터 키워드 확장
    for sector_key, kws in _SECTOR_KEYWORDS.items():
        if sector_key in sector or sector_key in company_name:
            keywords.extend(kws)
            break

    return keywords


def _prefilter(article: Article, keywords: list[str]) -> bool:
    """1단계: 제목+요약에 키워드가 하나라도 포함되면 통과."""
    text = f"{article.title} {article.summary}".lower()
    return any(kw.lower() in text for kw in keywords)


# ---------------------------------------------------------------------------
# 2단계: EXAONE 3.5 문맥 판별
# ---------------------------------------------------------------------------

async def _score_with_exaone(
    ticker: str,
    company_name: str,
    article: Article,
) -> int:
    """EXAONE 3.5로 기사 관련도 점수 반환 (0~5). API 실패 시 0 반환."""
    api_key = os.getenv("EXAONE_API_KEY")
    if not api_key:
        logger.warning("EXAONE_API_KEY 미설정 — 스코어 0 반환")
        return 0

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
        from CLAUDE import AsyncCLAUDE
        client = AsyncCLAUDE(api_key=api_key, base_url=_EXAONE_BASE_URL)
        response = await client.chat.completions.create(
            model=_EXAONE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=5,
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r"[0-5]", raw)
        if not match:
            logger.warning("EXAONE 응답 파싱 실패: %r", raw)
            return 0
        return int(match.group())
    except Exception as e:
        logger.error("EXAONE API 호출 실패 [%s]: %s", article.url, e)
        return 0


async def _score_with_semaphore(
    sem: asyncio.Semaphore,
    ticker: str,
    company_name: str,
    article: Article,
) -> tuple[Article, int]:
    async with sem:
        score = await _score_with_exaone(ticker, company_name, article)
        return (article, score)


# ---------------------------------------------------------------------------
# 퍼블릭 API
# ---------------------------------------------------------------------------

async def run(ticker: str, articles: list[Article]) -> list[ScoredArticle]:
    """
    종목 관련 기사 판별 후 4점 이상 기사만 점수 내림차순으로 반환.

    Args:
        ticker:   종목코드 (예: "005930")
        articles: 판별 대상 기사 목록

    Returns:
        score >= 4인 ScoredArticle 목록 (점수 내림차순)
    """
    if not articles:
        return []

    company_name, sector = _get_company_info(ticker)
    keywords = _build_keywords(company_name, sector)
    logger.info("[%s] %s — 키워드: %s", ticker, company_name, keywords[:5])

    # 1단계: 프리필터
    candidates = [a for a in articles if _prefilter(a, keywords)]
    logger.info("1단계 프리필터: %d → %d건", len(articles), len(candidates))

    if not candidates:
        return []

    # 2단계: EXAONE 병렬 스코어링
    sem = asyncio.Semaphore(_EXAONE_CONCURRENCY)
    tasks = [_score_with_semaphore(sem, ticker, company_name, a) for a in candidates]
    scored_pairs = await asyncio.gather(*tasks)

    results = [
        ScoredArticle(article=article, score=score)
        for article, score in scored_pairs
        if score >= _SCORE_THRESHOLD
    ]
    results.sort(key=lambda x: x.score, reverse=True)

    logger.info(
        "2단계 EXAONE 필터: %d → %d건 (4점 이상)",
        len(candidates),
        len(results),
    )
    return results


# ---------------------------------------------------------------------------
# 직접 실행 (테스트)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample = [
        Article(
            url="https://example.com/1",
            title="삼성전자, 3분기 반도체 영업이익 10조 돌파",
            summary="HBM 수요 급증으로 실적 개선",
        ),
        Article(
            url="https://example.com/2",
            title="국제 유가 하락세 지속",
            summary="중동 불안 완화로 원유 공급 증가",
        ),
        Article(
            url="https://example.com/3",
            title="AI 반도체 시장 2030년까지 5배 성장 전망",
            summary="엔비디아·삼성 HBM 경쟁 심화",
        ),
    ]

    results = asyncio.run(run("005930", sample))
    print(f"\n결과: {len(results)}건")
    for r in results:
        print(f"  [{r.score}점] {r.article.title}")
