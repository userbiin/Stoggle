import logging

from fastapi import APIRouter, Query
from models.schemas import NewsResponse
from services.news_service import fetch_news, rank_news

router = APIRouter(tags=["news"])
logger = logging.getLogger(__name__)


@router.get("/news/{ticker}", response_model=NewsResponse)
async def get_news(
    ticker: str,
    page: int = Query(default=1, ge=1, le=10),
):
    ticker = ticker.upper()
    items = await fetch_news(ticker, page=page)
    try:
        ranked = rank_news(items)
    except Exception as e:
        logger.warning("rank_news 실패 [%s]: %s", ticker, e)
        ranked = items
    return NewsResponse(ticker=ticker, news=ranked)
