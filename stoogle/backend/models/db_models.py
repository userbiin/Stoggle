# DB 모델
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Float, Integer,
    DateTime, Text, Boolean, UniqueConstraint, PrimaryKeyConstraint, event,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from dotenv import load_dotenv

try:
    from pgvector.sqlalchemy import Vector
    PGVECTOR_AVAILABLE = True
except ImportError:
    PGVECTOR_AVAILABLE = False

EMBED_DIM = 1024

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./Stoogle.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# 기본 모델
class Base(DeclarativeBase):
    pass


# 종목 테이블
class Company(Base):
    __tablename__ = "companies"

    ticker = Column(String(10), primary_key=True)
    name = Column(String(100), nullable=False, index=True)
    market = Column(String(20))
    sector = Column(String(100))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# 가격 히스토리 테이블
class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True)
    date = Column(String(10))
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_ticker_date"),)


# 뉴스 캐시 테이블
class NewsCache(Base):
    __tablename__ = "news_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True)
    title = Column(String(500))
    source = Column(String(100))
    published_at = Column(String(30))
    url = Column(String(1000))
    sentiment = Column(String(20), default="neutral")
    summary = Column(Text)
    category = Column(String(50))
    fetched_at = Column(DateTime, default=datetime.utcnow)


# 인사이트 캐시 테이블
class InsightCache(Base):
    __tablename__ = "insight_cache"

    ticker = Column(String(10), primary_key=True)
    summary = Column(Text)
    keywords_json = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# 관계 캐시 테이블
class RelationCache(Base):
    __tablename__ = "relation_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True)
    related_ticker = Column(String(10))
    correlation = Column(Float)
    relation_type = Column(String(50))
    reason = Column(String(500))
    source = Column(String(20), default="correlation")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("ticker", "related_ticker", name="uq_relation"),)


# 관계 엣지 테이블
class CompanyEdge(Base):
    __tablename__ = "company_edges"

    src = Column(String(10), nullable=False)
    dst = Column(String(10), nullable=False)
    relation_type = Column(String(20), nullable=False)
    direction = Column(String(10))
    weight = Column(Float)
    confidence = Column(Float)
    evidence = Column(Text)
    source = Column(String(20))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("src", "dst", "relation_type"),
    )


# DART 분석 테이블
class DartAnalysis(Base):
    __tablename__ = "dart_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True, nullable=False)
    filed_at = Column(String(10))
    revenue = Column(Float)
    op_profit = Column(Float)
    capex = Column(Float)
    inventory = Column(Float)
    insight = Column(Text)
    text_hash = Column(String(64), unique=True, nullable=False, index=True)
    analyzed_at = Column(DateTime, default=datetime.utcnow)


# 예측 로그 테이블
class PredictionLog(Base):
    __tablename__ = "prediction_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), index=True, nullable=False)
    source_ticker = Column(String(10), index=True)
    direction = Column(String(10), nullable=False)
    confidence = Column(Float, nullable=False)
    calibrated_confidence = Column(Float)
    reason = Column(Text)
    model_version = Column(String(50))
    prediction_date = Column(String(10), index=True)
    target_date = Column(String(10), index=True)
    predicted_at = Column(DateTime, default=datetime.utcnow)
    base_close = Column(Float)
    actual_close = Column(Float)
    actual_direction = Column(String(10))
    actual_change = Column(Float)
    abnormal_return = Column(Float)
    is_correct = Column(Boolean)
    status = Column(String(10), default="pending")
    evaluated_at = Column(DateTime)
    base_price_date = Column(String(10))
    latest_source_pubdate = Column(DateTime)


# 환각 로그 테이블
class HallucinationLog(Base):
    __tablename__ = "hallucination_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent = Column(String(50), index=True)
    module = Column(String(50))
    checked = Column(Integer, default=0)
    invalid_ticker = Column(Integer, default=0)
    missing_evidence = Column(Integer, default=0)
    faithfulness = Column(Float)
    logged_at = Column(DateTime, default=datetime.utcnow)


# 시장 모델 파라미터 테이블
class MarketModelParam(Base):
    __tablename__ = "market_model_params"

    ticker = Column(String(10), primary_key=True)
    estimation_date = Column(String(10), primary_key=True)
    alpha = Column(Float)
    beta = Column(Float)
    r_squared = Column(Float)


if PGVECTOR_AVAILABLE:
    # 뉴스 벡터 테이블
    class NewsVector(Base):
        __tablename__ = "news_vectors"

        id = Column(Integer, primary_key=True, autoincrement=True)
        news_cache_id = Column(Integer, index=True, nullable=True)
        url = Column(String(1000), unique=True, nullable=False)
        embedding = Column(Vector(EMBED_DIM), nullable=False)
        indexed_at = Column(DateTime, default=datetime.utcnow)

    # 예측 벡터 테이블
    class PredictionVector(Base):
        __tablename__ = "prediction_vectors"

        id = Column(Integer, primary_key=True, autoincrement=True)
        prediction_log_id = Column(Integer, index=True, unique=True, nullable=False)
        embedding = Column(Vector(EMBED_DIM), nullable=False)
        is_correct = Column(Boolean)
        calibrated_confidence = Column(Float)
        indexed_at = Column(DateTime, default=datetime.utcnow)

    # DART 청크 테이블
    class DartChunk(Base):
        __tablename__ = "dart_chunks"

        id = Column(Integer, primary_key=True, autoincrement=True)
        ticker = Column(String(10), index=True, nullable=False)
        corp_code = Column(String(8))
        rcept_no = Column(String(14), index=True)
        report_nm = Column(String(200))
        section_title = Column(String(200))
        chunk_index = Column(Integer, default=0)
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


# DB 세션 생성
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 마이그레이션 실행
def run_migrations() -> None:
    from sqlalchemy import text

    is_sqlite = DATABASE_URL.startswith("sqlite")

    columns_to_add = [
        ("prediction_log", "model_version", "VARCHAR(50)"),
        ("prediction_log", "actual_change", "FLOAT"),
        ("prediction_log", "abnormal_return", "FLOAT"),
        ("prediction_log", "status", "VARCHAR(10) DEFAULT 'pending'"),
        ("prediction_log", "base_price_date", "VARCHAR(10)"),
        ("prediction_log", "latest_source_pubdate", "TIMESTAMP"),
        ("relation_cache", "source", "VARCHAR(20) DEFAULT 'correlation'"),
    ]

    with engine.connect() as conn:
        for table, col, col_type in columns_to_add:
            try:
                if is_sqlite:
                    result = conn.execute(text(f"PRAGMA table_info({table})"))
                    existing = {row[1] for row in result.fetchall()}
                    if col not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                else:
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}")
                    )
            except Exception:
                pass

        try:
            if is_sqlite:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_company_edges_src ON company_edges (src)"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_company_edges_dst ON company_edges (dst)"
                ))
            else:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_company_edges_src ON company_edges (src)"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_company_edges_dst ON company_edges (dst)"
                ))
        except Exception:
            pass

        conn.commit()


if __name__ == "__main__":
    if PGVECTOR_AVAILABLE and not DATABASE_URL.startswith("sqlite"):
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
    Base.metadata.create_all(bind=engine)
    run_migrations()
    print("DB 테이블 생성 + 마이그레이션 완료:", DATABASE_URL)
