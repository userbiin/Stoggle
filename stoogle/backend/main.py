import os
import time
import logging
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from dotenv import load_dotenv

from routers import search, insight, news, relations
from observability import setup_logging

load_dotenv()
setup_logging()

logger = logging.getLogger("stoogle.http")

# ── Sentry (에러 트래킹) ────────────────────────────────────────────────────
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
        # 10% 요청 샘플링 — 트레이스 비용 절감
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
    )

app = FastAPI(
    title="Stoogle API",
    description="주식 종목 인사이트 플랫폼 백엔드",
    version="0.1.0",
)

allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

# ── Prometheus 메트릭 (/metrics 엔드포인트 자동 노출) ─────────────────────
from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402
Instrumentator().instrument(app).expose(app)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=200)


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "version": "0.1.0"}
