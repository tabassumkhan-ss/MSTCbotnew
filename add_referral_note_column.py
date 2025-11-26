import sqlite3
import os

DB = os.getenv("DATABASE_URL", "sqlite:///./data/mstcbot.db")
# normalize to sqlite path if it starts with sqlite:///
if DB.startswith("sqlite:///"):
    db_path = DB.replace("sqlite:///", "")
else:
    db_path = DB

if not os.path.exists(db_path):
    raise SystemExit(f"DB file not found: {db_path}")

con = sqlite3.connect(db_path)
cur = con.cursor()

# Check whether column already exists
cur.execute("PRAGMA table_info(referral_events);")
cols = [r[1] for r in cur.fetchall()]
if "note" in cols:
    print("Column 'note' already exists in referral_events — nothing to do.")
else:
    print("Adding column 'note' to referral_events ...")
    cur.execute("ALTER TABLE referral_events ADD COLUMN note TEXT;")
    con.commit()
    print("Done. Added 'note' column.")

con.close()
