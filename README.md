Create and activate virtualenv
   ```bash
   python -m venv venv
   source venv/bin/activate      # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   Copy .env.example to .env and edit values:

cp .env.example .env
# edit .env


Initialize DB and run backend:

python backend/app.py init-db
python backend/app.py run


In another terminal (same venv) run bot:

python bot/bot.py
