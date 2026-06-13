# Stoogle — Observability & Monitoring 명세

> **목적**: Stoogle 서비스의 로그·메트릭·트레이스를 수집하여 성능 병목, 장애, 비용, 품질을 추적한다.
> **대상 스택**: FastAPI 백엔드 · Celery 파이프라인 · LLM 에이전트(Claude/EXAONE) · Redis · PostgreSQL(pgvector)
> **구현 원칙**: 캡스톤 규모에 맞게 로그 우선 → 메트릭 → 알림 순으로 점진 적용

---

## 0. 용어 정리

| 용어 | 의미 |
|------|------|
| Observability | 시스템 내부 상태를 외부 출력(로그/메트릭/트레이스)으로 파악하는 능력 |
| Logs | "무슨 일이 일어났나" — 이벤트 단위 기록 |
| Metrics | "얼마나 자주/많이" — 수치 시계열 |
| Traces | "어디서 얼마나 걸렸나" — 요청의 단계별 경로 |
| SLI / SLO | 서비스 수준 지표 / 목표값 (예: P99 latency < 1s를 99.9% 보장) |
| P50 / P95 / P99 | 응답시간 분포의 백분위수. P99 = 상위 1% 느린 요청 |

---

## 1. 계층별 모니터링 지표

### 1.1 FastAPI 백엔드 (Application Layer)

| 지표 | 설명 | 임계값(제안) | 수집 위치 |
|------|------|-------------|-----------|
| 엔드포인트별 Latency (P50/P95/P99) | API 응답 시간 분포 | P99 < 2s | HTTP 미들웨어 |
| Error Rate (4xx/5xx) | 전체 요청 중 에러 비율 | 5xx < 1% | HTTP 미들웨어 |
| RPS (Requests Per Second) | 초당 요청 수 | — (추세 관찰) | HTTP 미들웨어 |
| 엔드포인트별 호출 빈도 | `/insight`, `/search` 등 인기도 | — | HTTP 미들웨어 |
| In-flight Requests | 동시 처리 중인 요청 수 | worker 수 기준 | HTTP 미들웨어 |

> **주의 엔드포인트**: `/api/v1/insight/{ticker}`는 pykrx + 뉴스 크롤링 + LLM 호출이 합쳐져 가장 느림. 별도 추적 필수.

### 1.2 Celery 파이프라인 (Worker Layer)

| 지표 | 설명 | 임계값(제안) | 수집 위치 |
|------|------|-------------|-----------|
| Task 성공/실패율 | 태스크별 성공 비율 | 실패율 < 5% | Celery signals |
| Task 처리 시간 | 태스크 실행 소요 시간 | 주기 < 스케줄 간격 | Celery signals |
| Queue 길이 | 대기 중인 태스크 수 | 지속 증가 시 경고 | Redis broker |
| Task Retry 횟수 | 재시도 발생 빈도 | 외부 API 불안정 신호 | Celery signals |
| 스케줄 지연 (Schedule Drift) | 예정 시각 대비 실제 실행 지연 | < 5분 | Celery beat |

> **핵심 태스크**: `crawl_all_news`(매시), `fetch_dart_filings`(매일), `recompute_correlations`(매일). 1시간 주기 태스크가 1시간 안에 끝나는지가 핵심.

### 1.3 LLM 에이전트 (AI Layer)

| 지표 | 설명 | 임계값(제안) | 수집 위치 |
|------|------|-------------|-----------|
| Token 사용량 (입력/출력) | 에이전트별 토큰 소비 → 비용 직결 | 일일 예산 대비 | API response.usage |
| 에이전트별 호출 횟수 | summary/relevance/news 각각 | — | 에이전트 래퍼 |
| LLM 응답 Latency | API 호출 왕복 시간 | P95 < 10s | 에이전트 래퍼 |
| TTFT (Time To First Token) | 첫 토큰까지 시간 (스트리밍 시) | — | 스트리밍 콜백 |
| Fallback 발생률 | API 키 없음/실패로 fallback 전환 빈도 | < 10% | 에이전트 래퍼 |
| 에러 타입 분류 | Rate limit / 네트워크 / 파싱 실패 | — | 에이전트 래퍼 |
| 관련성 점수 분포 | relevance_agent 0~5점 분포 | 편향 감지 | relevance_agent |
| 게이트 통과율 | 4점 이상 통과 기사 비율 | — | 파이프라인 게이트 |

> **품질 지표(선택)**: 통합 분석 에이전트의 structured output 파싱 성공률, 영향 종목 추론 결과의 근거 문장 누락률.

### 1.4 Redis 캐시 (Cache Layer)

| 지표 | 설명 | 임계값(제안) | 수집 위치 |
|------|------|-------------|-----------|
| Cache Hit Rate | 캐시 적중률 | > 80% | cache_service |
| Memory Usage | Redis 메모리 사용량 | < maxmemory 90% | Redis INFO |
| Eviction Rate | 메모리 부족으로 강제 삭제 | 0에 가깝게 | Redis INFO |
| Connected Clients | 연결 클라이언트 수 | — | Redis INFO |
| Key별 TTL 만료 패턴 | registry/price/news 캐시 만료 | — | cache_service |

> **캐시 키 종류**: 종목 레지스트리, 현재가, 90일 히스토리, page=1 뉴스. 각각 hit rate를 분리 추적하면 어떤 캐시가 비효율인지 보임.

### 1.5 PostgreSQL / pgvector (Storage Layer)

| 지표 | 설명 | 임계값(제안) | 수집 위치 |
|------|------|-------------|-----------|
| Query Latency (Slow Query) | 느린 쿼리 탐지 | > 1s 로깅 | pg_stat_statements |
| pgvector 검색 Latency | 벡터 유사도 검색 시간 | P95 < 500ms | 쿼리 래퍼 |
| Connection Pool 사용률 | 연결 수 / 최대 연결 | < 80% | SQLAlchemy pool |
| 테이블별 Row 증가량 | news/news_vectors 폭증 감지 | — | 주기적 COUNT |
| Index Hit Rate | 인덱스 사용 비율 | > 99% | pg_stat_user_tables |

> **Supabase 주의**: 무료 플랜은 동시 연결 수 제한이 있음. Connection Pool 사용률을 반드시 추적.

### 1.6 인프라 (Infrastructure Layer)

| 지표 | 설명 | 임계값(제안) |
|------|------|-------------|
| CPU Usage | 프로세스/컨테이너 CPU | < 80% |
| Memory Usage | RAM 사용량 (OOM 방지) | < 85% |
| Disk Usage | 디스크 잔여 공간 | < 80% |
| Network I/O | 외부 API 호출 대역폭 | 추세 관찰 |

> GPU 지표는 현재 외부 LLM API(Claude/EXAONE)를 호출하므로 자체 서버에는 불필요. 만약 임베딩 모델(BGE-M3 등)을 로컬 GPU로 돌리게 되면 GPU Utilization / VRAM 추가.

---

## 2. 구현 단계 (우선순위)

### Step 1 — 구조화 로그 (즉시, 코드 수정만)

모든 로그를 JSON 구조로 남겨 나중에 파싱·집계 가능하게 한다.

**1.1 FastAPI 요청 로그 미들웨어** (`backend/main.py`)

```python
import time, logging
from fastapi import Request

logger = logging.getLogger("stoogle")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000, 2)
    logger.info({
        "event": "http_request",
        "endpoint": request.url.path,
        "method": request.method,
        "status_code": response.status_code,
        "latency_ms": duration_ms,
        "ticker": request.path_params.get("ticker"),
    })
    return response
```

**1.2 LLM 에이전트 래퍼** (`backend/agents/*.py`)

```python
import time, logging
logger = logging.getLogger("stoogle.agent")

def track_llm_call(agent_name: str):
    """에이전트 호출을 감싸 토큰/지연/실패를 로깅하는 데코레이터."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                usage = getattr(result, "usage", None)
                logger.info({
                    "event": "llm_call",
                    "agent": agent_name,
                    "status": "success",
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                    "latency_ms": round((time.time() - start) * 1000, 2),
                })
                return result
            except Exception as e:
                logger.error({
                    "event": "llm_call",
                    "agent": agent_name,
                    "status": "error",
                    "error_type": type(e).__name__,
                    "latency_ms": round((time.time() - start) * 1000, 2),
                })
                raise
        return wrapper
    return decorator
```

**1.3 캐시 hit/miss 로그** (`backend/services/cache_service.py`)

```python
def get_cached(key: str):
    value = redis_client.get(key)
    logger.info({
        "event": "cache_hit" if value else "cache_miss",
        "key_prefix": key.split(":")[0],
    })
    return value
```

**1.4 Celery 태스크 로그** (`backend/tasks.py`)

```python
from celery.signals import task_prerun, task_postrun, task_failure

_task_start = {}

@task_prerun.connect
def on_start(task_id, task, **kw):
    _task_start[task_id] = time.time()

@task_postrun.connect
def on_done(task_id, task, retval, state, **kw):
    duration = time.time() - _task_start.pop(task_id, time.time())
    logger.info({
        "event": "celery_task",
        "task": task.name,
        "state": state,
        "duration_s": round(duration, 2),
    })

@task_failure.connect
def on_fail(task_id, exception, **kw):
    logger.error({
        "event": "celery_task_failure",
        "error_type": type(exception).__name__,
    })
```

### Step 2 — 메트릭 수집 (Prometheus + Grafana)

```bash
pip install prometheus-fastapi-instrumentator flower
```

```python
# backend/main.py
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)  # GET /metrics 자동 노출
```

```yaml
# docker-compose.yml 에 추가
services:
  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports: ["9090:9090"]
  grafana:
    image: grafana/grafana
    ports: ["3001:3000"]
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
```

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'stoogle-api'
    scrape_interval: 15s
    static_configs:
      - targets: ['host.docker.internal:8000']
```

Celery 태스크 모니터링:

```bash
celery -A tasks flower --port=5555   # http://localhost:5555
```

### Step 3 — 에러 트래킹 (Sentry)

```bash
pip install "sentry-sdk[fastapi]"
```

```python
# backend/main.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

sentry_sdk.init(
    dsn="<SENTRY_DSN>",
    integrations=[FastApiIntegration(), CeleryIntegration()],
    traces_sample_rate=0.1,
)
```

---

## 3. 전체 스택 구성

```
[코드 레벨]   구조화 JSON 로그 (Python logging)
                    |
[수집/저장]   Prometheus (메트릭 15초 scrape)
                    |
[시각화]      Grafana 대시보드
                    |
[알림]        Sentry (에러) + Grafana Alert (임계값)

[전용 UI]     Celery Flower (태스크) · Supabase Dashboard (DB)
```

---

## 4. 권장 Grafana 대시보드 패널

```
[API P95 Latency]   [API Error Rate]   [RPS]
[Cache Hit Rate]    [Redis Memory]     [Celery Queue 길이]
[LLM Token 사용량]  [Agent Fallback율] [Slow Query 수]
```

---

## 5. SLO 제안 (캡스톤 발표용)

| SLI | SLO 목표 |
|-----|---------|
| `/search` API 응답 P95 | < 1s |
| `/insight` API 응답 P95 | < 5s (LLM 포함) |
| 5xx 에러율 | < 1% |
| Redis 캐시 히트율 | > 80% |
| `crawl_all_news` 성공률 | > 95% |
| LLM fallback율 | < 10% |

---

## 6. 구현 체크리스트

- [ ] FastAPI 요청 로그 미들웨어 추가
- [ ] LLM 에이전트 래퍼/데코레이터 적용 (summary, relevance, news, dedup)
- [ ] cache_service hit/miss 로그
- [ ] Celery signals 기반 태스크 로그
- [ ] prometheus-fastapi-instrumentator 연동
- [ ] docker-compose에 Prometheus + Grafana 추가
- [ ] Celery Flower 실행
- [ ] Sentry 연동 (FastAPI + Celery)
- [ ] Grafana 대시보드 구성
- [ ] SLO 임계값 기반 알림 설정
