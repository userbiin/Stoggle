# DB 마이그레이션
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from models.db_models import engine, DATABASE_URL


def migrate():
    is_sqlite = DATABASE_URL.startswith("sqlite")

    new_columns = [
        # (table, column, col_type_sql)
        # abnormal_return은 이미 존재하지만 magnitude_hit(0.0/1.0) 용도로 재사용 중
        # direction_correct 컬럼을 추가해 방향 일치 여부를 명시적으로 저장
        ("prediction_log", "direction_correct", "BOOLEAN"),
    ]

    with engine.connect() as conn:
        for table, col, col_type in new_columns:
            try:
                if is_sqlite:
                    result = conn.execute(text(f"PRAGMA table_info({table})"))
                    existing = {row[1] for row in result.fetchall()}
                    if col not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                        print(f"  추가: {table}.{col} ({col_type})")
                    else:
                        print(f"  이미 존재: {table}.{col}")
                else:
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}")
                    )
                    print(f"  추가(IF NOT EXISTS): {table}.{col} ({col_type})")
            except Exception as e:
                print(f"  오류 (무시): {table}.{col} — {e}")

        conn.commit()
    print("마이그레이션 완료")


if __name__ == "__main__":
    migrate()