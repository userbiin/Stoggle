"""
Naver API pubDate(RFC 822) 파싱 + 시점 필터

백테스트의 핵심 보호막: as_of 이후 기사를 LLM 컨텍스트에서 제거한다.
라이브 경로에서도 동일 필터를 적용해 이중 보호를 제공한다.
"""
from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterable


def parse_pubdate(rfc822_str: str) -> datetime:
    """Naver API pubDate(RFC 822) → tz-naive datetime(UTC)."""
    dt = parsedate_to_datetime(rfc822_str)
    # tz-aware → tz-naive UTC 변환 (DB 비교 일관성)
    if dt.tzinfo is not None:
        import datetime as _dt
        dt = dt.utctimetuple()
        dt = datetime(*dt[:6])
    return dt


def filter_before(items: Iterable[dict], as_of: datetime) -> list[dict]:
    """
    as_of(tz-naive) 이전에 발행된 기사만 반환.

    pubDate 누락/파싱 실패 항목은 안전하게 제외 (누락보다 누출이 더 위험).
    반환 항목에 '_parsed_pubdate' 키(datetime)가 추가된다.
    """
    # as_of를 tz-naive로 정규화
    if hasattr(as_of, "tzinfo") and as_of.tzinfo is not None:
        as_of = as_of.replace(tzinfo=None)

    result = []
    for it in items:
        raw = it.get("pubDate")
        if not raw:
            continue
        try:
            pub = parse_pubdate(raw)
        except Exception:
            continue
        if pub < as_of:
            result.append({**it, "_parsed_pubdate": pub})
    return result
