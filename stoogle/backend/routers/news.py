from fastapi import APIRouter, Query
from models.schemas import NewsResponse
from services.news_service import fetch_news, rank_news

router = APIRouter(tags=["news"])


@router.get("/news/{ticker}", response_model=NewsResponse)
async def get_news(
    ticker: str,
    page: int = Query(default=1, ge=1, le=10),
):
    ticker = ticker.upper()
    items = await fetch_news(ticker, page=page)
    ranked = rank_news(items)
    return NewsResponse(ticker=ticker, news=ranked)
