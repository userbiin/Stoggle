# Stoogle — Company Relationship & Impact Inference Module

> **모듈 목적**: 뉴스가 떴을 때 주가 변동 가능성이 있는 종목 리스트를 제공한다. 단, 가격 동조화로 뻔하게 묶이는 대형주(삼성전자→SK하이닉스)가 아니라, 사업 관계(공급사·고객사·경쟁사·계열사)를 통해 실제로 영향받는 비자명한 종목까지 끌어낸다.
>
> **핵심 전환**: 가격 상관계수 기반 관계 도출 ❌ → 사업 관계 그래프 기반 영향 전파 추론 ⭕

최종 update : 2026-05-31

---

## 📌 프로젝트 컨텍스트

이 문서는 **Stoogle** — "주식 전용 구글" 컨셉의 KRX 종목 인사이트 플랫폼 — 의 기업 관계/영향 추론 모듈 재설계안이다. 계획서(1.2 최종 목표 3항)에 명시된 "뉴스 → 목표 기업 → 연관 기업 → 종목군 영향"의 확산 경로 추론을 실제로 구현하기 위한 설계 문서다.

관련 코드: `services/relation_service.py`, `agents/news_agent.py`, `agents/analysis_agent.py`, `tasks.py`

---

## 🔴 현재 구조와 한계

### 현재 3단계 레이어

| 단계 | 위치 | 역할 |
|------|------|------|
| 1. 가격 상관계수 | `relation_service.py:76-134` `compute_relations` | KOSPI200 상위 9개와 90일 종가 Pearson 상관계수 → 고정 임계값으로 관계 유형 분류 |
| 2. 뉴스 LLM 영향 추론 | `relation_service.py:159-207` `compute_impact` | 종목 뉴스 상위 10개 + 1단계 관계사 목록 → Claude가 영향 종목·방향 판단 |
| 3. 통합 분석 | `analysis_agent.py:302-381` `run` | 뉴스 + DART + RelationCache + pgvector + 과거 정확도 → GPT-4o structured output |

현재 1단계의 관계 유형 분류 매핑:

| 상관계수 구간 | 관계 유형 |
|---------------|-----------|
| 0.8 ~ 1.0 | 경쟁 |
| 0.6 ~ 0.8 | 협력 |
| 0.4 ~ 0.6 | 공급망 |
| 0.0 ~ 0.4 | 관심 |

### 왜 대형주만 출력되는가 (근본 원인 3가지)

**1. 후보 생성이 "KOSPI200 상위 9개"로 고정됨.**
`compute_relations`가 비교 대상을 KOSPI200 상위 종목으로 뽑으므로, 솔브레인·원익IPS 같은 중소 소재·장비사나 유통업체는 *애초에 후보에 들어오지 못한다*. 2단계 LLM은 1단계 관계 리스트 안에서만 고르도록 스키마 검증되므로, 후보에 없는 종목은 LLM이 아무리 잘 추론해도 결과에서 잘린다.
→ **병목은 추론이 아니라 후보 생성이다.**

**2. Pearson 상관계수는 "사업 관계"가 아니라 "동조화"를 측정한다.**
수익률은 대략 다음처럼 분해된다.

```
종목수익률 ≈ α + β_market·시장수익률 + β_sector·섹터수익률 + 고유요인
```

두 반도체 대형주가 0.85로 나오는 것은 둘이 경쟁/협력이라서가 아니라 시장 베타 + 섹터 베타가 공통으로 들어가서다. 섹터 상승장에서 같은 섹터 대형주는 고유요인이 무엇이든 무조건 높게 상관된다. 따라서 "0.8↑ = 경쟁" 매핑은 사실상 *"같이 큰, 같은 섹터 종목"*을 잡는 장치이지 관계를 잡는 것이 아니다. 삼성전자↔SK하이닉스가 항상 1등으로 나오는 것이 그 증거다.

**3. 2단계 LLM이 관계 리스트 내부에서만 선택하도록 필터링된다.**
새 종목을 만들어내지 못한다.

### 부수적 문제

- DART 기반 관계 분류는 DB 정의는 있으나 `update_relation_graphs` 태스크가 실제로 DART를 쓰는지 불분명.
- `analyze_single_ticker` Celery 태스크가 어떤 라우트에도 연결되지 않아 3단계 분석이 실시간 트리거되지 않음.

---

## 🎯 재설계 핵심: 두 개를 분리한다

지금은 "가격 상관계수"라는 하나의 신호로 *관계 구조*와 *영향 전파*를 동시에 풀려 해서 둘 다 안 된다. 이를 분리한다.

| 구분 | 무엇 | 변화 속도 | 출처 |
|------|------|-----------|------|
| **관계 구조** | 누가 누구와 거래하나, 무슨 관계인가 | 천천히 변함 (오프라인 구축) | DART + 뉴스 + LLM |
| **영향 전파** | 이 뉴스에 누가, 어느 방향으로 움직이나 | 이벤트마다 달라짐 (온라인) | 이벤트 + 그래프 탐색 + LLM 추론 |

**가격 상관계수의 새 역할**: 구조를 *만드는* 데 쓰지 않는다. 엣지 가중치(weight)나 사후 검증(Calibrator의 D+3 비교)에만 쓴다.

---

## 🏗️ 시스템 파이프라인

```
[오프라인] 사업 관계 그래프 구축
┌─────────────┐  ┌─────────────┐  ┌──────────────┐
│  DART 공시   │  │ 뉴스 코퍼스 │  │ LLM 관계 추출 │
│ 계열·출자·  │  │ 공급사·     │  │  엣지 정규화  │
│   거래      │  │  고객사     │  │              │
└──────┬──────┘  └──────┬──────┘  └──────┬───────┘
       └────────────────┼────────────────┘
                        ↓
        ┌───────────────────────────────────┐
        │  사업 관계 그래프 (company_edges)   │ ← 가격 상관계수
        │  공급·고객·경쟁·계열 (typed·directed)│   (엣지 가중치만)
        └───────────────┬───────────────────┘
                        │ (관계 그래프 RAG)
[온라인] 영향 전파 추론  │
                        │
   신규 뉴스 ─→ ┌─────────────────┐
                │  이벤트 추출      │  유형·시드 기업·극성
                └────────┬─────────┘
                         ↓
                ┌─────────────────┐
                │   그래프 탐색     │ ← 시드 1~2홉 → 후보 확장
                └────────┬─────────┘   (비대형주 포함)
                         ↓
                ┌─────────────────┐
                │ EXAONE 싼 필터   │  0~5점, 통과분만
                └────────┬─────────┘
                         ↓
                ┌─────────────────┐
                │ GPT-4o 통합 추론 │  방향·근거·신뢰도
                └────────┬─────────┘
                         ↓
                ┌─────────────────┐
                │ 영향 종목 리스트 │  비대형주 포함 랭킹
                └─────────────────┘
```

**핵심**: 후보가 그래프 이웃에서 나오므로 시총·상관계수와 무관하게 솔브레인 같은 중소사가 후보에 포함된다. EXAONE→GPT-4o 2단계 필터는 기존 비용 철학을 *후보 스코어링*에 재사용하는 것이라 추가 비용 부담이 거의 없다 (후보가 20개로 늘어도 GPT-4o는 EXAONE 통과분 몇 개에만 호출).

---

## 💎 비대형주 후보를 만드는 핵심: DART가 금광

"당연하지 않은 기업"을 끌어내려면 후보가 **사업 관계 그래프의 이웃**에서 나와야 한다. 그 그래프의 명시적·named 엣지는 DART에서 거의 공짜로 나온다.

| DART 항목 | 추출되는 엣지 | 비고 |
|-----------|---------------|------|
| 특수관계자 거래 주석 (재무제표 주석) | 계열사·관계사 (거래 상대 *기업명 + 거래 금액(원)*) | hard label, named |
| 타법인 출자 현황 | 지배·투자 엣지 | named |
| 사업의 내용 > 주요 원재료 및 매입처 | 공급사 엣지 | 일부 매입처 명시 |
| 사업의 내용 > 매출처 | 고객사 엣지 | 대형 고객사명은 "주요 고객 A"로 가려지는 경우 多 |

**DART가 가린 빈칸은 뉴스가 채운다.** "한미반도체가 SK하이닉스에 HBM 본더 공급" 같은 문장은 DART엔 없지만 뉴스엔 있다. 따라서:

- **DART** → named·구조적 엣지 (계열·출자·관계자 거래) — LLM 거의 불필요한 파싱
- **뉴스 LLM 추출** → 공급망·고객 엣지 (DART가 마스킹한 부분)

두 출처 모두 `(회사1, 회사2, 관계유형, 방향, 근거문장)` 형태로 추출해 한 테이블에 쌓는다.

규모는 ~2,500종목이라 1~2홉 탐색에 Neo4j 같은 그래프 DB도 필요 없다. 이미 있는 Postgres에 엣지 테이블 하나면 충분하다.

```sql
CREATE TABLE company_edges (
    src VARCHAR(10),                 -- 종목코드
    dst VARCHAR(10),
    relation_type VARCHAR(20),       -- supplier | customer | competitor | affiliate | distributor
    direction VARCHAR(10),           -- src→dst 의 방향성 힌트
    weight FLOAT,                    -- 상관계수/거래비중 등으로 보강
    confidence FLOAT,
    evidence TEXT,                   -- 근거 문장 (설명가능성)
    source VARCHAR(20),              -- dart | news | llm
    updated_at TIMESTAMPTZ,
    PRIMARY KEY (src, dst, relation_type)
);
```

---

## 🧠 진짜 "인사이트"가 나오는 곳: 방향 추론

뉴스가 떴을 때 흐름:

1. **이벤트 추출** (LLM): `{이벤트 유형, 시드 기업/섹터, 극성(호재/악재)}`
2. **시드 매칭**: 직접 언급/직접 섹터 기업
3. **그래프 탐색 1~2홉**: 후보 확장 (여기서 비대형주가 *이웃이라서* 후보에 들어옴)
4. **싼 필터**: EXAONE이 `(이벤트 + 후보 + 관계 경로)`를 0~5점, 통과분만
5. **GPT-4o structured output**: 후보별 방향·신뢰도·근거

방향은 **(시드의 이벤트 극성) × (엣지 유형)**으로 결정된다. 이것이 "삼성 오르면 SK하이닉스 오른다"와 다른 비자명한 추론이다.

| 시드 이벤트 | 엣지 유형 | 이웃 종목 방향 | 예시 |
|-------------|-----------|----------------|------|
| 악재 (수요↓) | 고객사 → 공급사 | negative | 완성차 판매 부진 → 부품사 negative |
| 악재 (생산 차질·화재) | 공급사 → 고객사 | negative | 부품 수급 차질 |
| 악재 (생산 차질·화재) | 공급사 → 경쟁 공급사 | **positive** | 반사이익 |
| 악재 (리콜·규제) | 경쟁사 | **positive** | 반사이익 |
| 호재 (보조금·정책) | 수혜 섹터 + 공급망 | positive | 정책 수혜 전파 |

**뻔한 결과 제거**: 사업 엣지(supplier/customer/distributor) 없이 가격 상관만 높은 쌍(삼성↔SK하이닉스 같은)은 별도 태깅하거나 down-rank. 사업 엣지가 있는 후보를 위로 올리는 것이 곧 "당연한 결과 제거"다.

---

## 🔬 Worked Example — 반도체 수출 규제

뉴스: **"미국, 대중 반도체 장비 수출 규제 강화"**

| 단계 | 결과 |
|------|------|
| 이벤트 추출 | `{유형: 수출규제, 시드: 반도체장비 섹터, 극성: negative}` |
| 시드 | 삼성전자·SK하이닉스 (← 여기까진 당연한 결과) |
| 그래프 1홉 | SK하이닉스의 *공급사*로 등록된 한미반도체(HBM 본더)·원익IPS(증착장비)·솔브레인(소재) → 후보 진입 |
| GPT-4o 방향 추론 | "고객사(SK하이닉스) 장비 도입 차질 → 장비 공급사 매출 이연 → **negative**", 근거 문장 첨부 |

현재 구조라면 솔브레인은 KOSPI200 상위 9개에 없어서 시작도 못 했을 종목이다. 이것이 원하던 "당연하지 않은 인사이트"다.

---

## 🔧 기존 코드에 붙이기

| # | 작업 | 대상 |
|---|------|------|
| 1 | 후보 생성을 "KOSPI200 상위 9개" → "`company_edges` 1~2홉 이웃"으로 교체. Pearson은 엣지 weight 계산으로 강등 | `relation_service.compute_relations` |
| 2 | `relation_type` 분류를 상관계수 구간 매핑에서 분리. DART(특수관계자·출자) + 뉴스 LLM 추출에서 유형을 받도록 변경 | `relation_service.py` |
| 3 | `analysis_agent`(structured output)는 그대로 재사용. 입력 컨텍스트의 RelationCache를 새 그래프 이웃으로 교체 | `analysis_agent.run` |
| 4 | 라우트에 미연결된 `analyze_single_ticker` Celery 태스크를 뉴스 인입 트리거에 연결 (3단계 실시간 구동) | `tasks.py` |

EXAONE→GPT-4o 2단계 필터는 이미 가진 비용 철학을 *후보 스코어링*에 재사용하는 것이므로 추가 비용 부담 거의 없음.

---

## 📅 마감(~6/9) 고려 우선순위

가장 효과 대비 빠른 첫 작업:

1. **DART 특수관계자 거래·타법인 출자 주석에서 named 엣지를 뽑아 `company_edges`를 채우기** — LLM 추론이 거의 필요 없는 파싱 작업. 하루이틀이면 "비대형주가 실제로 후보에 들어오는" 효과를 바로 검증 가능.
2. 그래프 1~2홉 탐색 + 섹터 동조화 필터 구현 (`compute_relations` 교체).
3. 뉴스 LLM 엣지 추출로 공급망·고객 엣지 보강.
4. `analyze_single_ticker` 트리거 연결.
5. Calibrator D+3 비교를 엣지 weight 사후 보정에 반영.

---

## 🎓 학술적 근거 (계획서 인용 문헌과 일치)

이 접근은 계획서에 이미 인용된 다음 연구와 같은 노선이라 보고서 정당화가 자연스럽다.

- **Zhou et al. (2025)** [5] — LLM zero-shot stock relationship graph 추출. 정적 그래프의 한계를 LLM으로 보완.
- **AlMahri et al. (2025)** [7] — GPT-4 기반 zero-shot supply chain knowledge graph 구축.
- **Nam and Seong (2019)** [4] — 한국 시장에서 기업 간 causality of influence 반영 시 예측 성능 개선.
- **Xu et al. (2025)** [6] — 뉴스에서 추출한 기업 관계를 그래프 edge feature로 사용.

---

## ⚠️ 주의사항

1. **상관계수는 관계가 아니다.** 절대 후보 생성/관계 유형 분류에 단독 사용 금지. weight·검증 용도만.
2. **Look-ahead 금지.** 그래프는 뉴스 시점 이전 데이터로 구축, 영향 추론에 미래 정보 유입 차단.
3. **DART 고객사 마스킹.** 매출처는 "주요 고객 A"로 가려지는 경우 많음 → 뉴스 엣지로 보완 필요.
4. **방향 추론은 (극성 × 엣지유형) 조합.** 같은 공급사 엣지라도 시드가 호재냐 악재냐에 따라 부호가 뒤집힘.
5. **저작권.** 뉴스 본문 evidence 저장 시 학술 목적 한정, 공개 시 주의.

---

## ⚡ 레이턴시 병목 분석 및 개선 전략

> **배경**: 로그 분석 결과 `/api/v1/insight/{ticker}`의 cold path가 최대 20초 이상 소요된다. 이는 관계 모듈 재설계와 맞물려 함께 해결해야 하는 구조적 문제다.

### Cold Path 병목 구조

현재 첫 요청 시 아래 작업이 모두 **직렬(serial)**로 실행된다:

| 단계 | 위치 | 소요(추정) | 설명 |
|------|------|-----------|------|
| 1 | cache_miss 확인 | ~1ms | Redis/메모리 조회 실패 |
| 2 | 네이버 금융 크롤링 | 2~8초 | HTTP 외부 요청, JPype JVM 포함 |
| 3 | 가격 상관계수 계산 | 1~3초 | `compute_relations` (90일 Pearson) |
| 4 | Claude API 호출 | 5~10초 | LLM structured output |
| 5 | 캐시 저장 | ~10ms | `insight_cache` upsert |

→ **합계 약 8~21초**. 단계 2~4가 모두 직렬이라 어느 하나만 느려져도 전체가 늦어진다.

---

### 전략 1 — Warm-up Cache ★ 1순위

**핵심**: 사용자 요청이 오기 전에 미리 캐시를 채워두는 것. Cold start 자체를 없앤다.

`tasks.py`에 이미 APScheduler/Celery 구조가 존재하므로 추가 인프라 없이 구현 가능하다. 장 시작 전(08:30) 인기 종목을 선제 분석해 캐시를 채운다.

```python
# tasks.py
@scheduler.scheduled_job("cron", hour="8", minute="30")
async def warmup_popular_tickers():
    popular = await get_popular_tickers(n=50)  # 조회수 기준 상위 N개
    for ticker in popular:
        if not await cache.get(f"insight:{ticker}"):
            await run_full_pipeline(ticker)    # 크롤링 + LLM + 캐시 저장
    logger.info(f"Warm-up 완료: {len(popular)}개 종목")
```

구현 포인트:
- 관계 모듈 재설계 완료 후에는 `company_edges` 1~2홉 이웃도 warm-up 대상에 포함
- TTL을 3600초(1시간)로 설정하면 장중 자연 갱신 주기와 맞아떨어짐
- warm-up 완료 시 `/metrics`에 커버리지 카운터 노출 → Prometheus로 추적

| 상황 | 현재 | Warm-up 적용 후 |
|------|------|----------------|
| 인기 종목 첫 요청 | 8~21초 | ~5ms (캐시 히트) |
| 비인기 종목 첫 요청 | 8~21초 | 8~21초 (별도 전략 필요) |

---

### 전략 2 — asyncio.gather 병렬 처리 ★ 2순위

**핵심**: 독립적인 I/O 작업(크롤링·히스토리·관계)을 동시에 실행해 직렬 시간을 단축한다.

현재 news, history, relations 조회가 순차적으로 실행되지만, 세 작업은 서로 의존성이 없으므로 `asyncio.gather`로 동시 실행이 가능하다.

```python
# 현재 (직렬): 합계 최대 ~14초
news    = await fetch_news(ticker)      # 3~8초
history = await fetch_history(ticker)   # 1~3초
related = await fetch_relations(ticker) # 1~3초

# 개선 (병렬): max(각 작업) = 3~8초
news, history, related = await asyncio.gather(
    fetch_news(ticker),        # ┐
    fetch_history(ticker),     # ├─ 동시 실행
    fetch_relations(ticker),   # ┘
)
insight = await call_claude(news, history, related)  # 5~10초
```

> **주의**: JPype JVM 초기화는 동기적이라 첫 실행 시 블로킹된다. JVM warm-up을 서버 `lifespan` startup 이벤트에서 미리 처리해야 병렬화 효과가 제대로 난다.

---

### 전략 3 — Stale-While-Revalidate 패턴 ★ 3순위

**핵심**: 캐시가 낡아도 일단 즉시 반환하고, 백그라운드에서 갱신한다. 사용자는 기다리지 않는다.

```python
async def get_insight(ticker: str):
    cached = await cache.get(f"insight:{ticker}")

    if cached:
        age = time.time() - cached["updated_at"]
        if age < 1800:      # FRESH (30분 이내): 즉시 반환
            return cached
        elif age < 7200:    # STALE (2시간 이내): 즉시 반환 + 백그라운드 갱신
            asyncio.create_task(refresh_insight(ticker))
            return cached   # ← 사용자는 기다리지 않음

    return await refresh_insight(ticker)  # EXPIRED: 블로킹 (Cold path)
```

관계 모듈 연계: 재설계 후 관계 구조(오프라인, 천천히 변함)와 영향 전파(온라인, 이벤트마다 변함)의 TTL을 분리 관리한다.

| key_prefix | 권장 TTL | 근거 |
|------------|---------|------|
| `price` | 60초 | 실시간성 최우선 |
| `news` | 1800초 | 뉴스 갱신 주기 30분 |
| `insight` | 3600초 | LLM 비용 절감 + 장중 1회 갱신 |
| `history` | 86400초 | 과거 가격은 불변 |
| `company_edges` | 86400초 | 관계 구조는 하루 단위 오프라인 갱신 |
| `registry` | 86400초 | 종목 메타데이터 불변 |

`company_edges` 캐시 주의: 오프라인 그래프 재구축(`update_relation_graphs`) 완료 시 해당 ticker의 엣지 캐시를 flush하도록 연결한다.

---

### 전략 4 — LLM Streaming 응답 (프론트 작업 필요)

**핵심**: 분석 결과가 생성되는 즉시 클라이언트로 전송. 20초를 기다리는 대신 텍스트가 실시간으로 출력된다.

```python
@router.get("/api/v1/insight/{ticker}")
async def get_insight_stream(ticker: str, stream: bool = False):
    if not stream:
        return await get_insight(ticker)  # 기존 방식

    async def generate():
        async with anthropic.messages.stream(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": prompt}]
        ) as s:
            async for text in s.text_stream:
                yield f"data: {json.dumps({'delta': text})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

> **주의**: `analysis_agent`의 GPT-4o structured output은 전체 응답을 받아야 파싱 가능하다. 텍스트 요약 파트와 structured 파트를 분리하는 리팩토링이 선행되어야 한다.

---

### 통합 로드맵: 병목 개선 × 관계 모듈 재설계

마감(~6/9)을 고려한 통합 순서. **DART 엣지 구축(1번)과 `compute_relations` 교체(3번)는 두 문제를 동시에 해결**하므로 먼저 집중한다.

| 순서 | 작업 | 대상 파일 | 레이턴시 효과 | 관계 모듈 효과 |
|------|------|----------|-------------|--------------|
| 1 | DART 엣지 추출 → `company_edges` 채우기 | `tasks.py`, `relation_service.py` | Warm-up 대상 확장 (비대형주 포함) | 비대형주 후보 진입 즉시 검증 가능 |
| 2 | `asyncio.gather` 병렬화 | `routers/insight.py`, `news_agent.py` | Cold path ~50% 단축 | 영향 없음 (독립 작업) |
| 3 | `compute_relations` 교체 (1~2홉 탐색) | `relation_service.compute_relations` | Pearson 계산 제거 → 1~3초 단축 | 핵심: 비자명 종목 후보 생성 |
| 4 | Warm-up Cache (장 시작 전 선제 분석) | `tasks.py` | 인기 종목 Cold start 제거 | 3번 완료 후 비대형주까지 warm-up |
| 5 | `analyze_single_ticker` 트리거 연결 | `tasks.py` | Stale-While-Revalidate 기반 실시간 갱신 | 3단계 분석 실시간 구동 |
| 6 | TTL 계층화 + `company_edges` invalidation | `services/cache_service.py` | 불필요한 LLM 재호출 감소 | 그래프 갱신-캐시 연동 |
| 7 (선택) | LLM Streaming 응답 | `routers/insight.py` | 체감 레이턴시 개선 (UX) | 프론트 작업 필요 |

---

## 🐛 부수 버그: 빈 JSON 응답 파싱 오류

로그에 반복 등장하는 아래 오류는 외부 API(네이버 금융)가 빈 body를 반환할 때 발생한다.

```
{"message": "Expecting value: line 1 column 1 (char 0)"}
```

발생 컨텍스트:
- `('20260523', '20260528', '066570')` → 날짜 범위 가격 조회
- `('20260528', '1028')` → 특정 날짜/ID 조회

수정:
```python
raw = await response.text()
if not raw.strip():
    logger.warning(f"Empty response for {params}")
    return None  # 또는 이전 캐시 값 유지
data = json.loads(raw)
```

현재 `INFO` 레벨로 로깅되고 있어 에러가 묻힌다. `WARNING` 레벨로 변경하고 Sentry 알림 대상에 포함시키는 것을 권장한다.

---

## 🔩 JPype JVM Restricted Method 경고

로그에 반복 등장:

```
WARNING: java.lang.System::load has been called by org.jpype.JPypeContext
WARNING: Restricted methods will be blocked in a future release
```

단기 해결 — JVM 시작 옵션 추가:
```python
jpype.startJVM(
    jpype.getDefaultJVMPath(),
    "--enable-native-access=ALL-UNNAMED",
    convertStrings=False
)
```

장기적으로는 JPype 의존성을 제거하고 `FinanceDataReader` 또는 `pykrx` 같은 순수 Python 라이브러리로 교체하는 것을 권장한다. JVM cold start 비용(로그상 ~700ms)도 함께 제거된다.
