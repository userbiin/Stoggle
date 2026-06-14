# 윈도우 점검
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="백테스트 뉴스 윈도우 점검")
    parser.add_argument("--start", required=True, help="시작 날짜 YYYYMMDD")
    parser.add_argument("--end", required=True, help="종료 날짜 YYYYMMDD")
    args = parser.parse_args()

    from datetime import datetime
    from sqlalchemy import func
    from models.db_models import NewsCache, SessionLocal

    start_str = datetime.strptime(args.start, "%Y%m%d").strftime("%Y-%m-%d")
    end_str = datetime.strptime(args.end, "%Y%m%d").strftime("%Y-%m-%d") + "T23:59:59"

    db = SessionLocal()
    try:
        # 날짜별 건수
        by_date = (
            db.query(
                func.substr(NewsCache.published_at, 1, 10).label("date"),
                func.count().label("n"),
            )
            .filter(
                NewsCache.published_at >= start_str,
                NewsCache.published_at <= end_str,
            )
            .group_by("date")
            .order_by("date")
            .all()
        )

        # 종목별 건수
        by_ticker = (
            db.query(NewsCache.ticker, func.count().label("n"))
            .filter(
                NewsCache.published_at >= start_str,
                NewsCache.published_at <= end_str,
            )
            .group_by(NewsCache.ticker)
            .order_by(func.count().desc())
            .limit(20)
            .all()
        )

        total = sum(r.n for r in by_date)
        print(f"\n=== 뉴스 윈도우: {args.start} ~ {args.end} ===")
        print(f"총 {total}건  (published_at 기준)\n")

        print("[날짜별]")
        for r in by_date:
            bar = "█" * min(r.n // 5, 40)
            print(f"  {r.date}  {r.n:5d}건  {bar}")

        print(f"\n[종목별 top{len(by_ticker)}]")
        for r in by_ticker:
            print(f"  {r.ticker}: {r.n}건")

        print()
        if total == 0:
            print("⚠️  해당 구간 뉴스 없음 — --start/--end 범위를 조정하거나 seed_news_pool.py 재실행")
        elif total < 30:
            print("⚠️  뉴스가 적습니다. 정확도가 낮을 수 있습니다.")
        else:
            print("✓  백테스트 실행 가능한 데이터 존재")

    finally:
        db.close()


if __name__ == "__main__":
    main()
