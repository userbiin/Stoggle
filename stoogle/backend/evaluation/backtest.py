"""
backtest.py
백테스트 시점 격리 예측 함수

[변경 내역]
  - _save_backtest_predictions: impacts 내 중복 ticker 방지 (같은 impact 내에서도)
  - direction='neutral' 저장 제외
  - published_at 필터 명시 주석 개선
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

USE_RAG = False


def _fetch_news_before(ticker: str, as_of: datetime, limit: int = 30) -> list:
    """
    NewsCache에서 as_of 이전에 발행된 기사를 반환한다.

    published_at(VARCHAR ISO) < as_of 로 필터링.
    fetched_at이 아닌 published_at을 사용하는 이유:
      - fetched_at: 수집 시각 → 미래 시점에 수집했을 수 있어 look-ahead 오염 가능
      - published_at: 실제 발행 시각 → 시점 격리의 올바른 기준
    """
    as_of_str = as_of.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        from models.db_models import NewsCache, SessionLocal
        db = SessionLocal()
        try:
            rows = (
                db.query(NewsCache)
                .filter(
                    NewsCache.ticker == ticker,
                    NewsCache.published_at < as_of_str,
                )
                .order_by(NewsCache.published_at.desc())
                .limit(limit)
                .all()
            )
            return rows
        finally:
            db.close()
    except Exception as e:
        logger.warning("NewsCache 조회 실패 (%s, %s): %s", ticker, as_of, e)
        return []


def _get_dart_context_before(ticker: str, as_of: datetime, max_chunks: int = 8) -> str:
    """
    DartChunk에서 as_of 이전 공시 청크를 공급망 키워드 기준으로 가져온다.
    시점 필터: rcept_no 앞 8자리(YYYYMMDD)를 as_of 날짜와 비교.
    """
    try:
        from models.db_models import DartChunk, SessionLocal, PGVECTOR_AVAILABLE
        if not PGVECTOR_AVAILABLE or DartChunk is None:
            return ""
        from sqlalchemy import or_, func
        _KEYWORDS = ["거래처", "협력사", "납품", "공급", "유통", "계열사", "고객사", "매출처"]
        as_of_date_str = as_of.strftime("%Y%m%d")
        db = SessionLocal()
        try:
            rows = (
                db.query(DartChunk)
                .filter(
                    DartChunk.ticker == ticker,
                    func.substr(DartChunk.rcept_no, 1, 8) < as_of_date_str,
                    or_(*[DartChunk.section_title.ilike(f"%{kw}%") for kw in _KEYWORDS]),
                )
                .order_by(func.substr(DartChunk.rcept_no, 1, 8).desc())
                .limit(max_chunks)
                .all()
            )
            if not rows:
                return ""
            parts = [f"[DART — {r.section_title}]\n{r.content[:600]}" for r in rows]
            return "\n\n".join(parts)
        finally:
            db.close()
    except Exception as e:
        logger.debug("DART 컨텍스트 조회 실패 (%s): %s", ticker, e)
        return ""


def _already_exists(
    db, source_ticker: str, impact_ticker: str, date_str: str, model_version: str
) -> bool:
    """같은 (source_ticker, ticker, prediction_date, model_version) 레코드가 있으면 True."""
    try:
        from models.db_models import PredictionLog
        return db.query(PredictionLog).filter(
            PredictionLog.source_ticker == source_ticker,
            PredictionLog.ticker == impact_ticker,
            PredictionLog.prediction_date == date_str,
            PredictionLog.model_version == model_version,
        ).first() is not None
    except Exception:
        return False


def _save_backtest_predictions(
    db,
    source_ticker: str,
    as_of: datetime,
    impacts: list[dict],
    model_version: str,
    latest_pubdate: Optional[datetime],
) -> int:
    """
    백테스트 예측 결과를 PredictionLog에 저장한다.

    [변경]
    - direction='neutral' 저장 제외 (채점 의미 없음)
    - impacts 내 중복 ticker 방지 (같은 source에서 같은 target이 두 번 나오면 첫 번째만)
    - 이미 존재하는 레코드 skip
    """
    from models.db_models import PredictionLog
    from services.stock_service import get_close_price_on

    date_str = as_of.strftime("%Y-%m-%d")
    target_str = (as_of + timedelta(days=5)).strftime("%Y-%m-%d")

    # 이번 impacts에서 중복 ticker 제거 (confidence 높은 쪽 우선)
    best_impacts: dict[str, dict] = {}
    for impact in impacts:
        t = impact.get("ticker", "")
        d = impact.get("direction", "neutral")
        if not t or d == "neutral":
            continue
        if t not in best_impacts:
            best_impacts[t] = impact
        else:
            if float(impact.get("confidence", 0)) > float(best_impacts[t].get("confidence", 0)):
                best_impacts[t] = impact

    count = 0
    for impact_ticker, impact in best_impacts.items():
        direction = impact.get("direction", "neutral")

        # DB 중복 체크
        if _already_exists(db, source_ticker, impact_ticker, date_str, model_version):
            logger.debug(
                "중복 skip: (%s→%s, %s, %s)",
                source_ticker, impact_ticker, date_str, model_version,
            )
            continue

        base_close = get_close_price_on(impact_ticker, as_of)
        if base_close is None:
            logger.debug("base_close 없음 — skip: %s @ %s", impact_ticker, as_of)
            continue

        db.add(PredictionLog(
            ticker=impact_ticker,
            source_ticker=source_ticker,
            direction=direction,
            confidence=float(impact.get("confidence", 0.5)),
            reason=impact.get("reason", ""),
            model_version=model_version,
            prediction_date=date_str,
            target_date=target_str,
            predicted_at=as_of,
            base_close=base_close,
            base_price_date=date_str,
            latest_source_pubdate=latest_pubdate,
            status="pending",
        ))
        count += 1

    db.commit()
    return count


async def run_analysis_at(
    ticker: str,
    as_of: datetime,
    db,
    model_version: str = "backtest_v1",
) -> dict:
    """
    as_of 시점의 데이터만으로 ticker를 분석하고 PredictionLog에 저장한다.
    """
    import os
    from agents.dedup_indexer import Article
    from agents.analysis_agent import run as agent_run

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"status": "no_api_key", "ticker": ticker, "as_of": str(as_of)}

    # 1) 뉴스: published_at < as_of (fetched_at 아님)
    news_rows = _fetch_news_before(ticker, as_of, limit=30)
    if not news_rows:
        return {"status": "no_news", "ticker": ticker, "as_of": str(as_of)}

    articles = [
        Article(
            url=row.url or f"http://naver/{row.id}",
            title=row.title or "",
            summary=row.summary or "",
            news_cache_id=row.id,
        )
        for row in news_rows
        if row.title
    ]

    # 가장 최신 기사 발행일 (누출 검증용)
    latest_pubdate: Optional[datetime] = None
    for row in news_rows:
        if not row.published_at:
            continue
        try:
            pub_dt = datetime.fromisoformat(row.published_at)
        except (ValueError, TypeError):
            continue
        if latest_pubdate is None or pub_dt > latest_pubdate:
            latest_pubdate = pub_dt

    # 2) DART 컨텍스트
    dart_context = _get_dart_context_before(ticker, as_of)

    # 3) 분석 에이전트 호출
    result = await agent_run(articles, ticker, dart_context=dart_context, use_rag=USE_RAG)

    if result is None or not result.impacts:
        return {"status": "no_impacts", "ticker": ticker, "as_of": str(as_of)}

    # neutral이 아닌 impacts만 저장 대상
    scoreable_impacts = [i for i in result.impacts if i.get("direction") != "neutral"]
    if not scoreable_impacts:
        return {"status": "no_impacts", "ticker": ticker, "as_of": str(as_of)}

    # 4) 저장
    n_saved = _save_backtest_predictions(
        db=db,
        source_ticker=ticker,
        as_of=as_of,
        impacts=scoreable_impacts,
        model_version=model_version,
        latest_pubdate=latest_pubdate,
    )

    return {
        "status": "ok",
        "ticker": ticker,
        "as_of": as_of.strftime("%Y-%m-%d %H:%M"),
        "n_impacts": len(result.impacts),
        "n_saved": n_saved,
    }