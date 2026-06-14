# 백테스트 재채점
from __future__ import annotations

import argparse
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


# 재채점
def rescore(model_version: str) -> None:
    from datetime import datetime
    from models.db_models import PredictionLog, SessionLocal
    from evaluation.prediction_scorer import MAGNITUDE_THRESHOLD

    db = SessionLocal()
    try:
        rows = (
            db.query(PredictionLog)
            .filter(
                PredictionLog.model_version == model_version,
                PredictionLog.status == "scored",
                PredictionLog.actual_direction.isnot(None),
                PredictionLog.direction.isnot(None),
            )
            .all()
        )

        if not rows:
            print(f"재채점 대상 레코드 없음 (model_version={model_version})")
            return

        rescored = 0
        for p in rows:
            direction_correct = (p.direction == p.actual_direction)
            magnitude_hit = (
                abs(p.actual_change) >= MAGNITUDE_THRESHOLD
                if p.actual_change is not None else False
            )

            old_is_correct = p.is_correct
            p.is_correct = direction_correct          # 방향 일치 (주지표)
            p.abnormal_return = 1.0 if magnitude_hit else 0.0  # 변동폭 (부지표)
            p.evaluated_at = datetime.utcnow()
            rescored += 1

        db.commit()
        print(f"재채점 완료: {rescored}건 (model_version={model_version})")
    finally:
        db.close()


# 편향 진단
def diagnose(model_version: str) -> None:
    from models.db_models import PredictionLog, SessionLocal

    db = SessionLocal()
    try:
        rows = (
            db.query(
                PredictionLog.ticker,
                PredictionLog.source_ticker,
                PredictionLog.direction,
                PredictionLog.actual_direction,
                PredictionLog.confidence,
                PredictionLog.is_correct,
                PredictionLog.actual_change,
                PredictionLog.prediction_date,
                PredictionLog.reason,
            )
            .filter(
                PredictionLog.model_version == model_version,
                PredictionLog.status == "scored",
            )
            .all()
        )

        if not rows:
            print(f"진단 대상 없음 (model_version={model_version})")
            return

        n = len(rows)
        pred_up = sum(1 for r in rows if r.direction == "up")
        pred_down = sum(1 for r in rows if r.direction == "down")
        actual_up = sum(1 for r in rows if r.actual_direction == "up")
        actual_down = sum(1 for r in rows if r.actual_direction == "down")
        correct = sum(1 for r in rows if r.is_correct)

        print(f"\n=== 예측 편향 진단: {model_version} ===")
        print(f"총 채점 레코드: {n}건\n")

        print("[예측 방향 분포]")
        print(f"  상승(up) 예측:  {pred_up:3d}건 ({pred_up/n:.1%})")
        print(f"  하락(down) 예측:{pred_down:3d}건 ({pred_down/n:.1%})")
        if abs(pred_up/n - 0.5) > 0.2:
            dominant = "상승" if pred_up > pred_down else "하락"
            print(f"  ⚠️  편향 감지: {dominant} 방향을 {max(pred_up,pred_down)/n:.1%} 비율로 예측")
            print(f"      → analysis_agent 프롬프트나 impacts 스키마 확인 필요")

        print(f"\n[실제 방향 분포]")
        print(f"  상승(up):  {actual_up:3d}건 ({actual_up/n:.1%})")
        print(f"  하락(down):{actual_down:3d}건 ({actual_down/n:.1%})")

        print(f"\n[방향 정확도 (재채점 기준)]")
        print(f"  Direction Accuracy: {correct}/{n} = {correct/n:.3f}")

        # 상세 케이스 출력
        print(f"\n[개별 레코드 상세]")
        print(f"{'source':>10} {'target':>10} {'pred':>6} {'actual':>8} {'Δ%':>8} {'conf':>6} {'ok':>4}")
        print("-" * 60)
        for r in rows:
            chg = f"{r.actual_change*100:+.1f}%" if r.actual_change else "N/A"
            ok = "✓" if r.is_correct else "✗"
            conf = f"{r.confidence:.2f}" if r.confidence else "N/A"
            print(
                f"{r.source_ticker:>10} {r.ticker:>10} "
                f"{r.direction:>6} {r.actual_direction or 'N/A':>8} "
                f"{chg:>8} {conf:>6} {ok:>4}"
            )

        # 편향 원인 추정
        print(f"\n[편향 원인 추정]")
        if pred_up / n > 0.7:
            print("  모델이 '상승'을 과도하게 예측하는 이유 가능성:")
            print("  1. analysis_agent 프롬프트가 긍정적 뉴스를 더 강조")
            print("  2. NewsCache에 긍정적 뉴스가 더 많이 적재됨")
            print("  3. impacts 스키마에서 direction='up'이 기본값으로 작동")
            print("  4. 백테스트 샘플이 특정 날짜(상승장)에 편중")
        elif pred_down / n > 0.7:
            print("  모델이 '하락'을 과도하게 예측 → 하락장 편중 샘플 가능성")

        # 날짜 분포 확인
        dates = {}
        for r in rows:
            dates[r.prediction_date] = dates.get(r.prediction_date, 0) + 1
        print(f"\n[예측 날짜 분포]")
        for d, cnt in sorted(dates.items()):
            print(f"  {d}: {cnt}건")

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_version", default="pilot_v1")
    parser.add_argument(
        "--diagnose", action="store_true",
        help="편향 진단 출력 (재채점 후 자동 실행)"
    )
    parser.add_argument(
        "--rescore_only", action="store_true",
        help="재채점만 하고 진단 출력 안 함"
    )
    args = parser.parse_args()

    print(f"=== {args.model_version} 재채점 시작 ===")
    rescore(args.model_version)

    if not args.rescore_only:
        diagnose(args.model_version)


if __name__ == "__main__":
    main()