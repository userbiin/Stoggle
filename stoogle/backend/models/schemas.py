from typing import Optional
from pydantic import BaseModel


# ---------- 검색 ----------

class CompanyBrief(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str
    price: Optional[float] = None
    change: Optional[float] = None


class SearchResponse(BaseModel):
    query: str
    results: list[CompanyBrief]


# ---------- 주가 ----------

class PricePoint(BaseModel):
    date: str
    close: float
    volume: int


# ---------- 키워드 ----------

class Keyword(BaseModel):
    text: str
    value: int


# ---------- 인사이트 ----------

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


# ---------- 뉴스 ----------

class NewsItem(BaseModel):
    id: int
    title: str
    source: str
    published_at: str
    url: str
    sentiment: str  # positive | negative | neutral
    summary: Optional[str] = None
    category: Optional[str] = None


class NewsResponse(BaseModel):
    ticker: str
    news: list[NewsItem]


# ---------- 관계 ----------

class RelationNode(BaseModel):
    id: str
    name: str
    group: int
    size: int


class RelationLink(BaseModel):
    source: str
    target: str
    value: float
    type: str


class RelatedCompany(BaseModel):
    ticker: str
    name: str
    correlation: float
    reason: str


class ImpactItem(BaseModel):
    ticker: str
    name: str
    impact: str  # positive | negative
    reason: str


class RelationsResponse(BaseModel):
    ticker: str
    nodes: list[RelationNode]
    links: list[RelationLink]
    related_companies: list[RelatedCompany]
    impact: list[ImpactItem] = []
