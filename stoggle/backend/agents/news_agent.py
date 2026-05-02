"""
LangChain 기반 뉴스 에이전트

두 가지 기능:
  1. run_news_analysis()   — 종목 뉴스 요약 (기존)
  2. run_impact_analysis() — 뉴스 기반 관계사 주가 영향 판단 (신규)
"""
import json
import os
from typing import Optional

try:
    from langchain_openai import ChatOpenAI
    from langchain.agents import AgentExecutor, create_openai_functions_agent
    from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain.tools import tool
    from openai import AsyncOpenAI
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

from services.news_service import fetch_news, rank_news
import asyncio


# ─────────────────────────────────────────────────────────────────────────────
# LangChain 도구 정의
# ─────────────────────────────────────────────────────────────────────────────

@tool
def fetch_stock_news(ticker: str) -> str:
    """주식 종목 코드를 받아 최신 뉴스를 가져옵니다."""
    # asyncio.run()은 이미 실행 중인 이벤트 루프에서 호출 불가.
    # nest_asyncio 또는 새 루프를 생성해 안전하게 실행한다.
    import nest_asyncio
    nest_asyncio.apply()
    loop = asyncio.get_event_loop()
    items = loop.run_until_complete(fetch_news(ticker))
    ranked = rank_news(items)
    return "\n".join(
        f"[{i.sentiment}] {i.title} ({i.source})"
        for i in ranked[:5]
    )


@tool
def analyze_sentiment(text: str) -> str:
    """뉴스 텍스트의 투자 관점 감성을 분석합니다."""
    positive_words = ["상승", "급등", "호실적", "흑자", "확정", "수혜"]
    negative_words = ["하락", "급락", "부진", "적자", "우려", "제재"]

    pos = sum(1 for w in positive_words if w in text)
    neg = sum(1 for w in negative_words if w in text)

    if pos > neg:
        return f"긍정적 ({pos}개 긍정 키워드)"
    elif neg > pos:
        return f"부정적 ({neg}개 부정 키워드)"
    return "중립적"


# ─────────────────────────────────────────────────────────────────────────────
# 뉴스 요약 에이전트 (기존)
# ─────────────────────────────────────────────────────────────────────────────

def build_news_agent() -> Optional[object]:
    """
    LangChain 뉴스 에이전트 생성.
    OPENAI_API_KEY 미설정 시 None 반환.
    """
    if not LANGCHAIN_AVAILABLE or not os.getenv("OPENAI_API_KEY"):
        return None

    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        temperature=0,
        api_key=os.getenv("OPENAI_API_KEY"),
    )

    tools = [fetch_stock_news, analyze_sentiment]

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "당신은 한국 주식 시장 전문 애널리스트입니다. "
            "주어진 종목의 최신 뉴스를 분석하고 투자자에게 유용한 인사이트를 제공합니다. "
            "항상 한국어로 응답하고, 투자 권유는 하지 않습니다."
        )),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_openai_functions_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=3)


async def run_news_analysis(ticker: str, company_name: str) -> Optional[str]:
    """에이전트를 실행하여 종목 뉴스 분석 결과 반환"""
    agent = build_news_agent()
    if agent is None:
        return None

    try:
        result = await agent.ainvoke({
            "input": (
                f"{company_name}({ticker}) 종목의 최신 뉴스를 분석하고 "
                "투자자 관점의 핵심 인사이트 2~3문장을 작성해주세요."
            )
        })
        return result.get("output")
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 관계사 영향 판단 에이전트 (신규)
# ─────────────────────────────────────────────────────────────────────────────

_IMPACT_SYSTEM_PROMPT = """\
당신은 한국 주식 시장 전문 애널리스트입니다.
아래 기업의 최신 뉴스 헤드라인을 읽고, 제공된 관계사 목록 중
주가에 영향을 받을 가능성이 있는 종목을 골라 그 이유를 설명하세요.

규칙:
- 관계사 목록에 없는 종목은 절대 포함하지 마세요.
- 영향이 명확하지 않은 종목은 제외하세요 (과잉 추론 금지).
- impact는 반드시 "positive" 또는 "negative" 중 하나여야 합니다.
- reason은 한국어 1문장, 구체적인 근거(공급망·경쟁·계열사 등)를 포함하세요.
- 반드시 아래 JSON 배열 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요.

[
  {{"ticker": "종목코드", "name": "기업명", "impact": "positive|negative", "reason": "근거"}},
  ...
]
"""


async def run_impact_analysis(
    ticker: str,
    company_name: str,
    news_titles: list[str],
    related_companies: list[dict],
) -> list[dict]:
    """
    뉴스 헤드라인을 LLM에게 읽히고, 관계사 중 영향받을 종목과 방향을 반환한다.

    Parameters
    ----------
    ticker          : 기준 종목코드
    company_name    : 기준 기업명
    news_titles     : 최신 뉴스 헤드라인 리스트 (최대 10개 사용)
    related_companies : compute_relations()에서 반환된 관계사 목록
                        [{"ticker": ..., "name": ..., "correlation": ..., "reason": ...}, ...]

    Returns
    -------
    [{"ticker": ..., "name": ..., "impact": "positive|negative", "reason": ...}, ...]
    빈 리스트를 반환해도 안전하다 (fallback).
    """
    if not LANGCHAIN_AVAILABLE or not os.getenv("OPENAI_API_KEY"):
        return []

    if not news_titles or not related_companies:
        return []

    # 관계사 목록을 LLM이 읽기 쉬운 형태로 변환
    related_text = "\n".join(
        f"- {c['ticker']} {c['name']} (관계: {c.get('reason', '')})"
        for c in related_companies
    )
    news_text = "\n".join(f"- {t}" for t in news_titles[:10])

    user_prompt = (
        f"기준 기업: {company_name} ({ticker})\n\n"
        f"최신 뉴스:\n{news_text}\n\n"
        f"관계사 목록:\n{related_text}"
    )

    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _IMPACT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=600,
        )
        raw = response.choices[0].message.content.strip()

        # JSON 파싱 — 최상위가 배열이거나 {"impacts": [...]} 형태 모두 처리
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            # LLM이 {"impacts": [...]} 등으로 감쌀 때
            items = next(
                (v for v in parsed.values() if isinstance(v, list)),
                [],
            )
        else:
            return []

        # 스키마 검증: 필수 필드만 유지, 관계사 목록에 있는 종목만 허용
        valid_tickers = {c["ticker"] for c in related_companies}
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("ticker", "")
            impact = item.get("impact", "")
            reason = item.get("reason", "")
            name = item.get("name", "")
            if t in valid_tickers and impact in ("positive", "negative") and reason:
                result.append({"ticker": t, "name": name, "impact": impact, "reason": reason})

        return result

    except Exception:
        return []
