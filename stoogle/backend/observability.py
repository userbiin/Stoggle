"""
Stoogle Observability — MONITORING.md Step 1

집중 관리 대상:
  - JSON 구조화 로그 포매터 및 setup_logging()
  - track_llm_call() 데코레이터 (sync/async 모두 지원)
  - get_logger() 헬퍼

실제 log 호출(logger.info 등)은 각 모듈에 인라인으로 유지한다.
"""
import asyncio
import json
import logging
import time
from functools import wraps


class _JsonFormatter(logging.Formatter):
    """모든 로그를 JSON 한 줄로 직렬화한다."""

    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, dict):
            payload = dict(record.msg)
        else:
            try:
                payload = {"message": record.getMessage()}
            except TypeError:
                # pykrx 등 써드파티 라이브러리가 logging.info(tuple, {}) 형태로
                # 호출할 때 % 포매팅 실패 → msg를 문자열로 직접 변환
                payload = {"message": str(record.msg)}

        payload.setdefault("level", record.levelname)
        payload.setdefault("logger", record.name)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    """
    루트 로거에 JSON 포매터를 설치한다.
    main.py 앱 시작 시 한 번만 호출하면 된다.
    중복 호출은 무시된다.
    """
    root = logging.getLogger()
    if any(isinstance(h.formatter, _JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.setLevel(level)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """stoogle 네임스페이스 로거를 반환한다."""
    if not name.startswith("stoogle"):
        name = f"stoogle.{name}"
    return logging.getLogger(name)


def track_llm_call(agent_name: str):
    """
    LLM API 호출 함수에 붙이는 데코레이터.
    latency, token 사용량(응답 객체에 .usage 있을 때), 에러 타입을 JSON 로그로 남긴다.
    sync / async 함수 모두 지원한다.

    사용 예:
        @track_llm_call("summary")
        async def _summarize(body: str) -> str: ...
    """
    _logger = get_logger("agent")

    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                start = time.time()
                try:
                    result = await func(*args, **kwargs)
                    usage = getattr(result, "usage", None)
                    _logger.info({
                        "event": "llm_call",
                        "agent": agent_name,
                        "status": "success",
                        "input_tokens": getattr(usage, "input_tokens", None),
                        "output_tokens": getattr(usage, "output_tokens", None),
                        "latency_ms": round((time.time() - start) * 1000, 2),
                    })
                    return result
                except Exception as e:
                    _logger.error({
                        "event": "llm_call",
                        "agent": agent_name,
                        "status": "error",
                        "error_type": type(e).__name__,
                        "latency_ms": round((time.time() - start) * 1000, 2),
                    })
                    raise
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                start = time.time()
                try:
                    result = func(*args, **kwargs)
                    usage = getattr(result, "usage", None)
                    _logger.info({
                        "event": "llm_call",
                        "agent": agent_name,
                        "status": "success",
                        "input_tokens": getattr(usage, "input_tokens", None),
                        "output_tokens": getattr(usage, "output_tokens", None),
                        "latency_ms": round((time.time() - start) * 1000, 2),
                    })
                    return result
                except Exception as e:
                    _logger.error({
                        "event": "llm_call",
                        "agent": agent_name,
                        "status": "error",
                        "error_type": type(e).__name__,
                        "latency_ms": round((time.time() - start) * 1000, 2),
                    })
                    raise
            return sync_wrapper

    return decorator
