# 관계 서비스
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


# 상관계수 가중치 변환
def _corr_to_weight(corr: float) -> float:
    return round(max(0.0, corr), 2)


# 엣지 유형 한국어 변환
def _edge_type_to_kr(edge_type: str) -> str:
    return _EDGE_TYPE_TO_KR.get(edge_type, "협력")


# 종목명 조회
def _get_name(ticker: str, registry: dict) -> str:
    return registry.get(ticker, {}).get("name", ticker)


# 종가 시계열 조회
def _fetch_close_series(
    ticker: str,
    days: int = 90,
    fromdate: Optional[str] = None,
    todate: Optional[str] = None,
) -> dict[str, float]:
    try:
        if fromdate:
            from services.stock_service import get_price_history_range
            history = get_price_history_range(ticker, fromdate=fromdate, todate=todate)
        else:
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


# 피어슨 상관계수
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


# 관계 캐시 조회
def _load_relation_cache(ticker: str) -> list[dict]:
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
                    seen[rt] = entry

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


# 발굴 관계 존재 여부
def has_discovered_relations(ticker: str) -> bool:
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


# 사업 관계 존재 여부
def has_business_relations(ticker: str) -> bool:
    try:
        from models.db_models import RelationCache, CompanyEdge, SessionLocal
        from sqlalchemy import or_

        db = SessionLocal()
        try:
            if (
                db.query(RelationCache)
                .filter(
                    RelationCache.ticker == ticker,
                    RelationCache.source.in_(["news", "dart"]),
                )
                .first()
            ):
                return True
            if (
                db.query(CompanyEdge)
                .filter(or_(CompanyEdge.src == ticker, CompanyEdge.dst == ticker))
                .first()
            ):
                return True
            return False
        finally:
            db.close()
    except Exception:
        return False


# 상관계수 캐시 저장
def save_correlations_to_cache(
    ticker: str,
    candidates: list[dict],
) -> int:
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

                if existing_source in ("news", "dart"):
                    continue

                reason = ""
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


# 관계 계산
def compute_relations(
    ticker: str,
    candidate_tickers: Optional[list[str]] = None,
) -> dict:
    from services.stock_service import get_or_build_registry

    registry = get_or_build_registry()
    center_name = _get_name(ticker, registry)
    nodes = [RelationNode(id=ticker, name=center_name, group=0, size=40)]
    links: list[RelationLink] = []
    related: list[RelatedCompany] = []

    cached = _load_relation_cache(ticker)
    candidates = [r for r in cached if r["source"] != "correlation"]

    if len(candidates) < 9:
        seen = {c["ticker"] for c in candidates}
        for n in _get_neighbors_from_edges(ticker):
            if n["ticker"] in seen or n["ticker"] == ticker:
                continue
            candidates.append(n)
            seen.add(n["ticker"])
            if len(candidates) >= 9:
                break

    for i, item in enumerate(candidates[:9]):
        cand = item["ticker"]
        cand_name = _get_name(cand, registry)
        size = max(10, 30 - i * 2)
        nodes.append(RelationNode(id=cand, name=cand_name, group=i % 3 + 1, size=size))
        links.append(RelationLink(
            source=ticker, target=cand,
            value=item.get("correlation", 0.0), type=item["relation_type"],
        ))
        related.append(RelatedCompany(
            ticker=cand, name=cand_name,
            correlation=item.get("correlation", 0.0), reason=item["reason"],
            relation_type=item.get("relation_type", "관심"),
        ))

    related.sort(key=lambda x: x.correlation, reverse=True)
    return {"nodes": nodes, "links": links, "related_companies": related[:5]}


# 엣지 이웃 조회
def _get_neighbors_from_edges(
    ticker: str,
    max_hops: int = 2,
    max_results: int = 20,
) -> list[dict]:
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


# 상관계수 계산 및 저장
def compute_correlations_only(
    ticker: str,
    fromdate: Optional[str] = None,
    todate: Optional[str] = None,
) -> int:
    from services.kospi200 import KOSPI200_TICKERS

    candidate_tickers = [t for t in KOSPI200_TICKERS if t != ticker]
    base_series = _fetch_close_series(ticker, fromdate=fromdate, todate=todate)
    if not base_series:
        return 0

    computed = []
    for cand in candidate_tickers:
        cand_series = _fetch_close_series(cand, fromdate=fromdate, todate=todate)
        corr = _pearson_corr(base_series, cand_series)
        if corr is not None:
            corr = round(corr, 2)
            computed.append({
                "ticker": cand,
                "correlation": corr,
                "relation_type": "관심",
            })

    return save_correlations_to_cache(ticker, computed)


# 영향 추론
async def compute_impact(ticker: str) -> list[ImpactItem]:
    from services.news_service import fetch_news, rank_news
    from services.stock_service import get_or_build_registry
    from agents.news_agent import run_impact_analysis

    registry = get_or_build_registry()
    company_name = _get_name(ticker, registry)

    news_items = await fetch_news(ticker)
    ranked = await rank_news(news_items, company_name=company_name)
    news_titles = [n.title for n in ranked[:10]]
    if not news_titles:
        return []

    candidates = _get_neighbors_from_edges(ticker)
    if not candidates:
        cached = _load_relation_cache(ticker)
        candidates = [c for c in cached if c.get("source") != "correlation"]
    if not candidates:
        return []

    related = [
        {
            "ticker": c["ticker"],
            "name": _get_name(c["ticker"], registry),
            "reason": c.get("reason", ""),
        }
        for c in candidates
    ]

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
            trigger_news=item.get("trigger_news"),
        )
        for item in raw_items
    ]
