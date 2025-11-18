import sqlite3 
con=sqlite3.connect("data/betzybot.db");cur=con.cursor();cur.execute("SELECT id, username, referrer_id FROM users WHERE id=2001;");print(cur.fetchone());con.close() 
