# 캐시 서비스
import json
import os
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

TTL_PRICE    = 60
TTL_HISTORY  = 600
TTL_NEWS     = 1800
TTL_INSIGHT  = 3600
TTL_EDGES    = 60 * 60 * 24
TTL_REGISTRY = 60 * 60 * 24

KEY_REGISTRY = "Stoogle:registry"

_client: Optional[Any] = None


# Redis 클라이언트 싱글턴
def _get_client():
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


# 클라이언트 초기화
def _reset_client() -> None:
    global _client
    _client = None


# 레지스트리 조회
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


# 레지스트리 저장
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


# 현재가 조회
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


# 현재가 저장
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


# 히스토리 조회
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


# 히스토리 저장
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


# 뉴스 조회
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


# 뉴스 저장
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


# 범용 조회
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


# 범용 저장
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


# 인사이트 조회
def get_insight_cache(ticker: str) -> Optional[dict]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"Stoogle:insight:{ticker}")
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning("인사이트 캐시 조회 실패 (%s): %s", ticker, e)
        return None


# 인사이트 저장
def set_insight_cache(ticker: str, data: dict, ttl: int = TTL_INSIGHT) -> bool:
    client = _get_client()
    if client is None:
        return False
    payload = {**data, "_cached_at": time.time()}
    try:
        client.setex(
            f"Stoogle:insight:{ticker}", ttl,
            json.dumps(payload, ensure_ascii=False, default=str),
        )
        return True
    except Exception as e:
        logger.warning("인사이트 캐시 저장 실패 (%s): %s", ticker, e)
        return False


# 엣지 캐시 조회
def get_edges_cache(ticker: str) -> Optional[list]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"Stoogle:edges:{ticker}")
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.debug("edges 캐시 조회 실패 (%s): %s", ticker, e)
        return None


# 엣지 캐시 저장
def set_edges_cache(ticker: str, data: list, ttl: int = TTL_EDGES) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(
            f"Stoogle:edges:{ticker}", ttl,
            json.dumps(data, ensure_ascii=False, default=str),
        )
        return True
    except Exception as e:
        logger.debug("edges 캐시 저장 실패 (%s): %s", ticker, e)
        return False


# 엣지 캐시 무효화
def invalidate_edges_cache(ticker: str) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(f"Stoogle:edges:{ticker}")
        logger.debug("edges 캐시 무효화: %s", ticker)
    except Exception as e:
        logger.debug("edges 캐시 무효화 실패 (%s): %s", ticker, e)
