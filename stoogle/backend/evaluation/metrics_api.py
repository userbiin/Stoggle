"""집계 조회 엔드포인트 — /api/v1/_internal/*

⚠️ 비용 단가(IN_PRICE / OUT_PRICE)는 Claude 공식 가격 기준으로 30일마다 갱신할 것.
   오래된 단가표는 비용을 20~40% 틀리게 계산한다.
"""
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import func

from evaluation.observability import get_agent_stats

router = APIRouter(prefix="/api/v1/_internal", tags=["evaluation"])

# claude-sonnet-4-6 기준 단가 (USD per 1M tokens) — 주기적으로 갱신 필요
IN_PRICE = 3.0
OUT_PRICE = 15.0


@router.get("/agent-stats")
async def agent_stats():
    """에이전트별 호출량·토큰·지연·추정 비용 조회."""
    stats = get_agent_stats()
    result = {}
    for key, s in stats.items():
        result[key] = dict(s)
        result[key]["avg_latency_ms"] = round(s["total_ms"] / max(s["calls"], 1), 1)
        result[key]["est_cost_usd"] = round(
            s["in_tok"] / 1e6 * IN_PRICE + s["out_tok"] / 1e6 * OUT_PRICE, 4
        )
    return result


@router.get("/prediction-metrics")
async def pred_metrics(
    model_version: Optional[str] = Query(
        None,
        description="필터할 model_version. 미지정 시 전체 집계. 예: backtest_v1",
    )
):
    """Direction Accuracy + High-confidence Accuracy 집계. model_version으로 백테스트/라이브 분리."""
    from models.db_models import SessionLocal
    from evaluation.prediction_scorer import prediction_metrics

    db = SessionLocal()
    try:
        result = prediction_metrics(db, model_version=model_version)
        result["model_version"] = model_version or "all"
        return result
    finally:
        db.close()


@router.get("/hallucination-summary")
async def hallu_summary():
    """에이전트별 할루시네이션율 + 요약 충실도 집계."""
    from models.db_models import HallucinationLog, SessionLocal

    db = SessionLocal()
    try:
        rows = (
            db.query(
                HallucinationLog.agent,
                func.avg(HallucinationLog.faithfulness).label("avg_faith"),
                func.sum(HallucinationLog.invalid_ticker).label("sum_invalid"),
                func.sum(HallucinationLog.missing_evidence).label("sum_missing"),
                func.sum(HallucinationLog.checked).label("sum_checked"),
            )
            .group_by(HallucinationLog.agent)
            .all()
        )
        result = []
        for r in rows:
            total_hallucinated = (r.sum_invalid or 0) + (r.sum_missing or 0)
            sum_checked = r.sum_checked or 0
            hallu_rate = (total_hallucinated / sum_checked) if sum_checked else None
            result.append(
                {
                    "agent": r.agent,
                    "avg_faithfulness": round(r.avg_faith, 3) if r.avg_faith is not None else None,
                    "hallucination_rate": round(hallu_rate, 3) if hallu_rate is not None else None,
                }
            )
        return result
    finally:
        db.close()
