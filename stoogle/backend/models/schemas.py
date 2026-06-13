# 스키마
from typing import Optional
from pydantic import BaseModel


# 종목 요약
class CompanyBrief(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str
    price: Optional[float] = None
    change: Optional[float] = None


# 검색 응답
class SearchResponse(BaseModel):
    query: str
    results: list[CompanyBrief]


# 주가 포인트
class PricePoint(BaseModel):
    date: str
    close: float
    volume: int


# 키워드
class Keyword(BaseModel):
    text: str
    value: int


# 인사이트 응답
class InsightResponse(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str
    price: Optional[float] = None
    change: Optional[float] = None
    change_amount: Optional[float] = None
    market_cap: Optional[float] = None
    per: Optional[float] = None
    pbr: Optional[float] = None
    eps: Optional[float] = None
    summary: Optional[str] = None
    keywords: list[Keyword] = []
    price_history: list[PricePoint] = []
    events: list[str] = []
    sentiment: Optional[str] = None
    analysis_impacts: list[dict] = []
    evidence: list[str] = []


# 뉴스 항목
class NewsItem(BaseModel):
    id: int
    title: str
    source: str
    published_at: str
    url: str
    sentiment: str
    summary: Optional[str] = None
    category: Optional[str] = None


# 뉴스 응답
class NewsResponse(BaseModel):
    ticker: str
    news: list[NewsItem]


# 관계 노드
class RelationNode(BaseModel):
    id: str
    name: str
    group: int
    size: int


# 관계 링크
class RelationLink(BaseModel):
    source: str
    target: str
    value: float
    type: str


# 관련 기업
class RelatedCompany(BaseModel):
    ticker: str
    name: str
    correlation: float
    relation_type: str = "관심"
    reason: str


# 영향 항목
class ImpactItem(BaseModel):
    ticker: str
    name: str
    impact: str
    reason: str
    trigger_news: Optional[str] = None


# 관계 응답
class RelationsResponse(BaseModel):
    ticker: str
    nodes: list[RelationNode]
    links: list[RelationLink]
    related_companies: list[RelatedCompany]
    impact: list[ImpactItem] = []
    discovery_status: str = "ready"
