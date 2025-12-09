# list_users.py
from backend.models import SessionLocal, User

session = SessionLocal()

users = session.query(User).order_by(User.created_at.asc()).all()

print("\n=== User List ===\n")
for u in users:
    print(f"ID: {u.id}, Name: {u.first_name}, Username: {u.username}, Referrer: {u.referrer_id}, Created: {u.created_at}")

print("\nTotal users:", len(users))
