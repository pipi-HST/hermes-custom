import sqlite3

db = sqlite3.connect('/Users/pipi/hermes-custom/data/captures.db')
cur = db.execute('SELECT COUNT(*) FROM captures')
total = cur.fetchone()[0]
cur2 = db.execute('SELECT COUNT(*) FROM captures WHERE theme_scanned_at IS NULL')
unscanned = cur2.fetchone()[0]
cur3 = db.execute('SELECT COUNT(DISTINCT capture_id) FROM capture_embeddings WHERE kind="identity" AND embedding IS NOT NULL')
indexed = cur3.fetchone()[0]
cur4 = db.execute("SELECT COUNT(*) FROM capture_embeddings WHERE kind='identity'")
total_emb = cur4.fetchone()[0]
cur5 = db.execute("SELECT strftime('%Y-%m-%d %H:%M', MAX(theme_scanned_at)) FROM captures WHERE theme_scanned_at IS NOT NULL")
last_scan = cur5.fetchone()[0] or 'never'
print(f'total_captures={total}')
print(f'unscanned={unscanned}')
print(f'indexed_identity={indexed}')
print(f'total_identity_emb={total_emb}')
print(f'last_theme_scan={last_scan}')
db.close()
