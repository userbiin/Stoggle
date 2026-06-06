"""
backtest.py
백테스트 시점 격리 예측 함수

기존 analysis_agent.run()을 호출하되, 5개 데이터 소스 모두에 as_of 필터를 적용한다.

시점 격리 현황:
  [완전 차단] 뉴스       — NewsCache.fetched_at < as_of
  [[완전 차단] DART 공시  — DartChunk.rcept_no 앞 8자리(YYYYMMDD) < as_of 날짜
  [완전 차단] base_price — as_of 거래일 종가 (pykrx 직접 조회)
  [완전 차단] 관계 컨텍스트 — RelationCache는 정적 사업 관계, 시점 필터 불필요
  [알려진 한계] pgvector 유사 기사 검색 — analysis_agent 내부에서 시점 필터 없이 조회됨
                → v2에서 as_of 파라미터를 _retrieve_similar_news_context에 주입 예정

중복 방지: (source_ticker, ticker, prediction_date, model_version) 유니크 조합으로 재실행 안전.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# v1: pgvector RAG 시점 필터 미구현 → look-ahead 방지를 위해 OFF
# v2: evaluation/rag_filter.search_rag_at 로 교체 후 True로 전환
USE_RAG = False


# ─────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_news_before(ticker: str, as_of: datetime, limit: int = 30) -> list:
    """
    NewsCache에서 as_of 이전에 발행된 기사를 반환한다.
    published_at(VARCHAR ISO) < as_of 로 필터링 — 미래 기사 완전 차단.
    fetched_at 대신 published_at 사용: fetched_at은 수집 시각(look-ahead 누출 가능),
    published_at은 실제 발행 시각이라 시점 격리에 올바른 기준.
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
    pgvector 미사용 환경에서는 빈 문자열 반환.

    시점 필터: rcept_no 앞 8자리(YYYYMMDD)를 as_of 날짜와 비교.
    DartChunk에 rcept_dt 컬럼이 없으므로 rcept_no substring으로 대체.
    indexed_at은 색인 시각(≥ 실제 공시일)이라 look-ahead 가능성 있어 사용 안 함.
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


def _already_exists(db, source_ticker: str, impact_ticker: str, date_str: str, model_version: str) -> bool:
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
    predicted_at = as_of (과거 시점 박제), base_price_date = as_of 거래일.
    """
    from models.db_models import PredictionLog
    from services.stock_service import get_close_price_on

    date_str = as_of.strftime("%Y-%m-%d")
    target_str = (as_of + timedelta(days=5)).strftime("%Y-%m-%d")  # 여유 있게 +5일 (채점은 D+3 거래일)

    count = 0
    for impact in impacts:
        impact_ticker = impact.get("ticker", "")
        direction = impact.get("direction", "neutral")
        if not impact_ticker or direction == "neutral":
            continue

        if _already_exists(db, source_ticker, impact_ticker, date_str, model_version):
            logger.debug("중복 skip: (%s→%s, %s, %s)", source_ticker, impact_ticker, date_str, model_version)
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
            predicted_at=as_of,            # ← 과거 시점 박제
            base_close=base_close,
            base_price_date=date_str,       # ← look-ahead 검증용
            latest_source_pubdate=latest_pubdate,
            status="pending",
        ))
        count += 1

    db.commit()
    return count


# ─────────────────────────────────────────────────────────────────────────────
# 퍼블릭 API
# ─────────────────────────────────────────────────────────────────────────────

async def run_analysis_at(
    ticker: str,
    as_of: datetime,
    db,
    model_version: str = "backtest_v1",
) -> dict:
    """
    as_of 시점의 데이터만으로 ticker를 분석하고 PredictionLog에 저장한다.

    Parameters
    ----------
    ticker        : 분석 기준 종목 (뉴스 소스)
    as_of         : 예측 시점 (이 시각 이후 데이터는 전혀 사용하지 않음)
    db            : SQLAlchemy Session (호출자가 관리)
    model_version : 백테스트 식별자 (prediction-metrics 필터용)

    Returns
    -------
    dict: {status, ticker, as_of, n_impacts}
    """
    import os
    from agents.dedup_indexer import Article
    from agents.analysis_agent import run as agent_run

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return {"status": "no_api_key", "ticker": ticker, "as_of": str(as_of)}

    # 1) 뉴스: published_at < as_of 필터 (fetched_at 아님 — 수집 시각은 look-ahead 오염 가능)
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

    # 가장 최신 기사 발행일 (누출 검증용 — published_at 기준)
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

    # 2) DART: indexed_at < as_of 필터
    dart_context = _get_dart_context_before(ticker, as_of)

    # 3) 분석 에이전트 호출 (relation_context는 auto-fetch — 정적 사업 관계라 시점 무관)
    #    주의: 내부 pgvector 유사 기사 검색은 시점 미필터 (알려진 한계, v2 개선 예정)
    result = await agent_run(articles, ticker, dart_context=dart_context, use_rag=USE_RAG)

    if result is None or not result.impacts:
        return {"status": "no_impacts", "ticker": ticker, "as_of": str(as_of)}

    # 4) 저장: predicted_at=as_of, base_close=as_of 거래일 종가
    n_saved = _save_backtest_predictions(
        db=db,
        source_ticker=ticker,
        as_of=as_of,
        impacts=result.impacts,
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
