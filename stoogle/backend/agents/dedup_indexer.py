"""
기사 중복 제거 + pgvector 색인

입력 : List[Article]
출력 : 중복 제거된 List[Article] (pgvector 색인 완료)

중복 판단 기준
  1. URL 동일 → 즉시 중복
  2. 제목+요약 임베딩 코사인 유사도 >= 0.9 → 의미적 중복
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
COSINE_DIST_THRESHOLD = 0.1   # 1 - 0.9 = 0.1 (유사도 0.9 이상 → 중복)
EMBED_BATCH_SIZE = 100         # 한 번에 처리할 최대 텍스트 수


@dataclass
class Article:
    url: str
    title: str
    summary: str = ""
    news_cache_id: Optional[int] = None


# 임베딩
async def _embed_batch(texts: list[str]) -> list[list[float]]:
    """OpenAI text-embedding-3-small 배치 호출"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY가 설정되지 않았습니다.")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)

    embeddings: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        response = await client.embeddings.create(model=EMBED_MODEL, input=batch)
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


# 유사도 계산
def _cosine_sim(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / norm) if norm > 0 else 0.0


# 1단계: 배치 내 중복 제거 (O(n²))
def _dedup_within_batch(
    articles: list[Article],
    embeddings: list[list[float]],
) -> tuple[list[Article], list[list[float]]]:
    unique: list[tuple[Article, list[float]]] = []

    for article, emb in zip(articles, embeddings):
        if any(_cosine_sim(emb, u_emb) >= 0.9 for _, u_emb in unique):
            logger.debug("배치 내 중복 제거: %.60s", article.title)
            continue
        unique.append((article, emb))

    if not unique:
        return [], []
    arts, embs = zip(*unique)
    return list(arts), list(embs)


# 2단계: DB(pgvector) 비교
def _is_db_duplicate(emb: list[float], url: str, db, NewsVector) -> bool:
    """URL 일치 또는 벡터 거리 0.1 이하(유사도 0.9+)이면 중복"""
    if db.query(NewsVector.id).filter(NewsVector.url == url).scalar():
        return True

    nearest = (
        db.query(NewsVector)
        .filter(NewsVector.embedding.cosine_distance(emb) <= COSINE_DIST_THRESHOLD)
        .first()
    )
    return nearest is not None


# 퍼블릭 API
async def run(articles: list[Article]) -> list[Article]:
    """
    중복 제거 후 pgvector에 색인한 기사 목록을 반환.

    - 임베딩 실패 시: 원본 목록 반환 (에러 로깅)
    - pgvector 미지원 또는 DB 오류 시: 배치 내 중복 제거 결과 반환
    """
    if not articles:
        return []

    # 임베딩 텍스트: "제목 요약"
    texts = [f"{a.title} {a.summary}".strip() for a in articles]

    try:
        embeddings = await _embed_batch(texts)
    except Exception as e:
        logger.error("임베딩 실패 — 원본 반환: %s", e)
        return articles

    # 1단계: 배치 내 중복 제거
    unique_articles, unique_embeddings = _dedup_within_batch(articles, embeddings)
    logger.info(
        "배치 내 중복 제거: %d → %d건",
        len(articles),
        len(unique_articles),
    )

    # 2단계: pgvector DB 비교 + 색인
    try:
        from models.db_models import SessionLocal, NewsVector, PGVECTOR_AVAILABLE

        if not PGVECTOR_AVAILABLE or NewsVector is None:
            logger.warning("pgvector 미지원 — 배치 중복 제거 결과만 반환")
            return unique_articles

        db = SessionLocal()
        try:
            final: list[Article] = []
            to_index: list = []

            for article, emb in zip(unique_articles, unique_embeddings):
                if _is_db_duplicate(emb, article.url, db, NewsVector):
                    logger.debug("DB 중복 제거: %.60s", article.title)
                    continue
                final.append(article)
                to_index.append(
                    NewsVector(
                        news_cache_id=article.news_cache_id,
                        url=article.url,
                        embedding=emb,
                    )
                )

            if to_index:
                db.bulk_save_objects(to_index)
                db.commit()

            logger.info(
                "pgvector 색인 완료: %d건 신규 / %d건 DB 중복 제거",
                len(to_index),
                len(unique_articles) - len(final),
            )
            return final

        except Exception as e:
            db.rollback()
            logger.error("pgvector 색인 실패 — 배치 결과 반환: %s", e)
            return unique_articles
        finally:
            db.close()

    except ImportError as e:
        logger.error("DB 모듈 임포트 실패: %s", e)
        return unique_articles
