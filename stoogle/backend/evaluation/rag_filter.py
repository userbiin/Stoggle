"""
백테스트 v2 — pgvector RAG 시점 격리 (옵션 A)

v1에서는 evaluation/backtest.py의 USE_RAG=False 로 RAG 자체를 끈다.
v2 전환 시:
  1. USE_RAG = True 로 변경
  2. analysis_agent.run() 에 use_rag=True 전달 (기본값이라 변경 불필요)
  3. analysis_agent._retrieve_similar_news_context 내부 호출을
     search_rag_at(db, query_embedding, as_of=as_of) 로 교체

미호출 상태로 커밋됨 — v2 준비용.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session


def search_rag_at(
    db: Session,
    query_embedding,
    as_of: Optional[datetime] = None,
    top_k: int = 20,
):
    """
    pgvector RAG 검색. as_of가 주어지면 그 이전 색인 청크만 검색한다.

    as_of=None  → 라이브 모드 (필터 없음, 기존 동작)
    as_of=시각  → 백테스트 모드 (NewsVector.created_at < as_of 또는 News.published_at < as_of)

    Parameters
    ----------
    db             : SQLAlchemy Session
    query_embedding: pgvector 쿼리 벡터
    as_of          : 시점 필터 (None이면 전체 검색)
    top_k          : 반환할 최대 청크 수

    Returns
    -------
    list of NewsVector rows (비어있을 수 있음)
    """
    try:
        from models.db_models import NewsVector, NewsCache, PGVECTOR_AVAILABLE

        if not PGVECTOR_AVAILABLE or NewsVector is None:
            return []

        stmt = select(NewsVector)

        if as_of is not None:
            if hasattr(NewsVector, "created_at"):
                stmt = stmt.where(NewsVector.created_at < as_of)
            else:
                as_of_str = as_of.strftime("%Y-%m-%dT%H:%M:%S")  # ISO 문자열 비교
                stmt = (
                    stmt.join(NewsCache, NewsCache.id == NewsVector.news_cache_id)
                    .where(NewsCache.published_at < as_of_str)   # ✓ published_at
                )

        stmt = stmt.order_by(
            NewsVector.embedding.cosine_distance(query_embedding)
        ).limit(top_k)

        return list(db.execute(stmt).scalars().all())

    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("rag_filter.search_rag_at 실패 (무시): %s", e)
        return []
