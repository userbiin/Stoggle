import os
import httpx
import asyncio
from datetime import datetime
from typing import Optional

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
NAVER_API_URL = "https://openapi.naver.com/v1/search/news.json"

CATEGORIES = {
    "정치": ["정치", "국회", "대통령", "정부", "선거"],
    "사회": ["사회", "최저임금", "노동", "복지", "교육"],
    "경제": ["경제", "금리", "환율", "무역", "수출", "반도체"],
}

async def fetch_category_news(category: str, query: str, display: int = 20) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": query,
        "display": display,
        "sort": "date",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(NAVER_API_URL, headers=headers, params=params)
            res.raise_for_status()
            items = res.json().get("items", [])
            for item in items:
                item["category"] = category
            return items
    except Exception as e:
        print(f"[{category}] 뉴스 수집 실패: {e}")
        return []

async def fetch_all_news() -> list[dict]:
    tasks = []
    for category, keywords in CATEGORIES.items():
        for keyword in keywords:
            tasks.append(fetch_category_news(category, keyword))
    results = await asyncio.gather(*tasks)
    all_news = []
    seen = set()
    for items in results:
        for item in items:
            title = item.get("title", "")
            if title not in seen:
                seen.add(title)
                all_news.append(item)
    return all_news

if __name__ == "__main__":
    news = asyncio.run(fetch_all_news())
    print(f"수집된 뉴스: {len(news)}건")
    for item in news[:3]:
        print(f"[{item['category']}] {item['title']}")
