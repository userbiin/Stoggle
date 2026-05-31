# 기업 관계 서비스
#
# 우선순위:
#   1. RelationCache에 발굴된 관계(source=news|dart) → 실제 비즈니스 파트너
#   2. RelationCache의 correlation 캐시 → 주 1회 계산된 상관계수
#   3. Live Pearson 상관계수 계산 → 캐시 없을 때 폴백
from typing import Optional
import logging
import numpy as np
from datetime import datetime

from models.schemas import RelationNode, RelationLink, RelatedCompany, ImpactItem

logger = logging.getLogger(__name__)

_EDGE_TYPE_TO_KR = {
    "supplier": "공급망",
    "customer": "공급망",
    "competitor": "경쟁",
    "affiliate": "협력",
    "distributor": "공급망",
}

# Pearson 상관계수는 엣지 weight 보강 용도로만 사용 (관계 유형 분류 금지)
def _corr_to_weight(corr: float) -> float:
    """상관계수를 엣지 weight(0~1)로 변환. 관계 유형 결정에는 사용하지 않는다."""
    return round(max(0.0, corr), 2)


def _edge_type_to_kr(edge_type: str) -> str:
    return _EDGE_TYPE_TO_KR.get(edge_type, "협력")


def _get_name(ticker: str, registry: dict) -> str:
    return registry.get(ticker, {}).get("name", ticker)


def _fetch_close_series(ticker: str, days: int = 90) -> dict[str, float]:
    try:
        from services.stock_service import get_price_history

        history = get_price_history(ticker, days=days)
        return {
            point.date: float(point.close)
            for point in history
            if point.close is not None
        }
    except Exception as e:
        logger.warning("종가 시계열 조회 실패 (%s): %s", ticker, e)
        return {}


def _pearson_corr(
    base_series: dict[str, float],
    candidate_series: dict[str, float],
    min_points: int = 20,
) -> Optional[float]:
    common_dates = sorted(set(base_series) & set(candidate_series))
    if len(common_dates) < min_points:
        return None

    base = np.array([base_series[d] for d in common_dates], dtype=np.float64)
    candidate = np.array([candidate_series[d] for d in common_dates], dtype=np.float64)

    if np.std(base) == 0 or np.std(candidate) == 0:
        return None

    try:
        corr = float(np.corrcoef(base, candidate)[0, 1])
    except Exception:
        return None

    return None if np.isnan(corr) else corr


# ─────────────────────────────────────────────────────────────────────────────
# RelationCache 조회
# ─────────────────────────────────────────────────────────────────────────────

def _load_relation_cache(ticker: str) -> list[dict]:
    """
    RelationCache에서 관계 목록을 조회한다.
    발굴된 관계(news|dart)를 앞에, correlation 기반을 뒤에 정렬하여 반환.
    같은 related_ticker가 여러 source로 존재하면 발굴 관계를 우선한다.
    """
    try:
        from models.db_models import RelationCache, SessionLocal

        db = SessionLocal()
        try:
            rows = (
                db.query(RelationCache)
                .filter(RelationCache.ticker == ticker)
                .order_by(RelationCache.correlation.desc())
                .limit(30)
                .all()
            )

            # related_ticker 기준 중복 제거 — 발굴 관계 우선
            seen: dict[str, dict] = {}
            for row in rows:
                rt = row.related_ticker
                source = getattr(row, "source", "correlation") or "correlation"
                entry = {
                    "ticker": rt,
                    "correlation": row.correlation or 0.0,
                    "relation_type": row.relation_type or "관심",
                    "reason": row.reason or "",
                    "source": source,
                }
                if rt not in seen:
                    seen[rt] = entry
                elif seen[rt]["source"] == "correlation" and source != "correlation":
                    seen[rt] = entry  # 발굴 관계로 교체

            # 발굴 우선 정렬: news/dart → correlation
            items = sorted(
                seen.values(),
                key=lambda x: (0 if x["source"] != "correlation" else 1, -x["correlation"]),
            )
            return items
        finally:
            db.close()
    except Exception as e:
        logger.warning("RelationCache 조회 실패 [%s]: %s", ticker, e)
        return []


def has_discovered_relations(ticker: str) -> bool:
    """RelationCache에 발굴된 관계(news|dart)가 있으면 True."""
    try:
        from models.db_models import RelationCache, SessionLocal

        db = SessionLocal()
        try:
            row = (
                db.query(RelationCache)
                .filter(
                    RelationCache.ticker == ticker,
                    RelationCache.source.in_(["news", "dart"]),
                )
                .first()
            )
            return row is not None
        finally:
            db.close()
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Correlation → RelationCache 저장 (update_relation_graphs 태스크용)
# ─────────────────────────────────────────────────────────────────────────────

def save_correlations_to_cache(
    ticker: str,
    candidates: list[dict],
) -> int:
    """
    Pearson 상관계수 계산 결과를 RelationCache에 저장한다.
    이미 발굴된 관계(source=news|dart)가 있는 종목은 덮어쓰지 않는다.

    candidates: [{"ticker": str, "correlation": float, "relation_type": str}, ...]
    Returns: 저장 수
    """
    if not candidates:
        return 0
    try:
        from models.db_models import RelationCache, SessionLocal

        db = SessionLocal()
        try:
            count = 0
            for item in candidates:
                rt = item["ticker"]
                existing = (
                    db.query(RelationCache)
                    .filter(
                        RelationCache.ticker == ticker,
                        RelationCache.related_ticker == rt,
                    )
                    .first()
                )

                existing_source = getattr(existing, "source", "correlation") if existing else None

                # 발굴 관계가 이미 있으면 건너뜀
                if existing_source in ("news", "dart"):
                    continue

                reason = f"{item['relation_type']} 관계 (상관계수 {item['correlation']:.2f})"
                if existing:
                    existing.correlation = item["correlation"]
                    existing.relation_type = item["relation_type"]
                    existing.reason = reason
                    existing.source = "correlation"
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(
                        RelationCache(
                            ticker=ticker,
                            related_ticker=rt,
                            correlation=item["correlation"],
                            relation_type=item["relation_type"],
                            reason=reason,
                            source="correlation",
                        )
                    )
                count += 1

            db.commit()
            return count
        finally:
            db.close()
    except Exception as e:
        logger.error("correlation cache 저장 실패 [%s]: %s", ticker, e)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 관계 계산 (퍼블릭 API)
# ─────────────────────────────────────────────────────────────────────────────

def compute_relations(
    ticker: str,
    candidate_tickers: Optional[list[str]] = None,
) -> dict:
    """
    기업 관계 데이터를 반환한다.

    우선순위:
      1. RelationCache의 발굴 관계 (source=news|dart) — 실제 비즈니스 파트너
      2. RelationCache의 correlation 캐시 — 보완적으로 추가
      3. 캐시 없을 때 Live Pearson 상관계수 (KOSPI200 상위 9개 폴백)

    발굴 관계가 있으면 correlation 기반 항목은 빈 슬롯 채우기 용도로만 사용한다.
    """
    from services.stock_service import get_or_build_registry
    from services.kospi200 import KOSPI200_TICKERS

    registry = get_or_build_registry()
    center_name = _get_name(ticker, registry)
    nodes = [RelationNode(id=ticker, name=center_name, group=0, size=40)]
    links: list[RelationLink] = []
    related: list[RelatedCompany] = []

    cached = _load_relation_cache(ticker)
    discovered = [r for r in cached if r["source"] != "correlation"]
    corr_cached = [r for r in cached if r["source"] == "correlation"]

    if discovered:
        # ── 발굴된 관계 우선 사용 ────────────────────────────────────────────
        primary = discovered[:9]
        for i, item in enumerate(primary):
            cand = item["ticker"]
            cand_name = _get_name(cand, registry)
            size = max(10, 30 - i * 2)
            nodes.append(RelationNode(id=cand, name=cand_name, group=i % 3 + 1, size=size))
            links.append(
                RelationLink(source=ticker, target=cand, value=item["correlation"], type=item["relation_type"])
            )
            related.append(
                RelatedCompany(
                    ticker=cand,
                    name=cand_name,
                    correlation=item["correlation"],
                    reason=item["reason"],
                )
            )

        # 빈 슬롯은 correlation 캐시로 보완
        existing_tickers = {r["ticker"] for r in primary}
        for i, item in enumerate(corr_cached, start=len(primary)):
            if len(nodes) >= 10:
                break
            cand = item["ticker"]
            if cand in existing_tickers:
                continue
            cand_name = _get_name(cand, registry)
            size = max(10, 18 - i * 2)
            nodes.append(RelationNode(id=cand, name=cand_name, group=i % 3 + 1, size=size))
            links.append(
                RelationLink(source=ticker, target=cand, value=item["correlation"], type=item["relation_type"])
            )
            related.append(
                RelatedCompany(
                    ticker=cand,
                    name=cand_name,
                    correlation=item["correlation"],
                    reason=item["reason"],
                )
            )
            existing_tickers.add(cand)

    elif corr_cached:
        # ── correlation 캐시만 있는 경우 ────────────────────────────────────
        for i, item in enumerate(corr_cached[:9]):
            cand = item["ticker"]
            cand_name = _get_name(cand, registry)
            size = max(10, 28 - i * 2)
            nodes.append(RelationNode(id=cand, name=cand_name, group=i % 3 + 1, size=size))
            links.append(
                RelationLink(source=ticker, target=cand, value=item["correlation"], type=item["relation_type"])
            )
            related.append(
                RelatedCompany(
                    ticker=cand,
                    name=cand_name,
                    correlation=item["correlation"],
                    reason=item["reason"],
                )
            )

    else:
        # ── 캐시 없음 → company_edges 1~2홉 탐색 → KOSPI200 폴백 ─────────
        graph_neighbors = _get_neighbors_from_edges(ticker)

        if graph_neighbors:
            # 사업 관계 그래프 이웃 사용 (비대형주 포함)
            for i, item in enumerate(graph_neighbors[:9]):
                cand = item["ticker"]
                cand_name = _get_name(cand, registry)
                size = max(10, 28 - i * 2)
                nodes.append(RelationNode(id=cand, name=cand_name, group=i % 3 + 1, size=size))
                links.append(
                    RelationLink(source=ticker, target=cand, value=item["correlation"], type=item["relation_type"])
                )
                related.append(
                    RelatedCompany(
                        ticker=cand,
                        name=cand_name,
                        correlation=item["correlation"],
                        reason=item["reason"],
                    )
                )
        else:
            # company_edges에 데이터 없으면 KOSPI200 Pearson 폴백
            logger.info("[%s] company_edges 이웃 없음 — KOSPI200 상관계수 폴백", ticker)
            if candidate_tickers is None:
                candidate_tickers = [t for t in KOSPI200_TICKERS if t != ticker][:9]

            base_series = _fetch_close_series(ticker)
            for i, cand in enumerate(candidate_tickers):
                if cand == ticker:
                    continue
                cand_name = _get_name(cand, registry)
                size = max(10, 28 - i * 2)
                cand_series = _fetch_close_series(cand)
                corr = _pearson_corr(base_series, cand_series)
                if corr is None:
                    logger.info("상관계수 계산 제외 (%s-%s): 데이터 부족", ticker, cand)
                    continue
                corr = round(corr, 2)
                nodes.append(RelationNode(id=cand, name=cand_name, group=i % 3 + 1, size=size))
                links.append(RelationLink(source=ticker, target=cand, value=corr, type="관심"))
                related.append(
                    RelatedCompany(
                        ticker=cand,
                        name=cand_name,
                        correlation=corr,
                        reason=f"가격 동조화 (상관계수 {corr:.2f})",
                    )
                )

    related.sort(key=lambda x: x.correlation, reverse=True)
    return {
        "nodes": nodes,
        "links": links,
        "related_companies": related[:5],
    }


def _get_neighbors_from_edges(
    ticker: str,
    max_hops: int = 2,
    max_results: int = 20,
) -> list[dict]:
    """
    company_edges에서 1~2홉 이웃 종목을 가져온다.

    Redis 캐시 → DB 쿼리 순서로 조회한다.
    가격 상관계수 기반 후보 생성 대신 실제 사업 관계 그래프를 탐색하므로
    KOSPI200 시총 순서와 무관하게 비대형주도 후보에 포함된다.
    """
    # Redis 캐시 우선 조회 (TTL_EDGES = 24h, update_relation_graphs 시 무효화)
    try:
        from services.cache_service import get_edges_cache, set_edges_cache
        cached = get_edges_cache(ticker)
        if cached is not None:
            return cached[:max_results]
    except Exception:
        get_edges_cache = set_edges_cache = None  # type: ignore

    try:
        from models.db_models import CompanyEdge, SessionLocal
        from sqlalchemy import or_

        db = SessionLocal()
        try:
            hop1_rows = (
                db.query(CompanyEdge)
                .filter(or_(CompanyEdge.src == ticker, CompanyEdge.dst == ticker))
                .all()
            )

            neighbors: dict[str, dict] = {}
            for edge in hop1_rows:
                neighbor = edge.dst if edge.src == ticker else edge.src
                if neighbor == ticker:
                    continue
                rtype = _edge_type_to_kr(edge.relation_type)
                entry = {
                    "ticker": neighbor,
                    "relation_type": rtype,
                    "correlation": edge.weight or edge.confidence or 0.5,
                    "reason": edge.evidence or f"{rtype} 관계 ({edge.source or 'dart/news'})",
                    "source": edge.source or "dart",
                }
                if neighbor not in neighbors or entry["correlation"] > neighbors[neighbor]["correlation"]:
                    neighbors[neighbor] = entry

            # 2홉: 결과가 부족하면 1홉 이웃의 이웃도 포함
            if len(neighbors) < max_results and max_hops >= 2:
                hop1_tickers = list(neighbors.keys())
                for h1 in hop1_tickers:
                    if len(neighbors) >= max_results:
                        break
                    hop2_rows = (
                        db.query(CompanyEdge)
                        .filter(
                            or_(CompanyEdge.src == h1, CompanyEdge.dst == h1),
                            CompanyEdge.src != ticker,
                            CompanyEdge.dst != ticker,
                        )
                        .all()
                    )
                    for edge in hop2_rows:
                        neighbor = edge.dst if edge.src == h1 else edge.src
                        if neighbor == ticker or neighbor in neighbors:
                            continue
                        rtype = _edge_type_to_kr(edge.relation_type)
                        base_w = edge.weight or edge.confidence or 0.3
                        neighbors[neighbor] = {
                            "ticker": neighbor,
                            "relation_type": rtype,
                            "correlation": round(base_w * 0.7, 3),
                            "reason": edge.evidence or f"{rtype} 관계 (2홉, {edge.source or 'dart/news'})",
                            "source": edge.source or "dart",
                        }

            result = sorted(neighbors.values(), key=lambda x: -x["correlation"])[:max_results]

            # Redis에 저장 (다음 요청부터 DB 쿼리 생략)
            try:
                from services.cache_service import set_edges_cache
                set_edges_cache(ticker, result)
            except Exception:
                pass

            return result
        finally:
            db.close()
    except Exception as e:
        logger.warning("company_edges 이웃 조회 실패 (%s): %s", ticker, e)
        return []


def compute_correlations_only(ticker: str) -> int:
    """
    단일 종목에 대해 KOSPI200 후보 종목들과의 상관계수를 계산하고
    RelationCache에 저장(발굴 관계를 덮어쓰지 않음)한다.
    상관계수는 관계 유형 분류에 사용하지 않으며 weight 보강 목적으로만 저장한다.
    반환값: 저장된 상관계수 수
    """
    from services.kospi200 import KOSPI200_TICKERS

    candidate_tickers = [t for t in KOSPI200_TICKERS if t != ticker]
    base_series = _fetch_close_series(ticker)
    if not base_series:
        return 0

    computed = []
    for cand in candidate_tickers:
        cand_series = _fetch_close_series(cand)
        corr = _pearson_corr(base_series, cand_series)
        if corr is not None:
            corr = round(corr, 2)
            computed.append({
                "ticker": cand,
                "correlation": corr,
                # 상관계수 기반 관계 유형 분류 제거 — "관심"으로 고정
                "relation_type": "관심",
            })

    return save_correlations_to_cache(ticker, computed)


# ─────────────────────────────────────────────────────────────────────────────
# Impact 추론
# ─────────────────────────────────────────────────────────────────────────────

async def compute_impact(ticker: str) -> list[ImpactItem]:
    """
    뉴스 기반 영향 종목 추론.

    발굴된 관계사(실제 비즈니스 파트너)를 우선적으로 영향 분석 대상으로 사용한다.
    발굴된 관계가 없으면 correlation 기반 관계사로 폴백.
    """
    from services.news_service import fetch_news, rank_news
    from services.stock_service import get_or_build_registry
    from agents.news_agent import run_impact_analysis

    registry = get_or_build_registry()
    company_name = _get_name(ticker, registry)

    news_items = await fetch_news(ticker)
    ranked = await rank_news(news_items)
    news_titles = [n.title for n in ranked[:10]]

    relation_data = compute_relations(ticker)
    related = [
        {"ticker": r.ticker, "name": r.name, "reason": r.reason}
        for r in relation_data.get("related_companies", [])
    ]

    if not news_titles or not related:
        return []

    raw_items = await run_impact_analysis(
        ticker=ticker,
        company_name=company_name,
        news_titles=news_titles,
        related_companies=related,
    )

    return [
        ImpactItem(
            ticker=item["ticker"],
            name=item["name"],
            impact=item["impact"],
            reason=item["reason"],
        )
        for item in raw_items
    ]
