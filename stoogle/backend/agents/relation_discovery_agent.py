"""
기업 관계 발굴 에이전트

가격 상관계수(동조화) 대신 뉴스·DART 공시에서 실제 비즈니스 관계를 발굴한다.
납품업체·공급사·유통사·계열사·고객사·협력사 관계를 Claude structured output으로 추출하고
RelationCache에 source='news'|'dart' 로 upsert한다.

흐름:
  1. 기준 종목의 최신 뉴스 제목+요약 수집
  2. DartChunk 테이블에서 거래처·공급망 관련 섹션 추출
  3. Claude tool_use 1회 호출로 실제 비즈니스 관계 구조화
  4. 기업명 → 종목코드 매핑 (레지스트리 역방향 조회 + 부분 매칭)
  5. RelationCache upsert (발굴된 관계는 correlation 기반보다 항상 우선)

반환: 저장된 관계 수 (int)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Claude Structured Output Schema
# ─────────────────────────────────────────────────────────────────────────────

class _RelationItem(BaseModel):
    company_name: str = Field(..., description="언급된 기업명 (원문 그대로)")
    relation_type: str = Field(
        ...,
        description="납품업체|공급사|유통사|계열사|경쟁사|고객사|협력사 중 하나",
    )
    reason: str = Field(..., description="관계 근거 — 원문 문장 직접 인용 포함, 한국어 1문장")
    source: str = Field(..., description="근거 출처: 'news' 또는 'dart'")
    confidence: float = Field(..., ge=0.0, le=1.0, description="확신도 0.0~1.0")


class _DiscoverySchema(BaseModel):
    relations: list[_RelationItem] = Field(
        ...,
        description=(
            "발굴된 실제 비즈니스 관계 목록. "
            "주가 동조화(섹터 전반 등락)는 절대 포함하지 말 것. "
            "실제 납품·공급·유통·계열·고객·협력 관계만."
        ),
    )


_SYSTEM_PROMPT = """\
당신은 한국 주식 시장 공급망·산업 분석 전문가입니다.
아래 뉴스 기사와 DART 공시 본문을 읽고, 기준 기업과 **실제 비즈니스 거래 관계**가 있는 기업들을 발굴하세요.

발굴 원칙:
- 포함 대상: 납품·공급·유통·계열·고객·협력 관계처럼 실제 계약·거래·지배구조로 연결된 기업
- 제외 대상: 단순 주가 동조화(예: "반도체주 전반이 함께 오름"), 같은 섹터라는 이유만으로 언급된 기업
- 근거 필수: 관계 근거가 제공된 원문에 명시되어 있어야 함 — 추측·상식 기반 추론 금지
- confidence 기준: 원문에 직접 명시=0.85~1.0 / 간접 암시=0.5~0.84 / 추정=0.3~0.49
- 한 기업이 여러 관계 유형을 가질 수 있음 (예: 동시에 납품업체이자 경쟁사)
- company_name은 원문에 등장한 기업명 그대로 사용 (괄호·주식회사 표기 포함 무관)
"""


# ─────────────────────────────────────────────────────────────────────────────
# Context builders
# ─────────────────────────────────────────────────────────────────────────────

async def _get_news_text(ticker: str, max_articles: int = 15) -> str:
    try:
        from services.news_service import fetch_news, rank_news

        items = await fetch_news(ticker, page=1)
        ranked = await rank_news(items)
        lines = []
        for i, item in enumerate(ranked[:max_articles]):
            lines.append(f"[뉴스 {i + 1}] {item.title}")
            if item.summary:
                lines.append(f"  요약: {item.summary}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("뉴스 수집 실패 (%s): %s", ticker, e)
        return ""


_DART_PARTNER_KEYWORDS = [
    "거래처", "협력사", "납품", "공급", "유통", "계열사",
    "고객사", "매출처", "원재료", "매입처", "수주", "발주",
]


def _get_dart_text(ticker: str, max_chunks: int = 10) -> str:
    """DART 공시에서 거래처·공급망 관련 섹션 추출. pgvector 미사용 시 빈 문자열."""
    try:
        from models.db_models import DartChunk, SessionLocal, PGVECTOR_AVAILABLE

        if not PGVECTOR_AVAILABLE or DartChunk is None:
            return ""

        from sqlalchemy import or_

        db = SessionLocal()
        try:
            rows = (
                db.query(DartChunk)
                .filter(
                    DartChunk.ticker == ticker,
                    or_(
                        *[DartChunk.section_title.ilike(f"%{kw}%") for kw in _DART_PARTNER_KEYWORDS],
                        *[DartChunk.content.ilike(f"%{kw}%") for kw in _DART_PARTNER_KEYWORDS[:6]],
                    ),
                )
                .order_by(DartChunk.indexed_at.desc())
                .limit(max_chunks)
                .all()
            )
            if not rows:
                return ""

            parts = [
                f"[DART 공시 — {row.section_title}]\n{row.content[:600]}"
                for row in rows
            ]
            return "\n\n".join(parts)
        finally:
            db.close()
    except Exception as e:
        logger.debug("DART 텍스트 조회 실패 (%s): %s", ticker, e)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Ticker name resolution
# ─────────────────────────────────────────────────────────────────────────────

_LEGAL_RE = re.compile(r"[\(（]?주[\)）]|^주식회사\s*|\s*㈜\s*", re.IGNORECASE)


def _normalize(name: str) -> str:
    return _LEGAL_RE.sub("", name).strip()


def _build_reverse_registry(registry: dict) -> dict[str, str]:
    """registry {ticker: {name: ...}} → {name_variant: ticker}"""
    rev: dict[str, str] = {}
    for ticker, info in registry.items():
        raw = info.get("name", "")
        if not raw:
            continue
        rev[raw] = ticker
        norm = _normalize(raw)
        if norm and norm != raw:
            rev[norm] = ticker
    return rev


def _resolve_ticker(company_name: str, reverse_registry: dict[str, str]) -> Optional[str]:
    # 1. 정확 매칭
    if company_name in reverse_registry:
        return reverse_registry[company_name]
    norm = _normalize(company_name)
    if norm in reverse_registry:
        return reverse_registry[norm]

    # 2. 부분 매칭 (추출명이 3자 이상이고 등록명에 포함되거나 반대)
    if len(norm) >= 3:
        for reg_name, ticker in reverse_registry.items():
            if norm in reg_name or reg_name in norm:
                return ticker

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Relation type mapping (발굴 유형 → RelationCache 유형)
# ─────────────────────────────────────────────────────────────────────────────

_TYPE_MAP = {
    "납품업체": "공급망",
    "공급사": "공급망",
    "유통사": "공급망",
    "고객사": "공급망",
    "계열사": "협력",
    "협력사": "협력",
    "경쟁사": "경쟁",
}


def _map_relation_type(raw_type: str) -> str:
    return _TYPE_MAP.get(raw_type, "협력")


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

_EDGE_TYPE_MAP = {
    "납품업체": "supplier",
    "공급사": "supplier",
    "고객사": "customer",
    "유통사": "distributor",
    "계열사": "affiliate",
    "협력사": "affiliate",
    "경쟁사": "competitor",
}


def _to_edge_type(raw_type: str) -> str:
    return _EDGE_TYPE_MAP.get(raw_type, "affiliate")


def _save_relations(ticker: str, resolved: list[dict]) -> int:
    """RelationCache + company_edges upsert. 발굴 관계(news/dart)는 correlation 기반보다 항상 우선."""
    if not resolved:
        return 0
    try:
        from models.db_models import RelationCache, CompanyEdge, SessionLocal
        from datetime import datetime

        db = SessionLocal()
        try:
            count = 0
            for item in resolved:
                related_ticker = item["ticker"]

                # ── RelationCache upsert ─────────────────────────────────────
                existing = (
                    db.query(RelationCache)
                    .filter(
                        RelationCache.ticker == ticker,
                        RelationCache.related_ticker == related_ticker,
                    )
                    .first()
                )

                existing_source = getattr(existing, "source", "correlation") if existing else None

                if existing:
                    if existing_source != "correlation":
                        if item["confidence"] > (existing.correlation or 0):
                            existing.relation_type = item["relation_type"]
                            existing.reason = item["reason"]
                            existing.source = item["source"]
                            existing.correlation = item["confidence"]
                            existing.updated_at = datetime.utcnow()
                            count += 1
                    else:
                        existing.relation_type = item["relation_type"]
                        existing.reason = item["reason"]
                        existing.source = item["source"]
                        existing.correlation = item["confidence"]
                        existing.updated_at = datetime.utcnow()
                        count += 1
                else:
                    db.add(
                        RelationCache(
                            ticker=ticker,
                            related_ticker=related_ticker,
                            correlation=item["confidence"],
                            relation_type=item["relation_type"],
                            reason=item["reason"],
                            source=item["source"],
                        )
                    )
                    count += 1

                # ── company_edges upsert ────────────────────────────────────
                # 발굴된 관계를 사업 관계 그래프 엣지로도 저장
                edge_type = item.get("edge_type") or _to_edge_type(
                    item["reason"].split("]")[0].lstrip("[") if item["reason"].startswith("[") else item["relation_type"]
                )
                existing_edge = (
                    db.query(CompanyEdge)
                    .filter(
                        CompanyEdge.src == ticker,
                        CompanyEdge.dst == related_ticker,
                        CompanyEdge.relation_type == edge_type,
                    )
                    .first()
                )
                if existing_edge:
                    if item["confidence"] > (existing_edge.confidence or 0):
                        existing_edge.confidence = item["confidence"]
                        existing_edge.evidence = item["reason"]
                        existing_edge.source = item["source"]
                        existing_edge.updated_at = datetime.utcnow()
                else:
                    db.add(
                        CompanyEdge(
                            src=ticker,
                            dst=related_ticker,
                            relation_type=edge_type,
                            direction="forward",
                            confidence=item["confidence"],
                            evidence=item["reason"],
                            source=item["source"],
                        )
                    )

            db.commit()
            return count
        finally:
            db.close()
    except Exception as e:
        logger.error("RelationCache/company_edges 저장 실패 [%s]: %s", ticker, e)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# LLM call
# ─────────────────────────────────────────────────────────────────────────────

async def _call_claude(client, model: str, user_prompt: str) -> Optional[_DiscoverySchema]:
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[
                {
                    "name": "output_relations",
                    "description": "발굴한 실제 비즈니스 관계 목록을 구조화하여 출력",
                    "input_schema": _DiscoverySchema.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": "output_relations"},
        )
        for block in response.content:
            if block.type == "tool_use":
                return _DiscoverySchema(**block.input)
    except Exception as e:
        logger.error("Claude 관계 발굴 호출 실패 [%s]: %s", ticker if "ticker" in dir() else "?", e)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def discover_relations(ticker: str) -> int:
    """
    뉴스 + DART 공시에서 실제 비즈니스 관계를 발굴하여 RelationCache에 저장.

    Returns
    -------
    int: 저장된 관계 수 (0 = 발굴 없음 또는 API 키 미설정)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — 관계 발굴 건너뜀")
        return 0

    news_text = await _get_news_text(ticker)
    dart_text = _get_dart_text(ticker)

    if not news_text and not dart_text:
        logger.info("[%s] 관계 발굴 컨텍스트 없음 (뉴스·DART 모두 없음)", ticker)
        return 0

    from services.stock_service import get_or_build_registry

    registry = get_or_build_registry()
    company_name = registry.get(ticker, {}).get("name", ticker)
    reverse_registry = _build_reverse_registry(registry)

    sections = [f"기준 기업: {company_name} ({ticker})"]
    if news_text:
        sections.append(f"[뉴스 기사]\n{news_text}")
    if dart_text:
        sections.append(f"[DART 공시]\n{dart_text}")
    user_prompt = "\n\n".join(sections)

    model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    result = await _call_claude(client, model, user_prompt)

    if result is None or not result.relations:
        logger.info("[%s] 발굴된 관계 없음", ticker)
        return 0

    # 기업명 → 종목코드 매핑 + 중복 제거 (같은 related_ticker는 confidence 높은 쪽 유지)
    best: dict[str, dict] = {}
    for item in result.relations:
        resolved_ticker = _resolve_ticker(item.company_name, reverse_registry)
        if resolved_ticker is None or resolved_ticker == ticker:
            logger.debug("종목코드 매핑 실패 또는 자기 자신: %s", item.company_name)
            continue

        entry = {
            "ticker": resolved_ticker,
            "relation_type": _map_relation_type(item.relation_type),
            "reason": f"[{item.relation_type}] {item.reason}",
            "source": item.source if item.source in ("news", "dart") else "news",
            "confidence": item.confidence,
        }

        existing = best.get(resolved_ticker)
        if existing is None or item.confidence > existing["confidence"]:
            best[resolved_ticker] = entry

    resolved = list(best.values())
    count = _save_relations(ticker, resolved)
    logger.info("[%s] 관계 발굴 완료: %d건 저장 (후보 %d건)", ticker, count, len(result.relations))
    return count


# ─────────────────────────────────────────────────────────────────────────────
# 소급 배치 발굴 (과거 뉴스 전체 탐색)
# ─────────────────────────────────────────────────────────────────────────────

def _load_news_cache_all(ticker: str, limit: int = 200) -> list[tuple[str, str]]:
    """
    NewsCache 테이블에서 해당 종목의 과거 뉴스 전체를 로드한다.
    반환: [(title, summary), ...]  최신순 정렬
    """
    try:
        from models.db_models import NewsCache, SessionLocal

        db = SessionLocal()
        try:
            rows = (
                db.query(NewsCache)
                .filter(NewsCache.ticker == ticker)
                .order_by(NewsCache.fetched_at.desc())
                .limit(limit)
                .all()
            )
            return [(r.title or "", r.summary or "") for r in rows if r.title]
        finally:
            db.close()
    except Exception as e:
        logger.warning("[%s] NewsCache 로드 실패: %s", ticker, e)
        return []


def _chunk_articles(
    articles: list[tuple[str, str]], chunk_size: int = 25
) -> list[list[tuple[str, str]]]:
    """기사 목록을 chunk_size 단위 배치로 분할한다."""
    return [articles[i : i + chunk_size] for i in range(0, len(articles), chunk_size)]


def _format_articles_text(articles: list[tuple[str, str]], offset: int = 0) -> str:
    lines = []
    for i, (title, summary) in enumerate(articles, start=offset + 1):
        lines.append(f"[뉴스 {i}] {title}")
        if summary:
            lines.append(f"  요약: {summary}")
    return "\n".join(lines)


async def _merge_results(
    batches: list[Optional[_DiscoverySchema]],
    ticker: str,
    reverse_registry: dict,
) -> list[dict]:
    """
    여러 배치의 LLM 결과를 합산·중복 제거한다.
    같은 related_ticker + relation_type 쌍은 confidence 최대값을 유지한다.
    """
    best: dict[tuple, dict] = {}

    for result in batches:
        if result is None or not result.relations:
            continue
        for item in result.relations:
            resolved_ticker = _resolve_ticker(item.company_name, reverse_registry)
            if resolved_ticker is None or resolved_ticker == ticker:
                continue

            key = (resolved_ticker, item.relation_type)
            entry = {
                "ticker": resolved_ticker,
                "relation_type": _map_relation_type(item.relation_type),
                "reason": f"[{item.relation_type}] {item.reason}",
                "source": item.source if item.source in ("news", "dart") else "news",
                "confidence": item.confidence,
            }
            existing = best.get(key)
            if existing is None or item.confidence > existing["confidence"]:
                best[key] = entry

    return list(best.values())


async def discover_relations_retroactive(ticker: str, max_articles: int = 150) -> int:
    """
    NewsCache에 저장된 과거 뉴스 전체 + 멀티페이지 크롤링 결과를 소급 탐색하여
    company_edges 및 RelationCache를 채운다.

    - 과거 뉴스를 25건씩 배치로 나눠 LLM 호출 → 결과 합산
    - 이미 발굴된 관계보다 confidence가 높은 경우에만 갱신
    - `discover_relations` 와 달리 단발성 씨딩용으로 설계됨

    Returns
    -------
    int: 저장/갱신된 관계 수
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — 소급 발굴 건너뜀")
        return 0

    from services.stock_service import get_or_build_registry

    registry = get_or_build_registry()
    company_name = registry.get(ticker, {}).get("name", ticker)
    reverse_registry = _build_reverse_registry(registry)
    model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=api_key)

    # 1. NewsCache 과거 데이터 로드
    cached_articles = _load_news_cache_all(ticker, limit=max_articles)

    # 2. 멀티페이지 크롤링으로 보완 (아직 캐시에 없는 과거 기사)
    live_articles = await _get_news_text_multipage(ticker, pages=5)

    # 중복 제거: title 기준
    seen_titles = {t for t, _ in cached_articles}
    combined = list(cached_articles)
    for title, summary in live_articles:
        if title not in seen_titles:
            combined.append((title, summary))
            seen_titles.add(title)

    if not combined:
        logger.info("[%s] 소급 발굴: 뉴스 없음", ticker)
        return 0

    logger.info("[%s] 소급 발굴 시작: 총 %d건 기사", ticker, len(combined))

    # DART 텍스트는 단일 섹션으로 추가
    dart_text = _get_dart_text(ticker)

    # 3. 25건씩 배치 처리
    chunks = _chunk_articles(combined, chunk_size=25)
    batch_results: list[Optional[_DiscoverySchema]] = []

    for idx, chunk in enumerate(chunks):
        articles_text = _format_articles_text(chunk, offset=idx * 25)
        sections = [f"기준 기업: {company_name} ({ticker})", f"[뉴스 기사]\n{articles_text}"]
        if dart_text and idx == 0:  # DART는 첫 배치에만 포함
            sections.append(f"[DART 공시]\n{dart_text}")
        prompt = "\n\n".join(sections)

        result = await _call_claude(client, model, prompt)
        batch_results.append(result)
        logger.debug("[%s] 배치 %d/%d 완료", ticker, idx + 1, len(chunks))

    # 4. 결과 합산 + 저장
    resolved = await _merge_results(batch_results, ticker, reverse_registry)
    if not resolved:
        logger.info("[%s] 소급 발굴: 관계 없음", ticker)
        return 0

    count = _save_relations(ticker, resolved)
    logger.info(
        "[%s] 소급 발굴 완료: %d건 저장 (배치 %d개, 기사 %d건)",
        ticker, count, len(chunks), len(combined),
    )
    return count


async def _get_news_text_multipage(
    ticker: str, pages: int = 5
) -> list[tuple[str, str]]:
    """
    네이버 금융에서 여러 페이지 뉴스를 수집한다.
    이미 NewsCache에 있는 내용과 합산하면 과거 기사 커버리지가 높아진다.
    """
    results: list[tuple[str, str]] = []
    try:
        from services.news_service import fetch_news

        for page in range(1, pages + 1):
            try:
                items = await fetch_news(ticker, page=page, force=True)
                for item in items:
                    results.append((item.title, item.summary or ""))
            except Exception as e:
                logger.debug("[%s] page=%d 수집 실패: %s", ticker, page, e)
                break
    except Exception as e:
        logger.warning("[%s] 멀티페이지 수집 실패: %s", ticker, e)
    return results
