"""
SQLAlchemy ORM 모델 + 테이블 생성 진입점

실행: python models/db_models.py
"""
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Float, Integer,
    DateTime, Text, Boolean, UniqueConstraint, event,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from dotenv import load_dotenv

try:
    from pgvector.sqlalchemy import Vector
    PGVECTOR_AVAILABLE = True
except ImportError:
    PGVECTOR_AVAILABLE = False

EMBED_DIM = 1536  # text-embedding-3-small

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./Stoogle.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    ticker = Column(String(10), primary_key=True)
    name = Column(String(100), nullable=False, index=True)
    market = Column(String(20))
    sector = Column(String(100))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True)
    date = Column(String(10))
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_ticker_date"),)


class NewsCache(Base):
    __tablename__ = "news_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True)
    title = Column(String(500))
    source = Column(String(100))
    published_at = Column(String(30))
    url = Column(String(1000), unique=True)
    sentiment = Column(String(20), default="neutral")
    summary = Column(Text)
    category = Column(String(50))
    fetched_at = Column(DateTime, default=datetime.utcnow)


class InsightCache(Base):
    __tablename__ = "insight_cache"

    ticker = Column(String(10), primary_key=True)
    summary = Column(Text)
    keywords_json = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RelationCache(Base):
    __tablename__ = "relation_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True)
    related_ticker = Column(String(10))
    correlation = Column(Float)
    relation_type = Column(String(50))
    reason = Column(String(500))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("ticker", "related_ticker", name="uq_relation"),)


class DartAnalysis(Base):
    __tablename__ = "dart_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True, nullable=False)
    filed_at = Column(String(10))       # YYYY-MM-DD, nullable
    revenue = Column(Float)             # 매출액 (억원)
    op_profit = Column(Float)           # 영업이익 (억원)
    capex = Column(Float)               # 설비투자 (억원)
    inventory = Column(Float)           # 재고자산 (억원)
    insight = Column(Text)
    text_hash = Column(String(64), unique=True, nullable=False, index=True)
    analyzed_at = Column(DateTime, default=datetime.utcnow)


class PredictionLog(Base):
    __tablename__ = "prediction_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True, nullable=False)   # 예측 대상 종목
    source_ticker = Column(String(10), index=True)            # 분석 출처 종목
    direction = Column(String(10), nullable=False)            # up/down/neutral
    confidence = Column(Float, nullable=False)                # 원시 confidence 0.0~1.0
    calibrated_confidence = Column(Float)                     # 보정 후 (nullable)
    reason = Column(Text)                                     # 판단 근거 (임베딩용)
    prediction_date = Column(String(10), index=True)          # D+0 YYYY-MM-DD
    target_date = Column(String(10), index=True)              # D+3 YYYY-MM-DD
    predicted_at = Column(DateTime, default=datetime.utcnow)
    base_close = Column(Float)                                # D+0 종가
    actual_close = Column(Float)                              # D+3 종가 (사후)
    actual_direction = Column(String(10))                     # 실제 방향 (사후)
    is_correct = Column(Boolean)                              # 방향 일치 여부 (사후)
    evaluated_at = Column(DateTime)                           # 평가 시각


if PGVECTOR_AVAILABLE:
    class NewsVector(Base):
        __tablename__ = "news_vectors"

        id = Column(Integer, primary_key=True, autoincrement=True)
        news_cache_id = Column(Integer, index=True, nullable=True)
        url = Column(String(1000), unique=True, nullable=False)
        embedding = Column(Vector(EMBED_DIM), nullable=False)
        indexed_at = Column(DateTime, default=datetime.utcnow)

    class PredictionVector(Base):
        __tablename__ = "prediction_vectors"

        id = Column(Integer, primary_key=True, autoincrement=True)
        prediction_log_id = Column(Integer, index=True, unique=True, nullable=False)
        embedding = Column(Vector(EMBED_DIM), nullable=False)  # reason 텍스트 임베딩
        is_correct = Column(Boolean)
        calibrated_confidence = Column(Float)
        indexed_at = Column(DateTime, default=datetime.utcnow)

    class DartChunk(Base):
        __tablename__ = "dart_chunks"

        id = Column(Integer, primary_key=True, autoincrement=True)
        ticker = Column(String(10), index=True, nullable=False)
        corp_code = Column(String(8))
        rcept_no = Column(String(14), index=True)   # DART 접수번호
        report_nm = Column(String(200))              # 보고서명
        section_title = Column(String(200))          # 섹션 제목
        chunk_index = Column(Integer, default=0)     # 섹션 내 청크 순서
        content = Column(Text)
        token_count = Column(Integer)
        embedding = Column(Vector(EMBED_DIM), nullable=False)
        indexed_at = Column(DateTime, default=datetime.utcnow)

        __table_args__ = (
            UniqueConstraint("rcept_no", "section_title", "chunk_index", name="uq_dart_chunk"),
        )
else:
    NewsVector = None        # type: ignore[assignment,misc]
    PredictionVector = None  # type: ignore[assignment,misc]
    DartChunk = None         # type: ignore[assignment,misc]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_news_cache_url_unique(db_engine=None) -> None:
    """
    news_cache.url 에 UNIQUE 인덱스를 추가하는 마이그레이션.

    실행 순서:
      1. URL 기준 중복 행 제거 (MAX(id) — 가장 최근 행 보존)
      2. CREATE UNIQUE INDEX IF NOT EXISTS 으로 인덱스 추가
    이미 인덱스가 존재하면 아무것도 하지 않는다.
    """
    from sqlalchemy import text, inspect as sa_inspect

    if db_engine is None:
        db_engine = engine

    insp = sa_inspect(db_engine)

    if not insp.has_table("news_cache"):
        print("news_cache 테이블 없음 — skip")
        return

    indexes = insp.get_indexes("news_cache")
    if any(idx.get("unique") and "url" in idx.get("column_names", []) for idx in indexes):
        print("news_cache.url UNIQUE 인덱스 이미 존재 — skip")
        return

    with db_engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM news_cache WHERE id NOT IN ("
            "  SELECT MAX(id) FROM news_cache GROUP BY url"
            ")"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_news_cache_url ON news_cache(url)"
        ))
    print("news_cache.url UNIQUE 인덱스 추가 완료")


if __name__ == "__main__":
    if PGVECTOR_AVAILABLE and not DATABASE_URL.startswith("sqlite"):
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
    Base.metadata.create_all(bind=engine)
    migrate_news_cache_url_unique()
    print("DB 테이블 생성 완료:", DATABASE_URL)
