import os
import sys
from sqlalchemy import func
# ensure models is importable when running backend/app.py directly
sys.path.append(os.path.dirname(__file__))
from models import SessionLocal, User, Transaction

def get_children(db, user_id):
    """Return direct children (users who have referrer_id == user_id)."""
    return db.query(User).filter(User.referrer_id == user_id).all()

def get_descendants(db, user_id):
    """Return a list of user ids that are descendants of user_id (BFS)."""
    descendants = []
    queue = [user_id]
    seen = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        children = get_children(db, current)
        for c in children:
            if c.id not in seen:
                descendants.append(c.id)
                queue.append(c.id)
    return descendants

def recompute_total_team_business(db, user_id):
    """Recompute and persist total_team_business for user_id by summing 'activation' transactions of all descendants."""
    descendant_ids = get_descendants(db, user_id)
    if not descendant_ids:
        total = 0.0
    else:
        total = db.query(func.coalesce(func.sum(Transaction.amount), 0.0)).filter(
            Transaction.user_id.in_(descendant_ids), Transaction.type == 'activation'
        ).scalar() or 0.0
    u = db.query(User).get(user_id)
    if u:
        u.total_team_business = float(total)
        db.add(u)
        db.commit()
    return float(total)

def recompute_all_users_team_business(db):
    users = db.query(User.id).all()
    results = {}
    for (uid,) in users:
        results[uid] = recompute_total_team_business(db, uid)
    return results
