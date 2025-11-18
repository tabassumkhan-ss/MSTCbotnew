import sqlite3 
con = sqlite3.connect("data/mstcbot.db") 
cur = con.cursor() 
cur.execute("SELECT id, username, role, self_activated, total_team_business, active_origin_count, balance_musd FROM users WHERE id=2001;") 
print(cur.fetchone()) 
con.close() 
