# 종목 통합 분석 에이전트
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from evaluation.observability import track_agent

load_dotenv()

logger = logging.getLogger(__name__)

from agents.dedup_indexer import Article  # noqa: E402


# 관계 항목
class _RelationItem(BaseModel):
    ticker: str = Field(..., description="관계 기업 종목코드")
    name: str = Field(..., description="관계 기업명")
    relation_type: str = Field(..., description="경쟁|협력|공급망|관심 중 하나")
    impact: Literal["positive", "negative", "neutral"] = Field(
        ..., description="기사 이슈로 인한 영향 방향"
    )
    reason: str = Field(..., description="영향 판단 근거 (한국어 1문장)")


# 영향 항목
class _ImpactItem(BaseModel):
    ticker: str = Field(..., description="영향 종목코드")
    name: str = Field(..., description="영향 종목명")
    direction: Literal["up", "down", "neutral"] = Field(..., description="주가 방향 예상")
    reason: str = Field(..., description="이유 (한국어 1문장)")
    confidence: float = Field(..., description="확신도 0.0~1.0", ge=0.0, le=1.0)


# 분석 스키마
class _AnalysisSchema(BaseModel):
    events: list[str] = Field(..., description="핵심 이벤트 목록 3~5개 (한국어)")
    relations: list[_RelationItem] = Field(
        ..., description="관계 기업 분석 — 관계 컨텍스트에 명시된 기업만"
    )
    summary: str = Field(..., description="종합 요약 3~4문장 (한국어)")
    sentiment: Literal["positive", "negative", "neutral"] = Field(
        ..., description="전체 뉴스 감성"
    )
    impacts: list[_ImpactItem] = Field(
        ..., description="영향받을 종목 목록"
    )
    evidence: list[str] = Field(..., description="근거 헤드라인 2~4개 (원문 인용)")


# 분석 결과
@dataclass
class AnalysisResult:
    ticker: str
    events: list[str]
    relations: list[dict]
    summary: str
    sentiment: str
    impacts: list[dict]
    evidence: list[str]


_SYSTEM_PROMPT = """\
당신은 한국 주식 시장 전문 애널리스트입니다.
제공된 뉴스 기사·DART 공시 데이터·기업 관계 정보를 종합해 구조화된 분석을 생성하세요.

분석 원칙:
- events: 오늘 발생한 핵심 이벤트를 중요도 순 3~5개
- relations: 관계 컨텍스트에 명시된 기업만 분석 — 없는 기업 추가 금지
- summary: 투자자가 즉시 활용할 수 있는 3~4문장 핵심 요약
- sentiment: 전체 뉴스 흐름의 감성 (positive/negative/neutral)
- impacts: 아래 방향 예측 원칙을 반드시 준수
- evidence: 분석 근거가 된 뉴스 헤드라인 2~4개를 원문 그대로 인용

[방향 예측 원칙 — 엄격히 준수]
1. 방향(direction)은 뉴스의 직접적인 인과관계에 근거해야 한다.
   근거가 명확하지 않으면 반드시 "neutral"로 표시하라.

2. 상승(up) 예측 조건:
   - 구체적인 호재 이벤트(신규 수주, 실적 상향, 규제 완화 등)가 뉴스에 명시된 경우
   - 공급망 상류 기업의 수요 증가가 하류 기업에 직접 영향을 주는 경우

3. 하락(down) 예측 조건:
   - 구체적인 악재 이벤트(수주 취소, 실적 하향, 규제 강화 등)가 뉴스에 명시된 경우
   - 고객사 감산, 공급망 차질, 경쟁 심화가 직접 언급된 경우

4. neutral 예측 조건:
   - 뉴스가 해당 종목에 미치는 방향이 불분명한 경우
   - 간접적 영향만 있는 경우 (섹터 전반 이슈, 거시경제 변수 등)
   - 상승과 하락 요인이 동시에 존재하는 경우

5. 절대 금지:
   - "뉴스가 있다"는 이유만으로 상승 예측 금지
   - 관계사라는 이유만으로 수혜 예측 금지
   - 확신 없는 방향에 높은 confidence(0.7 이상) 부여 금지
   - 같은 종목을 impacts에 두 번 이상 포함 금지
"""


# DART 컨텍스트
def _build_dart_context(ticker: str) -> str:
    try:
        from models.db_models import DartAnalysis, SessionLocal

        db = SessionLocal()
        try:
            rows = (
                db.query(DartAnalysis)
                .filter(DartAnalysis.ticker == ticker)
                .order_by(DartAnalysis.analyzed_at.desc())
                .limit(3)
                .all()
            )
            if not rows:
                return ""

            parts = []
            for row in rows:
                filed = f"(공시일: {row.filed_at})" if row.filed_at else ""
                parts.append(
                    f"[DART 공시 {filed}]\n"
                    f"  매출액: {f'{row.revenue:,.0f}억원' if row.revenue else 'N/A'}\n"
                    f"  영업이익: {f'{row.op_profit:,.0f}억원' if row.op_profit else 'N/A'}\n"
                    f"  CAPEX: {f'{row.capex:,.0f}억원' if row.capex else 'N/A'}\n"
                    f"  재고자산: {f'{row.inventory:,.0f}억원' if row.inventory else 'N/A'}\n"
                    f"  인사이트: {row.insight or ''}"
                )
            return "\n\n".join(parts)
        finally:
            db.close()
    except Exception as e:
        logger.warning("DART 컨텍스트 조회 실패: %s", e)
        return ""


# 정확도 컨텍스트
def _build_accuracy_context(ticker: str) -> str:
    try:
        from models.db_models import PredictionLog, SessionLocal

        db = SessionLocal()
        try:
            rows = (
                db.query(PredictionLog)
                .filter(
                    PredictionLog.source_ticker == ticker,
                    PredictionLog.is_correct.isnot(None),
                )
                .order_by(PredictionLog.predicted_at.desc())
                .limit(50)
                .all()
            )
            if not rows:
                return ""

            total = len(rows)
            correct = sum(1 for r in rows if r.is_correct)
            accuracy = correct / total if total else 0.0
            avg_cal = sum(r.calibrated_confidence for r in rows if r.calibrated_confidence) / max(
                sum(1 for r in rows if r.calibrated_confidence), 1
            )
            return (
                f"[과거 예측 정확도 — {ticker} 기준]\n"
                f"  최근 {total}건 방향 정확도: {accuracy:.1%}\n"
                f"  평균 보정 confidence: {avg_cal:.3f}\n"
                f"  (정확도가 낮으면 impacts의 confidence 수치를 보수적으로 제시하고 "
                f"방향 불확실 시 neutral을 적극 사용하세요)"
            )
        finally:
            db.close()
    except Exception as e:
        logger.debug("정확도 컨텍스트 조회 실패: %s", e)
    return ""


# 관계 컨텍스트
def _build_relation_context(ticker: str) -> str:
    try:
        from models.db_models import RelationCache, SessionLocal

        db = SessionLocal()
        try:
            rows = (
                db.query(RelationCache)
                .filter(RelationCache.ticker == ticker)
                .order_by(RelationCache.correlation.desc())
                .limit(10)
                .all()
            )
            if not rows:
                return ""

            lines = ["[관계 기업 목록]"]
            for row in rows:
                lines.append(
                    f"  - {row.related_ticker} ({row.relation_type}): "
                    f"상관계수 {row.correlation:.2f} — {row.reason}"
                )
            return "\n".join(lines)
        finally:
            db.close()
    except Exception as e:
        logger.warning("관계 컨텍스트 조회 실패: %s", e)
        return ""


# 유사 뉴스 컨텍스트
async def _retrieve_similar_news_context(
    articles: list[Article],
    top_k: int = 5,
) -> str:
    try:
        from models.db_models import NewsCache, NewsVector, SessionLocal, PGVECTOR_AVAILABLE

        if not PGVECTOR_AVAILABLE or NewsVector is None:
            return ""

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return ""

        from openai import AsyncOpenAI

        query_text = " ".join(a.title for a in articles[:3])
        client = AsyncOpenAI(api_key=api_key)
        emb_response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=query_text,
        )
        query_emb = emb_response.data[0].embedding

        current_urls = {a.url for a in articles}

        db = SessionLocal()
        try:
            rows = (
                db.query(NewsCache.title, NewsCache.source)
                .join(NewsVector, NewsVector.news_cache_id == NewsCache.id)
                .filter(NewsVector.url.notin_(current_urls))
                .order_by(NewsVector.embedding.cosine_distance(query_emb))
                .limit(top_k)
                .all()
            )
            if not rows:
                return ""

            lines = ["[유사 과거 기사]"]
            for title, source in rows:
                lines.append(f"  - [{source}] {title}")
            return "\n".join(lines)
        finally:
            db.close()

    except Exception as e:
        logger.debug("pgvector 유사 기사 검색 실패 (무시): %s", e)
        return ""


# Claude 호출
@track_agent("analysis_agent", "analysis_pipeline")
async def _call_claude(client, model: str, user_prompt: str):
    return await client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[{
            "name": "output_analysis",
            "description": "종합 분석 결과를 구조화된 형식으로 출력",
            "input_schema": _AnalysisSchema.model_json_schema(),
        }],
        tool_choice={"type": "tool", "name": "output_analysis"},
    )


# 분석 실행
async def run(
    articles: list[Article],
    ticker: str,
    dart_context: str = "",
    relation_context: str = "",
    use_rag: bool = True,
) -> Optional[AnalysisResult]:
    if not articles:
        logger.info("[%s] 통과 기사 없음 — 분석 건너뜀", ticker)
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — analysis_agent 건너뜀")
        return None

    if not dart_context:
        dart_context = _build_dart_context(ticker)
    if not relation_context:
        relation_context = _build_relation_context(ticker)

    similar_ctx = await _retrieve_similar_news_context(articles) if use_rag else ""
    accuracy_ctx = _build_accuracy_context(ticker)

    article_lines = "\n".join(
        f"- {a.title}: {a.summary}" for a in articles[:20]
    )
    user_prompt = f"종목: {ticker}\n\n[오늘 뉴스 기사]\n{article_lines}"

    for ctx in (dart_context, relation_context, similar_ctx, accuracy_ctx):
        if ctx:
            user_prompt += f"\n\n{ctx}"

    model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
        response = await _call_claude(client, model, user_prompt)

        parsed: Optional[_AnalysisSchema] = None
        for block in response.content:
            if block.type == "tool_use":
                parsed = _AnalysisSchema(**block.input)
                break

        if parsed is None:
            logger.error("structured output 결과가 None [ticker=%s]", ticker)
            return None

        seen_tickers: dict[str, _ImpactItem] = {}
        for item in parsed.impacts:
            if item.ticker not in seen_tickers:
                seen_tickers[item.ticker] = item
            else:
                if item.confidence > seen_tickers[item.ticker].confidence:
                    seen_tickers[item.ticker] = item
        deduped_impacts = list(seen_tickers.values())

        return AnalysisResult(
            ticker=ticker,
            events=parsed.events,
            relations=[r.model_dump() for r in parsed.relations],
            summary=parsed.summary,
            sentiment=parsed.sentiment,
            impacts=[i.model_dump() for i in deduped_impacts],
            evidence=parsed.evidence,
        )

    except Exception as e:
        logger.error("analysis_agent 실패 [ticker=%s]: %s", ticker, e)
        return None


# 예측 로그 저장
def _save_prediction_logs(ticker: str, result: "AnalysisResult") -> None:
    try:
        from models.db_models import PredictionLog, SessionLocal
        from services.stock_service import get_current_price
        from datetime import date, timedelta

        today_str = date.today().strftime("%Y-%m-%d")
        target_str = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")

        db = SessionLocal()
        try:
            existing = db.query(PredictionLog.ticker).filter(
                PredictionLog.source_ticker == ticker,
                PredictionLog.prediction_date == today_str,
            ).all()
            already_saved = {row.ticker for row in existing}

            model_ver = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
            saved = 0
            for impact in result.impacts:
                impact_ticker = impact.get("ticker", "")
                direction = impact.get("direction", "neutral")
                confidence = float(impact.get("confidence", 0.5))
                reason = impact.get("reason", "")

                if not impact_ticker or direction == "neutral":
                    continue

                if impact_ticker in already_saved:
                    logger.debug(
                        "중복 PredictionLog skip [%s→%s, %s]",
                        ticker, impact_ticker, today_str,
                    )
                    continue

                price_info = get_current_price(impact_ticker)
                base_close = price_info.get("price") if price_info else None

                db.add(PredictionLog(
                    ticker=impact_ticker,
                    source_ticker=ticker,
                    direction=direction,
                    confidence=confidence,
                    reason=reason,
                    model_version=model_ver,
                    prediction_date=today_str,
                    target_date=target_str,
                    predicted_at=datetime.utcnow(),
                    base_close=base_close,
                    status="pending",
                ))
                already_saved.add(impact_ticker)
                saved += 1

            db.commit()
            logger.info(
                "PredictionLog 저장 완료 [source=%s, %d건 (neutral/중복 제외)]",
                ticker, saved,
            )
        finally:
            db.close()
    except Exception as e:
        logger.error("PredictionLog 저장 실패 [ticker=%s]: %s", ticker, e)


# 분석 후 저장
async def run_and_save(
    articles: list[Article],
    ticker: str,
    dart_context: str = "",
    relation_context: str = "",
) -> Optional[AnalysisResult]:
    result = await run(articles, ticker, dart_context, relation_context)
    if result is None:
        return None

    try:
        from models.db_models import InsightCache, SessionLocal

        extra = json.dumps(
            {
                "events": result.events,
                "relations": result.relations,
                "impacts": result.impacts,
                "evidence": result.evidence,
                "sentiment": result.sentiment,
            },
            ensure_ascii=False,
        )

        db = SessionLocal()
        try:
            row = db.query(InsightCache).filter(InsightCache.ticker == ticker).first()
            if row:
                row.summary = result.summary
                row.keywords_json = extra
                row.updated_at = datetime.utcnow()
            else:
                db.add(
                    InsightCache(
                        ticker=ticker,
                        summary=result.summary,
                        keywords_json=extra,
                    )
                )
            db.commit()
            logger.info(
                "insight_cache upsert 완료 [ticker=%s, sentiment=%s]",
                ticker, result.sentiment,
            )
        finally:
            db.close()
    except Exception as e:
        logger.error("insight_cache 저장 실패 [ticker=%s]: %s", ticker, e)

    if result.impacts:
        _save_prediction_logs(ticker, result)

    if result.impacts:
        try:
            from services.stock_service import get_or_build_registry
            from evaluation.hallucination_check import check_grounding, log_hallucination

            registry = get_or_build_registry()
            valid_tickers = set(registry.keys())
            grounding_stats = check_grounding(
                {"impacts": result.impacts},
                valid_tickers,
                articles,
            )
            log_hallucination("analysis_agent", "analysis_pipeline", grounding_stats)
            logger.info(
                "할루시네이션 검증 완료 [%s]: rate=%.3f",
                ticker, grounding_stats["hallucination_rate"],
            )
        except Exception as e:
            logger.warning("할루시네이션 검증 실패 (무시): %s", e)

    return result


if __name__ == "__main__":
    import asyncio
    import sys

    ticker_arg = sys.argv[1] if len(sys.argv) > 1 else "005930"

    sample_articles = [
        Article(
            url="https://example.com/1",
            title="3분기 영업이익 큰 폭 증가 — 수출 호조 효과",
            summary="글로벌 수요 증가로 매출 급증",
        ),
        Article(
            url="https://example.com/2",
            title="신공정 수율 개선 — 양산 일정 확정",
            summary="내년 상반기 내 양산 목표",
        ),
    ]

    result = asyncio.run(run(sample_articles, ticker_arg))
    if result:
        print(f"\n[{result.ticker}] 분석 결과 (감성: {result.sentiment})")
        print(f"\n요약:\n{result.summary}")
        if result.impacts:
            print(f"\n영향 종목:")
            for i in result.impacts:
                print(
                    f"  {i['ticker']} {i['name']} "
                    f"({i['direction']}, {i['confidence']:.0%}): {i['reason']}"
                )
    else:
        print("분석 실패 (ANTHROPIC_API_KEY 확인 필요)")