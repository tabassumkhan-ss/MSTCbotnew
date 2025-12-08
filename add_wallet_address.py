from backend.models import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE users ADD COLUMN wallet_address VARCHAR(255);"))
    conn.commit()

print("wallet_address column added.")
