"""
상관계수 기반 연관 기업 관계 도출 서비스
"""
from typing import Optional
import logging
import numpy as np

from models.schemas import RelationNode, RelationLink, RelatedCompany, ImpactItem

logger = logging.getLogger(__name__)

RELATION_TYPES = {
    (0.8, 1.0): "경쟁",
    (0.6, 0.8): "협력",
    (0.4, 0.6): "공급망",
    (0.0, 0.4): "관심",
}


def _get_relation_type(corr: float) -> str:
    for (lo, hi), rtype in RELATION_TYPES.items():
        if lo <= corr < hi:
            return rtype
    return "관심"


def _get_name(ticker: str, registry: dict) -> str:
    """레지스트리에서 종목명 조회. 없으면 ticker 코드 반환."""
    return registry.get(ticker, {}).get("name", ticker)


def _fetch_close_series(ticker: str, days: int = 90) -> dict[str, float]:
    """
    Redis 캐시가 적용된 stock_service를 통해 일자별 종가를 조회한다.

    relation_service가 pykrx를 직접 호출하지 않게 해서 주가 히스토리 캐시 정책을
    한곳(stock_service/cache_service)에 모은다.
    """
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
    """두 종목의 공통 거래일 종가 기준 Pearson 상관계수."""
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

    if np.isnan(corr):
        return None
    return corr


def compute_relations(
    ticker: str,
    candidate_tickers: Optional[list[str]] = None,
) -> dict:
    """
    주어진 ticker와 후보 종목들 간의 상관계수를 계산하여 관계 데이터를 반환.

    candidate_tickers 미지정 시 레지스트리 전종목 중 KOSPI200 기준 상위 9개를 사용.
    종목명은 Redis 레지스트리에서 동적으로 조회한다.
    """
    from services.stock_service import get_or_build_registry
    from tasks import KOSPI200_TICKERS

    registry = get_or_build_registry()

    if candidate_tickers is None:
        candidate_tickers = [t for t in KOSPI200_TICKERS if t != ticker][:9]

    base_series = _fetch_close_series(ticker)

    nodes = []
    links = []
    related = []

    center_name = _get_name(ticker, registry)
    nodes.append(RelationNode(id=ticker, name=center_name, group=0, size=40))

    for i, cand in enumerate(candidate_tickers):
        if cand == ticker:
            continue

        cand_name = _get_name(cand, registry)
        size = max(10, 28 - i * 2)

        cand_series = _fetch_close_series(cand)
        correlation = _pearson_corr(base_series, cand_series)
        if correlation is None:
            logger.info("상관계수 계산 제외 (%s-%s): 공통 거래일 부족 또는 데이터 없음", ticker, cand)
            continue

        correlation = round(abs(correlation), 2)
        rtype = _get_relation_type(correlation)

        nodes.append(RelationNode(id=cand, name=cand_name, group=i % 3 + 1, size=size))
        links.append(RelationLink(source=ticker, target=cand, value=correlation, type=rtype))
        related.append(RelatedCompany(
            ticker=cand,
            name=cand_name,
            correlation=correlation,
            reason=f"{rtype} 관계 (상관계수 {correlation:.2f})",
        ))

    related.sort(key=lambda x: x.correlation, reverse=True)

    return {
        "nodes": nodes,
        "links": links,
        "related_companies": related[:5],
    }


def compute_correlations_only(ticker: str) -> int:
    """
    단일 종목에 대해 KOSPI200 후보 종목들과의 상관계수만 계산한다.
    관계 유형 재분류·노드 생성 없이 수치만 갱신하므로 매일 실행에 적합.
    반환값: 상관계수를 계산한 후보 종목 수
    """
    from tasks import KOSPI200_TICKERS

    candidate_tickers = [t for t in KOSPI200_TICKERS if t != ticker]
    base_series = _fetch_close_series(ticker)
    if not base_series:
        return 0

    count = 0
    for cand in candidate_tickers:
        cand_series = _fetch_close_series(cand)
        if _pearson_corr(base_series, cand_series) is not None:
            count += 1

    return count


async def compute_impact(ticker: str) -> list[ImpactItem]:
    """
    영향 종목 추론.

    흐름:
      1. 해당 종목의 최신 뉴스 수집 (news_service)
      2. 상관계수 기반 관계사 목록 조회 (compute_relations)
      3. LLM 에이전트가 뉴스를 읽고 관계사 중 영향받을 종목 판단
      4. LLM 미사용 환경(API 키 없음)이면 빈 리스트 반환
    """
    from services.news_service import fetch_news, rank_news
    from services.stock_service import get_or_build_registry
    from agents.news_agent import run_impact_analysis

    registry = get_or_build_registry()
    company_name = _get_name(ticker, registry)

    # 1. 뉴스 수집
    news_items = await fetch_news(ticker)
    ranked = rank_news(news_items)
    news_titles = [n.title for n in ranked[:10]]

    # 2. 관계사 목록 확보
    relation_data = compute_relations(ticker)
    related = [
        {"ticker": r.ticker, "name": r.name, "reason": r.reason}
        for r in relation_data.get("related_companies", [])
    ]

    if not news_titles or not related:
        return []

    # 3. LLM 에이전트 호출
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
