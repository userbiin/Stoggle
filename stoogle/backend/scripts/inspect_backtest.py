"""
백테스트 결과 조회/분석 스크립트

[프로젝트 평가 기준]
  핵심 질문: "뉴스가 뜬 종목이 실제로 유의미하게 변동했는가?"
  주지표: Magnitude Hit Rate — |actual_change| >= MAGNITUDE_THRESHOLD
  
  direction_accuracy(상승/하락 방향 일치)는 본 프로젝트의 목표가 아니므로 제거.

실행 예시:
    cd backend/
    python scripts/inspect_backtest.py --model_version backtest_v2
    python scripts/inspect_backtest.py --model_version backtest_v2 --breakdown confidence
    python scripts/inspect_backtest.py --model_version backtest_v2 --detail
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()


def show_summary(model_version: str) -> None:
    from models.db_models import PredictionLog, SessionLocal
    from evaluation.prediction_scorer import MAGNITUDE_THRESHOLD

    db = SessionLocal()
    try:
        base = db.query(PredictionLog).filter(PredictionLog.model_version == model_version)
        total = base.count()
        pending = base.filter(PredictionLog.status == "pending").count()
        scored = base.filter(PredictionLog.status == "scored").count()
        skipped = base.filter(PredictionLog.status == "skipped").count()

        rows = (
            db.query(
                PredictionLog.abnormal_return,   # 1.0=magnitude_hit, 0.0=miss
                PredictionLog.confidence,
                PredictionLog.actual_change,
            )
            .filter(PredictionLog.model_version == model_version, PredictionLog.status == "scored")
            .all()
        )
        n = len(rows)

        print(f"\n=== 백테스트 결과: {model_version} ===")
        print(f"전체 레코드:          {total}건")
        print(f"  pending (미채점):   {pending}건", " ← 있으면 문제" if pending > 0 else "")
        print(f"  scored  (채점 완):  {scored}건")
        print(f"  skipped (데이터無): {skipped}건")
        print(f"  skip률:             {skipped/max(total,1):.1%}  (주가 조회 실패 비율)")

        if n == 0:
            print("\n채점 데이터 없음")
            return

        # ── 주지표: Magnitude Hit Rate ──────────────────────────────────────
        mag_rows = [r for r in rows if r.abnormal_return is not None]
        hits = sum(1 for r in mag_rows if r.abnormal_return >= 0.5)
        mag_hit = hits / len(mag_rows) if mag_rows else 0

        # 실제 변동폭 분포
        changes = [abs(r.actual_change) * 100 for r in rows if r.actual_change is not None]
        avg_change = sum(changes) / len(changes) if changes else 0
        max_change = max(changes) if changes else 0

        print(f"\n[주지표 — 변동 적중률 (Magnitude Hit Rate)]")
        print(f"  임계값:                ±{MAGNITUDE_THRESHOLD:.0%}")
        print(f"  Hit Rate:              {mag_hit:.3f}  ({hits}/{len(mag_rows)})")
        print(f"  목표:                  ≥ 0.600")
        print(f"  평균 실제 변동폭:      {avg_change:.2f}%")
        print(f"  최대 실제 변동폭:      {max_change:.2f}%")

        # 랜덤 베이스라인 추정
        # KOSPI 일평균 변동폭이 약 1~1.5%, 2% 이상 변동하는 날은 약 30~40%
        print(f"\n  랜덤 베이스라인:       ~0.35  (KOSPI 2%+ 변동일 비율 추정)")
        baseline = 0.35
        lift = mag_hit - baseline
        if lift > 0.1:
            print(f"  Lift:                  +{lift:.3f}  ✓ 의미 있음")
        elif lift > 0:
            print(f"  Lift:                  +{lift:.3f}  △ 미미한 수준")
        else:
            print(f"  Lift:                  {lift:.3f}  ✗ 베이스라인 미달")

        # ── Calibration: confidence ↔ hit rate ──────────────────────────────
        high = [r for r in mag_rows if r.confidence is not None and r.confidence >= 0.7]
        high_hit = sum(1 for r in high if r.abnormal_return >= 0.5) / max(len(high), 1)

        low = [r for r in mag_rows if r.confidence is not None and r.confidence < 0.7]
        low_hit = sum(1 for r in low if r.abnormal_return >= 0.5) / max(len(low), 1)

        print(f"\n[Calibration — confidence 높을수록 변동 적중률이 높아야 정상]")
        print(f"  confidence < 0.7:   Hit {low_hit:.3f}  (n={len(low)})")
        print(f"  confidence ≥ 0.7:   Hit {high_hit:.3f}  (n={len(high)})")
        if len(high) >= 3 and len(low) >= 3:
            if high_hit > low_hit:
                print(f"  판정: ✓ 높은 confidence가 더 잘 적중 — calibration 의미 있음")
            else:
                diff = low_hit - high_hit
                print(f"  판정: △ 역전 현상 ({diff:.3f}) — 샘플 부족 또는 confidence 과신")
        else:
            print(f"  판정: 샘플 부족 — 더 많은 샘플 필요")

        # ── 최종 판정 ────────────────────────────────────────────────────────
        print(f"\n[최종 판정]")
        if mag_hit >= 0.70:
            print(f"  PASS  (magnitude_hit_rate {mag_hit:.3f} ≥ 0.700)")
            print(f"  → 모델이 뉴스 기반 주가 변동 종목 식별에 유효함")
        elif mag_hit >= 0.60:
            print(f"  MARGINAL  (magnitude_hit_rate {mag_hit:.3f}, 목표 0.600 달성)")
        else:
            print(f"  FAIL  (magnitude_hit_rate {mag_hit:.3f} < 0.600)")

    finally:
        db.close()


def show_by_confidence(model_version: str) -> None:
    from models.db_models import PredictionLog, SessionLocal
    from evaluation.prediction_scorer import MAGNITUDE_THRESHOLD

    db = SessionLocal()
    try:
        rows = (
            db.query(PredictionLog.confidence, PredictionLog.abnormal_return, PredictionLog.actual_change)
            .filter(PredictionLog.model_version == model_version, PredictionLog.status == "scored")
            .all()
        )

        bins = {
            "0.0-0.5": [], "0.5-0.6": [], "0.6-0.7": [], "0.7-0.8": [], "0.8-1.0": []
        }
        for r in rows:
            c = r.confidence or 0.0
            hit = (r.abnormal_return or 0) >= 0.5
            if c < 0.5:
                bins["0.0-0.5"].append(hit)
            elif c < 0.6:
                bins["0.5-0.6"].append(hit)
            elif c < 0.7:
                bins["0.6-0.7"].append(hit)
            elif c < 0.8:
                bins["0.7-0.8"].append(hit)
            else:
                bins["0.8-1.0"].append(hit)

        print(f"\n=== 신뢰도 구간별 Magnitude Hit Rate ({model_version}) ===")
        print(f"임계값: |Δ| ≥ {MAGNITUDE_THRESHOLD:.0%}")
        print(f"{'confidence':>12} {'n':>6} {'hit_rate':>10}")
        print("-" * 32)
        for band, vals in bins.items():
            n = len(vals)
            rate = sum(vals) / n if n else 0
            bar = "█" * int(rate * 20)
            print(f"{band:>12} {n:>6} {rate:>10.3f}  {bar}")

        print(f"\n기대치: confidence 높을수록 hit_rate가 높아야 calibration이 유효함")

    finally:
        db.close()


def show_detail(model_version: str) -> None:
    """개별 레코드 상세 출력."""
    from models.db_models import PredictionLog, SessionLocal
    from evaluation.prediction_scorer import MAGNITUDE_THRESHOLD

    db = SessionLocal()
    try:
        rows = (
            db.query(PredictionLog)
            .filter(PredictionLog.model_version == model_version, PredictionLog.status == "scored")
            .order_by(PredictionLog.prediction_date)
            .all()
        )

        print(f"\n=== 개별 레코드 상세: {model_version} ===")
        print(f"{'date':>12} {'source':>10} {'target':>10} {'conf':>6} {'Δ%':>8} {'≥{:.0%}?'.format(MAGNITUDE_THRESHOLD):>7}")
        print("-" * 60)

        for p in rows:
            chg = f"{p.actual_change*100:+.1f}%" if p.actual_change is not None else "N/A"
            hit = "✓" if (p.abnormal_return or 0) >= 0.5 else "✗"
            conf = f"{p.confidence:.2f}" if p.confidence else "N/A"
            print(
                f"{p.prediction_date:>12} {p.source_ticker:>10} {p.ticker:>10} "
                f"{conf:>6} {chg:>8} {hit:>7}"
            )

        hits = sum(1 for p in rows if (p.abnormal_return or 0) >= 0.5)
        print(f"\n합계: {hits}/{len(rows)} hit ({hits/max(len(rows),1):.1%})")

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="백테스트 결과 조회 (변동 적중률 기준)")
    parser.add_argument("--model_version", default="backtest_v2")
    parser.add_argument(
        "--breakdown",
        choices=["confidence"],
        help="신뢰도 구간별 분석",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="개별 레코드 상세 출력",
    )
    args = parser.parse_args()

    show_summary(args.model_version)

    if args.breakdown == "confidence":
        show_by_confidence(args.model_version)

    if args.detail:
        show_detail(args.model_version)


if __name__ == "__main__":
    main()