# 할루시네이션 검증
import logging
from datetime import datetime
from typing import Union

logger = logging.getLogger("stoogle.evaluation")


def check_grounding(
    result: dict,
    valid_tickers: set,
    source_articles: list,
) -> dict:
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
