"""
종목 통합 분석 에이전트

입력:
  - articles: 통과 기사 목록 (List[Article])
  - ticker: 종목 코드
  - dart_context: DART 공시 RAG 텍스트 (빈 문자열이면 DartAnalysis DB에서 자동 조회)
  - relation_context: 관계 기업 RAG 텍스트 (빈 문자열이면 RelationCache DB에서 자동 조회)

출력: AnalysisResult {events, relations, summary, sentiment, impacts, evidence}

- Claude structured output (tool_use) 1회 호출로 전체 분석 동시 생성
- pgvector cosine 검색으로 유사 과거 기사 보강 (OPENAI_API_KEY 설정 시)
- 통과 기사가 없으면 LLM 호출 없이 즉시 None 반환
- 결과는 insight_cache 테이블에 upsert
"""
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

# Article 타입 — 파이프라인 공통 정의 재사용
from agents.dedup_indexer import Article  # noqa: E402


class _RelationItem(BaseModel):
    ticker: str = Field(..., description="관계 기업 종목코드")
    name: str = Field(..., description="관계 기업명")
    relation_type: str = Field(..., description="경쟁|협력|공급망|관심 중 하나")
    impact: Literal["positive", "negative", "neutral"] = Field(
        ..., description="기사 이슈로 인한 영향 방향"
    )
    reason: str = Field(..., description="영향 판단 근거 (한국어 1문장)")


class _ImpactItem(BaseModel):
    ticker: str = Field(..., description="영향 종목코드")
    name: str = Field(..., description="영향 종목명")
    direction: Literal["up", "down", "neutral"] = Field(..., description="주가 방향 예상")
    reason: str = Field(..., description="이유 (한국어 1문장)")
    confidence: float = Field(..., description="확신도 0.0~1.0", ge=0.0, le=1.0)


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
        ..., description="영향받을 종목 — 관계 컨텍스트 내 종목만, 과잉 추론 금지"
    )
    evidence: list[str] = Field(..., description="근거 헤드라인 2~4개 (원문 인용)")


# ─────────────────────────────────────────────────────────────────────────────
# 퍼블릭 결과 타입
# ─────────────────────────────────────────────────────────────────────────────

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
오늘의 뉴스 기사·DART 공시 데이터·기업 관계 정보

분석 원칙:
- events: 오늘 발생한 핵심 이벤트를 중요도 순 3~5개
- relations: 관계 컨텍스트에 명시된 기업만 분석 — 없는 기업 추가 금지
- summary: 투자자가 즉시 활용할 수 있는 3~4문장 핵심 요약
- sentiment: 전체 뉴스 흐름의 감성 (positive/negative/neutral)
- impacts: 관계 컨텍스트 내 종목 중 실질적 영향이 예상되는 것만, 과잉 추론 금지
- evidence: 분석 근거가 된 뉴스 헤드라인 2~4개를 원문 그대로 인용
"""


# RAG 컨텍스트 빌더

def _build_dart_context(ticker: str) -> str:
    """DartAnalysis 테이블에서 최신 공시 분석 결과를 텍스트로 반환."""
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


def _build_accuracy_context(ticker: str) -> str:
    """
    PredictionLog에서 이 종목(source_ticker)의 과거 예측 정확도를 조회하여
    LLM 프롬프트에 삽입할 통계 텍스트로 반환.
    데이터 없거나 DB 오류 시 빈 문자열 반환 (non-blocking).
    """
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
                f"  (정확도가 낮으면 impacts의 confidence 수치를 보수적으로 제시하세요)"
            )
        finally:
            db.close()
    except Exception as e:
        logger.debug("정확도 컨텍스트 조회 실패: %s", e)
    return ""


def _build_relation_context(ticker: str) -> str:
    """RelationCache 테이블에서 관계 기업 정보를 텍스트로 반환."""
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


async def _retrieve_similar_news_context(
    articles: list[Article],
    top_k: int = 5,
) -> str:
    """
    현재 기사 제목을 쿼리로 pgvector cosine 검색하여
    유사 과거 기사를 추가 컨텍스트로 반환.

    pgvector 미지원·임베딩 실패 시 빈 문자열 반환 (non-blocking).
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# LLM 호출 (track_agent 데코레이터로 호출량·토큰·지연 집계)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# 퍼블릭 API
# ─────────────────────────────────────────────────────────────────────────────

async def run(
    articles: list[Article],
    ticker: str,
    dart_context: str = "",
    relation_context: str = "",
    use_rag: bool = True,
) -> Optional[AnalysisResult]:
    """
    기사 + 컨텍스트를 GPT-4o structured output 1회 호출로 통합 분석.

    Parameters
    ----------
    articles         : 관련성 검증을 통과한 기사 목록
    ticker           : 종목 코드
    dart_context     : DART RAG 텍스트 (빈 문자열이면 DartAnalysis DB 자동 조회)
    relation_context : 관계 기업 RAG 텍스트 (빈 문자열이면 RelationCache DB 자동 조회)
    use_rag          : pgvector 유사 기사 RAG 사용 여부.
                       False이면 similar_ctx="" (백테스트 v1 — 시점 필터 미구현으로 OFF)

    Returns
    -------
    AnalysisResult, 또는 기사 없음·API 키 미설정·파싱 실패 시 None
    """
    if not articles:
        logger.info("[%s] 통과 기사 없음 — 분석 건너뜀", ticker)
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — analysis_agent 건너뜀")
        return None

    # 컨텍스트 자동 조회 (미제공 시)
    if not dart_context:
        dart_context = _build_dart_context(ticker)
    if not relation_context:
        relation_context = _build_relation_context(ticker)

    # pgvector 유사 기사 보강 + 과거 예측 정확도
    # use_rag=False(백테스트 v1): 시점 필터 미구현이라 look-ahead 방지를 위해 OFF
    # use_rag=True (라이브/백테스트 v2): evaluation/rag_filter.search_rag_at 로 교체 예정
    similar_ctx = await _retrieve_similar_news_context(articles) if use_rag else ""
    accuracy_ctx = _build_accuracy_context(ticker)

    # 프롬프트 조립
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

        return AnalysisResult(
            ticker=ticker,
            events=parsed.events,
            relations=[r.model_dump() for r in parsed.relations],
            summary=parsed.summary,
            sentiment=parsed.sentiment,
            impacts=[i.model_dump() for i in parsed.impacts],
            evidence=parsed.evidence,
        )

    except Exception as e:
        logger.error("analysis_agent 실패 [ticker=%s]: %s", ticker, e)
        return None


def _save_prediction_logs(ticker: str, result: "AnalysisResult") -> None:
    """
    analysis_agent 결과의 impacts를 PredictionLog 테이블에 기록한다.

    각 impact 항목: {ticker, name, direction(up/down/neutral), reason, confidence}
    prediction_date = D+0 오늘, target_date = D+3
    base_close는 현재가(Redis/pykrx)에서 조회, 없으면 None으로 저장.
    """
    try:
        from models.db_models import PredictionLog, SessionLocal
        from services.stock_service import get_current_price
        from datetime import date, timedelta

        today_str = date.today().strftime("%Y-%m-%d")
        target_str = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")

        db = SessionLocal()
        try:
            for impact in result.impacts:
                impact_ticker = impact.get("ticker", "")
                direction = impact.get("direction", "neutral")
                confidence = float(impact.get("confidence", 0.5))
                reason = impact.get("reason", "")

                if not impact_ticker or not direction:
                    continue

                price_info = get_current_price(impact_ticker)
                base_close = price_info.get("price") if price_info else None

                model_ver = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
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

            db.commit()
            logger.info(
                "PredictionLog 저장 완료 [source=%s, %d건]", ticker, len(result.impacts)
            )
        finally:
            db.close()
    except Exception as e:
        logger.error("PredictionLog 저장 실패 [ticker=%s]: %s", ticker, e)


async def run_and_save(
    articles: list[Article],
    ticker: str,
    dart_context: str = "",
    relation_context: str = "",
) -> Optional[AnalysisResult]:
    """
    통합 분석 후 insight_cache 테이블에 upsert.

    summary 컬럼 → 종합 요약 텍스트
    keywords_json 컬럼 → events / relations / impacts / evidence / sentiment JSON
    impacts는 PredictionLog 테이블에도 기록하여 calibrator 피드백 루프를 활성화한다.
    """
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
            logger.info("insight_cache upsert 완료 [ticker=%s, sentiment=%s]", ticker, result.sentiment)
        finally:
            db.close()
    except Exception as e:
        logger.error("insight_cache 저장 실패 [ticker=%s]: %s", ticker, e)

    # PredictionLog 기록 (calibrator 피드백 루프 Step A)
    if result.impacts:
        _save_prediction_logs(ticker, result)

    # [2-A] 코드 기반 할루시네이션 검증 — 실패해도 결과에 영향 없음
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
                "할루시네이션 검증 완료 [%s]: rate=%.3f", ticker, grounding_stats["hallucination_rate"]
            )
        except Exception as e:
            logger.warning("할루시네이션 검증 실패 (무시): %s", e)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 직접 실행 (테스트)
# ─────────────────────────────────────────────────────────────────────────────

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
        Article(
            url="https://example.com/3",
            title="핵심 부품 가격 3개월 연속 상승 — 수요 견고",
            summary="교체 수요 맞물려 현물가 회복세",
        ),
    ]

    sample_relation = (
        "[관계 기업 목록]\n"
        "  - 000660 (경쟁): 상관계수 0.91\n"
        "  - 005387 (협력): 상관계수 0.74\n"
        "  - 009150 (공급망): 상관계수 0.62"
    )

    result = asyncio.run(run(sample_articles, ticker_arg, relation_context=sample_relation))
    if result:
        print(f"\n[{result.ticker}] 분석 결과 (감성: {result.sentiment})")
        print(f"\n요약:\n{result.summary}")
        print(f"\n핵심 이벤트:")
        for e in result.events:
            print(f"  - {e}")
        print(f"\n근거 헤드라인:")
        for ev in result.evidence:
            print(f"  - {ev}")
        if result.impacts:
            print(f"\n영향 종목:")
            for i in result.impacts:
                print(f"  {i['ticker']} {i['name']} ({i['direction']}, {i['confidence']:.0%}): {i['reason']}")
    else:
        print("분석 실패 (ANTHROPIC_API_KEY 확인 필요)")
