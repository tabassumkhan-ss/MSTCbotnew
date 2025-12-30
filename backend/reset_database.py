# reset_database.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import User, Transaction, ReferralEvent

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

db = SessionLocal()

db.query(Transaction).delete()
db.query(ReferralEvent).delete()
db.query(User).delete()

db.commit()
db.close()

print("DB reset complete")
# reset_database.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import User, Transaction, ReferralEvent

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

db = SessionLocal()

db.query(Transaction).delete()
db.query(ReferralEvent).delete()
db.query(User).delete()

db.commit()
db.close()

print("DB reset complete")
