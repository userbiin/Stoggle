"""
Redis 캐싱 서비스

저장 키 구조:
  Stoogle:registry           — 전종목 레지스트리   (TTL 24h)
  Stoogle:price:{ticker}     — 현재가              (TTL 60s)
  Stoogle:history:{ticker}   — 주가 히스토리        (TTL 24h, 장중 10분)
  Stoogle:news:{ticker}      — 뉴스 목록           (TTL 30min)
  Stoogle:insight:{ticker}   — LLM 분석 결과       (TTL 60min)
  Stoogle:edges:{ticker}     — company_edges 탐색  (TTL 24h, 재구축 시 무효화)

TTL 계층화 근거 (README §레이턴시 병목 분석):
  - price   60s   : 실시간성 최우선
  - news    1800s : 뉴스 갱신 주기 30분
  - insight 3600s : LLM 비용 절감 + 장중 1회 갱신
  - history 86400s: 과거 가격은 불변
  - edges   86400s: 관계 구조는 하루 단위 오프라인 갱신
  - registry86400s: 종목 메타데이터 불변
"""
import json
import os
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# TTL 상수 (초)
TTL_PRICE    = 60                # 현재가: 1분
TTL_HISTORY  = 600               # 히스토리 기본값 (장중); stock_service에서 장외는 86400
TTL_NEWS     = 1800              # 뉴스: 30분 (기존 1시간 → 단축)
TTL_INSIGHT  = 3600              # LLM 인사이트: 1시간
TTL_EDGES    = 60 * 60 * 24     # company_edges 탐색 결과: 24시간
TTL_REGISTRY = 60 * 60 * 24     # 종목 레지스트리: 24시간 (기존 7일 → 단축)

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


# ---------------------------------------------------------------------------
# LLM 인사이트 분석 캐싱 (Stale-While-Revalidate 지원)
# ---------------------------------------------------------------------------

def get_insight_cache(ticker: str) -> Optional[dict]:
    """
    Redis에서 인사이트 분석 캐시를 조회한다.
    반환 dict에 '_cached_at' 필드(unix timestamp)가 포함되어 있어
    호출부에서 나이를 계산할 수 있다.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"Stoogle:insight:{ticker}")
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning("인사이트 캐시 조회 실패 (%s): %s", ticker, e)
        return None


def set_insight_cache(ticker: str, data: dict, ttl: int = TTL_INSIGHT) -> bool:
    """
    인사이트 분석 결과를 Redis에 저장한다.
    '_cached_at' 타임스탬프를 자동으로 추가한다.
    """
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


# ---------------------------------------------------------------------------
# company_edges 탐색 결과 캐싱 (update_relation_graphs 시 무효화)
# ---------------------------------------------------------------------------

def get_edges_cache(ticker: str) -> Optional[list]:
    """company_edges 1~2홉 탐색 결과를 Redis에서 조회한다."""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"Stoogle:edges:{ticker}")
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.debug("edges 캐시 조회 실패 (%s): %s", ticker, e)
        return None


def set_edges_cache(ticker: str, data: list, ttl: int = TTL_EDGES) -> bool:
    """company_edges 탐색 결과를 Redis에 저장한다."""
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


def invalidate_edges_cache(ticker: str) -> None:
    """
    company_edges 재구축(update_relation_graphs) 완료 시 캐시를 무효화한다.
    다음 요청에서 DB를 새로 탐색해 최신 그래프를 반영한다.
    """
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(f"Stoogle:edges:{ticker}")
        logger.debug("edges 캐시 무효화: %s", ticker)
    except Exception as e:
        logger.debug("edges 캐시 무효화 실패 (%s): %s", ticker, e)
