# 뉴스 라우터
from fastapi import APIRouter, Query
from models.schemas import NewsResponse
from services.news_service import fetch_news, rank_news
from services.stock_service import get_or_build_registry

router = APIRouter(tags=["news"])


# 뉴스 조회
@router.get("/news/{ticker}", response_model=NewsResponse)
async def get_news(
    ticker: str,
    page: int = Query(default=1, ge=1, le=10),
):
    ticker = ticker.upper()
    items = await fetch_news(ticker, page=page)
    if all(i.sentiment == "neutral" for i in items):
        registry = get_or_build_registry()
        company_name = registry.get(ticker, {}).get("name", ticker)
        items = await rank_news(items, company_name=company_name)
    return NewsResponse(ticker=ticker, news=items)
