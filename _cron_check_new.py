import sqlite3

db = sqlite3.connect('/Users/pipi/hermes-custom/data/captures.db')

# Check captures newer than last theme scan or unscanned
rows = db.execute("""
  SELECT id, title, created_at, theme_scanned_at
  FROM captures 
  WHERE theme_scanned_at IS NULL
     OR (created_at IS NOT NULL AND created_at > (SELECT MAX(theme_scanned_at) FROM captures WHERE theme_scanned_at IS NOT NULL))
  ORDER BY created_at DESC
  LIMIT 10
""").fetchall()

print(f'new_or_unscanned_count={len(rows)}')
for r in rows:
    title = (r[1] or 'N/A')[:60]
    print(f'  id={r[0]}, title="{title}", created={r[2]}, scanned={r[3]}')

# Also check when last maintain_embedding_index ran
rows2 = db.execute("""
  SELECT MAX(updated_at) FROM capture_embeddings
""").fetchone()
print(f'last_embedding_updated={rows2[0]}')

db.close()
