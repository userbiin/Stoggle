# 시드 엣지

SEED_EDGES = [
    # SK하이닉스(000660) — HBM·메모리 공급망
    ("000660", "042700", "supplier", "한미반도체가 SK하이닉스에 HBM 본더 공급 (TCB 장비 단독 공급사)", 0.92),
    ("000660", "357780", "supplier", "솔브레인이 SK하이닉스에 식각액·반도체 소재 공급", 0.88),
    ("000660", "240810", "supplier", "원익IPS가 SK하이닉스에 증착·식각 장비 공급", 0.85),
    ("000660", "036830", "supplier", "솔브레인홀딩스 계열 — SK하이닉스 소재 공급망", 0.75),

    # 삼성전자(005930) — 반도체·디스플레이 공급망
    ("005930", "042700", "supplier", "한미반도체가 삼성전자 반도체 패키징 장비 공급", 0.85),
    ("005930", "357780", "supplier", "솔브레인이 삼성전자에 반도체 소재 공급", 0.82),
    ("005930", "009150", "affiliate", "삼성전기 — 삼성전자 계열사, MLCC·카메라모듈 공급", 0.95),
    ("005930", "028260", "affiliate", "삼성물산 — 삼성전자 최대주주(지분 5.01%)", 0.90),

    # LG전자(066570) — 디스플레이·부품 공급망
    ("066570", "034220", "supplier", "LG디스플레이가 LG전자에 OLED 패널 공급", 0.93),
    ("066570", "051900", "affiliate", "LG생활건강 — LG 계열사, 그룹 내 사업 연계", 0.80),
    ("066570", "003550", "affiliate", "LG — LG전자 지배주주(지분 33.67%)", 0.95),

    # 현대차(005380) — 전기차·부품 공급망
    ("005380", "000270", "affiliate", "기아 — 현대차그룹 계열사, 플랫폼 공유", 0.95),
    ("005380", "012330", "affiliate", "현대모비스 — 현대차 핵심 부품 계열사", 0.94),
    ("005380", "005387", "affiliate", "현대차우 — 동일 법인 우선주", 0.98),
]

def insert_seed_edges():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from models.db_models import CompanyEdge, SessionLocal

    db = SessionLocal()
    inserted = 0
    skipped = 0
    try:
        for src, dst, rel_type, evidence, confidence in SEED_EDGES:
            existing = (
                db.query(CompanyEdge)
                .filter(
                    CompanyEdge.src == src,
                    CompanyEdge.dst == dst,
                    CompanyEdge.relation_type == rel_type,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue
            db.add(CompanyEdge(
                src=src, dst=dst,
                relation_type=rel_type,
                direction="forward",
                weight=confidence,
                confidence=confidence,
                evidence=evidence,
                source="dart",
            ))
            inserted += 1
        db.commit()
        print(f"INSERT {inserted}건, SKIP {skipped}건")

        try:
            from services.cache_service import _get_client
            c = _get_client()
            if c:
                tickers = set(e[0] for e in SEED_EDGES) | set(e[1] for e in SEED_EDGES)
                for t in tickers:
                    c.delete(f"Stoogle:edges:{t}")
                print(f"Redis edges 캐시 무효화: {len(tickers)}개 종목")
        except Exception as e:
            print(f"캐시 무효화 실패(무시): {e}")
    finally:
        db.close()

if __name__ == "__main__":
    insert_seed_edges()
