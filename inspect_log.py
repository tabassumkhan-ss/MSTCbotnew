import csv
from collections import Counter

path = 'deposit_log.csv'
rows = []
with open(path, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)

print('Total requests:', len(rows))
status_counts = Counter(r['status'] for r in rows)
print('Status counts:', status_counts)

# Top 5 error messages (URL-decoded)
from urllib.parse import unquote
errors = [unquote(r['response']) for r in rows if r['status'] == 'ERROR']
for i, e in enumerate(errors[:5], 1):
    print(f'Error {i}:', e)
