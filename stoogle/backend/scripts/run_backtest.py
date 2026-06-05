"""
백테스트 메인 실행 스크립트

흐름:
  1. (ticker, as_of) 데이터셋 빌드 (KOSPI50 × 과거 기간 무작위 추출)
  2. 각 샘플에 대해 run_analysis_at() — 5개 소스 시점 격리 예측 생성
  3. 즉시 채점 — D+3가 이미 지난 과거 데이터이므로 한 번에 전부 채점
  4. /api/v1/_internal/prediction-metrics?model_version=backtest_v1 로 확인

실행 예시:
    cd backend/
    python scripts/run_backtest.py --n_samples 20 --start 20260301 --end 20260430
    python scripts/run_backtest.py --n_samples 300 --model_version backtest_v2
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

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
) -> None:
    from evaluation.dataset_builder import build_dataset
    from evaluation.backtest import run_analysis_at
    from evaluation.prediction_scorer import score_pending_predictions
    from models.db_models import SessionLocal

    samples = build_dataset(start=start, end=end, n_samples=n_samples, seed=seed)
    logger.info("데이터셋: %d 샘플 (%s ~ %s, seed=%d)", len(samples), start, end, seed)

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
                logger.warning("[%d/%d] ERROR %s@%s: %s", i + 1, len(samples), s["ticker"], s["as_of"], e)

    tasks = [_run_one(i, s) for i, s in enumerate(samples)]
    await asyncio.gather(*tasks)

    elapsed = time.time() - t0
    logger.info(
        "\n예측 생성 완료: ok=%d skip=%d err=%d (총 %.0f초)",
        ok, skipped, errors, elapsed,
    )

    # 즉시 채점 (D+3가 이미 지난 과거 레코드)
    logger.info("채점 시작 (model_version=%s)...", model_version)
    scored_result = score_pending_predictions(db, model_version=model_version)
    db.close()

    logger.info("채점 완료: %s", scored_result)
    logger.info(
        "\n결과 확인:\n"
        "  curl 'http://localhost:8000/api/v1/_internal/prediction-metrics?model_version=%s'\n"
        "  python scripts/inspect_backtest.py --model_version %s",
        model_version, model_version,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Stoogle 백테스트 실행")
    parser.add_argument("--start", default="20260301", help="시작 날짜 YYYYMMDD")
    parser.add_argument("--end", default="20260430", help="종료 날짜 YYYYMMDD")
    parser.add_argument("--n_samples", type=int, default=50, help="샘플 수 (기본 50)")
    parser.add_argument("--model_version", default="backtest_v1", help="모델 버전 식별자")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드")
    parser.add_argument("--concurrency", type=int, default=3, help="동시 LLM 호출 수 (기본 3)")
    args = parser.parse_args()

    asyncio.run(run(
        start=args.start,
        end=args.end,
        n_samples=args.n_samples,
        model_version=args.model_version,
        seed=args.seed,
        concurrency=args.concurrency,
    ))


if __name__ == "__main__":
    main()
