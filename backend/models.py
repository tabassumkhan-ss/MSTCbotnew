import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, ForeignKey, BigInteger, Boolean, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_timeout=30,
    pool_recycle=300,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

# ---------------- USER ----------------
class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)  # telegram id
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    balance_mstc = Column(Float, default=0.0)
    balance_musd = Column(Float, default=0.0)

    role = Column(String, default="user")
    self_activated = Column(Boolean, default=False)
    total_team_business = Column(Float, default=0.0)
    active_origin_count = Column(Integer, default=0)

    referrer_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    referrer = relationship("User", remote_side=[id])

    active = Column(Boolean, default=True)

# ---------------- TRANSACTIONS ----------------
class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))

    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    type = Column(String, nullable=False)
    external_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# ---------------- REFERRAL EVENTS ----------------
class ReferralEvent(Base):
    __tablename__ = "referral_events"

    id = Column(Integer, primary_key=True)
    from_user = Column(BigInteger)
    to_user = Column(BigInteger)
    amount = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    
def init_db():
    # Do NOT auto-create tables on Railway
    return