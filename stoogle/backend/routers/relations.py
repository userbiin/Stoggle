import asyncio
import logging

from fastapi import APIRouter
from models.schemas import RelationsResponse, RelationNode
from services.relation_service import (
    compute_relations, compute_impact, has_business_relations,
)
from services.stock_service import get_or_build_registry

router = APIRouter(tags=["relations"])
logger = logging.getLogger(__name__)

_bg_tasks: set = set()


async def _discover_background(ticker: str) -> None:
    try:
        from agents.relation_discovery_agent import discover_relations
        count = await discover_relations(ticker)
        logger.info("백그라운드 관계 발굴 완료 [%s]: %d건", ticker, count)
    except Exception as e:
        logger.warning("백그라운드 관계 발굴 실패 [%s]: %s", ticker, e)


def _trigger_discovery_once(ticker: str) -> None:
    """동일 종목에 대한 발굴 중복 트리거 방지 (10분 Redis 락). Celery 우선, 없으면 asyncio."""
    try:
        from services.cache_service import _get_client
        client = _get_client()
        if client is not None:
            if not client.set(f"Stoogle:discovery_lock:{ticker}", "1", nx=True, ex=600):
                return
    except Exception:
        pass

    try:
        from tasks import discover_relations_for_ticker
        discover_relations_for_ticker.delay(ticker)
        logger.info("[%s] Celery 관계 발굴 트리거", ticker)
        return
    except Exception as e:
        logger.debug("[%s] Celery 트리거 실패 — asyncio 폴백: %s", ticker, e)

    task = asyncio.create_task(_discover_background(ticker))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@router.get("/relations/{ticker}", response_model=RelationsResponse)
async def get_relations(ticker: str):
    ticker = ticker.upper()

    # 영향 종목은 발굴 상태와 무관하게 항상 계산 (company_edges 이웃 기반)
    impact = await compute_impact(ticker)

    # 사업 관계(news|dart|company_edges)가 없으면 Pearson 폴백 대신 빈 목록 반환
    if not has_business_relations(ticker):
        _trigger_discovery_once(ticker)
        registry = get_or_build_registry()
        name = registry.get(ticker, {}).get("name", ticker)
        return RelationsResponse(
            ticker=ticker,
            nodes=[RelationNode(id=ticker, name=name, group=0, size=40)],
            links=[],
            related_companies=[],
            impact=impact,
            discovery_status="pending",
        )

    relation_data = compute_relations(ticker)
    return RelationsResponse(
        ticker=ticker,
        nodes=relation_data["nodes"],
        links=relation_data["links"],
        related_companies=relation_data["related_companies"],
        impact=impact,
        discovery_status="ready",
    )
