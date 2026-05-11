from fastapi import APIRouter
from models.schemas import RelationsResponse
from services.relation_service import compute_relations, compute_impact

router = APIRouter(tags=["relations"])


@router.get("/relations/{ticker}", response_model=RelationsResponse)
async def get_relations(ticker: str):
    ticker = ticker.upper()
    relation_data = compute_relations(ticker)
    impact = await compute_impact(ticker)

    return RelationsResponse(
        ticker=ticker,
        nodes=relation_data["nodes"],
        links=relation_data["links"],
        related_companies=relation_data["related_companies"],
        impact=impact,
    )
