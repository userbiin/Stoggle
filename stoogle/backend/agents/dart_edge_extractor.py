"""
DART 공시에서 사업 관계 엣지를 추출하여 company_edges에 저장

두 가지 접근 (LLM 거의 불필요):
  A. DART API 구조화 데이터
     - majorstock.json: 최대주주 현황 → 법인 주주는 affiliate/investor 엣지
     - 계열회사 현황 섹션 regex 파싱
  B. DartChunk 텍스트 regex 파싱
     - 특수관계자 거래 섹션에서 기업명 추출 → affiliate 엣지
     - 주요 원재료·매입처 섹션에서 공급사 추출 → supplier 엣지

흐름:
  run(ticker) → A + B 병렬 실행 → company_edges upsert → 저장 건수 반환
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"

# ─────────────────────────────────────────────────────────────────────────────
# 법인명 패턴 (한국어 회사명 추출)
# ─────────────────────────────────────────────────────────────────────────────

# ㈜삼성전자, 삼성전자㈜, (주)삼성전자, 삼성전자(주), 삼성전자주식회사 등
_COMPANY_PATTERN = re.compile(
    r"(?:㈜|주식회사\s*|(?:\(주\)|\（주\）))\s*([가-힣a-zA-Z0-9&·\s]{2,20})"
    r"|([가-힣a-zA-Z0-9&·]{2,20})\s*(?:㈜|\(주\)|\（주\）|주식회사)",
    re.UNICODE,
)

# 특수관계자 섹션 헤더 패턴
_RELATED_PARTY_HEADERS = re.compile(
    r"특수관계자|관계회사|종속회사|계열회사|관련회사", re.IGNORECASE
)

# 공급사 섹션 헤더 패턴
_SUPPLIER_HEADERS = re.compile(
    r"원재료|매입처|납품|공급업체|주요\s*거래처", re.IGNORECASE
)

# 고객사 섹션 헤더 패턴
_CUSTOMER_HEADERS = re.compile(
    r"매출처|주요\s*고객|판매처", re.IGNORECASE
)

# 법적 접미사 제거 (정규화용)
_LEGAL_SUFFIX = re.compile(
    r"\s*(?:주식회사|㈜|\(주\)|\（주\）|co\.,?\s*ltd\.?|corp\.?|inc\.?)\s*",
    re.IGNORECASE,
)


def _normalize_name(name: str) -> str:
    """법인명 정규화: 법적 접미사 제거 + 공백 정리"""
    return _LEGAL_SUFFIX.sub("", name).strip()


def _extract_company_names(text: str) -> list[str]:
    """텍스트에서 한국 법인명 추출 (정규화 포함)"""
    names = set()
    for m in _COMPANY_PATTERN.finditer(text):
        raw = (m.group(1) or m.group(2) or "").strip()
        norm = _normalize_name(raw)
        if len(norm) >= 2 and not norm.isdigit():
            names.add(norm)
    return list(names)


# ─────────────────────────────────────────────────────────────────────────────
# corp_code 캐시 (dart_indexer와 공유하지 않아 독립적으로 유지)
# ─────────────────────────────────────────────────────────────────────────────

_CORP_CODE_CACHE: dict[str, str] = {}  # ticker → corp_code


async def _get_corp_code(ticker: str, api_key: str) -> Optional[str]:
    if ticker in _CORP_CODE_CACHE:
        return _CORP_CODE_CACHE[ticker]
    try:
        # dart_indexer의 _CORP_CODE_MAP을 재사용
        from agents.dart_indexer import _CORP_CODE_MAP, _load_corp_codes

        await _load_corp_codes(api_key)
        code = _CORP_CODE_MAP.get(ticker)
        if code:
            _CORP_CODE_CACHE[ticker] = code
        return code
    except Exception as e:
        logger.debug("corp_code 조회 실패 (%s): %s", ticker, e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# A. DART API 구조화 데이터 파싱
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_majorstock(corp_code: str, api_key: str) -> list[dict]:
    """최대주주 현황 조회 (majorstock.json). 법인 주주만 반환."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"{DART_BASE}/majorstock.json",
                params={"crtfc_key": api_key, "corp_code": corp_code},
            )
            res.raise_for_status()
        data = res.json()
        if data.get("status") != "000":
            return []
        return data.get("list", [])
    except Exception as e:
        logger.debug("majorstock.json 조회 실패 (%s): %s", corp_code, e)
        return []


async def _fetch_executive_member(corp_code: str, api_key: str) -> list[dict]:
    """임원 현황 조회 (executiveMember.json) — 겸직 계열사 정보 추출용."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"{DART_BASE}/executiveMember.json",
                params={"crtfc_key": api_key, "corp_code": corp_code},
            )
            res.raise_for_status()
        data = res.json()
        if data.get("status") != "000":
            return []
        return data.get("list", [])
    except Exception as e:
        logger.debug("executiveMember.json 조회 실패 (%s): %s", corp_code, e)
        return []


def _edges_from_majorstock(ticker: str, rows: list[dict], reverse_reg: dict) -> list[dict]:
    """
    최대주주 현황에서 법인 주주를 찾아 affiliate 엣지를 생성한다.
    개인 주주(nm에 '대표이사', '임원' 등 직함 포함)는 제외.
    """
    individual_keywords = re.compile(r"대표이사|이사|사장|회장|부회장|임원|상무|전무|부사장")
    edges = []

    for row in rows:
        shareholder_nm = (row.get("nm") or "").strip()
        relate = (row.get("relate") or "").strip()
        hold_pct_str = (row.get("bsis_posesn_stock_qota_rt") or "0").replace(",", "")

        # 개인 주주 제외
        if individual_keywords.search(shareholder_nm):
            continue

        norm = _normalize_name(shareholder_nm)
        if len(norm) < 2:
            continue

        dst_ticker = _resolve_ticker(norm, reverse_reg)
        if dst_ticker is None or dst_ticker == ticker:
            continue

        try:
            hold_pct = float(hold_pct_str)
        except ValueError:
            hold_pct = 0.0

        # 지분율을 confidence로 사용 (최대 1.0)
        confidence = min(hold_pct / 100.0, 1.0)
        evidence = f"최대주주 지분 {hold_pct:.2f}% ({relate})"

        edges.append({
            "src": ticker,
            "dst": dst_ticker,
            "relation_type": "affiliate",
            "direction": "forward",
            "weight": confidence,
            "confidence": confidence,
            "evidence": evidence,
            "source": "dart",
        })

    return edges


def _edges_from_executives(ticker: str, rows: list[dict], reverse_reg: dict) -> list[dict]:
    """
    임원 현황의 '주요경력/겸직' 컬럼에서 계열사명을 추출해 affiliate 엣지를 생성한다.
    """
    edges = []
    seen: set[str] = set()

    for row in rows:
        career = (row.get("main_career") or "") + " " + (row.get("spcmt_matter") or "")
        for name in _extract_company_names(career):
            dst_ticker = _resolve_ticker(name, reverse_reg)
            if dst_ticker is None or dst_ticker == ticker or dst_ticker in seen:
                continue
            seen.add(dst_ticker)
            edges.append({
                "src": ticker,
                "dst": dst_ticker,
                "relation_type": "affiliate",
                "direction": "forward",
                "weight": 0.5,
                "confidence": 0.5,
                "evidence": f"임원 겸직 계열사: {name}",
                "source": "dart",
            })

    return edges


# ─────────────────────────────────────────────────────────────────────────────
# B. DartChunk 텍스트 regex 파싱
# ─────────────────────────────────────────────────────────────────────────────

def _edges_from_dart_chunks(ticker: str, reverse_reg: dict) -> list[dict]:
    """
    DartChunk 테이블에서 특수관계자·공급사·고객사 섹션을 찾아
    기업명 regex로 추출 → company_edges 변환.
    pgvector 미설치 시 빈 리스트 반환.
    """
    try:
        from models.db_models import DartChunk, SessionLocal, PGVECTOR_AVAILABLE
        from sqlalchemy import or_

        if not PGVECTOR_AVAILABLE or DartChunk is None:
            return []

        db = SessionLocal()
        try:
            rows = (
                db.query(DartChunk)
                .filter(
                    DartChunk.ticker == ticker,
                    or_(
                        DartChunk.section_title.ilike("%특수관계%"),
                        DartChunk.section_title.ilike("%계열회사%"),
                        DartChunk.section_title.ilike("%종속회사%"),
                        DartChunk.section_title.ilike("%관계회사%"),
                        DartChunk.section_title.ilike("%원재료%"),
                        DartChunk.section_title.ilike("%매입처%"),
                        DartChunk.section_title.ilike("%매출처%"),
                        DartChunk.content.ilike("%특수관계자%"),
                    ),
                )
                .order_by(DartChunk.indexed_at.desc())
                .limit(15)
                .all()
            )
        finally:
            db.close()
    except Exception as e:
        logger.debug("DartChunk 조회 실패 (%s): %s", ticker, e)
        return []

    edges = []
    seen: set[str] = set()

    for row in rows:
        section = (row.section_title or "").lower()
        content = row.content or ""

        if _RELATED_PARTY_HEADERS.search(section) or "특수관계자" in content:
            relation_type = "affiliate"
            confidence = 0.8
        elif _SUPPLIER_HEADERS.search(section):
            relation_type = "supplier"
            confidence = 0.7
        elif _CUSTOMER_HEADERS.search(section):
            relation_type = "customer"
            confidence = 0.65
        else:
            relation_type = "affiliate"
            confidence = 0.5

        for name in _extract_company_names(content):
            dst_ticker = _resolve_ticker(name, reverse_reg)
            if dst_ticker is None or dst_ticker == ticker:
                continue
            key = (dst_ticker, relation_type)
            if key in seen:
                continue
            seen.add(key)

            edges.append({
                "src": ticker,
                "dst": dst_ticker,
                "relation_type": relation_type,
                "direction": "forward",
                "weight": None,
                "confidence": confidence,
                "evidence": f"[DART {row.section_title}] 에서 추출: {name}",
                "source": "dart",
            })

    return edges


# ─────────────────────────────────────────────────────────────────────────────
# 이름 → 종목코드 해석
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_ticker(name: str, reverse_reg: dict[str, str]) -> Optional[str]:
    """
    기업명을 종목코드로 변환.
    1. 정확 매칭 → 2. 정규화 매칭 → 3. 부분 매칭 (3자 이상)
    """
    if name in reverse_reg:
        return reverse_reg[name]
    norm = _normalize_name(name)
    if norm in reverse_reg:
        return reverse_reg[norm]
    if len(norm) >= 3:
        for reg_name, ticker in reverse_reg.items():
            if norm in reg_name or reg_name in norm:
                return ticker
    return None


def _build_reverse_registry() -> dict[str, str]:
    """레지스트리 역방향 조회 테이블 생성."""
    try:
        from services.stock_service import get_or_build_registry

        registry = get_or_build_registry()
        rev: dict[str, str] = {}
        for ticker, info in registry.items():
            raw = info.get("name", "")
            if not raw:
                continue
            rev[raw] = ticker
            norm = _normalize_name(raw)
            if norm and norm != raw:
                rev[norm] = ticker
        return rev
    except Exception as e:
        logger.warning("역방향 레지스트리 생성 실패: %s", e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# DB 저장
# ─────────────────────────────────────────────────────────────────────────────

def _save_edges(edges: list[dict]) -> int:
    """
    company_edges upsert.
    같은 (src, dst, relation_type) 쌍은 confidence가 높은 쪽으로 갱신.
    """
    if not edges:
        return 0
    try:
        from models.db_models import CompanyEdge, SessionLocal
        from datetime import datetime

        db = SessionLocal()
        try:
            count = 0
            for edge in edges:
                existing = (
                    db.query(CompanyEdge)
                    .filter(
                        CompanyEdge.src == edge["src"],
                        CompanyEdge.dst == edge["dst"],
                        CompanyEdge.relation_type == edge["relation_type"],
                    )
                    .first()
                )

                if existing:
                    # confidence가 높아지는 경우에만 갱신
                    new_conf = edge.get("confidence") or 0.0
                    old_conf = existing.confidence or 0.0
                    if new_conf > old_conf:
                        existing.confidence = new_conf
                        existing.evidence = edge.get("evidence") or existing.evidence
                        existing.weight = edge.get("weight") or existing.weight
                        existing.updated_at = datetime.utcnow()
                        count += 1
                else:
                    db.add(CompanyEdge(
                        src=edge["src"],
                        dst=edge["dst"],
                        relation_type=edge["relation_type"],
                        direction=edge.get("direction", "forward"),
                        weight=edge.get("weight"),
                        confidence=edge.get("confidence"),
                        evidence=edge.get("evidence"),
                        source=edge.get("source", "dart"),
                    ))
                    count += 1

            db.commit()
            return count
        finally:
            db.close()
    except Exception as e:
        logger.error("company_edges 저장 실패: %s", e)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def extract_dart_edges(ticker: str) -> int:
    """
    DART 공시에서 사업 관계 엣지를 추출하여 company_edges에 저장.

    Returns
    -------
    int: 저장된 엣지 수
    """
    api_key = os.getenv("DART_API_KEY")
    reverse_reg = _build_reverse_registry()
    if not reverse_reg:
        logger.warning("[%s] 레지스트리 없음 — dart_edge_extractor 건너뜀", ticker)
        return 0

    all_edges: list[dict] = []

    # A. DART API 구조화 데이터 (API 키 있을 때만)
    if api_key:
        corp_code = await _get_corp_code(ticker, api_key)
        if corp_code:
            major_rows, exec_rows = await _parallel_fetch(corp_code, api_key)
            all_edges.extend(_edges_from_majorstock(ticker, major_rows, reverse_reg))
            all_edges.extend(_edges_from_executives(ticker, exec_rows, reverse_reg))
        else:
            logger.info("[%s] corp_code 없음 — DART API 엣지 건너뜀", ticker)
    else:
        logger.debug("[%s] DART_API_KEY 없음 — DartChunk 파싱만 실행", ticker)

    # B. DartChunk 텍스트 regex 파싱 (pgvector 있을 때만)
    all_edges.extend(_edges_from_dart_chunks(ticker, reverse_reg))

    # 중복 제거: (src, dst, relation_type) 기준, confidence 최대값 유지
    deduped: dict[tuple, dict] = {}
    for edge in all_edges:
        key = (edge["src"], edge["dst"], edge["relation_type"])
        existing = deduped.get(key)
        if existing is None or (edge.get("confidence") or 0) > (existing.get("confidence") or 0):
            deduped[key] = edge

    saved = _save_edges(list(deduped.values()))
    logger.info("[%s] dart_edge_extractor 완료: %d건 저장 (후보 %d건)", ticker, saved, len(deduped))
    return saved


async def _parallel_fetch(corp_code: str, api_key: str):
    """최대주주·임원 현황을 병렬로 조회한다."""
    import asyncio

    major_task = asyncio.create_task(_fetch_majorstock(corp_code, api_key))
    exec_task = asyncio.create_task(_fetch_executive_member(corp_code, api_key))
    return await asyncio.gather(major_task, exec_task)


# ─────────────────────────────────────────────────────────────────────────────
# 직접 실행 (테스트)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys

    ticker_arg = sys.argv[1] if len(sys.argv) > 1 else "005930"
    count = asyncio.run(extract_dart_edges(ticker_arg))
    print(f"[{ticker_arg}] company_edges 저장: {count}건")
