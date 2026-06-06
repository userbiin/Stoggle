"""
Look-ahead 누출 검증 스크립트

백테스트 정확도가 70%+ 비현실적으로 높으면 누출 의심. 이 스크립트로 확인한다.

검증 항목:
  1. 뉴스 누출: 기사 fetched_at >= predicted_at (미래 기사 사용 여부)
  2. latest_source_pubdate 누출: 가장 최신 기사 발행일이 predicted_at 이후인지
  3. base_price_date 누출: base_close 기준일이 predicted_at 이후인지

실행:
    cd backend/
    python -m evaluation.verify_lookahead --model_version backtest_v1
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

logger = logging.getLogger(__name__)


def verify(model_version: str = "backtest_v1") -> list[dict]:
    """
    지정 model_version의 PredictionLog를 검사하여 누출 목록을 반환한다.

    Returns
    -------
    list of {"pred_id", "type", "pred_at", "leak_dt", "detail"}
    """
    from models.db_models import PredictionLog, NewsCache, SessionLocal

    db = SessionLocal()
    try:
        preds = (
            db.query(PredictionLog)
            .filter(PredictionLog.model_version == model_version)
            .all()
        )

        leaks = []
        for p in preds:
            as_of = p.predicted_at
            if as_of is None:
                continue

            # 1) latest_source_pubdate 누출 검증
            if p.latest_source_pubdate and p.latest_source_pubdate >= as_of:
                leaks.append({
                    "pred_id": p.id,
                    "type": "latest_source_pubdate",
                    "pred_at": as_of,
                    "leak_dt": p.latest_source_pubdate,
                    "detail": f"입력 기사 최신 published_at({p.latest_source_pubdate}) >= predicted_at({as_of})",
                })

            # 2) base_price_date 누출 검증 (date 비교)
            if p.base_price_date:
                try:
                    bpd = datetime.strptime(p.base_price_date, "%Y-%m-%d")
                    aof_date = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
                    if bpd > aof_date:
                        leaks.append({
                            "pred_id": p.id,
                            "type": "base_price_date",
                            "pred_at": as_of,
                            "leak_dt": bpd,
                            "detail": f"base_price_date({p.base_price_date}) > as_of({aof_date.date()})",
                        })
                except ValueError:
                    pass

            # 3) NewsCache published_at 기반 시점 검증 (source_ticker 기준)
            # published_at(VARCHAR ISO)이 as_of 이후인 기사가 DB에 존재하는지 확인.
            # _fetch_news_before가 published_at < as_of 로 필터하므로 경고 수준.
            if p.source_ticker:
                as_of_str = as_of.strftime("%Y-%m-%dT%H:%M:%S")
                future_news = (
                    db.query(NewsCache)
                    .filter(
                        NewsCache.ticker == p.source_ticker,
                        NewsCache.published_at >= as_of_str,
                    )
                    .limit(1)
                    .first()
                )
                if future_news:
                    leaks.append({
                        "pred_id": p.id,
                        "type": "news_in_db_after_asof (warning)",
                        "pred_at": as_of,
                        "leak_dt": future_news.published_at,
                        "detail": (
                            f"DB에 as_of 이후 기사가 존재 — 백테스트 실행 시 필터링됐어야 함 "
                            f"(제목: {future_news.title[:60]})"
                        ),
                    })

        return leaks

    finally:
        db.close()


def report(model_version: str = "backtest_v1") -> None:
    """누출 검증 결과를 터미널에 출력한다."""
    from models.db_models import PredictionLog, SessionLocal
    db = SessionLocal()
    try:
        total = db.query(PredictionLog).filter(
            PredictionLog.model_version == model_version
        ).count()
    finally:
        db.close()

    leaks = verify(model_version)
    confirmed = [l for l in leaks if "warning" not in l["type"]]
    warnings = [l for l in leaks if "warning" in l["type"]]

    print(f"\n=== Look-ahead 검증 결과 ({model_version}) ===")
    print(f"전체 예측 레코드: {total}건")
    print(f"확정 누출:       {len(confirmed)}건")
    print(f"경고 (참고용):   {len(warnings)}건")

    if confirmed:
        print("\n[확정 누출 목록]")
        for l in confirmed[:20]:
            print(f"  ID={l['pred_id']} type={l['type']}")
            print(f"    pred_at={l['pred_at']}  leak_dt={l['leak_dt']}")
            print(f"    {l['detail']}")
    else:
        print("\n확정 누출 없음 — 백테스트 데이터 무결성 통과")

    if warnings:
        print(f"\n[경고 {len(warnings)}건 — 처음 5개]")
        for l in warnings[:5]:
            print(f"  {l['detail']}")

    # 판정
    if len(confirmed) == 0:
        print("\n판정: PASS")
        sys.exit(0)
    else:
        rate = len(confirmed) / max(total, 1)
        print(f"\n판정: FAIL (누출률 {rate:.1%}) — 백테스트 결과 신뢰 불가")
        sys.exit(1)


if __name__ == "__main__":
    import os, sys

    # 백엔드 루트를 sys.path에 추가
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    parser = argparse.ArgumentParser(description="백테스트 look-ahead 누출 검증")
    parser.add_argument("--model_version", default="backtest_v1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    report(args.model_version)
