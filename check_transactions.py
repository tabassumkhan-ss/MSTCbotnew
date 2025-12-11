# check_transactions.py
from sqlalchemy import text
from backend.models import SessionLocal

def main():
    s = SessionLocal()
    try:
        print("transactions for TX100 or user ids 7955075358,8506522238:")
        rows = s.execute(text(
            "SELECT id,user_id,amount,currency,type,external_id,created_at "
            "FROM transactions WHERE external_id='TX100' OR user_id IN (7955075358,8506522238) "
            "ORDER BY created_at DESC LIMIT 50"
        )).fetchall()
        print(rows)
    finally:
        s.close()

if __name__ == '__main__':
    main()
