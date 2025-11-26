import requests
payload = {
  "update_id": 100000,
  "message": {
    "message_id": 1,
    "from": {"id": 111111, "is_bot": False, "first_name": "Test"},
    "chat": {"id": 111111, "type": "private", "first_name": "Test"},
    "date": 1700000000,
    "text": "/start"
  }
}
r = requests.post(
    "https://sesquicentennially-inapplicable-leroy.ngrok-free.dev/webhook",
    json=payload,
    headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t-mstc-2025"},
    timeout=10
)
print(r.status_code, r.text)
