"""
백테스트 결과 조회/분석 스크립트

실행 예시:
    cd backend/
    python scripts/inspect_backtest.py --model_version backtest_v1
    python scripts/inspect_backtest.py --model_version backtest_v1 --breakdown ticker
    python scripts/inspect_backtest.py --model_version backtest_v1 --breakdown confidence
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()


def _pct(n: int, total: int) -> str:
    return f"{n/total*100:.1f}%" if total else "—"


def show_summary(model_version: str) -> None:
    from models.db_models import PredictionLog, SessionLocal
    db = SessionLocal()
    try:
        base = db.query(PredictionLog).filter(PredictionLog.model_version == model_version)
        total = base.count()
        pending = base.filter(PredictionLog.status == "pending").count()
        scored = base.filter(PredictionLog.status == "scored").count()
        skipped = base.filter(PredictionLog.status == "skipped").count()

        rows = (
            db.query(PredictionLog.is_correct, PredictionLog.confidence)
            .filter(PredictionLog.model_version == model_version, PredictionLog.status == "scored")
            .all()
        )
        n = len(rows)
        acc = sum(1 for r in rows if r.is_correct) / max(n, 1)
        high = [r for r in rows if r.confidence is not None and r.confidence >= 0.7]
        high_acc = sum(1 for r in high if r.is_correct) / max(len(high), 1)

        print(f"\n=== 백테스트 결과: {model_version} ===")
        print(f"전체 레코드:          {total}건")
        print(f"  pending (미채점):   {pending}건")
        print(f"  scored  (채점 완):  {scored}건")
        print(f"  skipped (데이터無): {skipped}건")
        print(f"\n[정확도]")
        print(f"  Direction Accuracy:        {acc:.3f}  ({sum(1 for r in rows if r.is_correct)}/{n})")
        print(f"  High-conf Accuracy (≥0.7): {high_acc:.3f}  ({sum(1 for r in high if r.is_correct)}/{len(high)})")
        print(f"  목표: direction_accuracy ≥ 0.600")
        if acc >= 0.6:
            print(f"  판정: PASS")
        else:
            print(f"  판정: FAIL (부족분 {0.6 - acc:.3f})")
    finally:
        db.close()


def show_by_ticker(model_version: str) -> None:
    from models.db_models import PredictionLog, SessionLocal
    from sqlalchemy import func
    db = SessionLocal()
    try:
        rows = (
            db.query(
                PredictionLog.ticker,
                func.count().label("n"),
                func.sum(PredictionLog.is_correct.cast(db.bind.dialect.type_compiler.process(
                    PredictionLog.is_correct.type
                ) if False else type(None))).label("correct"),
            )
            .filter(PredictionLog.model_version == model_version, PredictionLog.status == "scored")
            .group_by(PredictionLog.ticker)
            .order_by(func.count().desc())
            .all()
        )

        # fallback: 수동 집계
        all_rows = (
            db.query(PredictionLog.ticker, PredictionLog.is_correct)
            .filter(PredictionLog.model_version == model_version, PredictionLog.status == "scored")
            .all()
        )
        ticker_stats: dict[str, dict] = {}
        for r in all_rows:
            s = ticker_stats.setdefault(r.ticker, {"n": 0, "correct": 0})
            s["n"] += 1
            if r.is_correct:
                s["correct"] += 1

        print(f"\n=== 종목별 정확도 ({model_version}) ===")
        print(f"{'ticker':<12} {'n':>5} {'acc':>8}")
        print("-" * 28)
        for ticker, stat in sorted(ticker_stats.items(), key=lambda x: -x[1]["n"]):
            n = stat["n"]
            acc = stat["correct"] / n if n else 0
            print(f"{ticker:<12} {n:>5} {acc:>8.3f}")
    finally:
        db.close()


def show_by_confidence(model_version: str) -> None:
    from models.db_models import PredictionLog, SessionLocal
    db = SessionLocal()
    try:
        rows = (
            db.query(PredictionLog.confidence, PredictionLog.is_correct)
            .filter(PredictionLog.model_version == model_version, PredictionLog.status == "scored")
            .all()
        )
        bins = {
            "0.0-0.5": [], "0.5-0.6": [], "0.6-0.7": [], "0.7-0.8": [], "0.8-1.0": []
        }
        for r in rows:
            c = r.confidence or 0.0
            if c < 0.5:
                bins["0.0-0.5"].append(r.is_correct)
            elif c < 0.6:
                bins["0.5-0.6"].append(r.is_correct)
            elif c < 0.7:
                bins["0.6-0.7"].append(r.is_correct)
            elif c < 0.8:
                bins["0.7-0.8"].append(r.is_correct)
            else:
                bins["0.8-1.0"].append(r.is_correct)

        print(f"\n=== 신뢰도 구간별 정확도 ({model_version}) ===")
        print(f"{'confidence':>12} {'n':>6} {'accuracy':>10}")
        print("-" * 32)
        for band, vals in bins.items():
            n = len(vals)
            acc = sum(vals) / n if n else 0
            print(f"{band:>12} {n:>6} {acc:>10.3f}")
        print("\n캘리브레이션 양호: high-conf가 low-conf보다 정확도 높아야 정상")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="백테스트 결과 조회")
    parser.add_argument("--model_version", default="backtest_v1")
    parser.add_argument(
        "--breakdown",
        choices=["ticker", "confidence"],
        help="세분화 분석 (ticker 또는 confidence 구간)",
    )
    args = parser.parse_args()

    show_summary(args.model_version)

    if args.breakdown == "ticker":
        show_by_ticker(args.model_version)
    elif args.breakdown == "confidence":
        show_by_confidence(args.model_version)


if __name__ == "__main__":
    main()
