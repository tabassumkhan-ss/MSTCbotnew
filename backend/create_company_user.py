import os
from dotenv import load_dotenv
load_dotenv()

if "DATABASE_URL" not in os.environ or not os.environ["DATABASE_URL"]:
    os.environ["DATABASE_URL"] = "postgresql://postgres:WVYXdfCNmQEsicJJPkZHIxwdzhDEPYcx@maglev.proxy.rlwy.net:39087/railway"

from backend.models import SessionLocal, User

db = SessionLocal()

COMPANY_ID = 1000000000001

existing = db.query(User).filter(User.telegram_id == COMPANY_ID).first()
if existing:
    print("Company user already exists:", existing)
else:
    u = User(
        id=COMPANY_ID,
        telegram_id=COMPANY_ID,
        first_name="company",
        role="company",
        self_activated=True,
        total_team_business=0.0
    )
    db.add(u)
    db.commit()
    print("Company user created successfully!")

db.close()
