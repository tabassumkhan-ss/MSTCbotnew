import os
import sys
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
from dotenv import load_dotenv

# allow running this file directly
sys.path.append(os.path.dirname(__file__))

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./data/betzybot.db')

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)  # telegram user id
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    balance_mstc = Column(Float, default=0.0)
    balance_musd = Column(Float, default=0.0)
    active = Column(Boolean, default=True)
    referrer_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # referral-related fields
    role = Column(String, default='user')  # 'user', 'origin', 'life_changer'
    self_activated = Column(Boolean, default=False)
    total_team_business = Column(Float, default=0.0)  # in USD
    active_origin_count = Column(Integer, default=0)
    club_income = Column(Float, default=0.0)

    referrals = relationship('User', remote_side=[id])

class Transaction(Base):
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    amount = Column(Float)
    currency = Column(String)
    type = Column(String)  # activation, credit, referral, mstc, etc.
    created_at = Column(DateTime, default=datetime.utcnow)

class ReferralEvent(Base):
    __tablename__ = 'referral_events'
    id = Column(Integer, primary_key=True)
    from_user = Column(Integer, ForeignKey('users.id'))
    to_user = Column(Integer, ForeignKey('users.id'))
    amount = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)

if __name__ == '__main__':
    init_db()
    print('DB initialized')