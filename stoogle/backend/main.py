# FastAPI 앱
import os
from dotenv import load_dotenv
load_dotenv()

import time
import logging
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from routers import search, insight, news, relations
from evaluation.metrics_api import router as metrics_router
from observability import setup_logging
setup_logging()

logger = logging.getLogger("stoogle.http")

_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=os.getenv("ENV", "development"),
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
    )

app = FastAPI(
    title="Stoogle API",
    description="주식 종목 인사이트 플랫폼 백엔드",
    version="0.1.0",
)

allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

_ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_BASE_URL = _ollama_base if not _ollama_base.endswith("/v1") else _ollama_base[:-3]
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "exaone3.5:7.8b")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# HTTP 로깅
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    logger.info({
        "event": "http_request",
        "method": request.method,
        "endpoint": request.url.path,
        "status_code": response.status_code,
        "latency_ms": round((time.time() - start) * 1000, 2),
        "ticker": request.path_params.get("ticker"),
    })
    return response

app.include_router(search.router, prefix="/api/v1")
app.include_router(insight.router, prefix="/api/v1")
app.include_router(news.router, prefix="/api/v1")
app.include_router(relations.router, prefix="/api/v1")
app.include_router(metrics_router)

from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402
Instrumentator().instrument(app).expose(app)


# 파비콘
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=200)


# 헬스체크
@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "version": "0.1.0"}


# Ollama 요청
class OllamaAskRequest(BaseModel):
    prompt: str = Field(..., min_length=1)


# Ollama 헬스
@app.get("/ollama/health")
async def ollama_health():
    return {
        "status": "ok",
        "ollama_base_url": OLLAMA_BASE_URL,
        "model": OLLAMA_MODEL,
    }


# Ollama 프록시
@app.post("/ollama/ask")
async def ollama_ask(req: OllamaAskRequest):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": req.prompt,
            }
        ],
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_ctx": 8192,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
            )
            response.raise_for_status()

    except httpx.ConnectError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Ollama server: {e}",
        ) from e

    except httpx.TimeoutException as e:
        raise HTTPException(
            status_code=504,
            detail="Ollama request timeout",
        ) from e

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=e.response.text,
        ) from e

    data = response.json()

    return {
        "model": data.get("model"),
        "answer": data.get("message", {}).get("content", ""),
    }
