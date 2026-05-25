from fastapi import APIRouter, HTTPException
from models.schemas import InsightResponse
from services.stock_service import get_price_history, get_market_cap_info, get_or_build_registry
from services.news_service import fetch_news
from services.nlp_service import extract_keywords, summarize_with_llm

router = APIRouter(tags=["insight"])


@router.get("/insight/{ticker}", response_model=InsightResponse)
async def get_insight(ticker: str):
    ticker = ticker.upper()

    # 종목명·시장 정보를 레지스트리에서 조회 (KOSPI/KOSDAQ 구분 포함)
    registry = get_or_build_registry()
    meta = registry.get(ticker, {})
    company_name = meta.get("name", ticker)
    market = meta.get("market", "KOSPI")

    price_history = get_price_history(ticker, days=90)
    cap_info = get_market_cap_info(ticker)
    news_items = await fetch_news(ticker, page=1)

    titles = [n.title for n in news_items]
    keywords = extract_keywords(titles) if titles else []
    summary = await summarize_with_llm(ticker, company_name, titles) if titles else None

    latest_price = None
    change = None
    change_amount = None
    if price_history:
        latest_price = price_history[-1].close
        if len(price_history) >= 2:
            prev_close = price_history[-2].close
            change_amount = round(latest_price - prev_close, 0)
            change = round((change_amount / prev_close * 100), 2) if prev_close else 0

    return InsightResponse(
        ticker=ticker,
        name=company_name,
        market=market,
        sector="",
        price=latest_price,
        change=change,
        change_amount=change_amount,
        market_cap=cap_info.get("market_cap") if cap_info else None,
        per=cap_info.get("per") if cap_info else None,
        pbr=cap_info.get("pbr") if cap_info else None,
        eps=cap_info.get("eps") if cap_info else None,
        summary=summary,
        keywords=keywords,
        price_history=price_history,
    )
