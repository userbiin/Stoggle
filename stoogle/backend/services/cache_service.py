"""
Redis 캐싱 서비스

저장 키 구조:
  Stoogle:registry           — 전종목 레지스트리 (JSON, 매주 갱신)
  Stoogle:price:{ticker}     — 종목 현재가 (TTL 60초)
  Stoogle:history:{ticker}   — 주가 히스토리 (TTL 10분)
  Stoogle:news:{ticker}      — 뉴스 목록 (TTL 1시간)
"""
import json
import os
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# TTL 상수 (초)
TTL_PRICE = 60          # 현재가: 1분
TTL_HISTORY = 600       # 히스토리: 10분
TTL_NEWS = 3600         # 뉴스: 60분
TTL_REGISTRY = 60 * 60 * 24 * 7  # 종목 레지스트리: 7일

KEY_REGISTRY = "Stoogle:registry"


def _get_client():
    """Redis 클라이언트 반환. 연결 실패 시 None 반환하여 캐싱을 무음 처리."""
    try:
        import redis
        client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception as e:
        logger.warning(f"Redis 연결 실패 (캐싱 비활성화): {e}")
        return None


# ---------------------------------------------------------------------------
# 종목 레지스트리
# ---------------------------------------------------------------------------

def get_ticker_registry() -> Optional[dict]:
    """
    Redis에서 종목 레지스트리를 조회한다.

    반환 형태:
      {
        "XXXXXX": {"ticker": "XXXXXX", "name": "종목명", "market": "KOSPI"},
        ...
      }
    ticker 키와 name 키 모두 검색에 사용할 수 있도록
    name_to_tickers 인덱스도 함께 저장된다.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(KEY_REGISTRY)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"레지스트리 조회 실패: {e}")
        return None


def set_ticker_registry(registry: dict) -> bool:
    """
    종목 레지스트리를 Redis에 저장한다.

    registry 형태:
      {
        "XXXXXX": {"ticker": "XXXXXX", "name": "종목명", "market": "KOSPI"},
        ...
      }
    """
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(KEY_REGISTRY, TTL_REGISTRY, json.dumps(registry, ensure_ascii=False))
        return True
    except Exception as e:
        logger.warning(f"레지스트리 저장 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# 현재가 캐싱
# ---------------------------------------------------------------------------

def get_price_cache(ticker: str) -> Optional[dict]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"Stoogle:price:{ticker}")
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"가격 캐시 조회 실패 ({ticker}): {e}")
        return None


def set_price_cache(ticker: str, data: dict, ttl: int = TTL_PRICE) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(f"Stoogle:price:{ticker}", ttl, json.dumps(data, ensure_ascii=False))
        return True
    except Exception as e:
        logger.warning(f"가격 캐시 저장 실패 ({ticker}): {e}")
        return False


# ---------------------------------------------------------------------------
# 주가 히스토리 캐싱
# ---------------------------------------------------------------------------

def get_history_cache(ticker: str) -> Optional[list]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"Stoogle:history:{ticker}")
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"히스토리 캐시 조회 실패 ({ticker}): {e}")
        return None


def set_history_cache(ticker: str, data: list, ttl: int = TTL_HISTORY) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(
            f"Stoogle:history:{ticker}", ttl,
            json.dumps(data, ensure_ascii=False, default=str)
        )
        return True
    except Exception as e:
        logger.warning(f"히스토리 캐시 저장 실패 ({ticker}): {e}")
        return False


# ---------------------------------------------------------------------------
# 뉴스 캐싱
# ---------------------------------------------------------------------------

def get_news_cache(ticker: str) -> Optional[list]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"Stoogle:news:{ticker}")
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"뉴스 캐시 조회 실패 ({ticker}): {e}")
        return None


def set_news_cache(ticker: str, data: list, ttl: int = TTL_NEWS) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(
            f"Stoogle:news:{ticker}", ttl,
            json.dumps(data, ensure_ascii=False, default=str)
        )
        return True
    except Exception as e:
        logger.warning(f"뉴스 캐시 저장 실패 ({ticker}): {e}")
        return False


# ---------------------------------------------------------------------------
# 범용 헬퍼
# ---------------------------------------------------------------------------

def cache_get(key: str) -> Optional[Any]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
        return True
    except Exception:
        return False
