"""
SQLAlchemy ORM 모델 + 테이블 생성 진입점

실행: python models/db_models.py
"""
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Float, Integer,
    DateTime, Text, UniqueConstraint, event,
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

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./stoggle.db")

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
    url = Column(String(1000))
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


if PGVECTOR_AVAILABLE:
    class NewsVector(Base):
        __tablename__ = "news_vectors"

        id = Column(Integer, primary_key=True, autoincrement=True)
        news_cache_id = Column(Integer, index=True, nullable=True)
        url = Column(String(1000), unique=True, nullable=False)
        embedding = Column(Vector(EMBED_DIM), nullable=False)
        indexed_at = Column(DateTime, default=datetime.utcnow)
else:
    NewsVector = None  # type: ignore[assignment,misc]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


if __name__ == "__main__":
    if PGVECTOR_AVAILABLE and not DATABASE_URL.startswith("sqlite"):
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
    Base.metadata.create_all(bind=engine)
    print("DB 테이블 생성 완료:", DATABASE_URL)
