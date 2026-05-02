"""
키워드 추출 + LLM 요약 서비스
"""
import os
import re
from collections import Counter
from typing import Optional

from models.schemas import Keyword

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from konlpy.tag import Okt
    OKT_AVAILABLE = True
except Exception:
    OKT_AVAILABLE = False


def extract_keywords(texts: list[str], top_n: int = 15) -> list[Keyword]:
    """
    뉴스 텍스트에서 명사 키워드 추출.
    konlpy 미설치 시 간단한 정규식 fallback 사용.
    """
    combined = " ".join(texts)

    if OKT_AVAILABLE:
        try:
            okt = Okt()
            nouns = okt.nouns(combined)
            nouns = [n for n in nouns if len(n) >= 2]
        except Exception:
            nouns = _regex_nouns(combined)
    else:
        nouns = _regex_nouns(combined)

    counter = Counter(nouns)
    most_common = counter.most_common(top_n)
    if not most_common:
        return []

    max_count = most_common[0][1]
    return [
        Keyword(text=word, value=int(cnt / max_count * 80) + 10)
        for word, cnt in most_common
    ]


def _regex_nouns(text: str) -> list[str]:
    """
    konlpy 없이 한글 단어 단순 추출 (fallback)
    """
    stopwords = {"있는", "없는", "이번", "지난", "위한", "통해", "대한", "관련", "이후", "위해"}
    tokens = re.findall(r"[가-힣]{2,}", text)
    return [t for t in tokens if t not in stopwords]


async def summarize_with_llm(
    ticker: str,
    company_name: str,
    news_titles: list[str],
) -> Optional[str]:
    """
    OpenAI API로 뉴스 요약 생성
    """
    if not OPENAI_AVAILABLE or not os.getenv("OPENAI_API_KEY"):
        return _fallback_summary(company_name, news_titles)

    titles_text = "\n".join(f"- {t}" for t in news_titles[:10])
    prompt = f"""다음은 {company_name}({ticker})의 최근 뉴스 헤드라인입니다:

{titles_text}

위 뉴스를 바탕으로 투자자 관점에서 2~3문장의 핵심 인사이트를 한국어로 작성하세요.
수치나 구체적인 내용을 포함하되, 투자 권유는 하지 마세요."""

    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return _fallback_summary(company_name, news_titles)


def _fallback_summary(company_name: str, titles: list[str]) -> str:
    if not titles:
        return f"{company_name}에 대한 최근 뉴스를 찾을 수 없습니다."
    return f"{company_name} 관련 최근 이슈: {titles[0]}"
