# 호출 추적
import asyncio
import json
import logging
import time
from functools import wraps

logger = logging.getLogger("stoogle.evaluation")

# 모듈/에이전트별 누적 통계: key = "{module}.{agent_name}"
_stats: dict[str, dict] = {}


def track_agent(agent_name: str, module: str):

    def decorator(func):
        key = f"{module}.{agent_name}"
        _stats.setdefault(
            key,
            {"calls": 0, "in_tok": 0, "out_tok": 0, "errors": 0, "total_ms": 0.0},
        )

        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                start = time.time()
                try:
                    result = await func(*args, **kwargs)
                    _record(key, result, start)
                    return result
                except Exception as e:
                    _record_err(key, e, start)
                    raise

            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                start = time.time()
                try:
                    result = func(*args, **kwargs)
                    _record(key, result, start)
                    return result
                except Exception as e:
                    _record_err(key, e, start)
                    raise

            return sync_wrapper

    return decorator


def _record(key: str, result, start: float) -> None:
    usage = getattr(result, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    ms = (time.time() - start) * 1000
    _stats[key]["calls"] += 1
    _stats[key]["in_tok"] += in_tok
    _stats[key]["out_tok"] += out_tok
    _stats[key]["total_ms"] += ms
    logger.info(
        json.dumps(
            {
                "event": "llm_call",
                "key": key,
                "status": "success",
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "latency_ms": round(ms, 2),
            },
            ensure_ascii=False,
        )
    )


def _record_err(key: str, exc: Exception, start: float) -> None:
    ms = (time.time() - start) * 1000
    _stats[key]["errors"] += 1
    logger.error(
        json.dumps(
            {
                "event": "llm_call",
                "key": key,
                "status": "error",
                "error_type": type(exc).__name__,
                "latency_ms": round(ms, 2),
            },
            ensure_ascii=False,
        )
    )


def get_agent_stats() -> dict:
    return dict(_stats)
