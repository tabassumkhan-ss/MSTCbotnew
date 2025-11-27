import os
import sys
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    ForeignKey, BigInteger, Boolean, UniqueConstraint, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from dotenv import load_dotenv

# allow running this file directly
sys.path.append(os.path.dirname(__file__))

load_dotenv()
DATABASE_URL = 'sqlite:///./data/mstcbotv2.db'

# For SQLite, ensure check_same_thread when using in multiple threads
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    id = Column(BigInteger, primary_key=True, index=True)  # telegram user id
    username = Column(String, nullable=True, index=True)
    first_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    balance_mstc = Column(Float, default=0.0, nullable=False)  # MSTC token balance
    balance_musd = Column(Float, default=0.0, nullable=False)  # MUSD balance
    active = Column(Boolean, default=True, nullable=False)

    # referral linkage
    referrer_id = Column(BigInteger, ForeignKey('users.id'), nullable=True, index=True)
    referrals = relationship('User', remote_side=[id], backref='referrer', lazy='joined')

    # referral-related fields
    role = Column(String, default='user')  # 'user', 'origin', 'life_changer'
    self_activated = Column(Boolean, default=False)
    total_team_business = Column(Float, default=0.0)  # in USD
    active_origin_count = Column(Integer, default=0)
    club_income = Column(Float, default=0.0)

    def __repr__(self):
        return f"<User id={self.id} username={self.username} role={self.role}>"


class Transaction(Base):
    """
    Generic transaction table. For deposits we will record:
      - one row for MUSD deposit (currency='MUSD', type='deposit')
      - one row for MSTC credit (currency='MSTC', type='credit_mstc')
    external_id can be used to ensure idempotency for external webhook/payment providers.
    """
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)  
    user_id = Column(BigInteger, ForeignKey('users.id'), index=True, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)  # 'MUSD' or 'MSTC' etc.
    type = Column(String, nullable=False)  # 'deposit', 'credit_mstc', 'referral', etc.
    external_id = Column(String, nullable=True)  # optional external identifier for idempotency
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # relationship
    user = relationship('User', backref='transactions', lazy='joined')

    __table_args__ = (
        # optional convenience index: user + created_at
        Index('ix_transactions_user_created', 'user_id', 'created_at'),
    )

    def __repr__(self):
        return f"<Transaction id={self.id} user_id={self.user_id} amount={self.amount} {self.currency}>"


class ReferralEvent(Base):
    """
    Records referral payouts and internal accounting.
    - from_user: the user who generated the referral (depositor)
    - to_user: the recipient user id (nullable for company_pool)
    """
    __tablename__ = 'referral_events'
    id = Column(BigInteger, primary_key=True)
    from_user = Column(BigInteger, ForeignKey('users.id'), index=True, nullable=False)
    to_user = Column(BigInteger, ForeignKey('users.id'), index=True, nullable=True)
    amount = Column(Float, nullable=False)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # relationships (optional)
    sender = relationship('User', foreign_keys=[from_user], lazy='joined', backref='referral_sent')
    recipient = relationship('User', foreign_keys=[to_user], lazy='joined', backref='referral_received')

    def __repr__(self):
        return f"<ReferralEvent id={self.id} from={self.from_user} to={self.to_user} amount={self.amount}>"


def init_db():
    """
    Create all tables. Call this from a bootstrap script or run `python backend/models.py`.
    """
    # ensure directory for sqlite DB exists
    if DATABASE_URL.startswith("sqlite:///"):
        # path is like sqlite:///./data/mstcbot.db
        path = DATABASE_URL.replace("sqlite:///", "")
        dirpath = os.path.dirname(os.path.abspath(path))
        if dirpath and not os.path.exists(dirpath):
            os.makedirs(dirpath, exist_ok=True)

    Base.metadata.create_all(bind=engine)


if __name__ == '__main__':
    init_db()
    print('DB initialized at', DATABASE_URL)
