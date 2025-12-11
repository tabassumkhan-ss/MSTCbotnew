# create_user_8506522238.py
from backend.models import SessionLocal
from sqlalchemy import text
from datetime import datetime, timezone

TG_ID = 8506522238

def main():
    s = SessionLocal()
    try:
        r = s.execute(text("SELECT id FROM users WHERE telegram_id = :tg"), {"tg": TG_ID}).fetchone()
        if r:
            print("User already exists with telegram_id =", TG_ID)
            return
        s.execute(text("""
            INSERT INTO users (
                id, telegram_id, username, first_name, created_at,
                balance_mstc, balance_musd, active, role
            ) VALUES (
                :id, :tg, :uname, :fname, :now,
                0.0, 0.0, TRUE, 'user'
            )
        """), {
            "id": TG_ID,
            "tg": TG_ID,
            "uname": f"testuser_{TG_ID}",
            "fname": "Test",
            "now": datetime.now(timezone.utc)
        })
        s.commit()
        print("Inserted user with telegram_id =", TG_ID)
    except Exception as e:
        s.rollback()
        print("Insert failed:", repr(e))
    finally:
        s.close()

if __name__ == "__main__":
    main()
