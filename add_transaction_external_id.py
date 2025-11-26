import sqlite3
import os

DB = os.getenv("DATABASE_URL", "sqlite:///./data/mstcbot.db")
if DB.startswith("sqlite:///"):
    db_path = DB.replace("sqlite:///", "")
else:
    db_path = DB

if not os.path.exists(db_path):
    raise SystemExit(f"DB file not found: {db_path}")

con = sqlite3.connect(db_path)
cur = con.cursor()

# Check whether column already exists
cur.execute("PRAGMA table_info(transactions);")
cols = [r[1] for r in cur.fetchall()]
if "external_id" in cols:
    print("Column 'external_id' already exists in transactions — nothing to do.")
else:
    print("Adding column 'external_id' to transactions ...")
    cur.execute("ALTER TABLE transactions ADD COLUMN external_id TEXT;")
    con.commit()
    print("Done. Added 'external_id' column.")

con.close()
