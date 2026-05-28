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
    # Celery 태스크가 랭킹 완료된 결과를 캐시에 저장하므로,
    # 캐시 히트 시(sentiment가 이미 설정된 경우) 재분석 없이 그대로 반환한다.
    if all(i.sentiment == "neutral" for i in items):
        items = await rank_news(items)
    return NewsResponse(ticker=ticker, news=items)
