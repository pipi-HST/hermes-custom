"""Check knowledge base state."""
import sqlite3, os

db_path = os.path.expanduser("~/hermes-custom/data/captures.db")
db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row

# Summary stats
total = db.execute("SELECT COUNT(*) AS c FROM captures").fetchone()["c"]
unscanned = db.execute("SELECT COUNT(*) AS c FROM captures WHERE theme_scanned_at IS NULL").fetchone()["c"]
indexed = db.execute("SELECT COUNT(DISTINCT capture_id) AS c FROM capture_embeddings WHERE kind='identity' AND embedding IS NOT NULL").fetchone()["c"]
last_scan = db.execute("SELECT MAX(theme_scanned_at) AS t FROM captures").fetchone()["t"]
last_capture = db.execute("SELECT MAX(created_at) AS t FROM captures").fetchone()["t"]

print(f"total_captures={total}")
print(f"indexed_with_embeddings={indexed}")
print(f"unscanned={unscanned}")
print(f"last_theme_scan={last_scan}")
print(f"last_capture_created={last_capture}")

# Recent captures
rows = db.execute("SELECT id, file_path, created_at FROM captures ORDER BY created_at DESC LIMIT 5").fetchall()
print(f"\n--- Recent 5 captures ---")
for r in rows:
    print(f"  {r['id']}: file={r['file_path']}, created={r['created_at']}")

# Pending questions
q_count = db.execute("SELECT COUNT(*) AS c FROM pending_questions WHERE agent_type='theme' AND status='pending' AND expires_at > datetime('now')").fetchone()["c"]
print(f"\npending_theme_questions={q_count}")

db.close()
