"""
기사 URL을 받아 본문 추출 → LLM 요약 → news_cache 업데이트
"""
import os
import re
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# 본문 추출 대상 태그 (우선순위 순)
_BODY_SELECTORS = [
    "article",
    "[class*='article-body']",
    "[class*='article_body']",
    "[class*='news-content']",
    "[class*='news_content']",
    "[id*='articleBody']",
    "[id*='article-body']",
    "div.content",
    "div#content",
]

_SENTENCE_END_RE = re.compile(r"[.!?。]\s")


@dataclass
class SummaryResult:
    body: str
    summary: str
    quality_score: float


def _extract_body(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for selector in _BODY_SELECTORS:
        tag = soup.select_one(selector)
        if tag:
            return tag.get_text(separator=" ", strip=True)

    # fallback: <p> 태그 합산
    paragraphs = soup.find_all("p")
    return " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)


def _quality_score(body: str) -> float:
    """
    본문 길이와 문장 완성도 기반 품질 점수 (0.0~1.0)
    - 길이 점수: 300자 이상이면 만점
    - 문장 완성도: 마침표로 끝나는 문장 비율
    """
    if not body:
        return 0.0

    length_score = min(len(body) / 300, 1.0)

    sentences = re.split(r"[.!?。]\s*", body.strip())
    sentences = [s for s in sentences if s.strip()]
    if not sentences:
        return length_score * 0.5

    # 10자 이상인 문장을 완성된 문장으로 간주
    complete = sum(1 for s in sentences if len(s.strip()) >= 10)
    completeness_score = complete / len(sentences)

    return round(length_score * 0.5 + completeness_score * 0.5, 3)


async def _summarize(body: str) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "당신은 뉴스 편집자입니다. 주어진 기사 본문을 3문장으로 요약하세요. 핵심 사실만 담고, 의견이나 추측은 제외하세요.",
                },
                {
                    "role": "user",
                    "content": f"다음 기사를 3문장으로 요약해주세요:\n\n{body[:3000]}",
                },
            ],
            temperature=0.2,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("OpenAI 요약 실패: %s", e)
        return None


async def run(url: str) -> Optional[SummaryResult]:
    """
    기사 URL → 본문 추출 → 요약 → 품질 점수 반환
    품질 점수 0.3 미만이면 None 반환
    """
    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; StoogleBot/1.0)"},
        ) as client:
            res = await client.get(url)
            res.raise_for_status()
            html = res.text
    except Exception as e:
        logger.error("기사 fetch 실패 [%s]: %s", url, e)
        return None

    body = _extract_body(html)
    score = _quality_score(body)

    if score < 0.3:
        logger.info("품질 미달 (%.3f) — 건너뜀: %s", score, url)
        return None

    summary = await _summarize(body)
    if not summary:
        return None

    return SummaryResult(body=body, summary=summary, quality_score=score)


async def run_and_save(url: str) -> Optional[SummaryResult]:
    """
    요약 결과를 news_cache 테이블에 업데이트
    """
    result = await run(url)
    if not result:
        return None

    try:
        from models.db_models import SessionLocal, NewsCache

        db = SessionLocal()
        try:
            db.query(NewsCache).filter(NewsCache.url == url).update(
                {"summary": result.summary},
                synchronize_session=False,
            )
            db.commit()
            logger.info("요약 저장 완료 [score=%.3f]: %s", result.quality_score, url)
        finally:
            db.close()
    except Exception as e:
        logger.error("DB 업데이트 실패: %s", e)

    return result


if __name__ == "__main__":
    import asyncio
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://n.news.naver.com/article/001/0015000000"
    result = asyncio.run(run(test_url))
    if result:
        print(f"품질 점수: {result.quality_score}")
        print(f"요약:\n{result.summary}")
        print(f"본문 길이: {len(result.body)}자")
    else:
        print("요약 실패 또는 품질 미달")
