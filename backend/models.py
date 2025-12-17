import os
import sys
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    ForeignKey, BigInteger, Boolean, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

# allow running this file directly
sys.path.append(os.path.dirname(__file__))

load_dotenv()

# =========================
# Database setup
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# =========================================================
# USER MODEL (Telegram users / MLM participants)
# =========================================================
class User(Base):
    __tablename__ = 'users'

    id = Column(BigInteger, primary_key=True, index=True)  # telegram user id
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)

    username = Column(String, nullable=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    balance_mstc = Column(Float, default=0.0, nullable=False)
    balance_musd = Column(Float, default=0.0, nullable=False)
    wallet_address = Column(String(128), nullable=True)

    role = Column(String, default='member')
    self_activated = Column(Boolean, default=False)
    total_team_business = Column(Float, default=0.0)
    active_origin_count = Column(Integer, default=0)

    referrer_id = Column(BigInteger, ForeignKey('users.id'), nullable=True, index=True)
    referrer = relationship('User', remote_side=[id], backref='referrals')

    active = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<User id={self.id} telegram_id={self.telegram_id} role={self.role}>"

# =========================================================
# ADMIN MODEL (Backoffice login ONLY)
# =========================================================
class Admin(Base):
    __tablename__ = 'admins'

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<Admin username={self.username}>"

# =========================================================
# TRANSACTIONS
# =========================================================
class Transaction(Base):
    __tablename__ = 'transactions'

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(BigInteger, ForeignKey('users.id'), nullable=False, index=True)

    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)   # MUSD, MSTC
    type = Column(String, nullable=False)       # deposit, credit_mstc, referral

    external_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship('User', backref='transactions')

    __table_args__ = (
        Index('ix_transactions_user_created', 'user_id', 'created_at'),
    )

    def __repr__(self):
        return f"<Transaction id={self.id} user={self.user_id} {self.amount} {self.currency}>"

# =========================================================
# REFERRAL EVENTS (Accounting / audit)
# =========================================================
class ReferralEvent(Base):
    __tablename__ = "referral_events"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 👇 plain integers (MATCH YOUR DB)
    from_user = Column(Integer, nullable=False, index=True)
    to_user   = Column(Integer, nullable=True, index=True)

    amount = Column(Float, nullable=False)
    note = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return (
            f"<ReferralEvent from_user={self.from_user} "
            f"to_user={self.to_user} amount={self.amount}>"
        )

# =========================================================
# DB INIT
# =========================================================
def init_db():
    Base.metadata.create_all(bind=engine)

if __name__ == '__main__':
    init_db()
    print("DB initialized:", DATABASE_URL)
