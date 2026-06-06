# scripts/diagnose_news_pool.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models.db_models import SessionLocal, NewsCache
from sqlalchemy import func

db = SessionLocal()
rows = (db.query(NewsCache.ticker, func.count())
          .filter(NewsCache.published_at >= "2026-05-20T00:00:00")
          .filter(NewsCache.published_at <  "2026-06-03T00:00:00")
          .group_by(NewsCache.ticker)
          .order_by(func.count().desc())
          .all())
for t, c in rows:
    print(f"  {t}: {c}건")
db.close()