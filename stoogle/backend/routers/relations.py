import logging

from fastapi import APIRouter
from models.schemas import RelationsResponse
from services.relation_service import compute_relations, compute_impact

router = APIRouter(tags=["relations"])
logger = logging.getLogger(__name__)


@router.get("/relations/{ticker}", response_model=RelationsResponse)
async def get_relations(ticker: str):
    ticker = ticker.upper()

    try:
        relation_data = compute_relations(ticker)
    except Exception as e:
        logger.warning("compute_relations 실패 [%s]: %s", ticker, e)
        relation_data = {"nodes": [], "links": [], "related_companies": []}

    try:
        impact = await compute_impact(ticker)
    except Exception as e:
        logger.warning("compute_impact 실패 [%s]: %s", ticker, e)
        impact = []

    return RelationsResponse(
        ticker=ticker,
        nodes=relation_data["nodes"],
        links=relation_data["links"],
        related_companies=relation_data["related_companies"],
        impact=impact,
    )
