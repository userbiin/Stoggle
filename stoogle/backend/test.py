import asyncio
from dotenv import load_dotenv
load_dotenv()

from services.stock_service import get_or_build_registry
from agents.relation_discovery_agent import (
    _get_news_text, _get_dart_text, _build_reverse_registry, _resolve_ticker,
)

t = "005930"
reg = get_or_build_registry()
print("registry 종목 수:", len(reg))          # 30이면 fallback → 발굴 불가의 주범
print("news_text 길이:", len(asyncio.run(_get_news_text(t))))
print("dart_text 길이:", len(_get_dart_text(t)))
rev = _build_reverse_registry(reg)
for n in ["솔브레인", "한미반도체", "원익아이피에스", "LG전자"]:
    print(n, "→", _resolve_ticker(n, rev))    # None 많으면 확정