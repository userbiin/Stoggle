# 모니터링
import asyncio
import json
import logging
import time
from functools import wraps


# JSON 포매터
class _JsonFormatter(logging.Formatter):

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
    root = logging.getLogger()
    if any(isinstance(h.formatter, _JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.setLevel(level)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    if not name.startswith("stoogle"):
        name = f"stoogle.{name}"
    return logging.getLogger(name)


def track_llm_call(agent_name: str):
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
