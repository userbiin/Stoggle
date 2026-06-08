"""
백테스트 메인 실행 스크립트

흐름:
  1. (ticker, as_of) 데이터셋 빌드
     --from_db: NewsCache에 실제 존재하는 (ticker, date) 페어에서만 추출 (no_news skip 최소화)
     기본: KOSPI50 fallback × 거래일 무작위
  2. 각 샘플에 대해 run_analysis_at() — 5개 소스 시점 격리 예측 생성
  3. 즉시 채점 — D+3가 이미 지난 과거 데이터이므로 force_score_all=True로 전부 채점
  4. /api/v1/_internal/prediction-metrics?model_version=backtest_v1 로 확인

실행 예시:
    cd backend/
    python scripts/run_backtest.py --from_db --start 20260520 --end 20260605 --safety_days 3 --n_samples 50
    python scripts/run_backtest.py --n_samples 30 --start 20260522 --end 20260530
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("backtest.run")


async def run(
    start: str,
    end: str,
    n_samples: int,
    model_version: str,
    seed: int,
    concurrency: int,
    safety_days: int = 5,
    from_db: bool = False,
) -> None:
    from evaluation.dataset_builder import build_dataset, build_dataset_from_db
    from evaluation.backtest import run_analysis_at
    # force_score_all=True 를 지원하는 함수 임포트
    from evaluation.prediction_scorer import score_all_pending, prediction_metrics
    from models.db_models import SessionLocal

    if from_db:
        start_iso = datetime.strptime(start, "%Y%m%d").strftime("%Y-%m-%d")
        end_iso = datetime.strptime(end, "%Y%m%d").strftime("%Y-%m-%d")
        samples = build_dataset_from_db(
            start=start_iso, end=end_iso,
            n_samples=n_samples, seed=seed, safety_days=safety_days,
        )
    else:
        samples = build_dataset(
            start=start, end=end,
            n_samples=n_samples, seed=seed, safety_days=safety_days,
        )

    logger.info(
        "데이터셋: %d 샘플 (%s ~ %s, seed=%d, safety_days=%d, from_db=%s)",
        len(samples), start, end, seed, safety_days, from_db,
    )

    db = SessionLocal()
    ok = skipped = errors = 0
    sem = asyncio.Semaphore(concurrency)
    t0 = time.time()

    async def _run_one(i: int, s: dict) -> None:
        nonlocal ok, skipped, errors
        async with sem:
            try:
                res = await run_analysis_at(s["ticker"], s["as_of"], db, model_version)
                status = res.get("status", "?")
                if status == "ok":
                    ok += 1
                else:
                    skipped += 1
                if (i + 1) % 10 == 0 or (i + 1) == len(samples):
                    elapsed = time.time() - t0
                    logger.info(
                        "[%d/%d] ok=%d skip=%d err=%d (%.0fs)",
                        i + 1, len(samples), ok, skipped, errors, elapsed,
                    )
            except Exception as e:
                errors += 1
                logger.warning(
                    "[%d/%d] ERROR %s@%s: %s",
                    i + 1, len(samples), s["ticker"], s["as_of"], e,
                )

    tasks = [_run_one(i, s) for i, s in enumerate(samples)]
    await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    logger.info(
        "\n예측 생성 완료: ok=%d skip=%d err=%d (총 %.0f초)",
        ok, skipped, errors, elapsed,
    )

    # ── 즉시 채점: force_score_all=True로 pending 잔류 방지 ──────────────
    # 백테스트 레코드는 predicted_at이 과거라 D+3가 이미 경과했으므로
    # is_d3_passed() 체크를 건너뛰고 전부 채점한다.
    logger.info("채점 시작 (model_version=%s, force_score_all=True)...", model_version)
    scored_result = score_all_pending(db, model_version=model_version)
    db.close()

    logger.info("채점 완료: %s", scored_result)

    # 지표 요약 출력
    db2 = SessionLocal()
    try:
        metrics = prediction_metrics(db2, model_version=model_version)
        logger.info(
            "\n=== 성능 요약 ===\n"
            "  scored:                  %d건\n"
            "  direction_accuracy:      %.3f  (주지표 — 랜덤 베이스라인: ~0.500)\n"
            "  magnitude_hit_rate:      %s  (부지표 — |Δ|≥%.0f%%)\n"
            "  high_confidence_acc:     %.3f  (confidence≥0.7 기준, n=%d)\n",
            metrics.get("n_scored", 0),
            metrics.get("direction_accuracy", 0),
            f"{metrics['magnitude_hit_rate']:.3f}" if metrics.get("magnitude_hit_rate") else "N/A",
            (metrics.get("threshold", 0.02)) * 100,
            metrics.get("high_confidence_accuracy", 0),
            metrics.get("n_high_conf", 0),
        )
    finally:
        db2.close()

    logger.info(
        "\n결과 확인:\n"
        "  curl 'http://localhost:8000/api/v1/_internal/prediction-metrics?model_version=%s'\n"
        "  python scripts/inspect_backtest.py --model_version %s\n"
        "  python scripts/inspect_backtest.py --model_version %s --breakdown confidence",
        model_version, model_version, model_version,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Stoogle 백테스트 실행")
    parser.add_argument("--start", default="20260301")
    parser.add_argument("--end", default="20260430")
    parser.add_argument("--n_samples", type=int, default=50)
    parser.add_argument("--model_version", default="backtest_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--safety_days", type=int, default=5,
        help="as_of <= today - safety_days 강제. 0이면 제약 없음. (기본 5)",
    )
    parser.add_argument(
        "--from_db", action="store_true",
        help="NewsCache에 실제 존재하는 (ticker, date) 페어에서만 추출. no_news skip 최소화.",
    )
    args = parser.parse_args()

    asyncio.run(run(
        start=args.start, end=args.end,
        n_samples=args.n_samples, model_version=args.model_version,
        seed=args.seed, concurrency=args.concurrency,
        safety_days=args.safety_days,
        from_db=args.from_db,
    ))


if __name__ == "__main__":
    main()