# DART 엣지 추출기
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

# 법인명 패턴
_COMPANY_PATTERN = re.compile(
    r"(?:㈜|주식회사\s*|(?:\(주\)|\（주\）))\s*([가-힣a-zA-Z0-9&·\s]{2,20})"
    r"|([가-힣a-zA-Z0-9&·]{2,20})\s*(?:㈜|\(주\)|\（주\）|주식회사)",
    re.UNICODE,
)

# 섹션 헤더 패턴
_RELATED_PARTY_HEADERS = re.compile(
    r"특수관계자|관계회사|종속회사|계열회사|관련회사", re.IGNORECASE
)

# 공급사 섹션 헤더 패턴
_SUPPLIER_HEADERS = re.compile(
    r"원재료|매입처|납품|공급업체|주요\s*거래처", re.IGNORECASE
)

_CUSTOMER_HEADERS = re.compile(
    r"매출처|주요\s*고객|판매처", re.IGNORECASE
)

_LEGAL_SUFFIX = re.compile(
    r"\s*(?:주식회사|㈜|\(주\)|\（주\）|co\.,?\s*ltd\.?|corp\.?|inc\.?)\s*",
    re.IGNORECASE,
)


# 법인명 정규화
def _normalize_name(name: str) -> str:
    return _LEGAL_SUFFIX.sub("", name).strip()


# 법인명 추출
def _extract_company_names(text: str) -> list[str]:
    names = set()
    for m in _COMPANY_PATTERN.finditer(text):
        raw = (m.group(1) or m.group(2) or "").strip()
        norm = _normalize_name(raw)
        if len(norm) >= 2 and not norm.isdigit():
            names.add(norm)
    return list(names)


# corp_code 캐시
_CORP_CODE_CACHE: dict[str, str] = {}


# corp_code 조회
async def _get_corp_code(ticker: str, api_key: str) -> Optional[str]:
    if ticker in _CORP_CODE_CACHE:
        return _CORP_CODE_CACHE[ticker]
    try:
        from agents.dart_indexer import _CORP_CODE_MAP, _load_corp_codes

        await _load_corp_codes(api_key)
        code = _CORP_CODE_MAP.get(ticker)
        if code:
            _CORP_CODE_CACHE[ticker] = code
        return code
    except Exception as e:
        logger.debug("corp_code 조회 실패 (%s): %s", ticker, e)
        return None


# 최대주주 현황 조회
async def _fetch_majorstock(corp_code: str, api_key: str) -> list[dict]:
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


# XML 특수관계자 추출
async def _fetch_related_party_from_xml(rcept_no: str, api_key: str) -> list[str]:
    import io, zipfile

    _GROUP_PATTERN = re.compile(
        r"(?:삼성|SK|LG|현대|롯데|한화|포스코|두산|GS|CJ|한진|효성|LS|코오롱|신세계|현대중공업)"
        r"[가-힣A-Za-z0-9&·\s]{1,15}",
        re.UNICODE,
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.get(
                f"{DART_BASE}/document.xml",
                params={"crtfc_key": api_key, "rcept_no": rcept_no},
            )
            res.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
            xml_name = z.namelist()[0]
            content = z.read(xml_name).decode("utf-8", errors="replace")

        # 특수관계자 섹션 추출 (최대 20,000자)
        idx = content.find("특수관계자와의 거래")
        if idx < 0:
            idx = content.find("특수관계자")
        if idx < 0:
            return []
        section = content[idx : idx + 20000]

        names = set()
        for m in _GROUP_PATTERN.finditer(section):
            name = m.group().strip().rstrip()
            if len(name) >= 3 and not re.search(r"^\s*(WIDTH|ACLASS|TBODY|TABLE)", name):
                names.add(name)
        return list(names)

    except Exception as e:
        logger.debug("XML 특수관계자 추출 실패 (%s): %s", rcept_no, e)
        return []


# 주요주주 엣지 생성
def _edges_from_majorstock(ticker: str, rows: list[dict], reverse_reg: dict) -> list[dict]:
    individual_keywords = re.compile(r"대표이사|이사|사장|회장|부회장|임원|상무|전무|부사장|펀드|투자|자산운용")
    edges = []
    seen: set[str] = set()

    for row in rows:
        shareholder_nm = (row.get("repror") or "").strip()
        hold_pct_str = (row.get("stkrt") or row.get("ctr_stkrt") or "0").replace(",", "")

        if individual_keywords.search(shareholder_nm):
            continue

        norm = _normalize_name(shareholder_nm)
        if len(norm) < 2 or norm in seen:
            continue
        seen.add(norm)

        dst_ticker = _resolve_ticker(norm, reverse_reg)
        if dst_ticker is None or dst_ticker == ticker:
            continue

        try:
            hold_pct = float(hold_pct_str)
        except ValueError:
            hold_pct = 0.0

        confidence = min(hold_pct / 100.0, 1.0)
        evidence = f"주요주주 지분 {hold_pct:.2f}% ({shareholder_nm})"

        edges.append({
            "src": dst_ticker,
            "dst": ticker,
            "relation_type": "affiliate",
            "direction": "forward",
            "weight": confidence,
            "confidence": confidence,
            "evidence": evidence,
            "source": "dart",
        })
        edges.append({
            "src": ticker,
            "dst": dst_ticker,
            "relation_type": "affiliate",
            "direction": "reverse",
            "weight": confidence,
            "confidence": confidence,
            "evidence": evidence,
            "source": "dart",
        })

    return edges


# XML 기업명 엣지 생성
def _edges_from_xml_names(ticker: str, names: list[str], reverse_reg: dict) -> list[dict]:
    edges = []
    seen: set[str] = set()

    for name in names:
        dst_ticker = _resolve_ticker(name, reverse_reg)
        if dst_ticker is None or dst_ticker == ticker or dst_ticker in seen:
            continue
        seen.add(dst_ticker)
        edges.append({
            "src": ticker,
            "dst": dst_ticker,
            "relation_type": "affiliate",
            "direction": "forward",
            "weight": 0.85,
            "confidence": 0.85,
            "evidence": f"사업보고서 특수관계자 섹션 명시: {name}",
            "source": "dart",
        })
        edges.append({
            "src": dst_ticker,
            "dst": ticker,
            "relation_type": "affiliate",
            "direction": "reverse",
            "weight": 0.85,
            "confidence": 0.85,
            "evidence": f"사업보고서 특수관계자 섹션 명시: {name}",
            "source": "dart",
        })

    return edges


# DartChunk 엣지 추출
def _edges_from_dart_chunks(ticker: str, reverse_reg: dict) -> list[dict]:
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


# 종목코드 해석
def _resolve_ticker(name: str, reverse_reg: dict[str, str]) -> Optional[str]:
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


# 역방향 레지스트리
def _build_reverse_registry() -> dict[str, str]:
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


# 엣지 저장
def _save_edges(edges: list[dict]) -> int:
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


# 엣지 추출 실행
async def extract_dart_edges(ticker: str) -> int:
    import asyncio

    api_key = os.getenv("DART_API_KEY")
    reverse_reg = _build_reverse_registry()
    if not reverse_reg:
        logger.warning("[%s] 레지스트리 없음 — dart_edge_extractor 건너뜀", ticker)
        return 0

    all_edges: list[dict] = []

    if api_key:
        corp_code = await _get_corp_code(ticker, api_key)
        if corp_code:
            major_rows, disclosures = await asyncio.gather(
                _fetch_majorstock(corp_code, api_key),
                _fetch_latest_annual_rcept(corp_code, api_key),
            )
            all_edges.extend(_edges_from_majorstock(ticker, major_rows, reverse_reg))

            if disclosures:
                rcept_no = disclosures[0].get("rcept_no", "")
                xml_names = await _fetch_related_party_from_xml(rcept_no, api_key)
                all_edges.extend(_edges_from_xml_names(ticker, xml_names, reverse_reg))
                logger.info("[%s] XML 특수관계자 추출: %d개 기업명", ticker, len(xml_names))
        else:
            logger.info("[%s] corp_code 없음 — DART API 엣지 건너뜀", ticker)
    else:
        logger.debug("[%s] DART_API_KEY 없음 — DartChunk 파싱만 실행", ticker)

    all_edges.extend(_edges_from_dart_chunks(ticker, reverse_reg))

    deduped: dict[tuple, dict] = {}
    for edge in all_edges:
        key = (edge["src"], edge["dst"], edge["relation_type"])
        existing = deduped.get(key)
        if existing is None or (edge.get("confidence") or 0) > (existing.get("confidence") or 0):
            deduped[key] = edge

    saved = _save_edges(list(deduped.values()))
    logger.info("[%s] dart_edge_extractor 완료: %d건 저장 (후보 %d건)", ticker, saved, len(deduped))
    return saved


# 최근 사업보고서 조회
async def _fetch_latest_annual_rcept(corp_code: str, api_key: str) -> list[dict]:
    from datetime import datetime, timedelta
    bgn_de = (datetime.today() - timedelta(days=400)).strftime("%Y%m%d")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"{DART_BASE}/list.json",
                params={
                    "crtfc_key": api_key,
                    "corp_code": corp_code,
                    "bgn_de": bgn_de,
                    "pblntf_ty": "A",
                    "page_count": 5,
                },
            )
            res.raise_for_status()
        items = res.json().get("list", [])
        annual = [i for i in items if "사업보고서" in i.get("report_nm", "")]
        return annual[:1]
    except Exception as e:
        logger.debug("사업보고서 목록 조회 실패 (%s): %s", corp_code, e)
        return []


if __name__ == "__main__":
    import asyncio
    import sys

    ticker_arg = sys.argv[1] if len(sys.argv) > 1 else "005930"
    count = asyncio.run(extract_dart_edges(ticker_arg))
    print(f"[{ticker_arg}] company_edges 저장: {count}건")
