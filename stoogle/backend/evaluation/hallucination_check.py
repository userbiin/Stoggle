"""[2-A] 코드 기반 grounding 검증 — 가짜 종목 / 근거 없는 추론 탐지

KRX 상장 종목 집합이라는 정답이 있으므로 LLM 없이 100% 자동 검증 가능하다.
완전 일치 방식의 한계(LLM이 근거 문장을 살짝 다듬으면 false positive):
  개선안 — evidence를 임베딩해 원문 청크와 cosine similarity > 0.8이면 "근거 있음"
  (pgvector 활용, 현재는 완전 일치로 구현)
"""
import logging
from datetime import datetime
from typing import Union

logger = logging.getLogger("stoogle.evaluation")


def check_grounding(
    result: dict,
    valid_tickers: set,
    source_articles: list,
) -> dict:
    """
    통합 에이전트 출력의 할루시네이션을 코드로 검증.

    Parameters
    ----------
    result         : AnalysisResult.impacts 포함 dict
    valid_tickers  : KRX 상장 종목코드 집합 (기준값)
    source_articles: Article 객체 또는 {"content": str} dict 목록

    Returns
    -------
    {checked, invalid_ticker, missing_evidence, hallucination_rate}
    """
    impacts = result.get("impacts", [])
    total = len(impacts)
    if total == 0:
        return {"checked": 0, "invalid_ticker": 0, "missing_evidence": 0, "hallucination_rate": 0.0}

    # Article 객체와 dict 모두 처리
    def _text(a) -> str:
        if hasattr(a, "title"):
            return f"{a.title} {getattr(a, 'summary', '')}"
        return a.get("content", "") + " " + a.get("summary", "")

    source_text = " ".join(_text(a) for a in source_articles)

    invalid_ticker = 0
    missing_evidence = 0

    for imp in impacts:
        # (1) 존재하지 않는 종목을 지어냈나?
        if imp.get("ticker") not in valid_tickers:
            invalid_ticker += 1
        # (2) 근거 문장이 실제 입력 기사에 있나?
        ev = imp.get("evidence") or imp.get("reason", "")
        if ev and len(ev) > 10 and ev not in source_text:
            missing_evidence += 1

    hallucinated = invalid_ticker + missing_evidence
    return {
        "checked": total,
        "invalid_ticker": invalid_ticker,
        "missing_evidence": missing_evidence,
        "hallucination_rate": round(hallucinated / total, 3),
    }


def log_hallucination(agent: str, module: str, stats: dict) -> None:
    """할루시네이션 검증 결과를 hallucination_logs 테이블에 저장한다."""
    try:
        from models.db_models import HallucinationLog, SessionLocal

        db = SessionLocal()
        try:
            db.add(
                HallucinationLog(
                    agent=agent,
                    module=module,
                    checked=stats.get("checked", 0),
                    invalid_ticker=stats.get("invalid_ticker", 0),
                    missing_evidence=stats.get("missing_evidence", 0),
                    faithfulness=stats.get("faithfulness"),
                    logged_at=datetime.utcnow(),
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("HallucinationLog 저장 실패: %s", e)
