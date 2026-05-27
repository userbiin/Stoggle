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

# 모듈 레벨 싱글턴 — 프로세스당 한 개의 연결 풀을 재사용
_client: Optional[Any] = None


def _get_client():
    """
    Redis 싱글턴 반환.
    최초 호출 시 연결 생성 후 모듈 레벨로 보관.
    Redis 미가용 시 None 반환 (캐싱 비활성화).
    연결 오류 후 _reset_client() 가 호출되면 다음 요청에서 재연결 시도.
    """
    global _client
    if _client is not None:
        return _client
    try:
        import redis
        client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        _client = client
        return _client
    except Exception as e:
        logger.warning(f"Redis 연결 실패 (캐싱 비활성화): {e}")
        return None


def _reset_client() -> None:
    """연결 오류 감지 시 싱글턴 초기화 — 다음 호출에서 재연결 시도."""
    global _client
    _client = None


# ---------------------------------------------------------------------------
# 종목 레지스트리
# ---------------------------------------------------------------------------

def get_ticker_registry() -> Optional[dict]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(KEY_REGISTRY)
        result = json.loads(raw) if raw else None
        logger.debug({"event": "cache_hit" if result else "cache_miss", "key_prefix": "registry"})
        return result
    except Exception as e:
        logger.warning(f"레지스트리 조회 실패: {e}")
        _reset_client()
        return None


def set_ticker_registry(registry: dict) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(KEY_REGISTRY, TTL_REGISTRY, json.dumps(registry, ensure_ascii=False))
        return True
    except Exception as e:
        logger.warning(f"레지스트리 저장 실패: {e}")
        _reset_client()
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
        result = json.loads(raw) if raw else None
        logger.debug({"event": "cache_hit" if result else "cache_miss", "key_prefix": "price", "ticker": ticker})
        return result
    except Exception as e:
        logger.warning(f"가격 캐시 조회 실패 ({ticker}): {e}")
        _reset_client()
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
        _reset_client()
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
        result = json.loads(raw) if raw else None
        logger.debug({"event": "cache_hit" if result else "cache_miss", "key_prefix": "history", "ticker": ticker})
        return result
    except Exception as e:
        logger.warning(f"히스토리 캐시 조회 실패 ({ticker}): {e}")
        _reset_client()
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
        _reset_client()
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
        result = json.loads(raw) if raw else None
        logger.debug({"event": "cache_hit" if result else "cache_miss", "key_prefix": "news", "ticker": ticker})
        return result
    except Exception as e:
        logger.warning(f"뉴스 캐시 조회 실패 ({ticker}): {e}")
        _reset_client()
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
        _reset_client()
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
        _reset_client()
        return None


def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
        return True
    except Exception:
        _reset_client()
        return False
