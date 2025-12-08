from sqlalchemy import text
from backend.models import engine

def main():
    print("=== CONNECTING TO DB ===")
    print("DB URL:", engine.url)

    with engine.connect() as conn:
        print("Adding wallet_address...")
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_address VARCHAR(255);"))

        print("Adding club_income...")
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS club_income NUMERIC(18, 2) DEFAULT 0;"))

        print("Adding active_origin_count...")
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS active_origin_count INTEGER DEFAULT 0;"))

        conn.commit()

    print("=== DONE ===")

if __name__ == "__main__":
    main()
