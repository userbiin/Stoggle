import asyncio
import logging

from fastapi import APIRouter
from models.schemas import RelationsResponse
from services.relation_service import compute_relations, compute_impact, has_discovered_relations

router = APIRouter(tags=["relations"])
logger = logging.getLogger(__name__)

# asyncio.create_task 레퍼런스 유지 (GC 방지)
_bg_tasks: set = set()


async def _discover_background(ticker: str) -> None:
    """
    첫 조회 시: 과거 뉴스 소급(멀티페이지) + DART 공시 + LLM 분석으로 비즈니스 관계 발굴.
    결과는 RelationCache + company_edges에 저장되며, 이후 요청부터 DB에서 로드된다.
    """
    try:
        from agents.relation_discovery_agent import discover_relations_retroactive

        count = await discover_relations_retroactive(ticker)
        logger.info("첫 조회 관계 발굴 완료 [%s]: %d건", ticker, count)
    except Exception as e:
        logger.warning("첫 조회 관계 발굴 실패 [%s]: %s", ticker, e)


@router.get("/relations/{ticker}", response_model=RelationsResponse)
async def get_relations(ticker: str):
    ticker = ticker.upper()

    # 발굴된 관계가 없으면 소급 발굴을 백그라운드에서 트리거 (첫 조회 1회만)
    # 이번 요청에는 correlation 캐시·폴백 결과를 반환하고, 다음 요청부터 DB 결과 사용
    first_time = not has_discovered_relations(ticker)
    if first_time:
        task = asyncio.create_task(_discover_background(ticker))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

    relation_data = compute_relations(ticker)
    impact = await compute_impact(ticker)

    return RelationsResponse(
        ticker=ticker,
        nodes=relation_data["nodes"],
        links=relation_data["links"],
        related_companies=relation_data["related_companies"],
        impact=impact,
        is_analyzing=first_time,
    )
