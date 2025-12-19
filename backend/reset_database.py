import os
from dotenv import load_dotenv

load_dotenv()

if "DATABASE_URL" not in os.environ or not os.environ["DATABASE_URL"]:
    os.environ["DATABASE_URL"] = "postgresql://postgres:WVYXdfCNmQEsicJJPkZHIxwdzhDEPYcx@maglev.proxy.rlwy.net:39087/railway"

from backend.models import SessionLocal, User, Transaction, ReferralEvent

db = SessionLocal()

# 1️⃣ delete all transactions first
db.query(Transaction).delete(synchronize_session=False)

# 2️⃣ delete referral event history
db.query(ReferralEvent).delete(synchronize_session=False)

# 3️⃣ now delete all users EXCEPT Tabassum and company
db.query(User).filter(~User.first_name.in_(["Tabassum","company"])).delete(synchronize_session=False)

# 4️⃣ reset Tabassum + company user accounts
db.query(User).filter(User.first_name.in_(["Tabassum","company"])).update(
    {
        User.total_team_business: 0.0,
        User.self_activated: False,
        User.active_origin_count: 0,
        User.balance_musd: 0.0,
        User.balance_mstc: 0.0,
        User.active: True,
        User.role: "origin"
    },
    synchronize_session=False
)

# 5️⃣ promote Tabassum to admin
db.query(User).filter(User.first_name == "Tabassum").update(
    {User.role: "admin"},
    synchronize_session=False
)

db.commit()
db.close()

print("\n=====================================")
print("DATABASE RESET COMPLETED SUCCESSFULLY")
print("=====================================")
