from backend.models import SessionLocal, User, Transaction, ReferralEvent

db = SessionLocal()

db.query(Transaction).delete()
db.query(ReferralEvent).delete()
db.query(User).delete()

db.commit()
db.close()

print("Database reset complete")
