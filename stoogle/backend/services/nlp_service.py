"""
키워드 추출 + LLM 요약 서비스
"""
import os
import re
from collections import Counter
from typing import Optional

from models.schemas import Keyword

try:
    from anthropic import AsyncAnthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

# JVM을 konlpy보다 먼저 시작하여 Restricted Method 경고를 억제
try:
    import jpype
    if not jpype.isJVMStarted():
        jpype.startJVM(
            jpype.getDefaultJVMPath(),
            "--enable-native-access=ALL-UNNAMED",
            convertStrings=False,
        )
except Exception:
    pass

try:
    from konlpy.tag import Okt
    OKT_AVAILABLE = True
except Exception:
    OKT_AVAILABLE = False


# 주식 도메인 불용어 (일반 명사지만 인사이트 가치 없음)
_STOCK_STOPWORDS = {
    # 동작/상태 명사
    "흐름", "형성", "강화", "집중", "매수", "추천", "강세", "상승률",
    "움직임", "전망", "분석", "영향", "관련", "기반", "진행", "확대",
    "증가", "감소", "하락", "상승", "급등", "급락", "회복", "둔화",
    "지속", "유지", "개선", "악화", "부진", "호조", "부담", "우려",
    "기대", "가능", "예상", "전일", "오전", "오후", "이날", "최근",
    "현재", "올해", "내년", "지난해", "올해", "분기", "하반기", "상반기",
    # 너무 일반적인 명사
    "시장", "주가", "종목", "투자", "기업", "업체", "회사", "사업",
    "실적", "수익", "매출", "영업", "이익", "비용", "수요", "공급",
    "가격", "거래", "규모", "수준", "대비", "이상", "이하", "기준",
    "국내", "해외", "글로벌", "부문", "분야", "업종", "섹터",
}


def extract_keywords(texts: list[str], top_n: int = 15) -> list[Keyword]:
    combined = " ".join(texts)
    if not combined.strip():
        return []

    if OKT_AVAILABLE:
        try:
            okt = Okt()
            # 명사만, 2글자 이상, 불용어 제거
            nouns = [
                n for n in okt.nouns(combined)
                if len(n) >= 2 and n not in _STOCK_STOPWORDS
            ]
        except Exception:
            nouns = _regex_nouns(combined)
    else:
        nouns = _regex_nouns(combined)

    counter = Counter(nouns)
    
    # 1회만 등장한 단어 제거 (노이즈)
    counter = Counter({k: v for k, v in counter.items() if v >= 2})
    
    most_common = counter.most_common(top_n)
    if not most_common:
        return []

    max_count = most_common[0][1]
    return [
        Keyword(text=word, value=int(cnt / max_count * 80) + 20)
        for word, cnt in most_common
    ]


def _regex_nouns(text: str) -> list[str]:
    stopwords = {
        "있는", "없는", "이번", "지난", "위한", "통해", "대한", "관련",
        "이후", "위해", "으로", "에서", "부터", "까지", "라는", "이라",
    } | _STOCK_STOPWORDS
    tokens = re.findall(r"[가-힣]{2,}", text)
    return [t for t in tokens if t not in stopwords]



def _get_cached_article_summary(
    ticker: str,
    urls: Optional[list[str]] = None,
) -> Optional[str]:
    """
    NewsCache DB에서 summary_agent가 저장한 요약을 조회한다.
    urls 지정 시 해당 URL 중 최신 요약 반환, 없으면 ticker 기준 최신 요약 반환.
    DB 오류 시 None 반환 (non-blocking).
    """
    try:
        from models.db_models import NewsCache, SessionLocal

        db = SessionLocal()
        try:
            q = db.query(NewsCache.summary).filter(
                NewsCache.ticker == ticker,
                NewsCache.summary.isnot(None),
                NewsCache.summary != "",
            )
            if urls:
                q = q.filter(NewsCache.url.in_(urls))
            row = q.order_by(NewsCache.fetched_at.desc()).first()
            return row[0] if row else None
        finally:
            db.close()
    except Exception:
        return None


async def summarize_with_llm(
    ticker: str,
    company_name: str,
    news_titles: list[str],
    news_urls: Optional[list[str]] = None,
) -> Optional[str]:
    """
    뉴스 요약 생성. 3단계 우선순위:
      1. NewsCache DB — summary_agent가 저장한 기사 요약 (DB 우선)
      2. summary_agent.run() on-demand — news_urls 제공 시 실시간 요약
      3. Claude 직접 호출 — 헤드라인 기반 투자 인사이트 (fallback)
    """
    # 1단계: DB 캐시 (summary_agent Celery 배치 결과)
    cached = _get_cached_article_summary(ticker, news_urls)
    if cached:
        return cached

    # 2단계: summary_agent on-demand (URL 제공 시)
    if news_urls:
        from agents.summary_agent import run as summary_run
        for url in news_urls[:3]:
            try:
                result = await summary_run(url)
                if result and result.summary:
                    return result.summary
            except Exception:
                pass

    # 3단계: Claude 직접 호출 — 헤드라인 기반 인사이트 (기존 로직)
    if not ANTHROPIC_AVAILABLE or not os.getenv("ANTHROPIC_API_KEY"):
        return _fallback_summary(company_name, news_titles)

    titles_text = "\n".join(f"- {t}" for t in news_titles[:10])
    prompt = f"""다음은 {company_name}({ticker})의 최근 뉴스 헤드라인입니다:

{titles_text}

위 뉴스를 바탕으로 투자자 관점에서 2~3문장의 핵심 인사이트를 한국어로 작성하세요.
수치나 구체적인 내용을 포함하되, 투자 권유는 하지 마세요."""

    try:
        client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=30.0)
        response = await client.messages.create(
            model=os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        return response.content[0].text.strip() if response.content else None
    except Exception:
        return _fallback_summary(company_name, news_titles)


def _fallback_summary(company_name: str, titles: list[str]) -> str:
    if not titles:
        return f"{company_name}에 대한 최근 뉴스를 찾을 수 없습니다."
    return f"{company_name} 관련 최근 이슈: {titles[0]}"
