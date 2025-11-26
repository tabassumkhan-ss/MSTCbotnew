CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at DATETIME,
    balance_mstc FLOAT DEFAULT 0,
    balance_musd FLOAT DEFAULT 0,
    active BOOLEAN DEFAULT 1,
    referrer_id INTEGER,
    role TEXT DEFAULT 'user',
    self_activated BOOLEAN DEFAULT 0,
    total_team_business FLOAT DEFAULT 0,
    active_origin_count INTEGER DEFAULT 0,
    club_income FLOAT DEFAULT 0
);

CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    amount FLOAT,
    currency TEXT,
    type TEXT,
    created_at DATETIME
);

CREATE TABLE referral_events (
    id INTEGER PRIMARY KEY,
    from_user INTEGER,
    to_user INTEGER,
    amount FLOAT,
    created_at DATETIME
);
