"""
OpenDART 공시·재무제표 수집 → 섹션 청크 분할 → pgvector 색인

입력 : 종목코드 (string)
출력 : 색인된 청크 수 (int)

데이터 소스
  - corpCode.xml   : stock_code → corp_code 매핑 (최초 1회 다운로드 후 캐시)
  - list.json      : 최근 정기공시 목록 (사업/반기/분기 보고서)
  - fnlttSinglAcnt.json : 재무제표 (재무상태표·손익계산서·현금흐름표)

청크 분할
  - 섹션(재무제표 종류) 단위로 줄 병합
  - tiktoken cl100k_base 기준 최대 400 토큰
  - 하나의 줄이 400 토큰 초과 시 강제 분할
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import zipfile
from datetime import datetime, timedelta
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"
EMBED_MODEL = "text-embedding-3-small"
EMBED_BATCH_SIZE = 100
MAX_TOKENS = 400

# 정기공시 보고서 코드
_REPRT_CODES = {
    "11011": "사업보고서",
    "11012": "반기보고서",
    "11013": "1분기보고서",
    "11014": "3분기보고서",
}

# ---------------------------------------------------------------------------
# corp_code 캐시 (프로세스 내 1회 로드)
# ---------------------------------------------------------------------------

_CORP_CODE_MAP: dict[str, str] = {}   # stock_code → corp_code
_CORP_CODE_LOCK: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _CORP_CODE_LOCK
    if _CORP_CODE_LOCK is None:
        _CORP_CODE_LOCK = asyncio.Lock()
    return _CORP_CODE_LOCK


async def _load_corp_codes(api_key: str) -> None:
    """DART corpCode.xml 다운로드 후 stock_code → corp_code 매핑 캐시."""
    if _CORP_CODE_MAP:
        return

    async with _get_lock():
        if _CORP_CODE_MAP:   # double-check after lock
            return

        url = f"{DART_BASE}/corpCode.xml"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                res = await client.get(url, params={"crtfc_key": api_key})
                res.raise_for_status()
        except Exception as e:
            logger.error("corpCode.xml 다운로드 실패: %s", e)
            return

        try:
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                xml_name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
                with z.open(xml_name) as f:
                    root = ET.parse(f).getroot()

            for item in root.findall("list"):
                stock = (item.findtext("stock_code") or "").strip()
                corp = (item.findtext("corp_code") or "").strip()
                if stock and corp:
                    _CORP_CODE_MAP[stock] = corp

            logger.info("corp_code 캐시 로드: %d개 종목", len(_CORP_CODE_MAP))
        except Exception as e:
            logger.error("corpCode.xml 파싱 실패: %s", e)


async def _get_corp_code(ticker: str, api_key: str) -> Optional[str]:
    await _load_corp_codes(api_key)
    code = _CORP_CODE_MAP.get(ticker)
    if not code:
        logger.warning("[%s] corp_code 없음", ticker)
    return code


# ---------------------------------------------------------------------------
# DART API 호출
# ---------------------------------------------------------------------------

async def _fetch_disclosure_list(corp_code: str, api_key: str) -> list[dict]:
    """최근 1년 정기공시 목록 조회."""
    bgn_de = (datetime.today() - timedelta(days=365)).strftime("%Y%m%d")
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "pblntf_ty": "A",    # 정기공시
        "page_count": 20,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(f"{DART_BASE}/list.json", params=params)
            res.raise_for_status()
        data = res.json()
        if data.get("status") != "000":
            logger.warning("공시 목록 조회 실패: %s", data.get("message"))
            return []
        return data.get("list", [])
    except Exception as e:
        logger.error("공시 목록 API 오류: %s", e)
        return []


async def _fetch_financial_statement(
    corp_code: str,
    bsns_year: str,
    reprt_code: str,
    api_key: str,
) -> list[dict]:
    """재무제표 단일회사 조회 (개별·연결 모두 시도)."""
    for fs_div in ("CFS", "OFS"):   # 연결 우선, 개별 fallback
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.get(f"{DART_BASE}/fnlttSinglAcnt.json", params=params)
                res.raise_for_status()
            data = res.json()
            if data.get("status") == "000" and data.get("list"):
                return data["list"]
        except Exception as e:
            logger.debug("재무제표 조회 오류 [%s %s %s]: %s", bsns_year, reprt_code, fs_div, e)
    return []


# ---------------------------------------------------------------------------
# 섹션 텍스트 구성
# ---------------------------------------------------------------------------

def _format_financial_section(items: list[dict], report_label: str) -> list[tuple[str, str]]:
    """
    재무제표 JSON 항목들 → (section_title, content) 목록.
    sj_nm(재무제표 종류)별로 그룹핑.
    """
    groups: dict[str, list[str]] = {}
    term_name = ""

    for item in items:
        sj_nm = item.get("sj_nm", "기타")
        account_nm = item.get("account_nm", "").strip()
        thstrm_amount = item.get("thstrm_amount", "").strip()
        frmtrm_amount = item.get("frmtrm_amount", "").strip()
        if not term_name:
            term_name = item.get("thstrm_nm", "")

        if not account_nm:
            continue

        line = f"  {account_nm}: 당기 {thstrm_amount}원 / 전기 {frmtrm_amount}원"
        groups.setdefault(sj_nm, []).append(line)

    sections = []
    for sj_nm, lines in groups.items():
        title = f"[{sj_nm}] {report_label} ({term_name})"
        content = f"{title}\n" + "\n".join(lines)
        sections.append((title, content))

    return sections


def _format_disclosure_section(disclosures: list[dict]) -> tuple[str, str]:
    """공시 목록 → 단일 메타 섹션."""
    title = "[공시 목록] 최근 1년 정기공시"
    lines = [title]
    for d in disclosures:
        lines.append(
            f"  {d.get('rcept_dt', '')} {d.get('report_nm', '')} "
            f"(접수번호: {d.get('rcept_no', '')})"
        )
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# 청크 분할 (tiktoken)
# ---------------------------------------------------------------------------

def _chunk_section(content: str, max_tokens: int = MAX_TOKENS) -> list[tuple[str, int]]:
    """
    섹션 텍스트를 줄 단위로 병합해 max_tokens 이하 청크 생성.
    Returns: List of (chunk_text, token_count)
    """
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")

    lines = [l for l in content.split("\n") if l.strip()]
    chunks: list[tuple[str, int]] = []
    buf: list[str] = []
    buf_tokens = 0

    for line in lines:
        line_tok = len(enc.encode(line))

        # 단일 줄이 max_tokens 초과 → 강제 분할
        if line_tok > max_tokens:
            if buf:
                chunks.append(("\n".join(buf), buf_tokens))
                buf, buf_tokens = [], 0
            tokens = enc.encode(line)
            for i in range(0, len(tokens), max_tokens):
                part = tokens[i : i + max_tokens]
                chunks.append((enc.decode(part), len(part)))
            continue

        if buf_tokens + line_tok > max_tokens and buf:
            chunks.append(("\n".join(buf), buf_tokens))
            buf, buf_tokens = [], 0

        buf.append(line)
        buf_tokens += line_tok

    if buf:
        chunks.append(("\n".join(buf), buf_tokens))

    return chunks


# ---------------------------------------------------------------------------
# 임베딩
# ---------------------------------------------------------------------------

async def _embed_batch(texts: list[str]) -> list[list[float]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)

    embeddings: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        response = await client.embeddings.create(model=EMBED_MODEL, input=batch)
        embeddings.extend(item.embedding for item in response.data)
    return embeddings


# ---------------------------------------------------------------------------
# 수집 기간 결정
# ---------------------------------------------------------------------------

def _target_periods() -> list[tuple[str, str]]:
    """
    현재 날짜 기준으로 수집할 (bsns_year, reprt_code) 목록 반환.
    최근 2개 연도 연간 + 현 연도 분기 포함.
    """
    now = datetime.today()
    year, month = now.year, now.month

    periods = [
        (str(year - 1), "11011"),   # 전년도 사업보고서
        (str(year - 2), "11011"),   # 전전년도 사업보고서
    ]
    if month >= 5:
        periods.append((str(year), "11013"))   # 1분기
    if month >= 8:
        periods.append((str(year), "11012"))   # 반기
    if month >= 11:
        periods.append((str(year), "11014"))   # 3분기

    return periods


# ---------------------------------------------------------------------------
# 퍼블릭 API
# ---------------------------------------------------------------------------

async def run(ticker: str) -> int:
    """
    종목코드의 공시·재무제표를 수집해 dart_chunks 테이블에 색인.

    Returns:
        색인된 청크 수 (이미 색인된 항목 제외)
    """
    api_key = os.getenv("DART_API_KEY")
    if not api_key:
        raise RuntimeError("DART_API_KEY가 설정되지 않았습니다.")

    from models.db_models import SessionLocal, DartChunk, PGVECTOR_AVAILABLE

    if not PGVECTOR_AVAILABLE or DartChunk is None:
        logger.error("pgvector 미지원 환경 — dart_indexer 실행 불가")
        return 0

    # 1. corp_code 조회
    corp_code = await _get_corp_code(ticker, api_key)
    if not corp_code:
        return 0

    db = SessionLocal()
    try:
        # 이미 색인된 접수번호 조회
        indexed_rcept = {
            row[0]
            for row in db.query(DartChunk.rcept_no).filter(DartChunk.ticker == ticker).all()
        }

        # 2. 공시 목록 조회
        disclosures = await _fetch_disclosure_list(corp_code, api_key)

        # 수집할 섹션 목록: (rcept_no, section_title, content)
        raw_sections: list[tuple[str, str, str]] = []

        # 공시 목록 메타 섹션 (항상 갱신)
        if disclosures:
            disc_title, disc_content = _format_disclosure_section(disclosures)
            raw_sections.append(("META", disc_title, disc_content))

        # 3. 재무제표 수집 (기간별)
        for bsns_year, reprt_code in _target_periods():
            report_label = f"{bsns_year}년 {_REPRT_CODES.get(reprt_code, reprt_code)}"
            # rcept_no는 보고서마다 다르지만 재무제표 조회는 year+code로 함
            pseudo_rcept = f"{bsns_year}_{reprt_code}"
            if pseudo_rcept in indexed_rcept:
                logger.debug("[%s] 이미 색인됨: %s", ticker, report_label)
                continue

            items = await _fetch_financial_statement(corp_code, bsns_year, reprt_code, api_key)
            if not items:
                continue

            for title, content in _format_financial_section(items, report_label):
                raw_sections.append((pseudo_rcept, title, content))

        if not raw_sections:
            logger.info("[%s] 새로 색인할 데이터 없음", ticker)
            return 0

        # 4. 청크 분할
        flat_chunks: list[tuple[str, str, str, int]] = []   # (rcept_no, title, text, tokens)
        for rcept_no, section_title, content in raw_sections:
            for idx, (chunk_text, token_count) in enumerate(_chunk_section(content)):
                chunk_title = f"{section_title}[{idx}]" if idx > 0 else section_title
                flat_chunks.append((rcept_no, chunk_title, chunk_text, token_count))

        # 5. 임베딩
        try:
            embeddings = await _embed_batch([c[2] for c in flat_chunks])
        except Exception as e:
            logger.error("[%s] 임베딩 실패: %s", ticker, e)
            return 0

        # 6. DB 저장
        rows = [
            DartChunk(
                ticker=ticker,
                corp_code=corp_code,
                rcept_no=rcept_no,
                report_nm=title.split("]")[0].lstrip("[") if "]" in title else title,
                section_title=title,
                chunk_index=i,
                content=text,
                token_count=tokens,
                embedding=emb,
            )
            for i, ((rcept_no, title, text, tokens), emb) in enumerate(zip(flat_chunks, embeddings))
        ]

        db.bulk_save_objects(rows)
        db.commit()
        logger.info("[%s] dart_chunks 저장 완료: %d건", ticker, len(rows))
        return len(rows)

    except Exception as e:
        db.rollback()
        logger.error("[%s] dart_indexer 실패: %s", ticker, e)
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 직접 실행 (테스트)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "005930"
    count = asyncio.run(run(ticker))
    print(f"[{ticker}] 색인 완료: {count}건")
