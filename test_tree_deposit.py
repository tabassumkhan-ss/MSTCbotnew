import json
import time
import urllib.request
from backend.models import SessionLocal, User

API = "http://127.0.0.1:8001/webapp/verify"
HEADERS = {"Content-Type": "application/json"}

def ensure_users():
    db = SessionLocal()
    try:
        alice = db.query(User).get(234)
        if not alice:
            alice = User(id=234, username="alice", first_name="Alice")
            db.add(alice)
        bob = db.query(User).get(345)
        if not bob:
            bob = User(id=345, username="bob", first_name="Bob")
            db.add(bob)
        depositor = db.query(User).get(123456)
        if not depositor:
            depositor = User(id=123456, username="test", first_name="Test")
            db.add(depositor)
        db.commit()

        depositor.referrer_id = 345
        bob.referrer_id = 234
        db.add_all([depositor, bob])
        db.commit()
        print("Referrer chain set: 123456 -> 345 -> 234")
    finally:
        db.close()

def call_deposit(amount=20):
    payload = {
        "initData": {"user": {"id": 123456, "username": "test", "first_name": "Test"}},
        "amount": amount
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API, data=data, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            b = r.read()
            print("HTTP", r.getcode())
            try:
                print(json.dumps(json.loads(b.decode("utf-8")), indent=2))
            except Exception:
                print("Non-JSON response:", b.decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print("HTTPError", e.code)
        print(body)
    except Exception as e:
        print("Request failed:", e)

def inspect_db():
    db = SessionLocal()
    try:
        print("\n=== Users ===")
        for u in db.query(User).order_by(User.id).filter(User.id.in_([123456,345,234])):
            print(u.id, u.username, "referrer_id=", u.referrer_id, "musd=", u.balance_musd, "mstc=", u.balance_mstc)
        from backend.models import Transaction, ReferralEvent
        print("\n=== Recent Transactions ===")
        for t in db.query(Transaction).order_by(Transaction.created_at.desc()).limit(10):
            print(t.id, t.user_id, t.amount, t.currency, t.type, t.created_at)
        print("\n=== Recent ReferralEvents ===")
        for r in db.query(ReferralEvent).order_by(ReferralEvent.created_at.desc()).limit(10):
            print(r.id, r.from_user, r.to_user, r.amount, r.note, r.created_at)
    finally:
        db.close()

if __name__ == "__main__":
    ensure_users()
    time.sleep(0.2)
    call_deposit(20)
    time.sleep(0.2)
    inspect_db()
