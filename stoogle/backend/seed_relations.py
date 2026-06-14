# 관계 발굴
import asyncio
import logging
import sys
import os

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("seed_relations")


async def seed_ticker(ticker: str, max_articles: int) -> int:
    from agents.relation_discovery_agent import discover_relations_retroactive
    try:
        count = await discover_relations_retroactive(ticker, max_articles=max_articles)
        logger.info("[%s] 완료: %d건 저장", ticker, count)
        return count
    except Exception as e:
        logger.error("[%s] 실패: %s", ticker, e)
        return 0


async def main():
    args = sys.argv[1:]
    max_articles = 150

    # --max-articles N 파싱
    if "--max-articles" in args:
        idx = args.index("--max-articles")
        max_articles = int(args[idx + 1])
        args = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]

    if args:
        tickers = args
        logger.info("지정 종목 %d개 소급 발굴 시작", len(tickers))
    else:
        from services.kospi200 import KOSPI200_TICKERS
        tickers = KOSPI200_TICKERS
        logger.info("KOSPI200 전체 %d개 소급 발굴 시작", len(tickers))

    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY 미설정 — 중단")
        sys.exit(1)

    total = 0
    for i, ticker in enumerate(tickers, 1):
        logger.info("[%d/%d] %s 처리 중...", i, len(tickers), ticker)
        count = await seed_ticker(ticker, max_articles)
        total += count

    logger.info("소급 발굴 완료: 총 %d건 저장", total)


if __name__ == "__main__":
    asyncio.run(main())
