from backend.models import SessionLocal, Transaction, ReferralEvent, User

db = SessionLocal()
print("Users:")
for u in db.query(User).order_by(User.id).limit(20):
    print(u.id, u.username, u.referrer_id, u.balance_musd, u.balance_mstc)
print("\nTransactions (last 20):")
for t in db.query(Transaction).order_by(Transaction.created_at.desc()).limit(20):
    print(t.id, t.user_id, t.amount, t.currency, t.type, t.created_at, t.external_id)
print("\nReferralEvents (last 20):")
for r in db.query(ReferralEvent).order_by(ReferralEvent.created_at.desc()).limit(20):
    print(r.id, r.from_user, r.to_user, r.amount, r.note, r.created_at)
db.close()
