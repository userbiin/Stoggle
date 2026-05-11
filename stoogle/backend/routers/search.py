from fastapi import APIRouter, Query, HTTPException
from models.schemas import SearchResponse
from services.stock_service import search_companies

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(q: str = Query(..., min_length=1, description="종목명 또는 종목코드")):
    if not q.strip():
        raise HTTPException(status_code=400, detail="검색어를 입력하세요.")

    results = search_companies(q.strip())
    return SearchResponse(query=q, results=results)
