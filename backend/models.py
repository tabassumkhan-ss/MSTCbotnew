# backend/models.py
import os
from datetime import datetime
from dotenv import load_dotenv

from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, ForeignKey, BigInteger, Boolean, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)

    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    balance_musd = Column(Float, default=0.0)
    balance_mstc = Column(Float, default=0.0)

    role = Column(String, default="user")
    self_activated = Column(Boolean, default=False)

    total_team_business = Column(Float, default=0.0)
    active_origin_count = Column(Integer, default=0)

    referrer_id = Column(BigInteger, ForeignKey("users.id"))
    referrer = relationship("User", remote_side=[id])

    active = Column(Boolean, default=True)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True)

    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    type = Column(String, nullable=False)
    external_id = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_tx_user_created", "user_id", "created_at"),
    )


class ReferralEvent(Base):
    __tablename__ = "referral_events"

    id = Column(Integer, primary_key=True)
    from_user = Column(BigInteger, index=True)
    to_user = Column(BigInteger, index=True)
    amount = Column(Float)
    note = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
