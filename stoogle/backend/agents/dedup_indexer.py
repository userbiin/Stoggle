# 기사 중복제거
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

EMBED_MODEL = "voyage-3"
EMBED_DIM = 1024
DEDUP_SIM_THRESHOLD = 0.82     # 배치 내 코사인 유사도 임계값 (패러프레이즈 쌍 실측 0.85 기준)
COSINE_DIST_THRESHOLD = 0.18   # pgvector cosine_distance 임계값 (= 1 - 0.82)
EMBED_BATCH_SIZE = 32          # Voyage AI 배치 한도 (128 최대, 안전하게 32 사용)


@dataclass
class Article:
    url: str
    title: str
    summary: str = ""
    news_cache_id: Optional[int] = None


# 임베딩
async def _embed_batch(texts: list[str]) -> list[list[float]]:
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key:
        return []

    import asyncio as _asyncio
    import voyageai
    client = voyageai.AsyncClient(api_key=api_key)

    embeddings: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        if i > 0:
            await _asyncio.sleep(21)   # 무료 3 RPM → 배치 간 21초 대기
        result = await client.embed(batch, model=EMBED_MODEL)
        embeddings.extend(result.embeddings)
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
        if any(_cosine_sim(emb, u_emb) >= DEDUP_SIM_THRESHOLD for _, u_emb in unique):
            logger.debug("배치 내 중복 제거: %.60s", article.title)
            continue
        unique.append((article, emb))

    if not unique:
        return [], []
    arts, embs = zip(*unique)
    return list(arts), list(embs)


# 2단계: DB(pgvector) 비교
def _is_db_duplicate(emb: list[float], url: str, db, NewsVector) -> bool:
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
    if not articles:
        return []

    # 임베딩 텍스트: "제목 요약"
    texts = [f"{a.title} {a.summary}".strip() for a in articles]

    try:
        embeddings = await _embed_batch(texts)
    except Exception as e:
        logger.error("임베딩 실패 — 원본 반환: %s", e)
        return articles

    if not embeddings:
        logger.warning("임베딩 결과 없음 (VOYAGE_API_KEY 미설정?) — 원본 반환")
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
