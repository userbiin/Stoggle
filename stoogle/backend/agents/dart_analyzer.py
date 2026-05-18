"""
DART 공시 원문 텍스트에서 핵심 재무 수치를 추출하는 에이전트

Claude structured output(tool_use)을 사용해
비정형 공시 문서에서 revenue / op_profit / capex / inventory 를 파싱하고
dart_analysis 테이블에 저장한다.

Usage:
    result = await run(dart_text, ticker="005930", filed_at="2024-03-31")
    result = await run_and_save(dart_text, ticker="005930")
"""
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
당신은 한국 DART(전자공시시스템) 공시 문서 분석 전문가입니다.
주어진 공시 원문에서 아래 재무 수치를 추출해 주세요.

추출 규칙:
1. 단위를 반드시 억원(KRW 100M)으로 통일하세요.
   - 백만원 단위 → ÷ 100
   - 천원 단위   → ÷ 100,000
   - 조원 단위   → × 10,000
2. 분기·반기보다 연간(누적) 수치를 우선합니다.
3. 연결 재무제표가 있으면 연결 기준을 우선합니다.
4. 수치를 문서에서 찾을 수 없으면 반드시 null을 반환하세요 (추측 금지).
5. insight는 투자자 관점의 핵심 내용을 한국어 2~3문장으로 작성하세요.
   전년 대비 수치 변화, 특이 사항, 주목할 리스크를 포함하세요.
"""


class _FinancialSchema(BaseModel):
    """Claude structured output 스키마 (억원 단위)"""

    revenue: Optional[float] = Field(None, description="매출액 (억원)")
    op_profit: Optional[float] = Field(None, description="영업이익 (억원)")
    capex: Optional[float] = Field(None, description="설비투자·CAPEX (억원)")
    inventory: Optional[float] = Field(None, description="재고자산 (억원)")
    insight: str = Field(..., description="핵심 인사이트 (한국어 2~3문장)")


@dataclass
class DartAnalysisResult:
    ticker: str
    revenue: Optional[float]
    op_profit: Optional[float]
    capex: Optional[float]
    inventory: Optional[float]
    insight: str
    text_hash: str
    filed_at: Optional[str]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:64]


async def run(
    dart_text: str,
    ticker: str,
    filed_at: Optional[str] = None,
) -> Optional[DartAnalysisResult]:
    """
    DART 원문에서 재무 수치를 추출한다.

    Parameters
    ----------
    dart_text : DART 공시 원문 (16,000자 초과분은 잘라냄)
    ticker    : 종목 코드 (e.g. "005930")
    filed_at  : 공시일 YYYY-MM-DD (선택)

    Returns
    -------
    DartAnalysisResult, 또는 API 키 미설정·파싱 실패 시 None
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — dart_analyzer 건너뜀")
        return None

    model = os.getenv("DART_ANALYZER_MODEL", "claude-sonnet-4-6")

    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"아래 DART 공시 원문을 분석해주세요:\n\n{dart_text[:16000]}",
            }],
            tools=[{
                "name": "output_financials",
                "description": "DART 공시에서 추출한 재무 수치와 인사이트 출력",
                "input_schema": _FinancialSchema.model_json_schema(),
            }],
            tool_choice={"type": "tool", "name": "output_financials"},
        )

        parsed: Optional[_FinancialSchema] = None
        for block in response.content:
            if block.type == "tool_use":
                parsed = _FinancialSchema(**block.input)
                break

        if parsed is None:
            logger.error("structured output 결과가 None [ticker=%s]", ticker)
            return None

        return DartAnalysisResult(
            ticker=ticker,
            revenue=parsed.revenue,
            op_profit=parsed.op_profit,
            capex=parsed.capex,
            inventory=parsed.inventory,
            insight=parsed.insight,
            text_hash=_hash(dart_text),
            filed_at=filed_at,
        )

    except Exception as e:
        logger.error("dart_analyzer 실패 [ticker=%s]: %s", ticker, e)
        return None


async def run_and_save(
    dart_text: str,
    ticker: str,
    filed_at: Optional[str] = None,
    *,
    skip_duplicate: bool = True,
) -> Optional[DartAnalysisResult]:
    """
    재무 수치 추출 후 dart_analysis 테이블에 저장한다.

    Parameters
    ----------
    skip_duplicate : True 이면 동일 text_hash가 이미 저장된 경우 LLM 호출 없이 건너뜀
    """
    text_hash = _hash(dart_text)

    if skip_duplicate:
        try:
            from models.db_models import DartAnalysis, SessionLocal

            db = SessionLocal()
            try:
                exists = (
                    db.query(DartAnalysis)
                    .filter(DartAnalysis.text_hash == text_hash)
                    .first()
                )
                if exists:
                    logger.info("중복 공시 건너뜀 [hash=%.12s…]", text_hash)
                    return None
            finally:
                db.close()
        except Exception as e:
            logger.warning("중복 체크 실패 (계속 진행): %s", e)

    result = await run(dart_text, ticker, filed_at)
    if result is None:
        return None

    try:
        from models.db_models import DartAnalysis, SessionLocal

        db = SessionLocal()
        try:
            row = DartAnalysis(
                ticker=result.ticker,
                filed_at=result.filed_at,
                revenue=result.revenue,
                op_profit=result.op_profit,
                capex=result.capex,
                inventory=result.inventory,
                insight=result.insight,
                text_hash=result.text_hash,
                analyzed_at=datetime.utcnow(),
            )
            db.add(row)
            db.commit()
            logger.info(
                "DART 분석 저장 완료 [ticker=%s, revenue=%s억원]",
                ticker,
                result.revenue,
            )
        finally:
            db.close()
    except Exception as e:
        logger.error("DB 저장 실패 [ticker=%s]: %s", ticker, e)

    return result


if __name__ == "__main__":
    import asyncio
    import sys

    ticker_arg = sys.argv[1] if len(sys.argv) > 1 else "005930"
    sample_text = (
        "당사의 2024년 연결 기준 매출액은 300조 870억원으로 전년 대비 5.3% 증가하였으며, "
        "영업이익은 32조 7,260억원을 기록하였습니다. "
        "설비투자(CAPEX)는 53조 5,360억원이며, 재고자산은 42조 1,500억원입니다. "
        "D램 가격 회복 및 HBM 공급 확대가 실적 개선을 견인하였으나, "
        "파운드리 부문 적자는 지속되고 있습니다."
    )

    result = asyncio.run(run(sample_text, ticker_arg))
    if result:
        print(f"매출액:   {result.revenue:,.0f}억원" if result.revenue else "매출액: N/A")
        print(f"영업이익: {result.op_profit:,.0f}억원" if result.op_profit else "영업이익: N/A")
        print(f"CAPEX:    {result.capex:,.0f}억원" if result.capex else "CAPEX: N/A")
        print(f"재고자산: {result.inventory:,.0f}억원" if result.inventory else "재고자산: N/A")
        print(f"인사이트:\n{result.insight}")
    else:
        print("분석 실패 (ANTHROPIC_API_KEY 확인 필요)")
