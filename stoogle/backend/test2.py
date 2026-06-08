from models.db_models import CompanyEdge, RelationCache, SessionLocal
db = SessionLocal()
print("company_edges 총:", db.query(CompanyEdge).count())
print("003550 엣지:")
for e in db.query(CompanyEdge).filter(CompanyEdge.src == "003550").all():
    print(" ", e.dst, e.relation_type, e.source)
print("003550 RelationCache:")
for r in db.query(RelationCache).filter(RelationCache.ticker == "003550").all():
    print(" ", r.related_ticker, r.relation_type, r.source, r.correlation)
db.close()