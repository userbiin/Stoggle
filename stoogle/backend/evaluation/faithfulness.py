"""[2-B] LLM-as-Judge 요약 충실도 평가

judge 모델은 평가 대상과 같거나 강한 모델을 사용해야 한다.
EXAONE이 만든 걸 EXAONE이 채점하면 자기편향 발생.
judge가 틀리는 경우(20~30%)를 대비해 judge accuracy 사람 검수 권장.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger("stoogle.evaluation")

_JUDGE_PROMPT = """\
원문과 요약을 비교한다. 요약의 각 문장이 원문으로 뒷받침되는지 판단하라.
원문에 없는 정보를 지어낸 문장 수를 세어라. JSON으로만 답하라.

원문: {source}
요약: {summary}

형식: {{"total": 정수, "unsupported": 정수, "unsupported_examples": [문자열]}}"""


def judge_faithfulness(source: str, summary: str, judge_client) -> dict:
    """동기 버전 — 배치 평가 또는 테스트에서 사용."""
    prompt = _JUDGE_PROMPT.format(source=source[:3000], summary=summary)
    resp = judge_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    data = json.loads(resp.content[0].text)
    total = max(data.get("total", 1), 1)
    return {
        "faithfulness": round(1 - data.get("unsupported", 0) / total, 3),
        "unsupported_count": data.get("unsupported", 0),
        "examples": data.get("unsupported_examples", []),
    }


async def judge_faithfulness_async(
    source: str, summary: str, judge_client
) -> Optional[dict]:
    """비동기 버전 — summary_agent 파이프라인에서 사용."""
    try:
        prompt = _JUDGE_PROMPT.format(source=source[:3000], summary=summary)
        resp = await judge_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(resp.content[0].text)
        total = max(data.get("total", 1), 1)
        return {
            "faithfulness": round(1 - data.get("unsupported", 0) / total, 3),
            "unsupported_count": data.get("unsupported", 0),
            "examples": data.get("unsupported_examples", []),
        }
    except Exception as e:
        logger.warning("faithfulness judge 실패: %s", e)
        return None
